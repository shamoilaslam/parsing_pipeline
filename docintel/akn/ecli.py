"""ECLI-style identifiers: ECLI:PK:<court>:<year>:<ordinal>.

The ordinal is the court's neutral citation number when the document has one
(``2024 LHC 1690``), otherwise the case type's initials and the numbers of the
case number (``W.P No.10464 of 2021`` -> ``WP10464.2021``).  An id is never
invented: no court code, no year or no case identity means no id.
"""

from __future__ import annotations

import re
from typing import Any

from docintel.legal.courts import COURTS

NEUTRAL_CITATION_RE = re.compile(r"(?P<year>\d{4})\s+[A-Z]+\s+(?P<number>\d+)")
CASE_NUMBER_RE = re.compile(r"(?i)^(?P<type>.*?)\s*\bNos?\b\.?\s*(?P<rest>.+)$")
MAX_ORDINAL = 25  # the ECLI grammar's limit


def _court_code(court_id: str | None) -> str | None:
    return next((court.ecli_code for court in COURTS if court.court_id == court_id and court.ecli_code), None)


def _case_ordinal(case_number: str | None) -> str | None:
    match = CASE_NUMBER_RE.match(case_number or "")
    if not match:
        return None
    initials = "".join(word if len(word) == 1 else word[0] for word in re.findall(r"[A-Za-z]+", match["type"]))
    numbers = re.findall(r"\d+", match["rest"])
    if not initials or not numbers:
        return None
    return f"{initials.upper()}{'.'.join(numbers)}"[:MAX_ORDINAL]


def ecli(metadata: dict[str, Any]) -> str | None:
    code = _court_code(metadata.get("court_id"))
    if not code:
        return None
    neutral = NEUTRAL_CITATION_RE.fullmatch(metadata.get("neutral_citation") or "")
    if neutral:
        return f"ECLI:PK:{code}:{neutral['year']}:{neutral['number']}"
    year = (metadata.get("decision_date_iso") or "")[:4]
    ordinal = _case_ordinal(metadata.get("case_number"))
    return f"ECLI:PK:{code}:{year}:{ordinal}" if year.isdigit() and ordinal else None
