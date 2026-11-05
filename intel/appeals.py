"""LI-2, rung 5: appeal chains, from the recital an appellate judgment opens with.

A Supreme Court judgment states what it is against, in one shape:

    Against the judgment dated 24.03.2022 passed by the Lahore High Court,
    Lahore in W.P. No. 75382/2021)

That is a court, a case number and a date, which are keys the identity layer
already holds.  No reporter is involved, so this rung reaches judgments that no
citation can.  An intra-court appeal says the same about its own court ("passed
by the learned Single Judge in W.P. No. ..."), and so does a review ("passed by
this Court in CPLA No. 304/2022").

An edge resolves only when the case number **and** the recited date both match
a held judgment.  One case number carries many orders (interim orders, the
final judgment, a review), so a case-number match on a different date is kept
as an unresolved candidate with its reason, never linked.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from intel.case_numbers import parse
from intel.corpus import ParsedDocument
from intel.evidence import Evidence, Fact
from rag.normalise import flatten

EXTRACTOR = "intel.appeals"
VERSION = "2"

# Recitals sit at the top.  Several are kept only within the first HEAD_MANY
# characters (a consolidated judgment lists one per petition); beyond that only
# the first one is, because later "against the order ..." is argument about
# other orders, not what this judgment is against.
HEAD = 6000
HEAD_MANY = 2500
RECITAL = re.compile(
    r"(?i)\b(?:against|from)\s*(?:the\s*)?(?:impugned\s+|consolidated\s+|common\s+)?"
    r"(?:judgments?|orders?|decrees?)(?:\s*(?:/|and)\s*(?:judgments?|orders?|decrees?))*\b")
RECITAL_END = re.compile(r"[)\]]|\b(?:Petitioner|Appellant|Respondent|Versus|VERSUS)\b")
MAX_RECITAL = 450
DATE = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*((?:19|20)\d{2}|\d{2})\b")

# Courts as recitals name them -> registry ids.  Only LHC and SC are held, but
# the others are named so an edge to an unheld court says which court.
COURTS = [
    ("lahore_high_court", r"lahore\s+high\s+court|high\s+court\s+of\s+lahore"),
    ("sindh_high_court", r"high\s+court\s+of\s+sindh|sindh\s+high\s+court"),
    ("peshawar_high_court", r"peshawar\s+high\s+court"),
    ("islamabad_high_court", r"islamabad\s+high\s+court"),
    ("balochistan_high_court", r"high\s+court\s+of\s+baluchistan|high\s+court\s+of\s+balochistan|bal[ou]chistan\s+high\s+court"),
    ("federal_shariat_court", r"federal\s+shariat\s+court"),
    ("subordinate_court", r"sessions\s+judge|district\s+judge|civil\s+judge|magistrate|anti[\s-]*terrorism\s+court|"
                          r"trial\s+court|family\s+court|rent\s+controller|special\s+judge|banking\s+court|"
                          r"accountability\s+court|judge\s+family|guardian\s+judge"),
    ("tribunal", r"tribunal"),
    ("same_court", r"this\s+court|learned\s+single\s+judge|single\s+bench|learned\s+bench"),
]
# A case number starts with its type; "in" usually introduces it.
CASE_START = re.compile(r"(?i)\bin\s+(?=[A-Za-z][A-Za-z.\s]{0,40}?\bnos?\b)")


def _iso(day: str, month: str, year: str) -> str | None:
    d, m, y = int(day), int(month), int(year)
    if y < 100:
        y += 2000 if y <= 30 else 1900
    if not (1 <= d <= 31 and 1 <= m <= 12):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _court(text: str, own_court: str) -> str:
    for court_id, pattern in COURTS:
        if re.search(pattern, text, re.I):
            return own_court if court_id == "same_court" else court_id
    return "unknown"


def recitals(text: str, own_court: str) -> list[dict[str, Any]]:
    """Every appeal recital in the head of a judgment, each with court, case numbers, dates and span."""
    found = []
    for match in RECITAL.finditer(text[:HEAD]):
        rest = text[match.end():match.end() + MAX_RECITAL]
        end = RECITAL_END.search(rest)
        segment = text[match.start():match.end() + (end.start() if end else len(rest))]
        dates = [iso for iso in (_iso(*d) for d in DATE.findall(segment)) if iso]
        if not dates:
            continue
        starts = list(CASE_START.finditer(segment))
        cases = parse(segment[starts[-1].end():]) if starts else []
        if not cases:
            continue
        if found and match.start() > HEAD_MANY:
            break
        found.append({"court_id": _court(segment, own_court), "dates": dates,
                      "cases": [case.key for case in cases], "start": match.start(),
                      "end": match.start() + len(segment), "quote": " ".join(segment.split())})
    return found


def read(document: ParsedDocument) -> dict[str, Any]:
    """The appeal recitals in one document, with their page and box.  Pure."""
    record = document.metadata()
    court = ((record.get("label_check") or {}).get("labels") or {}).get("court_id") \
        or (record.get("metadata") or {}).get("court_id") or "unknown_court"
    text = flatten(document.payload(), document.stem, document.corpus)
    rows = []
    for recital in recitals(text.text, court):
        where = text.provenance(recital["start"], recital["end"])
        rows.append({**recital, "page": where["page_start"], "bboxes": where["bboxes"],
                     "block_ids": where["block_ids"]})
    return {"corpus": document.corpus, "stem": document.stem, "court_id": court, "recitals": rows}


def build(reads: list[dict[str, Any]], documents: list[dict[str, Any]], aliases: list[dict[str, Any]],
          judgments: list[dict[str, Any]]) -> dict[str, Any]:
    stem_to_doc = {(d["corpus"], stem): d for d in documents for stem in d["stems"]}
    by_case: dict[str, set[str]] = defaultdict(set)
    for row in aliases:
        if row["alias_type"] == "case_number":
            by_case[row["alias"]].add(row["judgment_id"])
    decided = {j["judgment_id"]: j.get("decision_date") for j in judgments}
    held_courts = {j["court_id"] for j in judgments}

    edges, outcomes = [], Counter()
    appellate_court: dict[str, str] = {}
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc is None:
            continue
        appellate = doc["judgment_id"]
        appellate_court[appellate] = item["court_id"]
        for recital in item["recitals"]:
            evidence = Evidence(document_id=doc["document_id"], page=recital["page"], bboxes=recital["bboxes"],
                                block_ids=recital["block_ids"], quote=recital["quote"])
            for case in recital["cases"]:
                alias = f"{recital['court_id']}:{case}"
                candidates = sorted(by_case.get(alias, set()) - {appellate})
                dated = [j for j in candidates if decided.get(j) in recital["dates"]]
                if recital["court_id"] not in held_courts:
                    state = "court_not_held"
                elif case.endswith("/?"):
                    state = "no_year_in_case_number"
                elif not candidates:
                    state = "case_not_held"
                elif len(dated) == 1:
                    state = "resolved"
                elif len(dated) > 1:
                    state = "ambiguous"
                else:
                    state = "case_held_date_differs"
                outcomes[state] += 1
                edges.append({
                    "from_judgment": appellate, "lower_court": recital["court_id"], "lower_case": case,
                    "lower_dates": recital["dates"], "to_judgment": dated[0] if state == "resolved" else None,
                    "state": state,
                    "candidates": [{"judgment_id": j, "decision_date": decided.get(j)} for j in candidates][:10],
                    **Fact(value=alias, method="deterministic", extractor=EXTRACTOR, extractor_version=VERSION,
                           confidence=1.0 if state == "resolved" else 0.0, evidence=[evidence]).model_dump(),
                })

    with_recital = sum(1 for item in reads if item["recitals"])
    by_corpus = Counter(item["corpus"] for item in reads if item["recitals"])
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "documents_read": len(reads),
        "documents_with_a_recital": with_recital,
        "documents_with_a_recital_by_corpus": dict(sorted(by_corpus.items())),
        "appeal_edges": len(edges),
        "by_state": dict(sorted(outcomes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "lower_courts": dict(sorted(Counter(e["lower_court"] for e in edges).items(), key=lambda kv: (-kv[1], kv[0]))),
        "resolved_by_direction": dict(sorted(Counter(
            f"{appellate_court[e['from_judgment']]}->{e['lower_court']}" for e in edges if e["to_judgment"]).items())),
    }
    return {"edges": sorted(edges, key=lambda e: (e["from_judgment"], e["lower_case"])), "report": report}

