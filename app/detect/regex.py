"""Regex / structured detection. A pattern hit is only a candidate; validators and context grade it."""
from __future__ import annotations

import re

from app.detect.base import NormalizedDoc
from app.detect.validators import PRESETS, VALIDATORS
from app.schemas import Candidate, Criterion

CONTEXT_CHARS = 80
_SMALL_WORDS = {"of", "and", "the", "for", "in", "on", "to", "a", "an", "&"}


def is_acronym_definition(text: str, start: int, raw: str) -> bool:
    """True when "(TS)" follows words whose initials spell TS, e.g. "Team Schriever (TS)"."""
    letters = re.sub(r"[^A-Za-z]", "", raw).lower()
    if not letters:
        return False
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text[max(0, start - 120):start])
             if w.lower() not in _SMALL_WORDS]
    initials = "".join(w[0].lower() for w in words[-len(letters):])
    return len(words) >= len(letters) and initials == letters


# Validators that only rule out look-alikes (a DFARS clause shaped like a phone number), unlike checksums
FILTER_ONLY = {"phone"}


class RegexDetector:
    method = "regex"

    def detect(self, ndoc: NormalizedDoc, criteria: list[Criterion]) -> list[Candidate]:
        compiled = []
        for c in criteria:
            for p in c.patterns:
                if p.detector:
                    rx, flags, validator = PRESETS[p.detector]
                    name = p.detector
                else:
                    rx, flags, validator = p.regex, re.I if p.ignore_case else 0, None
                    name = "custom"
                validator = p.validator or validator
                compiled.append((c, re.compile(rx, flags), name, validator, [w.lower() for w in p.context_words], p))

        out: list[Candidate] = []
        for np_ in ndoc.readable_pages():
            text = np_.view.text
            lower = text.lower()
            for crit, rx, name, validator, ctx_words, spec in compiled:
                for m in rx.finditer(text):
                    raw = m.group(0)
                    validated = None
                    if validator:
                        validated = VALIDATORS[validator](raw)
                        if not validated:
                            continue      # failed checksum/format rules: not an identifier
                        if validator in FILTER_ONLY:
                            validated = None      # it only rules look-alikes out; it is no proof of an identifier
                    window = lower[max(0, m.start() - CONTEXT_CHARS): m.end() + CONTEXT_CHARS]
                    ctx = [w for w in ctx_words if w in window]
                    if spec.require_context and not ctx:
                        continue
                    os_, oe = np_.view.orig_span(m.start(), m.end())
                    strength = spec.strength or ("validated" if validated else ("strong" if ctx else "weak"))
                    acronym = spec.acronym_check and is_acronym_definition(text, m.start(), raw)
                    if acronym:
                        strength = "weak"
                    score = {"validated": 0.95, "strong": 0.8, "weak": 0.6}[strength]
                    out.append(Candidate(
                        criterion_id=crit.id, list_no=crit.list_no, page=np_.page.no, start=os_, end=oe,
                        text=np_.page.text[os_:oe], level="L4", method="regex", score=score,
                        details={"pattern": name, "validator": validator, "validated": bool(validated),
                                 "context_words": ctx, "strength": strength, "acronym_definition": acronym},
                    ))
        return out
