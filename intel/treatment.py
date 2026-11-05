"""LI-5, rules only: how a judgment treats each case it cites, and how it disposed of its own case.

Both readings are cue phrases on the judgment's own words, chosen for
precision.  No model is involved, so a citing sentence whose cue is weak or
unclear is read as ``referred``.  That is the safe direction: a ``referred``
edge says nothing about the cited case, but a wrong ``overruled`` would.

**Treatment** is read from the sentence around each citation:

* ``overruled``, ``not_good_law``, ``per_incuriam``, ``reversed`` (negative)
  and ``doubted`` (caution).  These are bound to one citation only.  Either
  the sentence cites a single case, or the cue directly follows the citation
  ("PLD 1990 SC 1 (overruled)").  A cue the citation follows instead ("was
  overruled in X") marks X as the *overruling* case, not the overruled one.
* ``distinguished``: the court found the case inapplicable.  A string cite
  can be distinguished together, so this label is not bound to one citation.
* ``cited_by_counsel``: a party's counsel relied on the case.  That is not
  the court's treatment of it.
* ``followed``: the court relies on it ("reliance is placed on", "fortified
  by").
* ``referred``: anything else.

A negated cue ("has not been overruled", "is not distinguishable") is not
read as that label.

**Disposition** is read from the end of the judgment, the part that states
the order.  It is the rules baseline that LI-4's model extraction will
replace.  Here it gives the result on ``APPEAL_FROM`` edges.
"""

from __future__ import annotations

import re
from typing import Any

from intel.context import citation_spans, parallel_groups
from intel.corpus import ParsedDocument
from intel.evidence import Evidence
from rag.normalise import DocumentText, flatten

EXTRACTOR = "intel.treatment"
VERSION = "4"

# A sentence ends at ". " after a lower-case word of three letters or more, so
# "No.", "Vs." and "Cr.P.C." do not end one.  A paragraph number ("12. The") starts one.
BOUNDARY = re.compile(r"(?<=[a-z\)]{3}[.?!])\s+(?=[A-Z\"“(])|(?<=\d\)\.)\s+(?=[A-Z])|(?<=\b(?:19|20)\d\d\.)\s+(?=The\s|[A-Z][a-z]+\s)|\s(?=\d{1,3}\.\s+[A-Z])")
BEFORE, AFTER = 450, 300
NEGATION = re.compile(r"\b(?:not|never|cannot|nor|hardly)\b[^.;]{0,25}$", re.I)
NEGATIVE = ("overruled", "not_good_law", "per_incuriam", "reversed")
BOUND = (*NEGATIVE, "doubted")
# Strongest first; an edge cited several times keeps its strongest reading.
PRIORITY = (*NEGATIVE, "doubted", "distinguished", "followed", "cited_by_counsel", "referred")
CUES: list[tuple[str, re.Pattern[str]]] = [
    ("not_good_law", re.compile(r"\b(?:no\s+longer|not|ceased\s+to\s+be)\s+(?:a\s+)?good\s+law\b", re.I)),
    ("per_incuriam", re.compile(r"\bper\s+incuriam\b", re.I)),
    ("overruled", re.compile(r"\bover-?\s?ruled\b", re.I)),
    ("reversed", re.compile(r"\b(?:set\s+aside|reversed)\s+(?:by|in)\s+(?:the\s+)?(?:august\s+|hon'?ble\s+)?"
                            r"(?:supreme\s+court|apex\s+court|appeal)\b", re.I)),
    ("doubted", re.compile(r"\bdoubted\b|\bunable\s+to\s+(?:agree|subscribe)\s+(?:with|to)\s+(?:the\s+)?"
                           r"(?:view|ratio|dictum|observations?|law)\b", re.I)),
    # "Not applicable" is said of statutes as often as of cases: it counts only with a case as its subject.
    ("distinguished", re.compile(
        r"\bdistinguishable\b|\bdistinguished\b(?!\s+(?:counsel|jurist|judge|lawyer))|"
        r"\b(?:judgments?|cases?|dicta|dictum|precedents?|authorit(?:y|ies)|case[-\s]law|rulings?|citations?)\b"
        r"[^.]{0,80}\b(?:(?:not|in)\s*applicable|not\s+attracted|of\s+no\s+(?:help|avail)|not\s+helpful|"
        r"do(?:es)?\s+not\s+advance)\b|\bon\s+(?:a\s+)?different\s+(?:footing|facts)\b|"
        r"\bfacts\s+(?:and\s+circumstances\s+)?of\s+(?:the\s+)?(?:said|cited|referred|instant)?\s*cases?\s+"
        r"(?:are|were)\s+(?:quite\s+|altogether\s+|entirely\s+)?(?:different|distinct)\b", re.I)),
]
# A sentence that explains the doctrine is not a finding about the case it cites.
DOCTRINE = re.compile(r"\b(?:term|doctrine|concept|principle|rule|meaning)\s+(?:of\s+)?[\"“']?per\s+incuriam|"
                      r"per\s+incuriam[\"”']?\s+(?:means|is\s+a|has\s+been\s+(?:defined|explained))", re.I)
