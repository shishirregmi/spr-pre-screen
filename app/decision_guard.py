"""The non-negotiable rule: only a human reviewer can release or return a paper.

Enforced here on the server; the UI's disabled buttons are a convenience, not the control.
"""
from __future__ import annotations

FINAL_STATES = {"RELEASED", "RETURNED"}
DECIDABLE_STATES = {"SCREENED", "SCREEN_FAILED", "IN_REVIEW"}


class DecisionDenied(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def check_decision(*, role: str | None, paper: dict, flag_ids_requiring_disposition: list[str],
                   dispositions: dict[str, dict], outcome: str, attested: bool, note: str | None) -> None:
    if role != "reviewer":
        raise DecisionDenied("Only a human reviewer can make a release decision.", 403)
    if outcome not in ("released", "returned"):
        raise DecisionDenied("Outcome must be 'released' or 'returned'.", 422)
    if paper["state"] in FINAL_STATES:
        raise DecisionDenied(f"Paper already {paper['state'].lower()}.")
    if paper["state"] not in DECIDABLE_STATES:
        raise DecisionDenied(f"Paper is {paper['state'].lower()}; screening must finish first.")
    pending = [fid for fid in flag_ids_requiring_disposition if fid not in dispositions]
    if pending:
        raise DecisionDenied(f"{len(pending)} flag(s) still need accept/override before a decision.")
    if not attested:
        raise DecisionDenied("Reviewer must confirm they personally reviewed the paper.", 422)
    result = paper.get("result") or {}
    if result.get("completeness") != "complete" and not (note and note.strip()):
        raise DecisionDenied("Screening was incomplete; a note describing the manual review is required.", 422)
