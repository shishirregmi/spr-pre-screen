"""Merge candidates from all detectors and levels into flags, and grade evidence strength.

Rules:
- merge only within the same criterion and page; never across criteria or lists
- a flag's span is its most specific member (L4 > L3 > L2 > L1)
- repeated identical L4 findings collapse into one flag with several occurrences
- nothing is deleted: weak or below-threshold findings are kept, marked low-confidence where appropriate
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from app import config
from app.detect.base import NormalizedDoc
from app.rules.loader import Rulebook
from app.schemas import Candidate, Criterion, Flag, Occurrence

LEVEL_RANK = {"L4": 0, "L3": 1, "L2": 2, "L1": 3}
DETERMINISTIC = {"exact", "regex", "fuzzy"}
MAX_EVIDENCE = 12


_RANK = {"weak": 0, "strong": 1, "validated": 2}


def strength_of(members: list[Candidate]) -> str:
    """validated/strong = the listed words or a validated pattern are plainly there (a clear match);
    weak = a possible match (spelling variant, similar meaning, pattern without confirming context)."""
    best = "weak"
    for m in members:
        if m.method == "regex":
            s = m.details.get("strength", "weak")
        elif m.method == "exact":
            d = m.details
            s = "weak" if (d.get("related") or d.get("lowercase") or d.get("acronym_definition")) else "strong"
        else:
            s = "weak"
        if _RANK[s] > _RANK[best]:
            best = s
    return best


def _why_unsure(members: list[Candidate]) -> str:
    methods = {m.method for m in members}
    if any(m.details.get("acronym_definition") for m in members):
        return "Looks like an acronym being defined (e.g. “Team Schriever (TS)”), not a marking."
    if any(m.method == "exact" and m.details.get("lowercase") for m in members):
        return "The term appears in ordinary text, not as an upper-case marking or banner."
    if any(m.method == "exact" and m.details.get("related") for m in members):
        return "A related term was found, not the list item itself; context decides whether it applies."
    if "fuzzy" in methods:
        return "Close spelling variant of a listed term, not an exact match."
    if "regex" in methods:
        return "A pattern matched, but nothing nearby confirms it (for example, it could be a figure label)."
    return "The tool is not sure this matches."


_DISCLAIMER = re.compile(r"\b(?:contains? no|does not contain|do not contain|not contain any|no (?:classified|"
                         r"controlled)|is not classified|are not classified|is unclassified|free of)\b", re.I)


_OFFICIAL_DOMAIN = re.compile(r"\.(?:mil|gov|edu)$", re.I)


def _not_personal(members: list[Candidate]) -> str | None:
    """List 1 #6 covers personal details. Pattern hits that are not personal go to "Not counted", with the reason."""
    plain = [m for m in members if m.method == "regex" and not m.details.get("context_words")]
    if len(plain) != len(members):
        return None
    if all(m.details.get("pattern") == "email" and _OFFICIAL_DOMAIN.search(m.text.strip()) for m in plain):
        return "An official work email address; List 1 #6 covers personal emails."
    if all(m.details.get("pattern") == "street_address" for m in plain):
        return ("Looks like a street address, but no nearby word says it is someone's home (it is often a court, "
                "case or public building).")
    return None


def _list1_context_doubt(page_text: str, start: int, end: int, section: str | None) -> str | None:
    """Context that turns a List 1 hit into a possible match: a disclaimer, or a cited title in the references."""
    if _DISCLAIMER.search(page_text[max(0, start - 200):end + 40]):
        return "It appears in a statement saying the paper contains no such information."
    if section == "references":
        return "It appears in the references, as part of a cited title or source."
    return None


def color_for(crit: Criterion, strength: str, members: list[Candidate]) -> tuple[str, str | None]:
    """Guide rules: anything found on List 1 -> red (a possible List 1 match too, with a note on why the tool is
    unsure); List 2 clear match -> red, possible match -> yellow; List 2 judgment items -> yellow by default."""
    clear = strength in ("validated", "strong")
    if crit.list_no == 1:
        if clear:
            return "red", None
        return config.list_color(1, "yellow"), "Possible List 1 item. " + _why_unsure(members)
    if crit.judgment:
        return "yellow", "Judgment item: whether it matters depends on context, so the guide says lean yellow."
    if clear:
        return "red", None
    return "yellow", _why_unsure(members)


# Plain-language reasons for identifier findings (validator name, else pattern name).
PATTERN_WORDS = {
    "ssn": "Looks like a Social Security number (passes the SSN number rules)",
    "luhn": "Looks like a payment card number (passes the card checksum)",
    "aba": "Looks like a bank routing number (passes the routing checksum)",
    "labeled_ssn": "A number written as a Social Security number",
    "labeled_financial": "A number written as a bank account, routing or card number",
    "labeled_gov_id": "A number written as a government ID (DoD ID, passport, licence, tax ID)",
    "card_number": "Looks like a payment card number",
    "personal_email": "A personal email address",
    "email": "An email address",
    "phone_us": "A phone number",
    "street_address": "A street address",
    "private_ipv4": "An internal network address",
    "internal_host": "An internal host name",
    "custom": "A classification or control marking",
}