QUOTE_START = re.compile(r"[:]\s*-*\s*[\"“‘']|\bobserved\s+(?:as\s+follows|that)\b|\bheld\s+as\s+under\b", re.I)
# What counsel argued is not what the court held.
ARGUED = re.compile(r"\b(?:contended|argued|urged|submitted|contends|argues|submits|canvassed|averred|"
                    r"asserts|asserted|contentions?|according\s+to\s+(?:the\s+)?learned)\b", re.I)
# An objection or a plea is "overruled" far more often than a precedent is.
NOT_A_PRECEDENT = re.compile(r"\b(?:objections?|pleas?|contentions?|arguments?|submissions?)\b[^.]{0,140}"
                             r"\b(?:is|are|stands?|was|were|being)\s+(?:hereby\s+)?over-?\s?ruled", re.I)
OVERRULED_BY = re.compile(r"over-?\s?ruled\s+(?:in|by|vide)\b", re.I)
# The words just before a cue that make the case before it its subject.
PASSIVE = re.compile(r"\b(?:is|was|were|are|been|be|being|stands?|stood|as|declared)\s+(?:\w+ly\s+|since\s+|also\s+|"
                     r"declared\s+|held\s+(?:to\s+be\s+)?|(?:as\s+)?(?:being\s+)?)*[\"“‘']?$", re.I)
MODAL = re.compile(r"\b(?:can|could|may|might|would|should|cannot|must)\s+(?:not\s+)?(?:\w+ly\s+)?be\s+(?:\w+\s+)?$", re.I)
CONTENT_OF_HOLDING = re.compile(r"\b(?:held|observed|wherein|in\s+which|ruled\s+that|laid\s+down|whereby)\b", re.I)
AGENT = re.compile(r"\b(?:overruled|over-\s?ruled|declared|set\s+aside|reversed|abandoned|held|disapproved)\s+"
                   r"(?:by|in|vide)\b[^()]*[(\[]?\s*$", re.I)
COUNSEL = re.compile(
    r"\b(?:counsel|A\.?S\.?C|advocates?|D\.?A\.?G|A\.?A\.?G|A\.?G|law\s+officers?|prosecutor|"
    r"learned\s+[A-Z]\.?[A-Z]\.?[A-Z]?|petitioners?|appellants?|respondents?)\b"
    r"[^.]{0,160}\b(?:relied|placed\s+reliance|referred|cited|relies|reliance)\b|"
    r"\b(?:relied|reliance)\b[^.]{0,80}\b(?:by|of)\s+(?:the\s+)?(?:learned\s+)?(?:counsel|A\.?S\.?C|advocates?)\b",
    re.I)
FOLLOWED = re.compile(
    # Present tense is the court's voice; "reliance was placed", "he placed reliance" report counsel's.
    r"\breliance\s+(?:is|can\s+be|may\s+be)\s+(?:also\s+)?(?:placed|made)\b|\b(?:we|i)\s+(?:also\s+)?"
    r"(?:rely|relied|respectfully\s+follow|follow)\b|\bfortified\s+by\b|\b(?:are|am)\s+fortified\b|"
    r"\b(?:is|are|stands?)\s+(?:fully\s+|also\s+)?(?:supported|reinforced)\s+by\b|\bguided\s+by\b|"
    # Bare "followed" and "binding" are as often about directions or rules as about precedent.
    r"\b(?:has|have|was|were|been)\s+(?:\w+ly\s+|further\s+|also\s+)*followed\s+(?:by|in)\b|"
    r"\bbinding\s+(?:on|upon)\s+(?:this|all|the\s+subordinate)\b|"
    r"\bthe\s+law\s+(?:laid\s+down|enunciated|declared)\b|"
    r"\b(?:we|i|this\s+court)\s+(?:have\s+|has\s+|also\s+)*placed\s+reliance\b|"
    r"\bsupports?\s+(?:the|this|our)\b", re.I)

