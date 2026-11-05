"""Structure checks: where in a paper the concrete issues sit, not which topic words it uses.

Findings on real papers clustered in the front matter, the acknowledgments and the
notes. These checks look at names, roles, institutions and cited sources in those places. They are deterministic and
fast, and their findings stand on their own like other identifiers (the model does not dismiss them).
"""
from __future__ import annotations

import hashlib
import re

from app import config
from app.detect.base import NormalizedDoc
from app.extract.pdf import Page
from app.rules.loader import Rulebook
from app.schemas import Candidate, Flag, Occurrence

FRONT_MATTER_CHARS = 4000            # title page, author block, advisors, disclaimer
BODY_START = re.compile(r"^\s*(?:[0-9IVX]+[.)]?\s+)?(?:abstract|introduction|executive summary|background)\s*$",
                        re.I | re.M)

# L1 #6: a family member named (unnamed "my wife" is fine)
_TITLE = r"(?:(?:Captain|Capt|Major|Maj|Lieutenant|Lt|Colonel|Col|Dr|Mrs|Mr|Ms|Sergeant|Sgt)\.?\s+)?"
_NAME = r"[A-Z][a-zA-Z'-]{2,}(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z'-]{2,})?"
FAMILY_NAMED = re.compile(
    r"\bmy\s+(?:lovely\s+|wonderful\s+|beautiful\s+|amazing\s+)?(wife|husband|spouse|fianc[ée]e?|son|daughter|"
    r"children|kids|sons|daughters)\s*,?\s*(" + _TITLE + _NAME + r"(?:\s*(?:,|and)\s*" + _NAME + r")*)")
NOT_A_NAME = {"For", "And", "The", "Who", "Whose", "Without", "Thank", "Thanks", "Throughout", "During", "Through",
              "Her", "His", "Their", "This", "That", "Whom", "With", "Was", "Were", "Has", "Have", "Is", "Are"}

# L2 #19: the paper originates in the Washington D.C. area (front matter only; citations do not count)
DC_ORIGIN = re.compile(r"\b(?:National Defense University|National War College|Eisenhower School|Dwight D\. Eisenhower "
                       r"School|College of International Security Affairs|Fort (?:Lesley J\. )?McNair|the Pentagon|"
                       r"Joint Base Anacostia[- ]Bolling|Joint Base Andrews|Washington,?\s+D\.?\s?C\.?\s+\d{5}|"
                       r"Arlington,?\s+(?:VA|Virginia)\s+\d{5})")

# L2 #24: the author is a general officer / SES (author block only; generals mentioned in the text do not count)
GO_AUTHOR = re.compile(r"\b(?:Brig(?:adier)?\.?\s+Gen(?:eral)?|Maj(?:or)?\.?\s+Gen(?:eral)?|Lt\.?\s+Gen|"
                       r"Lieutenant\s+General|BGen|MajGen|LtGen|RDML|RADM|VADM|Rear\s+Admiral|Vice\s+Admiral|"
                       r"Senior\s+Executive\s+Service)\b|,\s*(?:Brig Gen|Maj Gen|Lt Gen|Gen),?\s+(?:USAF|USSF)")
AUTHOR_CUE = re.compile(r"\b(?:by|author|authors|written by)\b", re.I)

# L2 #27 / #28: work with another service or another wing, coordination not shown
ROLE_LINE = re.compile(r"\b(?:Topic POC|POC|Sponsor(?:ing organization)?|Research sponsor|Advisors?|Mentor|"
                       r"Client|Stakeholder)\s*:?", re.I)
OTHER_SERVICE = re.compile(r"\b(?:U\.?S\.? Army|Army|U\.?S\.? Navy|Navy|USN|Marine Corps|USMC|Coast Guard|USCG|"
                           r"LTC|COL|MAJ|CPT|LCDR|CDR|CAPT|Brigade|Battalion|(?<!Air Force )Division|Fleet)\b")
OTHER_WING = re.compile(r"\b\d{1,3}(?:st|nd|rd|th|d)\s+(?:ISR\s+)?(?:Wing|Group|Squadron|Air Base Wing|Operations Group|"
                        r"Fighter Wing|Bomb Wing|Airlift Wing|Air Refueling Wing|Space Wing|Delta)\b")
