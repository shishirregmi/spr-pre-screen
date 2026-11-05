"""Detector interface and the normalized document every detector reads."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from app.extract.pdf import Document, Page
from app.schemas import Candidate, Criterion, DetectorStatus
from app.text.normalize import TextView, build_views
from app.text.segment import Unit, _section_at, section_map, segment


@dataclass
class NormPage:
    page: Page
    view: TextView
    match: TextView


@dataclass
class NormalizedDoc:
    doc: Document
    pages: list[NormPage]
    units: dict[str, list[Unit]]
    sections: dict = field(default_factory=dict)

    @classmethod
    def build(cls, doc: Document) -> "NormalizedDoc":
        pages = []
        for p in doc.pages:
            view, match = build_views(p.text)
            pages.append(NormPage(p, view, match))
        return cls(doc=doc, pages=pages, units=segment(doc), sections=section_map(doc))

    def readable_pages(self) -> list[NormPage]:
        return [np_ for np_ in self.pages if np_.page.ok and np_.page.text]

    def page(self, no: int) -> NormPage:
        return self.pages[no - 1]

    def section_at(self, page_no: int, offset: int) -> str | None:
        return _section_at(self.sections, self.page(page_no).page, offset)


class Detector(Protocol):
    method: str

    def detect(self, ndoc: NormalizedDoc, criteria: list[Criterion]) -> list[Candidate]: ...


def run_detector(det: Detector, ndoc: NormalizedDoc, criteria: list[Criterion],
                 list_no: int) -> tuple[list[Candidate], DetectorStatus]:
    """Run one detector; a failure is recorded in coverage, never swallowed."""
    relevant = [c for c in criteria if det.method in c.detection]
    t0 = time.perf_counter()
    if not relevant:
        return [], DetectorStatus(list_no=list_no, method=det.method, ok=True)
    try:
        cands = det.detect(ndoc, relevant)
        return cands, DetectorStatus(list_no=list_no, method=det.method, ok=True, candidates=len(cands),
                                     seconds=round(time.perf_counter() - t0, 4))
    except Exception as e:
        return [], DetectorStatus(list_no=list_no, method=det.method, ok=False,
                                  seconds=round(time.perf_counter() - t0, 4), error=f"{type(e).__name__}: {e}")
