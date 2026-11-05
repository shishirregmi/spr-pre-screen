"""FastAPI app: upload, screening, review, human-only decisions, criteria, audit."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import queue
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import audit, config
from app.assist import draft_check, mask_identifiers, suggest_rewrites
from app.db import DB, now
from app.decision_guard import FINAL_STATES, DecisionDenied, check_decision
from app.extract.docx import doc_to_pdf, docx_to_pdf
from app.extract.pdf import bboxes_for_span, extract_pdf
from app.judge.contextual import _sentence
from app.judge.llm import LLMClient, OllamaClient
from app.pipeline import screen
from app.progress import ScreenProgress
from app.redact import is_sensitive, mask_value, redact_flag
from app.rules.custom import CustomCheckIn, OfficialEditIn, TryIn, next_id, to_criterion, try_check
from app.rules.loader import (Rulebook, RulebookError, delete_custom_criterion, delete_override, load_rulebook,
                              save_custom_criterion, save_override)
from app.schemas import Criterion

WEB_DIR = config.ROOT / "web"
mimetypes.add_type("font/woff2", ".woff2")      # bundled fonts (not every system maps this type)
STATUS_RANK = {"RED": 0, "SCREEN_FAILED": 1, "INCOMPLETE": 1, "YELLOW": 2, "GREEN": 3, None: 4}
SECURITY_HEADERS = {
    # Everything is served by this app; no third-party scripts, styles, fonts or connections.
    # No 'unsafe-eval': also blocks the pdf.js font-eval path used by malicious PDFs (CVE-2024-4367).
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
                                "font-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; "
                                "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}



class User(BaseModel):
    name: str
    role: str


class DispositionIn(BaseModel):
    action: str                       # accepted | overridden
    reason: str | None = None


class ManualFlagIn(BaseModel):
    criterion_id: str | None = None
    page: int
    quote: str
    note: str | None = None


class BatchIn(BaseModel):
    name: str


class DeleteIn(BaseModel):
    ids: list[str]


class DraftIn(BaseModel):
    idea: str = Field(min_length=5, max_length=2000)


class DecisionIn(BaseModel):
    outcome: str                      # released | returned
    attested: bool = False
    note: str | None = None


def create_app(data_dir: Path = config.DATA_DIR, criteria_dir: Path = config.CRITERIA_DIR,
               llm: LLMClient | None = None, queue_worker: bool = True) -> FastAPI:
    """queue_worker: screen papers one at a time on a background thread, in upload order (False: screen inside the
    request's background task, used by the tests)."""
    files_dir = data_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    db = DB(data_dir / "spr.sqlite3")
    state = {"rulebook": load_rulebook(criteria_dir)}
    llm = llm if llm is not None else (OllamaClient() if config.LLM_ENABLED else None)

    pending: queue.Queue[str] = queue.Queue()
    live: dict[str, ScreenProgress] = {}      # live progress of the screenings running now (and the last few)

    def worker() -> None:
        while True:
            pid = pending.get()
            try:
                run_screening(pid)
            except Exception:  # never let one paper stop the queue
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if queue_worker:
            # papers left queued or half-screened by a restart are picked up again, oldest first
            for r in db.all("SELECT id FROM papers WHERE state IN ('UPLOADED','SCREENING') ORDER BY uploaded_at"):
                db.execute("UPDATE papers SET state='UPLOADED' WHERE id=?", (r["id"],))
                pending.put(r["id"])
            threading.Thread(target=worker, daemon=True, name="screening-queue").start()
        yield

    app = FastAPI(title="SPR Pre-Screen", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"     # paper content must not sit in browser caches
        else:
            response.headers["Cache-Control"] = "no-cache"     # always re-check the page/scripts after an update
        return response

    # ---------- helpers ----------

    def current_user() -> User:
        # No accounts: whoever uses this local tool is the reviewer. A release still needs a person to choose
        # Release, after every finding is reviewed and the attestation is ticked (decision_guard); the screening
        # itself never releases anything.
        return User(name=config.REVIEWER_NAME, role="reviewer")

    def get_paper(paper_id: str) -> dict:
        p = db.paper(paper_id)
        if not p:
            raise HTTPException(404, "Paper not found")
        return p

    def dispositions_for(paper_id: str) -> dict[str, dict]:
        return {d["flag_id"]: d for d in db.all("SELECT * FROM dispositions WHERE paper_id=?", (paper_id,))}

    def required_flag_ids(paper: dict) -> list[str]:
        result = paper.get("result") or {}
        return [f["id"] for f in result.get("flags", []) if not f["low_confidence"]]

    def rulebook() -> Rulebook:
        return state["rulebook"]

    def prepare_pdf(p: dict) -> str | None:
        """Make sure the viewable/screenable PDF exists. Word files are rendered to PDF."""
        pdf = Path(p["path"])
        if pdf.exists():
            return None
        original = Path(p["original_path"])
        if p.get("source_format") == "docx":
            return docx_to_pdf(original, pdf)
        if p.get("source_format") == "doc":
            return doc_to_pdf(original, pdf)
        shutil.copyfile(original, pdf)
        return None

    def run_screening(paper_id: str) -> None:
        p = db.paper(paper_id)
        if p is None:
            return                       # deleted while it waited in the queue
        db.execute("UPDATE papers SET state='SCREENING' WHERE id=?", (paper_id,))
        rb = rulebook()
        tracker = live[paper_id] = ScreenProgress(p["title"] or p["filename"])
        for old in list(live)[:-20]:          # keep only the latest few finished ones
            if live[old].finished:
                live.pop(old, None)

        def on_event(kind: str, data: dict) -> None:
            audit.record(db, "system", kind, paper_id, data, actor_type="system")

        try:
            docx_title = prepare_pdf(p)
            result = screen(Path(p["path"]), rb, on_event=on_event, llm=llm, on_progress=tracker)
            result.source_format = p.get("source_format") or "pdf"
            if docx_title:
                result.title = docx_title
            new_state = "SCREEN_FAILED" if result.status == "SCREEN_FAILED" else "SCREENED"
            db.execute("UPDATE papers SET state=?, result_json=?, title=COALESCE(title, ?), error=?, screened_at=? "
                       "WHERE id=?", (new_state, result.model_dump_json(), result.title, result.error, now(), paper_id))
            audit.record(db, "system", "screened", paper_id, {
                "status": result.status, "completeness": result.completeness, "flag_count": result.flag_count,
                "rulebook_version": result.rulebook_version, "models": result.model_versions,
                "timings": result.timings}, actor_type="system")
        except Exception as e:  # fail safe: never green
            db.execute("UPDATE papers SET state='SCREEN_FAILED', error=?, screened_at=? WHERE id=?",
                       (str(e), now(), paper_id))
            audit.record(db, "system", "screen_failed", paper_id, {"error": str(e)}, actor_type="system")

    def enqueue(pid: str, background: BackgroundTasks) -> None:
        if queue_worker:
            pending.put(pid)
        else:
            background.add_task(run_screening, pid)

    def top_items(r: dict) -> list[dict]:
        """List items found in a paper (most serious first), for the bulk table."""
        seen: dict[str, dict] = {}
        for f in r.get("flags", []):
            if f["low_confidence"]:
                continue
            for it in [f] + f.get("also", []):          # a merged passage counts every item it matched
                k = it["criterion_id"]
                if k not in seen or it["color"] == "red":
                    seen[k] = {"id": k, "list": it["list_no"], "number": it.get("criterion_number"),
                               "name": it["criterion_name"], "color": it["color"]}
        return sorted(seen.values(), key=lambda i: (i["color"] != "red", i["list"], i["number"] or 0))

    def summary(p: dict) -> dict:
        r = p.get("result") or {}
        disp = dispositions_for(p["id"])
        req = required_flag_ids(p)
        return {
            "batch_id": p.get("batch_id"), "items": top_items(r), "screened_at": p.get("screened_at"),
            "pages": (r.get("coverage") or {}).get("pages_total"), "seconds": (r.get("timings") or {}).get("total"),
            "id": p["id"], "title": p["title"] or p["filename"], "filename": p["filename"],
            "state": p["state"], "uploaded_by": p["uploaded_by"], "uploaded_at": p["uploaded_at"],
            "status": r.get("status") or ("SCREEN_FAILED" if p["state"] == "SCREEN_FAILED" else None),
            "completeness": r.get("completeness"), "flag_count": r.get("flag_count"),
            "red_count": r.get("red_count"), "yellow_count": r.get("yellow_count"),
            "source_format": p.get("source_format") or "pdf",
            "reviewed": sum(1 for f in req if f in disp), "to_review": len(req), "error": p.get("error"),
            "progress": live[p["id"]].brief() if p["state"] == "SCREENING" and p["id"] in live else None,
        }

    # ---------- routes ----------

    @app.get("/api/health")
    def health():
        return {"ok": True, "rulebook_version": rulebook().version,
                "llm": getattr(llm, "name", None),
                "llm_ready": (llm.available()[0] if hasattr(llm, "available") else llm is not None),
                "cloud_llm_allowed": config.CLOUD_LLM_ALLOWED}

    @app.post("/api/papers")
    async def upload(background: BackgroundTasks, files: list[UploadFile] = File(...),
                     batch: str | None = Form(None), user: User = Depends(current_user)):
        """Batch upload of PDF and Word (.docx) files. Each file is checked and reported separately."""
        if batch and not db.one("SELECT id FROM batches WHERE id=?", (batch,)):
            raise HTTPException(404, "Unknown batch")
        results = []
        for file in files:
            name = Path(file.filename or "upload").name
            data = await file.read()
            if len(data) > config.MAX_UPLOAD_BYTES:
                results.append({"filename": name, "error": "File too large"})
                continue
            if data.startswith(b"%PDF"):
                fmt = "pdf"
            elif data.startswith(b"PK") and name.lower().endswith(".docx"):
                fmt = "docx"
            elif data.startswith(b"\xd0\xcf\x11\xe0") and name.lower().endswith(".doc"):
                fmt = "doc"
            else:
                results.append({"filename": name, "error": "Only PDF and Word (.docx, .doc) files are supported"})
                continue
            pid = uuid.uuid4().hex[:12]
            original = files_dir / f"{pid}.original.{fmt}"
            original.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            db.execute("INSERT INTO papers (id, title, filename, path, sha256, uploaded_by, uploaded_at, state, "
                       "source_format, original_path, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, None, name, str(files_dir / f"{pid}.pdf"), sha, user.name, now(), "UPLOADED",
                        fmt, str(original), batch))
            audit.record(db, user.name, "uploaded", pid, {"filename": name, "sha256": sha, "format": fmt,
                                                          "batch": batch})
            enqueue(pid, background)
            results.append({"filename": name, "id": pid})
        if not any("id" in r for r in results):
            raise HTTPException(415, "; ".join(f"{r['filename']}: {r['error']}" for r in results))
        return {"papers": results}

    @app.get("/api/papers")
    def list_papers(batch: str | None = None, user: User = Depends(current_user)):
        rows = db.all("SELECT id FROM papers WHERE batch_id=?", (batch,)) if batch else db.all("SELECT id FROM papers")
        papers = [summary(p) for p in (db.paper(r["id"]) for r in rows)]
        papers.sort(key=lambda s: (s["state"] in FINAL_STATES, STATUS_RANK.get(s["status"], 4),
                                   -(s["flag_count"] or 0), s["uploaded_at"]))
        waiting = [r["id"] for r in db.all("SELECT id FROM papers WHERE state='UPLOADED' ORDER BY uploaded_at")]
        for item in papers:
            item["queue_position"] = waiting.index(item["id"]) + 1 if item["id"] in waiting else None
        return papers

    @app.post("/api/batches")
    def create_batch(body: BatchIn, user: User = Depends(current_user)):
        bid = uuid.uuid4().hex[:10]
        name = " ".join(body.name.split())[:120] or f"Batch {now()}"
        db.execute("INSERT INTO batches (id, name, created_by, created_at) VALUES (?,?,?,?)",
                   (bid, name, user.name, now()))
        audit.record(db, user.name, "batch_created", None, {"batch": bid, "name": name})
        return {"id": bid, "name": name}

    @app.get("/api/batches")
    def list_batches(user: User = Depends(current_user)):
        out = []
        for b in db.all("SELECT * FROM batches ORDER BY created_at DESC"):
            rows = db.all("SELECT state, result_json FROM papers WHERE batch_id=?", (b["id"],))
            colors = {"RED": 0, "YELLOW": 0, "GREEN": 0, "INCOMPLETE": 0}
            for r in rows:
                st = json.loads(r["result_json"]).get("status") if r["result_json"] else None
                if r["state"] == "SCREEN_FAILED" or st in ("INCOMPLETE", "SCREEN_FAILED"):
                    colors["INCOMPLETE"] += 1
                elif st in colors:
                    colors[st] += 1
            waiting = sum(r["state"] in ("UPLOADED", "SCREENING") for r in rows)
            out.append({**b, "papers": len(rows), "waiting": waiting, "done": len(rows) - waiting, **colors})
        return out

    def delete_paper(pid: str, user: User) -> str | None:
        """Remove a paper, its files, findings, reviews and decision. The history log keeps a 'paper_deleted' entry
        (it is append-only). A paper being screened right now cannot be deleted; returns why not, or None."""
        p = db.paper(pid)
        if p is None:
            return "not found"
        if p["state"] == "SCREENING":
            return "being screened right now"
        for table in ("dispositions", "manual_flags", "decisions"):
            db.execute(f"DELETE FROM {table} WHERE paper_id=?", (pid,))
        db.execute("DELETE FROM papers WHERE id=?", (pid,))
        for f in files_dir.glob(f"{pid}.*"):
            f.unlink(missing_ok=True)
        status = (p.get("result") or {}).get("status")
        audit.record(db, user.name, "paper_deleted", pid, {"filename": p["filename"], "status": status,
                                                            "state": p["state"], "batch": p.get("batch_id")})
        return None

    def delete_many(ids: list[str], user: User) -> dict:
        skipped = {}
        for pid in dict.fromkeys(ids):
            why = delete_paper(pid, user)
            if why:
                skipped[pid] = why
        return {"deleted": len(set(ids)) - len(skipped), "skipped": skipped}

    @app.delete("/api/papers/{paper_id}")
    def delete_one(paper_id: str, user: User = Depends(current_user)):
        why = delete_paper(paper_id, user)
        if why == "not found":
            raise HTTPException(404, "Paper not found")
        if why:
            raise HTTPException(409, f"This paper is {why}; try again when it finishes")
        return {"ok": True}

    @app.post("/api/papers/delete")
    def delete_papers(body: DeleteIn, user: User = Depends(current_user)):
        """Delete several papers (a batch view or every paper)."""
        return delete_many(body.ids, user)

    @app.delete("/api/batches/{batch_id}")
    def delete_batch(batch_id: str, user: User = Depends(current_user)):
        b = db.one("SELECT * FROM batches WHERE id=?", (batch_id,))
        if not b:
            raise HTTPException(404, "Batch not found")
        out = delete_many([r["id"] for r in db.all("SELECT id FROM papers WHERE batch_id=?", (batch_id,))], user)
        if not out["skipped"]:
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            audit.record(db, user.name, "batch_deleted", None, {"batch": batch_id, "name": b["name"]})
        return out

    @app.get("/api/papers/{paper_id}")
    def paper_detail(paper_id: str, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        disp = dispositions_for(paper_id)
        result = p.get("result")
        if result:
            result["flags"] = [dict(redact_flag(f), disposition=disp.get(f["id"])) for f in result["flags"]]
        manual = [dict(json.loads(m["data_json"]), id=m["id"], reviewer=m["reviewer"], at=m["at"])
                  for m in db.all("SELECT * FROM manual_flags WHERE paper_id=? ORDER BY at", (paper_id,))]
        decision = db.one("SELECT * FROM decisions WHERE paper_id=?", (paper_id,))
        return {"summary": summary(p), "result": result, "manual_flags": manual, "decision": decision}

    @app.post("/api/papers/{paper_id}/flags/{flag_id}/reveal")
    def reveal(paper_id: str, flag_id: str, user: User = Depends(current_user)):
        """Show one masked identifier in full. Every reveal is audited."""
        p = get_paper(paper_id)
        flag = next((f for f in (p.get("result") or {}).get("flags", []) if f["id"] == flag_id), None)
        if flag is None:
            raise HTTPException(404, "Flag not found")
        audit.record(db, user.name, "identifier_revealed", paper_id,
                     {"flag_id": flag_id, "criterion_id": flag["criterion_id"], "page": flag["page"]})
        return {"text": flag["text"]}

    @app.get("/api/papers/{paper_id}/progress")
    def paper_progress(paper_id: str, user: User = Depends(current_user)):
        """What the screen is doing right now: stage, % done, the page and lines being read, early findings."""
        p = get_paper(paper_id)
        if p["state"] == "UPLOADED":
            waiting = [r["id"] for r in db.all("SELECT id FROM papers WHERE state='UPLOADED' ORDER BY uploaded_at")]
            return {"state": "UPLOADED", "queued": True,
                    "position": waiting.index(paper_id) + 1 if paper_id in waiting else None}
        t = live.get(paper_id)
        if t is None:
            return {"state": p["state"], "finished": p["state"] != "SCREENING"}
        return {"state": p["state"], **t.snapshot()}

    @app.get("/api/papers/{paper_id}/file")
    def paper_file(paper_id: str, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        if not Path(p["path"]).exists():
            raise HTTPException(404, "Document not ready")
        return FileResponse(p["path"], media_type="application/pdf")

    @app.get("/api/papers/{paper_id}/original")
    def paper_original(paper_id: str, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        return FileResponse(p["original_path"] or p["path"], filename=p["filename"])

    @app.post("/api/papers/{paper_id}/rescreen")
    def rescreen(paper_id: str, background: BackgroundTasks, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        if p["state"] in FINAL_STATES or p["state"] == "SCREENING":
            raise HTTPException(409, f"Cannot rescreen a paper that is {p['state'].lower()}")
        cleared = db.execute("DELETE FROM dispositions WHERE paper_id=?", (paper_id,)).rowcount
        db.execute("UPDATE papers SET state='UPLOADED' WHERE id=?", (paper_id,))
        audit.record(db, user.name, "rescreen_requested", paper_id, {"dispositions_cleared": cleared})
        enqueue(paper_id, background)
        return {"ok": True}

    @app.post("/api/papers/{paper_id}/flags/{flag_id}/disposition")
    def disposition(paper_id: str, flag_id: str, body: DispositionIn, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        if p["state"] in FINAL_STATES:
            raise HTTPException(409, "Decision already made")
        flags = {f["id"]: f for f in (p.get("result") or {}).get("flags", [])}
        if flag_id not in flags:
            raise HTTPException(404, "Flag not found")
        if body.action not in ("accepted", "overridden"):
            raise HTTPException(422, "action must be 'accepted' or 'overridden'")
        if body.action == "overridden" and not (body.reason and body.reason.strip()):
            raise HTTPException(422, "Overriding a flag requires a reason")
        db.execute("INSERT OR REPLACE INTO dispositions (paper_id, flag_id, action, reason, reviewer, at) "
                   "VALUES (?,?,?,?,?,?)", (paper_id, flag_id, body.action, body.reason, user.name, now()))
        if p["state"] in ("SCREENED", "SCREEN_FAILED"):
            db.execute("UPDATE papers SET state='IN_REVIEW' WHERE id=?", (paper_id,))
        f = flags[flag_id]
        audit.record(db, user.name, f"flag_{body.action}", paper_id,
                     {"flag_id": flag_id, "criterion_id": f["criterion_id"], "reason": body.reason,
                      "also": [a["criterion_id"] for a in f.get("also", [])]})
        return {"ok": True}

    @app.post("/api/papers/{paper_id}/manual-flags")
    def manual_flag(paper_id: str, body: ManualFlagIn, user: User = Depends(current_user)):
        p = get_paper(paper_id)
        if p["state"] in FINAL_STATES:
            raise HTTPException(409, "Decision already made")
        if body.criterion_id and not rulebook().get(body.criterion_id):
            raise HTTPException(422, "Unknown criterion")
        doc = extract_pdf(Path(p["path"]))
        if not 1 <= body.page <= len(doc.pages):
            raise HTTPException(422, "Page out of range")
        page = doc.pages[body.page - 1]
        k = page.text.lower().find(body.quote.strip().lower()) if body.quote.strip() else -1
        loc = {"start": k, "end": k + len(body.quote.strip()),
               "bboxes": bboxes_for_span(page, k, k + len(body.quote.strip()))} if k >= 0 else {"bboxes": []}
        mid = uuid.uuid4().hex[:12]
        data = {"criterion_id": body.criterion_id, "page": body.page, "quote": body.quote, "note": body.note,
                "location_exact": bool(loc["bboxes"]), **loc}
        db.execute("INSERT INTO manual_flags (id, paper_id, data_json, reviewer, at) VALUES (?,?,?,?,?)",
                   (mid, paper_id, json.dumps(data), user.name, now()))
        if p["state"] in ("SCREENED", "SCREEN_FAILED"):
            db.execute("UPDATE papers SET state='IN_REVIEW' WHERE id=?", (paper_id,))
        audit.record(db, user.name, "manual_flag_added", paper_id, {"manual_flag_id": mid, **data})
        return {"id": mid, **data}

    @app.post("/api/papers/{paper_id}/decision")
    def decide(paper_id: str, body: DecisionIn, user: User = Depends(current_user)):
        with db.lock:
            p = get_paper(paper_id)
            try:
                check_decision(role=user.role, paper=p, flag_ids_requiring_disposition=required_flag_ids(p),
                               dispositions=dispositions_for(paper_id), outcome=body.outcome,
                               attested=body.attested, note=body.note)
            except DecisionDenied as e:
                audit.record(db, user.name, "decision_denied", paper_id, {"outcome": body.outcome, "why": str(e)})
                raise HTTPException(e.status_code, str(e)) from e
            new_state = "RELEASED" if body.outcome == "released" else "RETURNED"
            rb_version = (p.get("result") or {}).get("rulebook_version")
            db.execute("INSERT INTO decisions (paper_id, outcome, reviewer, note, rulebook_version, at) "
                       "VALUES (?,?,?,?,?,?)", (paper_id, body.outcome, user.name, body.note, rb_version, now()))
            db.execute("UPDATE papers SET state=? WHERE id=?", (new_state, paper_id))
            audit.record(db, user.name, f"decision_{body.outcome}", paper_id,
                         {"note": body.note, "attested": body.attested, "rulebook_version": rb_version})
        return {"ok": True, "state": new_state}

    @app.get("/api/criteria")
    def criteria(user: User = Depends(current_user)):
        rb = rulebook()
        return {"version": rb.version, "criteria": [c.model_dump() for c in rb.criteria]}

    @app.post("/api/criteria")
    def add_criterion(c: Criterion, user: User = Depends(current_user)):
        # Guide: "Use only the lists you are given" but "make it updatable". Only an administrator records
        # an update that comes from new official guidance; reviewers cannot add their own rules.
        if not c.source_policy.strip():
            raise HTTPException(422, "source_policy is required: cite the guidance this update comes from")
        existing = rulebook().get(c.id)
        if existing and existing.origin == "rulebook":
            raise HTTPException(409, "Official list items are edited in the YAML files, not via the API")
        c = c.model_copy(update={"origin": "custom", "created_by": user.name})
        try:
            save_custom_criterion(criteria_dir, c)
            state["rulebook"] = load_rulebook(criteria_dir)
        except (RulebookError, ValueError) as e:
            (criteria_dir / "custom" / f"{c.id}.yaml").unlink(missing_ok=True)
            state["rulebook"] = load_rulebook(criteria_dir)
            raise HTTPException(422, str(e)) from e
        audit.record(db, user.name, "criterion_saved", None, c.model_dump())
        return {"ok": True, "rulebook_version": state["rulebook"].version}

    # ---------- custom checks entered in the app (they become criteria) ----------

    def all_criteria() -> list[Criterion]:
        rb = rulebook()
        return rb.all_criteria or rb.criteria

    def custom_or_404(check_id: str) -> Criterion:
        c = next((x for x in all_criteria() if x.id == check_id), None)
        if c is None:
            raise HTTPException(404, "Check not found")
        if c.origin != "custom":
            raise HTTPException(409, "Official list items are changed with /api/lists/{id}")
        return c

    def store_check(c: Criterion, action: str, user: User) -> dict:
        try:
            save_custom_criterion(criteria_dir, c)
            state["rulebook"] = load_rulebook(criteria_dir)
        except (RulebookError, ValueError) as e:
            state["rulebook"] = load_rulebook(criteria_dir)
            raise HTTPException(422, str(e)) from e
        audit.record(db, user.name, action, None, {"check": c.id, "name": c.name, "list": c.list_no,
                                                     "active": c.active})
        return {"check": c.model_dump(), "rulebook_version": state["rulebook"].version}

    @app.get("/api/checks")
    def list_checks(user: User = Depends(current_user)):
        crit = all_criteria()
        return {"custom": [c.model_dump() for c in crit if c.origin == "custom"],
                "official": [c.model_dump() for c in crit if c.origin != "custom"],
                "rulebook_version": rulebook().version}

    @app.post("/api/checks")
    def create_check(body: CustomCheckIn, user: User = Depends(current_user)):
        return store_check(to_criterion(body, next_id(all_criteria()), user.name), "check_created", user)

    @app.put("/api/checks/{check_id}")
    def update_check(check_id: str, body: CustomCheckIn, user: User = Depends(current_user)):
        old = custom_or_404(check_id)
        return store_check(to_criterion(body, old.id, old.created_by or user.name), "check_updated", user)

    @app.delete("/api/checks/{check_id}")
    def delete_check(check_id: str, user: User = Depends(current_user)):
        c = custom_or_404(check_id)
        delete_custom_criterion(criteria_dir, c.id)
        state["rulebook"] = load_rulebook(criteria_dir)
        audit.record(db, user.name, "check_deleted", None, {"check": c.id, "name": c.name})
        return {"ok": True, "rulebook_version": state["rulebook"].version}

    def official_or_404(item_id: str) -> Criterion:
        c = next((x for x in all_criteria() if x.id == item_id), None)
        if c is None or c.origin == "custom":
            raise HTTPException(404, "List item not found")
        return c

    def store_override(c: Criterion, change: dict, action: str, user: User) -> dict:
        """Save an edit to an official item beside the list file. Refused if it would leave a list empty."""
        path = criteria_dir / "overrides" / f"{c.id}.yaml"
        before = path.read_bytes() if path.exists() else None
        save_override(criteria_dir, c.id, change)
        try:
            state["rulebook"] = load_rulebook(criteria_dir)
        except (RulebookError, ValueError) as e:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
            state["rulebook"] = load_rulebook(criteria_dir)
            msg = str(e)
            if "at least one" in msg:
                msg = f"List {c.list_no} would have no items left; a screen must always check both lists"
            raise HTTPException(409, msg) from e
        audit.record(db, user.name, action, None, {"item": c.id, "list": c.list_no, "change": change})
        item = next(x for x in all_criteria() if x.id == c.id)
        return {"item": item.model_dump(), "rulebook_version": state["rulebook"].version}

    def current_override(item_id: str) -> dict:
        path = criteria_dir / "overrides" / f"{item_id}.yaml"
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}

    @app.put("/api/lists/{item_id}")
    def edit_list_item(item_id: str, body: OfficialEditIn, user: User = Depends(current_user)):
        c = official_or_404(item_id)
        change = {**current_override(c.id), **body.model_dump()}
        return store_override(c, change, "list_item_edited", user)

    @app.delete("/api/lists/{item_id}")
    def remove_list_item(item_id: str, user: User = Depends(current_user)):
        c = official_or_404(item_id)
        return store_override(c, {**current_override(c.id), "active": False}, "list_item_removed", user)

    @app.post("/api/lists/{item_id}/restore")
    def restore_list_item(item_id: str, user: User = Depends(current_user)):
        """Undo every change to an official item (edits and removal)."""
        c = official_or_404(item_id)
        delete_override(criteria_dir, c.id)
        state["rulebook"] = load_rulebook(criteria_dir)
        audit.record(db, user.name, "list_item_restored", None, {"item": c.id, "list": c.list_no})
        item = next(x for x in all_criteria() if x.id == c.id)
        return {"item": item.model_dump(), "rulebook_version": state["rulebook"].version}

    # ---------- suggestions from the local model (it never decides or changes anything) ----------

    def assistant() -> LLMClient:
        if llm is None:
            raise HTTPException(503, "The local model is not set up, so suggestions are not available")
        if hasattr(llm, "available"):
            ok, why = llm.available()
            if not ok:
                raise HTTPException(503, f"The local model is not available: {why}")
        return llm

    def ask(fn, *args):
        try:
            return fn(assistant(), *args)
        except HTTPException:
            raise
        except Exception as e:  # timeouts, connection drops, malformed replies
            raise HTTPException(503, f"The model did not answer ({type(e).__name__}); try again") from e

    @app.post("/api/checks/draft")
    def draft_custom_check(body: DraftIn, user: User = Depends(current_user)):
        """Fill the Checks form from a short idea. Nothing is saved until the reviewer saves the form."""
        official = [c for c in all_criteria() if c.origin != "custom"]
        out = ask(draft_check, body.idea, official)
        audit.record(db, user.name, "check_drafted", None, {"idea": body.idea[:300]})
        return out

    @app.post("/api/papers/{paper_id}/flags/{flag_id}/rewrites")
    def rewrite_flag(paper_id: str, flag_id: str, user: User = Depends(current_user)):
        """2-3 replacement sentences for a finding. The paper is not changed; the author decides."""
        p = get_paper(paper_id)
        f = next((x for x in (p.get("result") or {}).get("flags", []) if x["id"] == flag_id), None)
        if f is None:
            raise HTTPException(404, "Finding not found")
        crit = next((c for c in all_criteria() if c.id == f["criterion_id"]), None) or Criterion(
            id=f["criterion_id"], list_no=f["list_no"], name=f["criterion_name"], detection=[])
        prepare_pdf(p)
        pages = extract_pdf(Path(p["path"])).pages
        if not 1 <= f["page"] <= len(pages):
            raise HTTPException(409, "The page of this finding could not be read")
        text = pages[f["page"] - 1].text
        st, en = _sentence(text, f["start"], f["end"], 0, len(text))
        sentence = " ".join(text[st:en].split())
        flagged = text[f["start"]:f["end"]]
        reason = f.get("llm_reason") or f.get("uncertain_reason") or f.get("explanation") or ""
        out = ask(suggest_rewrites, rulebook(), crit, sentence, flagged, " ".join(text[max(0, st - 400):st].split()),
                  " ".join(text[en:en + 400].split()), reason)
        shown = mask_identifiers(sentence, rulebook())     # never echo a full identifier back to the screen
        if is_sensitive(f):
            shown = shown.replace(" ".join(flagged.split()), mask_value(flagged))
        audit.record(db, user.name, "rewrites_suggested", paper_id,
                     {"flag_id": flag_id, "criterion_id": f["criterion_id"], "suggestions": len(out["suggestions"])})
        return {"original": shown, **out}

    @app.post("/api/checks/try")
    def try_custom_check(body: TryIn, user: User = Depends(current_user)):
        return try_check(body.check, body.text)

    @app.post("/api/papers/rescreen-open")
    def rescreen_open(background: BackgroundTasks, user: User = Depends(current_user)):
        """Apply the current checks to every paper that has no decision yet."""
        rows = db.all("SELECT id FROM papers WHERE state IN ('SCREENED','SCREEN_FAILED','IN_REVIEW') "
                      "ORDER BY uploaded_at")
        for r in rows:
            db.execute("DELETE FROM dispositions WHERE paper_id=?", (r["id"],))
            db.execute("UPDATE papers SET state='UPLOADED' WHERE id=?", (r["id"],))
            enqueue(r["id"], background)
        audit.record(db, user.name, "rescreen_open_papers", None, {"papers": len(rows),
                                                                  "rulebook_version": rulebook().version})
        return {"queued": len(rows)}

    @app.get("/api/papers/{paper_id}/audit")
    def paper_audit(paper_id: str, user: User = Depends(current_user)):
        get_paper(paper_id)
        return audit.entries(db, paper_id)

    @app.get("/api/audit/verify")
    def audit_verify(user: User = Depends(current_user)):
        return {"chain_intact": audit.verify_chain(db)}

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/bulk")
    def bulk_page():
        return FileResponse(WEB_DIR / "bulk.html")

    @app.get("/checks")
    def checks_page():
        return FileResponse(WEB_DIR / "checks.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    app.state.db = db
    app.state.run_screening = run_screening
    return app


# Run with: uvicorn app.main:create_app --factory
