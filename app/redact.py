"""Masking of personal identifiers in API responses and exports.

The PDF itself still shows the text (a reviewer must be able to read the paper), but lists, flag cards, exports
and screenshots of the queue do not expose full SSNs, phone numbers, emails or addresses. A reviewer can reveal a
single value; every reveal is written to the audit log.
"""
from __future__ import annotations

import re

MASKED_PATTERNS = {"custom", "personal_email", "email", "phone_us", "street_address", "labeled_ssn",
                   "labeled_financial", "labeled_gov_id", "card_number"}


def mask_value(text: str) -> str:
    t = text.strip()
    if "@" in t:
        local, _, domain = t.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = sum(ch.isdigit() for ch in t)
    if digits >= 7:
        keep, out = 4, []
        for ch in reversed(t):
            if ch.isdigit():
                out.append(ch if keep > 0 else "•")
                keep -= 1
            else:
                out.append(ch)
        return "".join(reversed(out))
    if re.match(r"\d", t):                     # street address: hide the house number and street
        return re.sub(r"[^\s]", "•", t[:-6]) + t[-6:] if len(t) > 6 else "•" * len(t)
    return t[:1] + "•" * max(0, len(t) - 2) + t[-1:]


def is_sensitive(flag: dict) -> bool:
    """Identifier findings under List 1 #6 (personal privacy) found by a pattern."""
    return flag.get("criterion_id") == "L1-6" and any(
        e.get("method") == "regex" and e.get("details", {}).get("pattern") in MASKED_PATTERNS
        for e in flag.get("evidence", []))


def redact_flag(flag: dict) -> dict:
    if not is_sensitive(flag):
        return flag
    flag = dict(flag)
    flag["text"] = mask_value(flag["text"])
    flag["evidence"] = [dict(e, text=mask_value(e["text"])) for e in flag.get("evidence", [])]
    flag["masked"] = True
    return flag
