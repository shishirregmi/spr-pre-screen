"""Exact keyword/phrase matching with one Aho-Corasick pass per page over the folded text."""
from __future__ import annotations

import ahocorasick

from app.detect.base import NormalizedDoc
from app.detect.regex import is_acronym_definition
from app.schemas import Candidate, Criterion
from app.text.normalize import fold_term


class ExactDetector:
    method = "exact"

    def detect(self, ndoc: NormalizedDoc, criteria: list[Criterion]) -> list[Candidate]:
        auto = ahocorasick.Automaton()
        terms: dict[str, list[tuple[Criterion, str, bool]]] = {}
        for c in criteria:
            for kw, related in [(k, False) for k in c.keywords] + [(k, True) for k in c.related_terms]:
                t = fold_term(kw)
                if t:
                    terms.setdefault(t, []).append((c, kw, related))
        if not terms:
            return []
        for t, owners in terms.items():
            auto.add_word(t, (t, owners))
        auto.make_automaton()

        out: list[Candidate] = []
        for np_ in ndoc.readable_pages():
            hay = np_.match.text
            for end_idx, (t, owners) in auto.iter(hay):
                s, e = end_idx - len(t) + 1, end_idx + 1
                # simple plural: "drone" also matches "drones"
                if e < len(hay) and hay[e] == "s" and (e + 1 == len(hay) or hay[e + 1] == " "):
                    e += 1
                # whole words only: "cui" must not match inside "circuit"
                if (s > 0 and hay[s - 1] != " ") or (e < len(hay) and hay[e] != " "):
                    continue
                os_, oe = np_.match.orig_span(s, e)
                text = np_.page.text[os_:oe]
                has_lower = any(ch.islower() for ch in text)
                for crit, kw, related in owners:
                    # Markings are written in capitals; "proprietary" in a sentence is a topic, not a marking.
                    lowercase = crit.markings_uppercase and not related and has_lower
                    acronym = (crit.markings_uppercase and not related
                               and is_acronym_definition(np_.page.text, os_, text))
                    out.append(Candidate(
                        criterion_id=crit.id, list_no=crit.list_no, page=np_.page.no, start=os_, end=oe,
                        text=text, level="L4", method="exact", score=0.7 if related else 1.0,
                        # variant=True: matched only after case/punctuation/OCR folding
                        details={"keyword": kw, "related": related, "lowercase": lowercase,
                                 "acronym_definition": acronym,
                                 "variant": " ".join(text.lower().split()) != kw.lower()},
                    ))
        return out
