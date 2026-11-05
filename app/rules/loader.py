"""Load and validate the two ordered screening lists (+ custom criteria) into a versioned rulebook."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.detect.validators import PRESETS, VALIDATORS
from app.schemas import ContextSpec, Criterion


class RulebookError(ValueError):
    pass


@dataclass
class Rulebook:
    criteria: list[Criterion]            # active criteria: what the screen uses
    version: str
    all_criteria: list[Criterion] | None = None   # including switched-off custom checks (for the Checks page)

    def lists_in_order(self) -> list[int]:
        return [1, 2]

    def lists_checked(self) -> list[dict]:
        return [{"list": n, "items": len(self.for_list(n))} for n in self.lists_in_order()]

    def for_list(self, list_no: int) -> list[Criterion]:
        return [c for c in self.criteria if c.list_no == list_no]

    def get(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == criterion_id), None)


def _validate(c: Criterion) -> None:
    if "regex" in c.detection and not c.patterns:
        raise RulebookError(f"{c.id}: regex detection needs patterns")
    if "exact" in c.detection and not (c.keywords or c.related_terms):
        raise RulebookError(f"{c.id}: exact detection needs keywords or related_terms")
    if "fuzzy" in c.detection and not c.keywords:
        raise RulebookError(f"{c.id}: fuzzy detection needs keywords")
    for p in c.patterns:
        if bool(p.regex) == bool(p.detector):
            raise RulebookError(f"{c.id}: each pattern needs exactly one of regex/detector")
        if p.regex:
            try:
                re.compile(p.regex)
            except re.error as e:
                raise RulebookError(f"{c.id}: bad regex: {e}") from e
        if p.detector and p.detector not in PRESETS:
            raise RulebookError(f"{c.id}: unknown detector {p.detector}")
        if p.validator and p.validator not in VALIDATORS:
            raise RulebookError(f"{c.id}: unknown validator {p.validator}")


def load_rulebook(criteria_dir: Path) -> Rulebook:
    criteria: list[Criterion] = []
    digest = hashlib.sha256()
    for list_no in (1, 2):
        path = criteria_dir / f"list_{list_no}.yaml"
        if not path.exists():
            # A missing list must never be silently skipped.
            raise RulebookError(f"missing screening list file: {path.name}")
        raw = path.read_bytes()
        digest.update(raw)
        doc = yaml.safe_load(raw) or {}
        if doc.get("list") != list_no:
            raise RulebookError(f"{path.name}: 'list' must be {list_no}")
        for item in doc.get("criteria") or []:
            item.setdefault("policy_version", doc.get("policy_version", "draft"))
            criteria.append(Criterion(list_no=list_no, **item))

    # Official items edited or removed in the app: the list files stay as issued, the changes live beside them
    overrides_dir = criteria_dir / "overrides"
    if overrides_dir.exists():
        by_id = {c.id: i for i, c in enumerate(criteria)}
        for path in sorted(overrides_dir.glob("*.yaml")):
            raw = path.read_bytes()
            digest.update(raw)
            change = yaml.safe_load(raw) or {}
            i = by_id.get(path.stem)
            if i is None:
                continue                  # the item left the official list; its override no longer applies
            criteria[i] = apply_override(criteria[i], change)

    custom_dir = criteria_dir / "custom"
    if custom_dir.exists():
        for path in sorted(custom_dir.glob("*.yaml")):
            raw = path.read_bytes()
            digest.update(raw)
            item = yaml.safe_load(raw)
            item["origin"] = "custom"
            criteria.append(Criterion(**item))

    seen: set[str] = set()
    for c in criteria:
        if c.related_terms and "exact" not in c.detection:
            c.detection.append("exact")
        if c.context is None:
            c.context = ContextSpec(sections_exclude=["references"] if c.list_no == 2 else [])
        if c.id in seen:
            raise RulebookError(f"duplicate criterion id {c.id}")
        seen.add(c.id)
        _validate(c)
    if not any(c.list_no == 1 and c.active for c in criteria) or not any(c.list_no == 2 and c.active for c in criteria):
        raise RulebookError("both screening lists must contain at least one criterion")
    return Rulebook(criteria=[c for c in criteria if c.active], version=digest.hexdigest()[:12],
                    all_criteria=criteria)


def save_custom_criterion(criteria_dir: Path, criterion: Criterion) -> Path:
    custom_dir = criteria_dir / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    _validate(criterion)
    path = custom_dir / f"{criterion.id}.yaml"
    data = criterion.model_dump(exclude={"origin"}, exclude_defaults=False)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


# Fields of an official item that can be changed in the app. Patterns, detectors and validators stay as issued.
OVERRIDE_FIELDS = ("name", "description", "flag_when", "not_when", "keywords", "related_terms",
                   "positive_examples", "negative_examples", "judgment", "active")


def apply_override(c: Criterion, change: dict) -> Criterion:
    update = {k: v for k, v in change.items() if k in OVERRIDE_FIELDS}
    out = c.model_copy(update={**update, "edited": True}, deep=True)
    if not (out.keywords or out.related_terms):
        out.detection = [d for d in out.detection if d not in ("exact", "fuzzy")]
    elif out.keywords and "exact" not in out.detection:
        out.detection.append("exact")
    return out


def save_override(criteria_dir: Path, criterion_id: str, change: dict) -> Path:
    d = criteria_dir / "overrides"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{criterion_id}.yaml"
    data = {k: v for k, v in change.items() if k in OVERRIDE_FIELDS}
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def delete_override(criteria_dir: Path, criterion_id: str) -> bool:
    path = criteria_dir / "overrides" / f"{criterion_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False


def delete_custom_criterion(criteria_dir: Path, criterion_id: str) -> bool:
    path = criteria_dir / "custom" / f"{criterion_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False