# The order's verbs, strongest first: allowed-and-remanded is a remand.
DISPOSITION_SHARE, DISPOSITION_MIN, DISPOSITION_MAX = 0.25, 2000, 6000
SUBJECT = r"(?:appeals?|petitions?|revisions?|applications?|references?|reviews?|it|the\s+same)"
DISPOSITIONS: list[tuple[str, re.Pattern[str]]] = [
    # The order remands; a sentence about remand orders in general does not.
    ("remanded", re.compile(r"\b(?:is|are|be|being|stands?|hereby)\s+(?:hereby\s+)?(?:remanded|remitted|sent\s+back)\b|"
                            r"\bremand(?:ed)?\s+(?:the\s+)?(?:case|matter|suit|appeal)\b", re.I)),
    ("partly_allowed", re.compile(r"\b(?:partly|partially)\s+(?:allowed|accepted)\b", re.I)),
    ("allowed", re.compile(SUBJECT + r"[^.]{0,80}\b(?:is|are|stands?|being)\s+(?:hereby\s+|accordingly\s+)?"
                           r"(?:allowed|accepted)\b|\bimpugned\s+(?:judgments?|orders?|decrees?)[^.]{0,120}\b"
                           r"(?:is|are)\s+(?:hereby\s+)?set\s+aside\b|\bleave\s+(?:to\s+appeal\s+)?(?:is\s+)?granted\b",
                           re.I)),
    ("leave_refused", re.compile(r"\bleave\s+(?:to\s+appeal\s+)?(?:is\s+)?(?:refused|declined)\b", re.I)),
    ("dismissed", re.compile(SUBJECT + r"[^.]{0,80}\b(?:is|are|stands?|being)\s+(?:hereby\s+|accordingly\s+)?"
                             r"dismissed\b", re.I)),
    # Of the main matter only: "these CMAs are disposed of" is a side application.
    ("disposed_of", re.compile(r"(?<!C\.\s)(?<!C\.M\.\s)(?<!M\.\s)" + SUBJECT + r"[^.]{0,80}\b(?:is|are|stands?|being)\s+"
                               r"(?:hereby\s+|also\s+|accordingly\s+)?disposed\s+of\b", re.I)),
]


def sentence(text: str, start: int, end: int) -> tuple[int, int]:
    """The sentence around a citation, bounded so a long paragraph is not one sentence."""
    lo = max(0, start - BEFORE)
    starts = [m.end() for m in BOUNDARY.finditer(text, lo, start)]
    left = starts[-1] if starts else lo
    after = BOUNDARY.search(text, end, min(len(text), end + AFTER))
    right = after.start() if after else min(len(text), end + AFTER)
    return left, right


def _negated(text: str, at: int, left: int) -> bool:
    return bool(NEGATION.search(text[max(left, at - 40):at]))


def _binds(text: str, label: str, at: int, cue_end: int, start: int, end: int,
           in_sentence: list[tuple[int, int]], left: int) -> bool:
    """Whether a negative cue is said *of this citation*, not of a case it overruled or that overruled it.

    The cited case is the one treated only when it is the subject of a passive cue
    ("X (cite) was overruled", "(cite) is per incuriam", "(cite) (overruled)"),
    or the object of an active one ("their Lordships overruled X (cite)").  A
    citation that is the agent ("declared not good law *in* X (cite)", "X (cite)
    overruled both judgments", "X (cite), wherein it was held ...") is not."""
    if at >= end:
        between = text[end:at]
        if len(between) > 250 or any(end <= s < at for s, _ in in_sentence):
            return False
        if re.match(r"^\s*[(\[]\s*$", between):
            return True   # "PLD 1990 SC 1 (overruled)"
        before = text[max(left, at - 45):at]
        if MODAL.search(before) or not PASSIVE.search(before):
            return False
        if CONTENT_OF_HOLDING.search(between) or AGENT.search(text[max(left, start - 150):start]):
            return False
        return True
    if (label not in {"overruled", "doubted"} or OVERRULED_BY.match(text, at)
            or PASSIVE.search(text[max(left, at - 45):at]) or text[max(left, at - 2):at].strip().endswith(("(", "["))):
        return False
    return cue_end <= start <= cue_end + 150 and not any(cue_end <= s < start for s, _ in in_sentence)


