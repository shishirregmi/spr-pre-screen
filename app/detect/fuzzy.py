"""Fuzzy matching of criterion terms against same-sized document n-grams (bounded edit distance).

Avoids comparing every term to every substring: n-grams are built only for the token counts the
terms need (k-1..k+1), deduplicated, length-filtered, then scored in one vectorized RapidFuzz call
with an early-exit distance cutoff. A fuzzy score is evidence strength only, never a risk score.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from app.detect.base import NormalizedDoc
from app.schemas import Candidate, Criterion
from app.text.normalize import fold_term


def max_edits(term_len: int) -> int:
    if term_len <= 5:
        return 0        # CUI, ITAR, FOUO: exact only
    if term_len <= 10:
        return 1
    if term_len <= 20:
        return 2
    return 3


class FuzzyDetector:
    method = "fuzzy"

    def detect(self, ndoc: NormalizedDoc, criteria: list[Criterion]) -> list[Candidate]:
        # term -> owners; skip terms too short for fuzzy
        terms: dict[str, list[tuple[Criterion, str]]] = defaultdict(list)
        for c in criteria:
            if not c.fuzzy.enabled:
                continue
            for kw in c.keywords:
                t = fold_term(kw)
                if len(t) >= max(c.fuzzy.min_term_len, 6):
                    terms[t].append((c, kw))
        if not terms:
            return []

        sizes = {len(t.split()) for t in terms}
        ngram_sizes = {n for k in sizes for n in (k - 1, k, k + 1) if n >= 1}
        ocr_pages = {np_.page.no for np_ in ndoc.pages if np_.page.is_ocr}

        # Build deduplicated n-grams: text -> [(page_idx, token_start, token_end)]
        ngrams: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        pages = ndoc.readable_pages()
        for pi, np_ in enumerate(pages):
            text = np_.match.text
            toks: list[tuple[int, int]] = []
            i = 0
            for tok in text.split(" "):
                if tok:
                    toks.append((i, i + len(tok)))
                i += len(tok) + 1
            for n in ngram_sizes:
                for j in range(len(toks) - n + 1):
                    s, e = toks[j][0], toks[j + n - 1][1]
                    ngrams[text[s:e]].append((pi, s, e))
        if not ngrams:
            return []
        choices = list(ngrams)
        lengths = np.fromiter((len(x) for x in choices), dtype=np.int32, count=len(choices))

        out: list[Candidate] = []
        by_cutoff: dict[int, list[str]] = defaultdict(list)
        for t in terms:
            by_cutoff[max_edits(len(t))].append(t)

        for base_cut, group in by_cutoff.items():
            cutoff = base_cut + 1                 # +1 headroom for OCR pages, filtered per occurrence below
            lo = min(len(t) for t in group) - cutoff
            hi = max(len(t) for t in group) + cutoff
            idx = np.nonzero((lengths >= lo) & (lengths <= hi))[0]
            if idx.size == 0:
                continue
            sub = [choices[i] for i in idx]
            dist = process.cdist(group, sub, scorer=Levenshtein.distance, score_cutoff=cutoff, workers=-1)
            for ti, ci in zip(*np.nonzero(dist <= cutoff)):
                term, choice, d = group[ti], sub[ci], int(dist[ti, ci])
                if d == 0:
                    continue                      # identical after folding: the exact detector owns it
                if not _short_tokens_match(term, choice):
                    continue                      # "statement b" must not match "statement a"
                for pi, s, e in ngrams[choice]:
                    np_ = pages[pi]
                    for crit, kw in terms[term]:
                        allowed = base_cut + (1 if np_.page.no in ocr_pages and not crit.fuzzy.strict else 0)
                        if d > allowed:
                            continue
                        os_, oe = np_.match.orig_span(s, e)
                        out.append(Candidate(
                            criterion_id=crit.id, list_no=crit.list_no, page=np_.page.no, start=os_, end=oe,
                            text=np_.page.text[os_:oe], level="L4", method="fuzzy",
                            score=round(1 - d / len(term), 3),
                            details={"keyword": kw, "edits": d, "max_edits": allowed},
                        ))
        return _best_per_span(out)


def _short_tokens_match(term: str, choice: str) -> bool:
    """Tokens of 1-2 chars carry meaning (letters, numbers) and must match exactly."""
    choice_toks = set(choice.split())
    return all(tok in choice_toks for tok in term.split() if len(tok) <= 2)


def _best_per_span(cands: list[Candidate]) -> list[Candidate]:
    """k-1/k/k+1 n-grams overlap; keep the closest match per (criterion, page, overlapping region)."""
    cands.sort(key=lambda c: (c.criterion_id, c.page, c.start, -c.score))
    kept: list[Candidate] = []
    for c in cands:
        prev = kept[-1] if kept else None
        if prev and prev.criterion_id == c.criterion_id and prev.page == c.page and c.start < prev.end:
            if c.score > prev.score:
                kept[-1] = c
            continue
        kept.append(c)
    return kept
