"""Split pages into analysis units: L1 windows, L2 paragraphs, L3 sentences. (L4 = tokens, handled by detectors.)"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app import config
from app.extract.pdf import Document, Page

SECTION_NAMES = {
    "abstract", "introduction", "background", "related work", "method", "methods", "methodology",
    "approach", "experiments", "experimental setup", "results", "discussion", "conclusion",
    "conclusions", "acknowledgements", "acknowledgments", "references", "bibliography", "appendix",
    "table of contents", "contents", "list of figures", "list of tables", "disclaimer", "abbreviations",
    "acronyms", "glossary", "preface", "dedication", "abstract",
}
_HEADING_PREFIX = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*|[IVXLC]+)[.)]?\s+", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[])")


@dataclass
class Unit:
    id: str
    level: str                     # L1 | L2 | L3
    page: int
    start: int
    end: int
    text: str                      # whitespace-collapsed text used for embedding
    section: str | None


REFERENCE_NAMES = {"references", "bibliography", "notes", "endnotes", "works cited", "sources", "selected bibliography"}


def _heading(text: str) -> str | None:
    t = _HEADING_PREFIX.sub("", " ".join(text.split())).strip(" :.").lower()
    if t in REFERENCE_NAMES:
        return "references"
    if t in SECTION_NAMES:
        return t
    if t.startswith("appendix"):
        return "appendix"
    return None


def section_map(doc: Document) -> dict[tuple[int, int], str | None]:
    """(page, block_start) -> section active at that block."""
    current: str | None = None
    out: dict[tuple[int, int], str | None] = {}
    for p in doc.pages:
        for s, e in p.blocks:
            text = p.text[s:e]
            if len(text) < 60:
                h = _heading(text)
                if h:
                    current = h
            out[(p.no, s)] = current
    return out


def _section_at(sections: dict, page: Page, offset: int) -> str | None:
    sec = None
    for s, _ in page.blocks:
        if s <= offset:
            sec = sections.get((page.no, s))
        else:
            break
    return sec


def _clean(t: str) -> str:
    return " ".join(t.split())


def segment(doc: Document) -> dict[str, list[Unit]]:
    sections = section_map(doc)
    units: dict[str, list[Unit]] = {"L1": [], "L2": [], "L3": []}
    for p in doc.pages:
        if not p.ok or not p.words:
            continue
        # L2 paragraphs and L3 sentences
        for s, e in p.blocks:
            text = p.text[s:e]
            sec = sections.get((p.no, s))
            if len(text.split()) >= 5:
                units["L2"].append(Unit(f"p{p.no}-L2-{s}", "L2", p.no, s, e, _clean(text), sec))
            pos = 0
            for part in _SENT_SPLIT.split(text):
                k = text.find(part, pos)
                if k < 0:
                    continue
                pos = k + len(part)
                if len(part.split()) >= 4:
                    units["L3"].append(Unit(f"p{p.no}-L3-{s + k}", "L3", p.no, s + k, s + k + len(part),
                                            _clean(part), sec))
        # L1 overlapping windows
        words = p.words
        size, step = config.L1_WINDOW_WORDS, config.L1_STEP_WORDS
        if len(words) >= 5:
            i = 0
            while True:
                chunk = words[i:i + size]
                s, e = chunk[0].start, chunk[-1].end
                units["L1"].append(Unit(f"p{p.no}-L1-{s}", "L1", p.no, s, e, _clean(p.text[s:e]),
                                        _section_at(sections, p, s)))
                if i + size >= len(words):
                    break
                i += step
    return units