def classify(text: str, start: int, end: int, others: list[tuple[int, int]]) -> tuple[str, str | None, int, int]:
    """One citation's treatment, the cue that decided it, and the sentence span.

    ``others`` are the other citations in the text (as spans), so a bound cue
    can be checked against them; a parallel citation of the same case is not an other."""
    left, right = sentence(text, start, end)
    # What follows "(PLD 1975 SC 331):- “..." is the cited case's own words, not this court's.
    quoted = QUOTE_START.search(text, end, right)
    right = quoted.start() if quoted else right
    window = text[left:right]
    in_sentence = [(s, e) for s, e in others if left <= s < right]
    argued = ARGUED.search(window)
    if argued:
        return "cited_by_counsel", argued.group(0), left, right
    for label, pattern in CUES:
        for match in pattern.finditer(window):
            at = left + match.start()
            if label != "not_good_law" and _negated(text, at, left):
                continue
            if label == "overruled" and NOT_A_PRECEDENT.search(window):
                continue
            if label == "per_incuriam" and DOCTRINE.search(window):
                continue
            if label in BOUND and not _binds(text, label, at, left + match.end(), start, end, in_sentence, left):
                continue
            return label, match.group(0), left, right
    if COUNSEL.search(window) and not FOLLOWED.search(window[: max(0, start - left)][-120:]):
        return "cited_by_counsel", COUNSEL.search(window).group(0)[:60], left, right
    found = FOLLOWED.search(window)
    if found:
        return "followed", found.group(0), left, right
    return "referred", None, left, right


def disposition(text: str) -> dict[str, Any] | None:
    """The order stated at the end of the judgment, by its strongest verb; None if none is printed."""
    size = min(DISPOSITION_MAX, max(DISPOSITION_MIN, int(len(text) * DISPOSITION_SHARE)))
    tail_start = max(0, len(text) - size)
    tail = text[tail_start:]
    for label, pattern in DISPOSITIONS:
        matches = list(pattern.finditer(tail))
        if matches:
            last = matches[-1]
            left, right = sentence(text, tail_start + last.start(), tail_start + last.end())
            return {"outcome": label, "cue": last.group(0), "start": left, "end": right}
    return None


def _evidence(text: DocumentText, start: int, end: int) -> dict[str, Any]:
    where = text.provenance(start, end)
    return Evidence(document_id=text.document_id, page=where["page_start"], bboxes=where["bboxes"],
                    block_ids=where["block_ids"], char_start=start, char_end=end,
                    quote=text.text[start:end]).model_dump()


def read(document: ParsedDocument) -> dict[str, Any]:
    """Every citation's treatment in one judgment, and its own disposition.  Pure."""
    text = flatten(document.payload(), document.stem, document.corpus)
    spans = citation_spans(text.text)
    same_case = {}
    for group in parallel_groups(text.text):
        for member in group:
            same_case[member] = set(group)
    readings = []
    for start, end, citation in spans:
        partners = same_case.get(citation["id"], {citation["id"]})
        others = [(s, e) for s, e, c in spans if c["id"] not in partners]
        label, cue, left, right = classify(text.text, start, end, others)
        readings.append({"alias": citation["id"], "treatment": label, "cue": cue,
                         "evidence": _evidence(text, left, right)})
    found = disposition(text.text)
    if found:
        found = {"outcome": found["outcome"], "cue": found["cue"],
                 "evidence": _evidence(text, found["start"], found["end"])}
    judges = (document.metadata().get("metadata") or {}).get("judges") or []
    return {"corpus": document.corpus, "stem": document.stem, "readings": readings, "disposition": found,
            "judges_named": len(judges)}
