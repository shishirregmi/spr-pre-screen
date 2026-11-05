"""Evidence verification: every flag must map back to real document text, page and boxes."""
from __future__ import annotations

from app.detect.base import NormalizedDoc
from app.extract.pdf import bboxes_for_span
from app.schemas import Flag


def verify(flags: list[Flag], ndoc: NormalizedDoc) -> list[Flag]:
    for f in flags:
        ok = 1 <= f.page <= len(ndoc.pages) and ndoc.page(f.page).page.text[f.start:f.end] == f.text and f.text != ""
        if not ok:
            # Kept and visibly marked, never silently dropped.
            f.evidence_status = "mismatch"
            f.strength = "weak"
        for occ in f.occurrences:
            if 1 <= occ.page <= len(ndoc.pages):
                occ.bboxes = bboxes_for_span(ndoc.page(occ.page).page, occ.start, occ.end)
            occ.location_exact = bool(occ.bboxes)   # False -> UI highlights the whole page instead
    return flags
