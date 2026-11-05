"""Validators that turn a raw regex hit into a validated identifier, and named regex presets."""
from __future__ import annotations

import re

# Numbers that appear in examples and advertising, never issued to a person.
_DUMMY_SSNS = {"078051120", "219099999", "123456789", "111111111", "222222222", "333333333",
               "444444444", "555555555", "777777777", "888888888", "999999999"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def ssn(value: str) -> bool:
    d = _digits(value)
    if len(d) != 9 or d in _DUMMY_SSNS:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    if area in ("000", "666") or area[0] == "9":
        return False
    return group != "00" and serial != "0000"


def luhn(value: str) -> bool:
    d = _digits(value)
    if not 13 <= len(d) <= 19 or len(set(d)) == 1:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def aba(value: str) -> bool:
    d = _digits(value)
    if len(d) != 9 or len(set(d)) == 1:
        return False
    w = (3, 7, 1) * 3
    return sum(int(c) * k for c, k in zip(d, w)) % 10 == 0 and d[:2] in _ABA_PREFIXES


_ABA_PREFIXES = {f"{n:02d}" for n in list(range(0, 13)) + list(range(21, 33)) + list(range(61, 73)) + [80]}

def phone(value: str) -> bool:
    """Not a DFARS clause number (252.2xx-7xxx), which OCR of a clause chart can turn into 252-227-7025."""
    d = _digits(value)[-10:]
    return not (d.startswith("2522") and d[6] == "7")


VALIDATORS = {"ssn": ssn, "luhn": luhn, "aba": aba, "phone": phone}

# Named presets: (regex, flags, validator name or None)
PRESETS: dict[str, tuple[str, int, str | None]] = {
    "email": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", 0, None),
    "personal_email": (r"\b[A-Za-z0-9._%+-]+@(?:gmail|yahoo|hotmail|outlook|aol|icloud|me|proton|protonmail|live|msn|comcast)\.[A-Za-z.]{2,6}\b", re.I, None),
    "street_address": (r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Boulevard|Blvd|Way|Circle|Cir|Place|Pl|Trail|Terrace)\b\.?", 0, None),
    "general_officer": (r"\b(?:Brig(?:adier)?\.? Gen(?:eral)?|Maj(?:or)?\.? Gen(?:eral)?|Lt\.? Gen(?:eral)?|Lieutenant General|Gen\.?|BGen|MajGen|LtGen|RDML|RADM|VADM|ADM)\.? [A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-z]+)?", 0, None),
    # One separator style throughout: 334-953-1234, 334.953.1234, 334 953 1234 or (334) 953-1234. Mixed styles
    # such as the DFARS clause 252.227-7018 are document numbers, not phones.
    "phone_us": (r"(?<![\d.-])(?:\+1[ .-]?)?(?:\(\d{3}\) ?\d{3}[ .-]\d{4}|\d{3}-\d{3}-\d{4}|\d{3}\.\d{3}\.\d{4}"
                 r"|\d{3} \d{3} \d{4})(?![\d-])", 0, "phone"),
    "private_ipv4": (r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b", 0, None),
    "internal_host": (r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|intranet|corp|local|lan)\b", re.I, None),
    "private_key": (r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", 0, None),
    "aws_access_key": (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0, None),
    "password_assignment": (r"\b(?:password|passwd|pwd)\s*[:=]\s*\S{6,}", re.I, None),
    "generic_api_key": (r"\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", re.I, None),
    # A number written right after its label ("SSN: 445 56781", "Account number: 12848488489") is an identifier
    # whatever its shape: typos, partial or made-up numbers are still not publishable. Spaces and dashes allowed.
    "labeled_ssn": (r"\b(?:SSN|SSAN|social\s+security(?:\s+account)?(?:\s+(?:number|no\b\.?|num\b|#))?)"
                    r"\s*[:=#.-]?\s*\d(?:[ -]?\d){3,18}(?![\d-])", re.I, None),
    "labeled_financial": (r"\b(?:(?:bank|checking|savings|routing|ABA|credit\s+card|debit\s+card|card)\s+"
                          r"(?:account\s+)?(?:number|no\b\.?|num\b|#)|(?:bank\s+)?(?:account|acct)\.?\s*"
                          r"(?:number|no\b\.?|num\b|#)|IBAN)\s*[:=#.-]?\s*[A-Z]{0,4}\d(?:[ -]?\d){3,30}(?![\d-])",
                          re.I, None),
    "labeled_gov_id": (r"\b(?:DoD\s+ID|EDIPI|passport|driver'?s?\s+licen[cs]e|military\s+ID|tax\s+ID"
                       r"|alien\s+(?:registration\s+)?number)(?:\s+(?:number|no\b\.?|num\b|#))?"
                       r"\s*[:=#.-]?\s*[A-Z]{0,2}\d(?:[ -]?\d){5,14}(?![\d-])", re.I, None),
    # Bare card numbers: 13-19 digits that pass the card checksum; only used near card words (ISBNs look similar).
    "card_number": (r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])", 0, "luhn"),
}
