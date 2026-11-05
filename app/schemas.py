"""Shared types: the contract between extraction, detectors, evidence, policy, API and UI."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["L1", "L2", "L3", "L4"]
Method = Literal["exact", "regex", "fuzzy", "manual", "llm"]
Color = Literal["red", "yellow"]
Strength = Literal["validated", "strong", "weak"]
Status = Literal["RED", "YELLOW", "GREEN", "INCOMPLETE", "SCREEN_FAILED"]
Completeness = Literal["complete", "incomplete", "failed"]
Disposition = Literal["pending", "accepted", "overridden", "manual"]
BBox = tuple[float, float, float, float]


# ---------- criteria ----------

class PatternSpec(BaseModel):
    regex: str | None = None
    detector: str | None = None          # named preset in detect/validators.py (email, phone, ipv4, ...)
    validator: str | None = None         # ssn, luhn, aba
    context_words: list[str] = []
    ignore_case: bool = True
    strength: Strength | None = None     # fixed evidence strength for this pattern (else from validator/context)
    require_context: bool = False        # drop the hit unless a context word is nearby (e.g. bare 9-digit SSN)
    acronym_check: bool = False          # "(TS)" right after "Team Schriever" is an acronym, not a marking


class FuzzySpec(BaseModel):
    enabled: bool = False
    min_term_len: int = 6                # shorter terms are exact-only
    strict: bool = False                 # no extra edit on OCR pages


class ContextSpec(BaseModel):
    sections_exclude: list[str] = []


class Criterion(BaseModel):
    id: str
    list_no: Literal[1, 2]
    number: int | None = None            # item number in the official list
    name: str
    description: str = ""
    source_policy: str = ""
    policy_version: str = "draft"
    judgment: bool = False               # context-dependent item: yellow by default (List 2 rule)
    markings_uppercase: bool = False     # keyword is a clear match only when written as an upper-case marking
    mandatory: bool = True               # official list items cannot be switched off
    detection: list[Literal["exact", "regex", "fuzzy"]]
    keywords: list[str] = []             # the item itself is named: an exact hit is a clear match
    related_terms: list[str] = []        # associated vocabulary: a hit is only a possible match (yellow)
    patterns: list[PatternSpec] = []
    fuzzy: FuzzySpec = FuzzySpec()
    hypothesis: str | None = None
    flag_when: str = ""                  # decision card: what makes a passage a finding for this item
    not_when: str = ""                   # decision card: look-alikes that are not findings
    positive_examples: list[str] = []
    negative_examples: list[str] = []
    context: ContextSpec | None = None   # None -> loader default (List 2 excludes references)
    review: str = ""
    origin: Literal["rulebook", "custom"] = "rulebook"
    created_by: str | None = None
    active: bool = True                  # False: switched off (custom) or removed (official); not screened
    edited: bool = False                 # official item changed in the app (criteria/overrides/<id>.yaml)
    # A subject on the guide's List 2 (drones, cyber, hypersonic weapons ...): a paper that discusses it in public
    # terms gets one "topic" finding. Default: List 2 items that are not judgment items.
    topic: bool | None = None
    # The statement itself is the concern, public or not (List 2: "potential to become an item of national
    # interest", "references President and Secretary of Defense authority"): a passage about it is a possible match.
    statement: bool = False
    # The material itself is withheld (List 1: bank examination reports, well data, witness statements and response
    # plans): a specific passage about it is a finding even when the model thinks it is public.
    withheld_by_nature: bool = False

    @property
    def is_topic(self) -> bool:
        return self.topic if self.topic is not None else (self.list_no == 2 and not self.judgment)


# ---------- detection ----------

class Candidate(BaseModel):
    criterion_id: str
    list_no: int
    page: int                            # 1-based
    start: int                           # offsets into the page's original extracted text
    end: int
    text: str                            # literal slice of the document, never generated
    level: Level
    method: Method
    score: float
    details: dict = {}
    below_threshold: bool = False        # surfaced only by top-K, not by the threshold


class DetectorStatus(BaseModel):
    list_no: int
    method: str
    ok: bool
    candidates: int = 0
    seconds: float = 0.0
    error: str | None = None
    required: bool = True                # False: a failure is reported but does not make coverage incomplete


# ---------- flags ----------

class Occurrence(BaseModel):
    page: int
    start: int
    end: int
    bboxes: list[BBox] = []
    location_exact: bool = False


class Flag(BaseModel):
    id: str
    criterion_id: str
    criterion_name: str
    criterion_number: int | None = None
    list_no: int
    color: Color                         # red = clear match, yellow = possible match (guide rules)
    judgment: bool = False
    uncertain_reason: str | None = None  # why the tool is unsure (shown for yellow flags)
    origin: str
    source_policy: str
    page: int
    start: int
    end: int
    text: str
    section: str | None = None
    occurrences: list[Occurrence] = []
    levels: list[str] = []
    methods: list[str] = []
    evidence: list[Candidate] = []
    score: float = 0.0
    strength: Strength = "weak"
    low_confidence: bool = False
    low_confidence_reason: str | None = None
    explanation: str = ""
    evidence_status: Literal["verified", "mismatch"] = "verified"
    # Contextual judgment by the local language model (None = not reviewed by the model)
    llm_verdict: Literal["clear", "possible", "topic", "not_applicable"] | None = None
    llm_confidence: float | None = None
    llm_reason: str | None = None
    keyword_color: Color | None = None   # color before the model's reading, for transparency
    # Other list items found in this same passage (one finding per passage; this flag holds the most serious item)
    also: list[dict] = []
    # A List 2 topic the paper discusses in public terms: one finding for the whole paper, not one per sentence
    topic_passages: int = 0
    topic_pages: list[int] = []


# ---------- coverage & result ----------

class PageCoverage(BaseModel):
    page: int
    ok: bool
    words: int = 0
    error: str | None = None
    images_unread: int = 0               # pictures that may hold text but could not be read
    ocr: bool = False                    # text inside pictures on this page was read with OCR


class Coverage(BaseModel):
    pages_total: int = 0
    pages: list[PageCoverage] = []
    detectors: list[DetectorStatus] = []
    lists_run: list[int] = []

    @property
    def complete(self) -> bool:
        return (
            self.pages_total > 0
            and all(p.ok and not p.images_unread for p in self.pages)
            and all(d.ok for d in self.detectors if d.required)
            and self.lists_run == [1, 2]
        )


class ScreeningResult(BaseModel):
    status: Status
    completeness: Completeness
    flag_count: int
    red_count: int = 0
    yellow_count: int = 0
    lists_checked: list[dict] = []       # [{"list": 1, "items": 8}, ...] shown with every result
    flags: list[Flag]
    coverage: Coverage
    rulebook_version: str
    model_versions: dict[str, str] = {}
    timings: dict[str, float] = {}
    error: str | None = None
    title: str | None = None
    source_format: str = "pdf"
    page_sizes: list[tuple[float, float]] = Field(default_factory=list)