# The author's own interviews (not interviews reported by a published study)
INTERVIEW = re.compile(r"\binterview(?:ed)? (?:with|by) the author\b|\b(?:I|we) (?:interviewed|spoke with)\b|"
                       r"\b(?:my|our) interviews?\b|\bthis (?:research|study|project) conducted\b.{0,40}\binterviews?\b|"
                       r"\binterview(?:ed|s)? (?:was |were )?(?:conducted )?with (?:Mr\.|Ms\.|Mrs\.|Dr\.|LCDR|CDR|"
                       r"CAPT|LTC|COL|MAJ|CPT|Lt Col|Col|Maj|Capt|SES|[A-Z][a-z]+ [A-Z][a-z]+)|\bpersonal communication\b")
SHORT_BLOCK = 220                    # address lines, author lines, advisor lines: short blocks, not prose
# L1 #3: names, ranks and duty details of personnel in deployed units (the guide's first example). A rank and a name
# in a sentence that places them in a deployed or expeditionary unit or tour.
RANK_NAME = re.compile(r"\b(?:A1C|SrA|SSgt|TSgt|MSgt|SMSgt|CMSgt|Amn|AB|2d Lt|2nd Lt|1st Lt|Capt|Maj|Lt Col|Col|"
                       r"SPC|SGT|SSG|SFC|CPT|MAJ|LTC|PO[123]|LCDR|LT)\.?\s+[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+")
FIRST_HAND = re.compile(r"(?i:\b(?:my|our|we|us|currently)\b)|\bI\b")
DEPLOYED = re.compile(r"\b(?:Expeditionary|deployed|deployment|forward operating|AOR|area of responsibility|"
                      r"(?:my|our|a|the) (?:\d{4} )?tour|downrange|combat zone)\b", re.I)

# L1 #5: unpublished / internal staff products cited
INTERNAL_SOURCE = re.compile(r"\b(?:unpublished|talking paper|point paper|bullet background paper|staff summary sheet|"
                             r"pre-?decisional|draft (?:report|memo|memorandum|policy|guidance|findings)|"
                             r"internal (?:memo|memorandum|briefing|brief|document|report))\b", re.I)


def _front_end(page: Page) -> int:
    m = BODY_START.search(page.text)
    return min(m.start() if m else FRONT_MATTER_CHARS, FRONT_MATTER_CHARS)


# A sentence ends at . ! ? followed by optional closing quotes/brackets ("effects.” Perceived ...") and whitespace.
_END = re.compile(r"[.!?][\"'”’)\]]*(?=\s|$)|\n\n")


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    s = 0
    for m in _END.finditer(text, 0, start):
        s = m.end()
    while s < start and text[s].isspace():
        s += 1
    m = _END.search(text, end)
    e = m.end() if m and m.group() != "\n\n" else (m.start() if m else len(text))
    if e - s > 500:
        s, e = max(s, start - 200), min(e, end + 250)
    return s, e


def _block(page: Page, pos: int) -> tuple[int, int]:
    for s, e in page.blocks:
        if s <= pos < e:
            return s, e
    return pos, pos


def _line(text: str, start: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, start) + 1
    e = text.find("\n", start)
    return s, (len(text) if e < 0 else e)


