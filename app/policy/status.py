"""Paper color, per the review guide: the worst flag color wins.

RED    - any List 1 item, or a clear List 2 match
YELLOW - only possible matches (the tool is unsure)
GREEN  - nothing found on List 1 or List 2, and every page and detector ran
INCOMPLETE - nothing found, but part of the paper could not be checked ("when unsure, don't guess green")
The color is the tool's output and is never changed by reviewer dispositions.
"""
from __future__ import annotations

from app.schemas import Coverage, Flag


def active_flags(flags: list[Flag]) -> list[Flag]:
    return [f for f in flags if not f.low_confidence]


def preliminary_status(flags: list[Flag], coverage: Coverage) -> tuple[str, str]:
    active = active_flags(flags)
    completeness = "complete" if coverage.complete else "incomplete"
    if any(f.color == "red" for f in active):
        status = "RED"
    elif active:
        status = "YELLOW"
    else:
        status = "GREEN" if completeness == "complete" else "INCOMPLETE"
    return status, completeness
