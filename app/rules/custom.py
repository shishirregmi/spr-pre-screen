"""Custom checks entered in the app. They become criteria like the official list items.

- What to look for / flag when / not a finding / examples feed the model's decision cards.
- Words or names are hints that point the model at passages; on their own they never create a finding.
- Exact patterns (regular expressions) are identifiers: a match is a finding by itself, like an SSN.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas import Criterion, PatternSpec
from app.text.normalize import fold_term

MAX_PATTERN = 300
# Nested quantifiers like (a+)+ or (\w*)* can take exponential time on unlucky text.
_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


def _one_line(v: str) -> str:
    return " ".join(v.split())


def _clean(v: list[str]) -> list[str]:
    out = []
    for item in v:
        item = " ".join(str(item).split())[:300]
        if item and item not in out:
            out.append(item)
    return out


class CustomCheckIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    list_no: Literal[1, 2] = 2
    description: str = Field(min_length=10, max_length=1000)
    flag_when: str = Field("", max_length=600)
    not_when: str = Field("", max_length=600)
    keywords: list[str] = Field(default_factory=list, max_length=60)
    patterns: list[str] = Field(default_factory=list, max_length=10)
    positive_examples: list[str] = Field(default_factory=list, max_length=5)
    negative_examples: list[str] = Field(default_factory=list, max_length=5)
    judgment: bool = False
    source_policy: str = Field("", max_length=300)
    active: bool = True

    _strip = field_validator("name", "description", "flag_when", "not_when", "source_policy")(
        classmethod(lambda cls, v: _one_line(v)))
    _clean_list = field_validator("keywords", "positive_examples", "negative_examples")(
        classmethod(lambda cls, v: _clean(v)))

    @field_validator("patterns")
    @classmethod
    def _check_patterns(cls, v: list[str]) -> list[str]:
        out = []
        for rx in (p.strip() for p in v):
            if not rx:
                continue
            check_pattern(rx)
            out.append(rx)
        return out


class OfficialEditIn(BaseModel):
    """What can be changed on an official List 1 / List 2 item. Its list, number, patterns and detectors stay as
    issued; the edit is stored beside the list (criteria/overrides) and can be undone with "Restore original"."""
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=1000)
    flag_when: str = Field("", max_length=600)
    not_when: str = Field("", max_length=600)
    keywords: list[str] = Field(default_factory=list, max_length=60)
    related_terms: list[str] = Field(default_factory=list, max_length=60)
    positive_examples: list[str] = Field(default_factory=list, max_length=5)
    negative_examples: list[str] = Field(default_factory=list, max_length=5)
    judgment: bool = False
    active: bool = True                  # False: removed from screening (it can be put back)

    _strip = field_validator("name", "description", "flag_when", "not_when")(classmethod(lambda cls, v: _one_line(v)))
    _clean_list = field_validator("keywords", "related_terms", "positive_examples", "negative_examples")(
        classmethod(lambda cls, v: _clean(v)))


class TryIn(BaseModel):
    check: CustomCheckIn
    text: str = Field(max_length=20000)


def check_pattern(rx: str) -> None:
    """Refuse patterns that are broken, too long, match nothing useful, or could hang the screen."""
    if len(rx) > MAX_PATTERN:
        raise ValueError(f"pattern is longer than {MAX_PATTERN} characters")
    try:
        compiled = re.compile(rx)
    except re.error as e:
        raise ValueError(f"pattern is not valid: {e}") from e
    if _NESTED.search(rx):
        raise ValueError("pattern has a repeated group inside a repeat (like (a+)+), which can hang; simplify it")
    if compiled.fullmatch(""):
        raise ValueError("pattern matches empty text; it needs at least one required character")


def next_id(existing: list[Criterion]) -> str:
    nums = [int(m.group(1)) for c in existing if (m := re.fullmatch(r"C-(\d+)", c.id))]
    return f"C-{(max(nums) + 1) if nums else 1:03d}"


def to_criterion(body: CustomCheckIn, cid: str, created_by: str) -> Criterion:
    detection = (["regex"] if body.patterns else []) + (["exact"] if body.keywords else [])
    return Criterion(
        id=cid, list_no=body.list_no, number=None, name=body.name, description=body.description,
        source_policy=body.source_policy or "Custom check added in the app", policy_version="custom",
        judgment=body.judgment, mandatory=False, detection=detection, keywords=body.keywords,
        patterns=[PatternSpec(regex=rx, ignore_case=True, strength="strong") for rx in body.patterns],
        flag_when=body.flag_when, not_when=body.not_when, positive_examples=body.positive_examples,
        negative_examples=body.negative_examples, origin="custom", created_by=created_by, active=body.active,
    )


def try_check(body: CustomCheckIn, text: str) -> dict:
    """What the words and patterns of a check find in a sample text (no model; the meaning-based part runs during
    screening)."""
    text = text[:20000]
    folded = " " + " ".join(fold_term(text).split()) + " "
    words = []
    for kw in body.keywords:
        k = fold_term(kw)
        n = folded.count(f" {k} ") if k else 0
        if n:
            words.append({"word": kw, "count": n})
    patterns = []
    for rx in body.patterns:
        hits = []
        for m in re.finditer(rx, text, re.I):
            hits.append({"match": m.group(0)[:80], "context": " ".join(text[max(0, m.start() - 50):m.end() + 50].split())})
            if len(hits) >= 10:
                break
        patterns.append({"pattern": rx, "matches": hits})
    return {"words": words, "patterns": patterns}
