"""Take back court metadata the legal enricher guessed for a document that is not a court document.

The enricher reads every file as a judgment.  A letter that says "approved by the Islamabad High
Court" comes out with that court, a case number and a judgment label.  Once the classifier says what
a file is, a letter, contract or ledger keeps only what its page states (dates, acts, sections),
never a court, parties or a decision, and takes the classifier's kind.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docintel.akn.build import COURT_KINDS, names_a_court
from docintel.parsed import documents

SCALAR_FIELDS = (
    "court", "court_id", "bench", "case_number", "decision_date", "decision_date_iso", "hearing_date",
    "hearing_date_iso", "parties", "petitioner", "respondent", "neutral_citation",
)
LIST_FIELDS = ("judges", "counsel", "citations")


def _retract(document: dict[str, Any], kind: str) -> None:
    document["document_kind"] = kind
    metadata = document.get("metadata") or {}
    for field in SCALAR_FIELDS:
        if field in metadata:
            metadata[field] = None
    for field in LIST_FIELDS:
        if field in metadata:
            metadata[field] = []
    for field in (*SCALAR_FIELDS, *LIST_FIELDS):
        (document.get("metadata_provenance") or {}).pop(field, None)
    document["label_check"] = None


def retract_non_court(parsed: Path, types: dict[str, dict[str, Any]]) -> list[str]:
    changed = []
    for stem, payload in documents(parsed):
        kind = types.get(stem, {}).get("kind", "unknown")
        # An "order" or "judgment" whose court was found in the body but never in a heading (a licence
        # order quoting a court) is no court document.  One with no court at all (a law-journal
        # reprint) is left as it is.
        guessed = not names_a_court(payload) and (payload["document"].get("metadata") or {}).get("court_id")
        court = kind in COURT_KINDS and not guessed
        if kind == "unknown" or court or kind == "statute":
            continue
        _retract(payload["document"], kind)
        (parsed / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = parsed / "metadata" / f"{stem}.json"
        if meta.exists():
            record = json.loads(meta.read_text(encoding="utf-8"))
            _retract(record, kind)
            meta.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(stem)
    return changed
