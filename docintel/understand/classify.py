"""Say what kind of document a text is, and why.

A transparent cue scorer, not a model: every kind has weighted cues, a cue in the
first lines (a title) counts more than the same words in the body, and the result
names the cues that fired so a wrong call can be debugged and the cues tuned on
real documents.  Below ``MIN_SCORE`` the answer is ``unknown`` rather than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docintel.parsed import documents

# How much of the start of a document counts as its title area.
HEAD_CHARS = 400
TITLE_WEIGHT, BODY_WEIGHT = 3, 1
MIN_SCORE = 3

# kind -> cue name -> pattern.  Cue names appear in the result's reasons.
CUES: dict[str, dict[str, re.Pattern[str]]] = {
    "fir": {
        "first_information_report": re.compile(r"(?i)first\s+information\s+report"),
        "fir_no": re.compile(r"(?i)\bF\.?I\.?R\.?\s*(?:No|#)"),
        "section_154": re.compile(r"(?i)section\s+154"),
        "police_station": re.compile(r"(?i)police\s+station"),
        "first_information_report_ur": re.compile(r"ابتدائی\s+اطلاعی\s+رپورٹ"),
        "police_station_ur": re.compile(r"تھانہ"),
        "case_no_ur": re.compile(r"مقدمہ\s+نمبر"),
    },
    "judgment": {
        "judgment": re.compile(r"(?i)\bjudg(?:e)?ment\b"),
        "disposal": re.compile(r"(?i)\b(?:is|are|stands?)\s+(?:hereby\s+)?(?:dismissed|allowed|disposed\s+of)\b"),
        "versus": re.compile(r"(?i)\bversus\b"),
    },
    "order": {
        "order_sheet": re.compile(r"(?i)order\s+sheet"),
        "proceedings": re.compile(r"(?i)order\s+or\s+other\s+proceedings"),
        # The form's own column headings: "Date of order/ Proceedings".
        "form_columns": re.compile(r"(?i)date\s+of\s+order"),
        "adjourned": re.compile(r"(?i)\badjourned\b"),
    },
    "pleading": {
        "petition_or_plaint": re.compile(r"(?i)writ\s+petition|\bplaint\b|written\s+statement"),
        "sheweth": re.compile(r"(?i)respectfully\s+(?:sheweth|submitted)"),
        "prayer": re.compile(r"(?i)\bprayer\b|it\s+is\s+(?:most\s+)?(?:respectfully\s+)?prayed"),
    },
    "affidavit": {
        "affidavit": re.compile(r"(?i)\baffidavit\b"),
        "solemnly": re.compile(r"(?i)solemnly\s+(?:affirm|swear|declare)"),
        "deponent": re.compile(r"(?i)\bdeponent\b"),
    },
    "witness_statement": {
        "statement_of_witness": re.compile(r"(?i)statement\s+of\s+(?:the\s+)?witness"),
        "section_161": re.compile(r"(?i)section\s+161|u/s\.?\s*161"),
        "prosecution_witness": re.compile(r"\bP\.?W\.?-?\s?\d+"),
    },
    "medical_report": {
        "medico_legal": re.compile(r"(?i)medico-?\s?legal|\bMLC\b"),
        "injuries": re.compile(r"(?i)\binjur(?:y|ies)\b"),
        "medical_officer": re.compile(r"(?i)medical\s+officer"),
        "post_mortem": re.compile(r"(?i)post-?\s?mortem"),
    },
    "statute": {
        "an_act_to": re.compile(r"(?i)\bAn\s+Act\s+to\b"),
        "be_it_enacted": re.compile(r"(?i)be\s+it\s+enacted"),
        "commencement": re.compile(r"(?i)come\s+into\s+force|short\s+title"),
    },
    "contract": {
        "agreement": re.compile(r"(?i)\bagreement\b"),
        "non_disclosure": re.compile(r"(?i)non-?\s?disclosure"),
        "hereinafter": re.compile(r"(?i)hereinafter"),
        "whereas": re.compile(r"\bWHEREAS\b"),
        "confidential_information": re.compile(r"(?i)confidential\s+information"),
    },
}


@dataclass(frozen=True)
class Classification:
    kind: str
    confidence: float
    reasons: tuple[str, ...]


def _score(kind: str, text: str) -> tuple[int, list[str]]:
    head = text[:HEAD_CHARS]
    score, reasons = 0, []
    for name, pattern in CUES[kind].items():
        if pattern.search(head):
            score += TITLE_WEIGHT
            reasons.append(f"title:{name}")
        elif pattern.search(text):
            score += BODY_WEIGHT
            reasons.append(f"body:{name}")
    return score, reasons


def classify(text: str) -> Classification:
    scored = sorted(((*_score(kind, text), kind) for kind in CUES), key=lambda item: item[0], reverse=True)
    top_score, reasons, kind = scored[0]
    if top_score < MIN_SCORE:
        return Classification("unknown", 0.0, ())
    runner_up = scored[1][0]
    return Classification(kind, top_score / (top_score + runner_up), tuple(reasons))


# Pages read for the call: a document names itself near its start.
PAGES_READ = 2


def classify_folder(parsed: Path) -> dict[str, dict[str, Any]]:
    """The kind of every document in a parsed folder, keyed by output stem."""
    types = {}
    for stem, document in documents(parsed):
        text = "\n".join(block["text"] for page in document["pages"][:PAGES_READ] for block in page["blocks"])
        result = classify(text)
        types[stem] = {
            "kind": result.kind,
            "confidence": round(result.confidence, 3),
            "reasons": list(result.reasons),
        }
    return types