def check_structure(ndoc: NormalizedDoc, rb: Rulebook) -> list[Flag]:
    out: list[Flag] = []
    seen: set[tuple[str, int, int]] = set()

    def add(cid: str, page: Page, s: int, e: int, kind: str, color: str, why: str, explanation: str) -> None:
        crit = rb.get(cid)
        if crit is None or (cid, page.no, s) in seen:
            return
        seen.add((cid, page.no, s))
        text = page.text[s:e]
        unsure = color == "yellow"
        color = config.list_color(crit.list_no, color)
        out.append(Flag(
            id=hashlib.sha1(f"s|{cid}|{page.no}|{s}|{e}".encode()).hexdigest()[:12],
            criterion_id=cid, criterion_name=crit.name, criterion_number=crit.number, list_no=crit.list_no,
            color=color, judgment=crit.judgment, origin=crit.origin, source_policy=crit.source_policy,
            page=page.no, start=s, end=e, text=text, section=ndoc.section_at(page.no, s),
            occurrences=[Occurrence(page=page.no, start=s, end=e)], levels=["L4"], methods=["structure"],
            evidence=[Candidate(criterion_id=cid, list_no=crit.list_no, page=page.no, start=s, end=e, text=text,
                                level="L4", method="regex", score=0.9,
                                details={"pattern": f"structure:{kind}", "strength": "strong"})],
            score=0.9, strength="strong", uncertain_reason=why if unsure else None,
            explanation=explanation))

    pages = [p.page for p in ndoc.readable_pages()]
    first = pages[:2]

    for page in pages:
        text = page.text
        section = None
        # L1 #6: named family members (usually in acknowledgments, dedications or thanks in the notes)
        for m in FAMILY_NAMED.finditer(text):
            names = m.group(2).strip()
            first_word = re.sub(_TITLE, "", names).split()[0] if names else ""
            if not first_word or first_word in NOT_A_NAME:
                continue
            s, e = _sentence(text, m.start(), m.end())
            add("L1-6", page, s, e, "family_named", "red", "",
                f"A family member is named (“my {m.group(1)}, {names}”). The guide lists spouse and children's "
                "names under personal privacy.")
        # L1 #5: internal or unpublished staff products cited (notes and bibliography)
        for m in INTERNAL_SOURCE.finditer(text):
            section = ndoc.section_at(page.no, m.start())
            if section != "references":
                continue
            s, e = _line(text, m.start())
            add("L1-5", page, s, e, "internal_source", "yellow",
                "A cited source looks internal or unpublished; check it is releasable.",
                f"Cites an internal or unpublished product (“{m.group(0)}”).")
        # L1 #3: named personnel of a deployed unit
        for m in RANK_NAME.finditer(text):
            if (ndoc.section_at(page.no, m.start()) or "") in ("references", "bibliography"):
                continue
            s, e = _sentence(text, m.start(), m.end())
            # the author's own unit or colleagues, or "currently" deployed (not a decade-old news story)
            if DEPLOYED.search(text[s:e]) and FIRST_HAND.search(text[s:e]):
                add("L1-3", page, s, e, "deployed_personnel", "yellow",
                    "Names, ranks and duties of people in a deployed unit; check they may be published.",
                    "Names a service member by rank in a deployed or expeditionary unit.")
        # L2 #27: interviews with officials of another service
        for m in INTERVIEW.finditer(text):
            s, e = _sentence(text, m.start(), m.end())
            if OTHER_SERVICE.search(text[s:e]):
                add("L2-27", page, s, e, "interview_other_service", "yellow",
                    "Input from another service: check that the coordination is documented and that the named "
                    "official may be quoted.",
                    "The author's own interview with someone from another service, quoted in the paper.")

    for page in first:
        text = page.text
        end = _front_end(page) if page.no == 1 else min(len(text), FRONT_MATTER_CHARS // 2)
        front = text[:end]
        # L2 #19: written at a Washington D.C. institution
        for m in DC_ORIGIN.finditer(front):
            s, e = _block(page, m.start())
            if e - s > SHORT_BLOCK:
                continue                     # a sentence about the Pentagon is not where the paper comes from
            add("L2-19", page, s, e, "dc_origin", "yellow", "Judgment item: the paper appears to originate in the "
                "Washington D.C. area.", f"Front matter names “{m.group(0)}”.")
            break
        # L2 #24: general officer / SES as author
        for m in GO_AUTHOR.finditer(front):
            ls, le = _block(page, m.start())
            if le - ls > SHORT_BLOCK:
                continue                     # a general discussed in a paragraph is not the author
            ctx = text[max(0, ls - 150):le]
            if AUTHOR_CUE.search(ctx) or re.search(r"\b(?:USAF|USSF|USA|USN|USMC)\b", text[ls:le]):
                add("L2-24", page, ls, le, "go_author", "yellow", "Judgment item: the author may be an official "
                    "DoD spokesperson by rank.", "The author block shows a general officer or SES rank.")
                break
        # L2 #27 / #28: sponsors, POCs, advisors from another service or another wing
        for m in ROLE_LINE.finditer(front):
            s, e = _line(text, m.start())
            # a label ("Advisor:") or a short author-block line, not prose ("As military advisors, we were ...")
            if not (m.group(0).rstrip().endswith(":") or e - s <= SHORT_BLOCK):
                continue
            e = min(len(text), max(e, m.end() + 160))
            seg = text[s:e]
            if OTHER_SERVICE.search(seg):
                add("L2-27", page, s, e, "role_other_service", "yellow", "A sponsor, POC or advisor from another "
                    "service; check the coordination is documented.", "Front matter lists someone from another service.")
            elif OTHER_WING.search(seg):
                add("L2-28", page, s, e, "role_other_wing", "yellow", "A sponsor, POC or advisor from another Air "
                    "Force unit; check the coordination is documented.", "Front matter lists another Air Force unit.")
    return out
