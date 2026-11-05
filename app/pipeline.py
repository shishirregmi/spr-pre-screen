"""screen(): extract -> normalize/segment -> List 1 -> List 2 -> consolidate -> verify
-> contextual review by a local language model (optional) -> policy."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from app.detect.base import NormalizedDoc, run_detector
from app.detect.exact import ExactDetector
from app.detect.fuzzy import FuzzyDetector
from app.detect.regex import RegexDetector
from app.evidence.consolidate import consolidate
from app.evidence.merge import merge_same_passage
from app.detect.structure import check_structure
from app.evidence.verify import verify
from app.extract.pdf import extract_pdf
from app.extract.pdf import bboxes_for_span
from app.judge.contextual import is_identifier, judge
from app.judge.llm import LLMClient
from app.policy.status import active_flags, preliminary_status
from app.rules.loader import Rulebook
from app.schemas import Candidate, Coverage, PageCoverage, ScreeningResult

EventFn = Callable[[str, dict], None]


def _noop(_: str, __: dict) -> None:
    pass


def screen(path: Path, rulebook: Rulebook,
           on_event: EventFn = _noop, llm: LLMClient | None = None,
           on_progress: EventFn = _noop) -> ScreeningResult:
    """on_event: milestones for the history log. on_progress: small live steps for the review page (no paper
    text, only pages, positions, counts and item names)."""
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    cov = Coverage()

    try:
        doc = extract_pdf(path)
    except Exception as e:
        return ScreeningResult(status="SCREEN_FAILED", completeness="failed", flag_count=0, flags=[],
                               coverage=cov, rulebook_version=rulebook.version,
                               error=f"could not open document: {e}")
    cov.pages_total = len(doc.pages)
    cov.pages = [PageCoverage(page=p.no, ok=p.ok, words=len(p.words), error=p.error,
                              images_unread=p.images_unread, ocr=p.is_ocr) for p in doc.pages]
    timings["extract"] = time.perf_counter() - t0
    on_progress("extracted", {"pages": len(doc.pages), "words": sum(len(p.words) for p in doc.pages),
                              "unreadable": sum(1 for p in doc.pages if not p.ok)})

    t = time.perf_counter()
    ndoc = NormalizedDoc.build(doc)
    timings["normalize_segment"] = time.perf_counter() - t

    detectors = [ExactDetector(), RegexDetector(), FuzzyDetector()]
    model_versions: dict[str, str] = {}

    candidates: list[Candidate] = []
    for list_no in rulebook.lists_in_order():          # List 1 strictly before List 2
        t = time.perf_counter()
        crit = rulebook.for_list(list_no)
        with ThreadPoolExecutor(max_workers=len(detectors)) as pool:
            futures = [pool.submit(run_detector, d, ndoc, crit, list_no) for d in detectors]
            found: list[Candidate] = []
            for fut in futures:
                cands, status = fut.result()
                cov.detectors.append(status)
                found.extend(cands)
        candidates.extend(found)
        cov.lists_run.append(list_no)
        timings[f"list_{list_no}"] = time.perf_counter() - t
        on_event("list_screened", {"list": list_no, "candidates": len(found),
                                   "detectors": [s.model_dump() for s in cov.detectors if s.list_no == list_no]})
        on_progress("list_done", {"list": list_no, "items": len(crit), "hits": len(found)})

    t = time.perf_counter()
    # Structure checks: front matter, acknowledgments and notes (names, roles, institutions, cited sources).
    flags = verify(consolidate(candidates, rulebook, ndoc) + check_structure(ndoc, rulebook), ndoc)
    timings["consolidate_verify"] = time.perf_counter() - t
    on_progress("structure_done", {"found": [
        {"page": f.page, "color": f.color, "label": f.criterion_name,
         "boxes": bboxes_for_span(ndoc.page(f.page).page, f.start, f.end)[:6]}
        for f in flags if is_identifier(f, rulebook)][:100]})

    # Identifiers stand alone; every other hit becomes a passage finding (read in context by the model if available).
    t = time.perf_counter()
    why = None
    if llm is not None and hasattr(llm, "available"):
        ok, why = llm.available()
        why = None if ok else why
    flags, llm_status = judge(flags, ndoc, rulebook, llm, doc.title, unavailable=why, on_progress=on_progress)
    if llm is not None and not why:
        model_versions["llm"] = llm.name
    cov.detectors.append(llm_status)
    on_event("contextual_review", llm_status.model_dump())
    timings["contextual_review"] = time.perf_counter() - t

    t = time.perf_counter()
    flags = merge_same_passage(flags)
    flags.sort(key=lambda f: (f.low_confidence, f.list_no, f.color != "red", f.page, f.start))
    status, completeness = preliminary_status(flags, cov)
    active = active_flags(flags)
    on_progress("final", {"status": status, "red": sum(f.color == "red" for f in active),
                          "yellow": sum(f.color == "yellow" for f in active)})
    timings["policy"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0
    active = active_flags(flags)

    return ScreeningResult(
        status=status, completeness=completeness, flag_count=len(active), flags=flags,
        red_count=sum(f.color == "red" for f in active), yellow_count=sum(f.color == "yellow" for f in active),
        lists_checked=rulebook.lists_checked(),
        coverage=cov, rulebook_version=rulebook.version, model_versions=model_versions,
        timings={k: round(v, 3) for k, v in timings.items()}, title=doc.title,
        page_sizes=[(p.width, p.height) for p in doc.pages],
    )
