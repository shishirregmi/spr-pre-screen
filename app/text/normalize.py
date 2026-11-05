"""Normalized views of page text, each with a map back to original offsets.

- `view`  : NFKC + line-break de-hyphenation, case kept     -> regex detectors
- `match` : `view` lowercased, punctuation -> space, whitespace collapsed,
            OCR confusions folded (0->o, 1->i, 5->s, ...)   -> exact and fuzzy detectors
Keywords go through the same `match` folding, so both sides compare like with like.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

OCR_FOLD = str.maketrans({"0": "o", "1": "i", "5": "s", "|": "l", "$": "s", "@": "a"})


@dataclass
class TextView:
    text: str
    to_orig: list[int]             # index in this view -> index in original page text

    def orig_span(self, start: int, end: int) -> tuple[int, int]:
        return self.to_orig[start], self.to_orig[end - 1] + 1


def _fold_char(c: str) -> str:
    lc = c.lower()
    if len(lc) != 1:
        lc = c
    if lc.isspace() or not lc.isalnum():
        return " "
    return lc.translate(OCR_FOLD)


def build_views(orig: str) -> tuple[TextView, TextView]:
    chars: list[str] = []
    idx: list[int] = []
    i, n = 0, len(orig)
    while i < n:
        c = orig[i]
        # "infor-\nmation" -> "information"
        if (c == "-" and i + 2 < n and orig[i + 1] == "\n" and i > 0
                and orig[i - 1].isalpha() and orig[i + 2].islower()):
            i += 2
            continue
        for nc in unicodedata.normalize("NFKC", c):
            chars.append(nc)
            idx.append(i)
        i += 1
    view = TextView("".join(chars), idx)

    mchars: list[str] = []
    midx: list[int] = []
    for c, o in zip(view.text, view.to_orig):
        f = _fold_char(c)
        if f == " " and (not mchars or mchars[-1] == " "):
            continue
        mchars.append(f)
        midx.append(o)
    return view, TextView("".join(mchars), midx)


def fold_term(term: str) -> str:
    """Apply the `match` folding to a keyword."""
    s = unicodedata.normalize("NFKC", term)
    return " ".join("".join(_fold_char(c) for c in s).split())
