"""One finding per passage: when several list items flag the same words, show them as one card.

The reviewer accepts or overrides the passage once. The finding is filed under its most serious item (red before
yellow, List 1 before List 2); the other items are kept in `also` so nothing the screen found is hidden.
"""
from __future__ import annotations

from app.schemas import Flag


def _rank(f: Flag) -> tuple:
    return (f.color != "red", f.list_no, f.criterion_number if f.criterion_number is not None else 999, f.criterion_id)


def _item(f: Flag) -> dict:
    return {"criterion_id": f.criterion_id, "criterion_name": f.criterion_name,
            "criterion_number": f.criterion_number, "list_no": f.list_no, "color": f.color,
            "reason": f.llm_reason or f.uncertain_reason or f.explanation or ""}


def merge_same_passage(flags: list[Flag]) -> list[Flag]:
    groups: dict[tuple, list[Flag]] = {}
    for f in flags:
        # findings and set-aside passages are merged separately: a dismissed reading never hides a finding
        groups.setdefault((f.page, f.start, f.end, f.low_confidence), []).append(f)
    out = []
    for group in groups.values():
        group.sort(key=_rank)
        head = group[0]
        if len(group) > 1:
            seen = {head.criterion_id}
            for other in group[1:]:
                if other.criterion_id in seen:
                    # the same item found twice (e.g. a structure check and the model): keep every passage
                    have = {(o.page, o.start, o.end) for o in head.occurrences}
                    head.occurrences += [o for o in other.occurrences if (o.page, o.start, o.end) not in have]
                    continue
                seen.add(other.criterion_id)
                head.also.append(_item(other))
                head.methods = sorted(set(head.methods) | set(other.methods))
        out.append(head)
    return out
