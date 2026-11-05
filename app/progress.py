"""Live progress of one screening, for the review page to draw while it runs.

The pipeline reports small steps (`on_progress(kind, data)`); this turns them into a snapshot: the stage, how much is
done, which page and lines are being read right now, which pages are finished, early findings, and a plain-language
activity feed. It is kept in memory only (the history log records the outcome, not every step) and never holds
paper text, so nothing sensitive is exposed by it.
"""
from __future__ import annotations

import threading
import time
from collections import deque

STAGES = [("extract", "Reading the text"), ("list1", "List 1 checks"), ("list2", "List 2 checks"),
          ("structure", "Front matter, notes and sources"), ("reading", "Reading passages in context"),
          ("final", "Working out the result")]
# share of the bar each stage fills (the model's reading is most of the time)
_WEIGHT = {"extract": 5, "list1": 5, "list2": 5, "structure": 5, "reading": 75, "final": 5}


class ScreenProgress:
    def __init__(self, title: str = ""):
        self._lock = threading.Lock()
        self.title = title
        self.started = time.time()
        self.stage = "extract"
        self.done_stages: list[str] = []
        self.pages_total = 0
        self.chunks_total = 0
        self.chunks_done = 0
        self.chunks_by_page: dict[int, int] = {}
        self.done_by_page: dict[int, int] = {}
        self.reading: dict[str, dict] = {}       # chunk key -> {page, band, items}
        self.found: list[dict] = []              # early findings: page, boxes, color, label (no paper text)
        self.log: deque[dict] = deque(maxlen=80)
        self.finished = False
        self.model = True

    # ---------------------------------------------------------------- events from the pipeline

    def __call__(self, kind: str, data: dict) -> None:
        with self._lock:
            getattr(self, f"_on_{kind}", lambda d: None)(data)

    def _say(self, msg: str, kind: str = "info") -> None:
        self.log.append({"t": round(time.time() - self.started, 1), "msg": msg, "kind": kind})

    def _finish(self, stage: str) -> None:
        if stage not in self.done_stages:
            self.done_stages.append(stage)

    def _on_extracted(self, d: dict) -> None:
        self.pages_total = d["pages"]
        self._finish("extract")
        self.stage = "list1"
        bad = d.get("unreadable", 0)
        self._say(f"Read the text of {d['pages']} page(s), {d['words']:,} words"
                  + (f"; {bad} page(s) have no readable text" if bad else "") + ".", "warn" if bad else "info")

    def _on_list_done(self, d: dict) -> None:
        n = d["list"]
        self._finish(f"list{n}")
        self.stage = "list2" if n == 1 else "structure"
        self._say(f"List {n}: checked {d['items']} item(s) for markings, identifiers and named terms"
                  f" ({d['hits']} lead(s) to look at).")

    def _on_structure_done(self, d: dict) -> None:
        self._finish("structure")
        self.stage = "reading"
        for f in d.get("found", []):
            self._add_found(f)
        n_id = sum(1 for f in d.get("found", []))
        self._say(f"Checked the front matter, acknowledgments and notes; {n_id} finding(s) stand on their own "
                  "(identifiers, markings, cited sources)." if n_id else
                  "Checked the front matter, acknowledgments and notes.", "find" if n_id else "info")

    def _on_reading_plan(self, d: dict) -> None:
        self.chunks_total = d["chunks"]
        self.model = d.get("model", True)
        for p, n in d.get("by_page", {}).items():
            self.chunks_by_page[int(p)] = n
        if not self.model:
            self._say(f"The model is not available ({d.get('why', '')}); named terms are shown as possible "
                      "matches.", "warn")
        else:
            self._say(f"Reading {d['chunks']} passage group(s) in context with the local model"
                      f" ({d.get('skipped', 0)} reference/contents block(s) skipped).")

    def _on_chunk_start(self, d: dict) -> None:
        self.reading[d["key"]] = {"page": d["page"], "band": d.get("band"), "items": []}

    def _on_chunk_items(self, d: dict) -> None:
        r = self.reading.get(d["key"])
        if r is not None:
            r["items"] = d["items"]

    def _on_chunk_done(self, d: dict) -> None:
        self.reading.pop(d["key"], None)
        self.chunks_done += 1
        self.done_by_page[d["page"]] = self.done_by_page.get(d["page"], 0) + 1
        for f in d.get("found", []):
            self._add_found(f)
        if d.get("error"):
            self._say(f"Page {d['page']}: the model did not answer ({d['error']}).", "warn")
        elif d.get("found"):
            names = ", ".join(sorted({f["label"] for f in d["found"]}))
            self._say(f"Page {d['page']}: possible finding(s) under {names}.", "find")
        elif d.get("checked"):
            self._say(f"Page {d['page']}: read against {', '.join(d['checked'])}; nothing found.")
        if self.chunks_by_page.get(d["page"]) == self.done_by_page[d["page"]]:
            self._say(f"Page {d['page']} done.", "page")

    def _on_budget(self, d: dict) -> None:
        self._say(f"Time limit reached; {d['left']} passage group(s) were not read in context.", "warn")

    def _on_final(self, d: dict) -> None:
        self._finish("reading")
        self._finish("final")
        self.stage = "final"
        self.reading.clear()
        self.finished = True
        self._say(f"Result: {d['status']} ({d['red']} red, {d['yellow']} yellow). A reviewer decides.", "done")

    def _add_found(self, f: dict) -> None:
        if len(self.found) < 200:
            self.found.append(f)

    # ---------------------------------------------------------------- snapshot for the page

    def percent(self) -> int:
        pct = sum(_WEIGHT[s] for s in self.done_stages if s in _WEIGHT and s != "reading")
        if "reading" in self.done_stages:
            pct += _WEIGHT["reading"]
        elif self.stage == "reading" and self.chunks_total:
            pct += _WEIGHT["reading"] * self.chunks_done / self.chunks_total
        return min(100, int(pct))

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.started
            pct = self.percent()
            eta = None
            if self.stage == "reading" and self.chunks_done >= 2 and self.chunks_total:
                eta = round(elapsed / max(pct, 1) * (100 - pct))
            pages = {p: "done" if self.done_by_page.get(p, 0) >= n else ("reading" if any(
                r["page"] == p for r in self.reading.values()) else "waiting")
                for p, n in self.chunks_by_page.items()}
            label = dict(STAGES)[self.stage]
            if self.stage == "reading" and self.reading:
                label = "Reading page " + ", ".join(str(p) for p in sorted({r["page"] for r in self.reading.values()}))
            return {
                "stage": self.stage, "label": label, "percent": pct, "elapsed": round(elapsed),
                "eta": eta, "pages_total": self.pages_total, "chunks_total": self.chunks_total,
                "chunks_done": self.chunks_done, "pages": pages, "finished": self.finished, "model": self.model,
                "stages": [{"id": s, "label": lbl, "state": "done" if s in self.done_stages else
                            ("active" if s == self.stage else "waiting")} for s, lbl in STAGES],
                "reading": list(self.reading.values()), "found": self.found, "log": list(self.log)[::-1],
            }

    def brief(self) -> dict:
        with self._lock:
            return {"percent": self.percent(), "label": dict(STAGES)[self.stage]}