def explain(members: list[Candidate]) -> str:
    parts: list[str] = []
    best: dict[str, Candidate] = {}
    for m in members:
        if m.method not in best or m.score > best[m.method].score:
            best[m.method] = m
    if (m := best.get("exact")):
        kind = "related term" if m.details.get("related") else "listed term"
        parts.append(f"Found {kind} “{m.details.get('keyword')}”"
                     + (" after case/punctuation/OCR normalization" if m.details.get("variant") else "") + ".")
    if (m := best.get("regex")):
        s = PATTERN_WORDS.get(m.details.get("validator") or m.details.get("pattern"), "Matches an identifier pattern")
        if m.details.get("acronym_definition"):
            s = "Looks like a marking but follows words with the same initials (an acronym)"
        if m.details.get("context_words"):
            s += f"; nearby words: {', '.join(m.details['context_words'])}"
        parts.append(s + ".")
    if (m := best.get("fuzzy")):
        parts.append(f"Close variant of “{m.details.get('keyword')}” ({m.details.get('edits')} character edit(s)).")
    return " ".join(parts)


def _flag_id(cid: str, page: int, start: int, end: int) -> str:
    return hashlib.sha1(f"{cid}|{page}|{start}|{end}".encode()).hexdigest()[:12]


def consolidate(cands: list[Candidate], rulebook: Rulebook, ndoc: NormalizedDoc) -> list[Flag]:
    groups: dict[tuple[str, int], list[Candidate]] = defaultdict(list)
    for c in cands:
        groups[(c.criterion_id, c.page)].append(c)

    flags: list[Flag] = []
    for (cid, page), cs in groups.items():
        crit = rulebook.get(cid)
        cs.sort(key=lambda c: (LEVEL_RANK[c.level], c.below_threshold, -c.score))
        clusters: list[list[Candidate]] = []
        for c in cs:
            for cl in clusters:
                a = cl[0]
                if c.start < a.end and a.start < c.end:
                    cl.append(c)
                    break
            else:
                clusters.append([c])

        for cl in clusters:
            anchor = cl[0]
            valid = [m for m in cl if not m.below_threshold]
            section = ndoc.section_at(page, anchor.start)
            low, reason = False, None
            if not valid:
                low, reason = True, "Only surfaced as a nearest match below the similarity threshold."
            elif section and section in [s.lower() for s in crit.context.sections_exclude]:
                low, reason = True, f"Found in the “{section}” section, which this criterion excludes."
            basis = valid or cl
            strength = strength_of(valid)
            color, why = color_for(crit, strength, basis)
            # a personal identifier (SSN, account number ...) is a leak wherever it is, even beside a disclaimer
            personal_id = crit.id == "L1-6" and any(m.method == "regex" for m in valid)
            aside = _not_personal(valid) if personal_id else None
            if aside:
                personal_id, low, reason = False, True, aside
            if crit.list_no == 1 and not personal_id and not low:
                # The text itself shows the hit is not List 1 content: an acronym being defined ("Team Schriever
                # (TS)"), a statement that the paper contains none ("no classified information or CUI"), or a cited
                # title. Every List 1 finding is red, so these go to "Not counted", visible with the reason.
                acronym = any(m.details.get("acronym_definition") for m in basis)
                page_text = ndoc.page(page).page.text
                in_disclaimer = _DISCLAIMER.search(page_text[max(0, anchor.start - 200):anchor.end + 40])
                doubt = (_why_unsure(basis) if acronym and not in_disclaimer
                         else _list1_context_doubt(page_text, anchor.start, anchor.end, section))
                if doubt and not acronym and not in_disclaimer and any(m.method == "regex" for m in basis):
                    # a marking on a cited source ("PSDM 22-125 (CUI)") is a finding: the source may not be releasable
                    color = config.list_color(1, "yellow")
                    why = "A cited source carries this marking; check that the cited material is releasable."
                elif doubt:
                    low, reason = True, doubt
                    if config.LIST1_UNCERTAIN_COLOR == "yellow":      # earlier behaviour: show them as yellow
                        low, reason, color, why = False, None, "yellow", "Possible List 1 item. " + doubt
            flags.append(Flag(
                id=_flag_id(cid, page, anchor.start, anchor.end),
                criterion_id=cid, criterion_name=crit.name, criterion_number=crit.number,
                list_no=crit.list_no, color=color, judgment=crit.judgment, uncertain_reason=why,
                origin=crit.origin, source_policy=crit.source_policy,
                page=page, start=anchor.start, end=anchor.end, text=anchor.text, section=section,
                occurrences=[Occurrence(page=page, start=anchor.start, end=anchor.end)],
                levels=sorted({m.level for m in basis}, key=lambda l: LEVEL_RANK[l]),
                methods=sorted({m.method for m in basis}),
                evidence=cl[:MAX_EVIDENCE],
                score=max(m.score for m in basis),
                strength=strength,
                low_confidence=low, low_confidence_reason=reason,
                explanation=explain(basis),
            ))
    return _collapse_repeats(flags)


def _collapse_repeats(flags: list[Flag]) -> list[Flag]:
    """The same marking in every page footer is one finding with many occurrences, not 20 flags."""
    out: list[Flag] = []
    seen: dict[tuple[str, str, bool], Flag] = {}
    for f in sorted(flags, key=lambda f: (f.page, f.start)):
        if set(f.methods) <= DETERMINISTIC:
            # case matters: the marking "PROPRIETARY" on a figure is not the word "proprietary" in a sentence
            key = (f.criterion_id, " ".join(f.text.split()), f.low_confidence)
            if key in seen:
                seen[key].occurrences.extend(f.occurrences)
                continue
            seen[key] = f
        out.append(f)
    return out
