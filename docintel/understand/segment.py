"""Split a run of pages into documents.

A case file often arrives as one PDF of many documents.  A page starts a new
document when its opening is a title the page before did not already show;
a page without one continues the document before it.  Each document's kind is then
read from all its pages, so a page-one title and the body cues vote together.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from docintel.parsed import documents

from docintel.understand.classify import CUES, Classification, classify

# How much of a page counts as its opening line(s).
OPENING_CHARS = 90
# Cues that name a document at its top (a title or a form heading).  The weaker cues a
# classifier also uses -- "versus", "petition", "police station" -- turn up in the first
# line of any continuation page of a judgment, so they never open a document.
OPENERS = {
    "first_information_report", "order_sheet", "grant_of", "affidavit", "statement_of_witness",
    "medico_legal", "an_act_to", "agreement", "non_disclosure", "addendum", "salutation", "reference",
    "notification", "application_for", "minutes_of", "ledger", "report_extract", "from_to", "judgment",
}


@dataclass(frozen=True)
class Segment:
    first_page: int  # 1-based, inclusive
    last_page: int
    kind: str
    confidence: float
    reasons: tuple[str, ...]


def _opening_kinds(text: str) -> set[str]:
    opening = text.strip()[:OPENING_CHARS]
    return {
        kind
        for kind, cues in CUES.items()
        for name, pattern in cues.items()
        if name in OPENERS and pattern.search(opening)
    }


def _kind(page_texts: list[str], pages: list[int]) -> Classification:
    return classify(chr(10).join(page_texts[index] for index in pages))


def segment(page_texts: list[str]) -> list[Segment]:
    groups: list[list[int]] = []
    previous: set[str] = set()
    for index, text in enumerate(page_texts):
        opening = _opening_kinds(text)
        # A title starts a document only if the page before did not already show it (a running
        # header) and the document so far is not of that kind (a judgment quoting "judgment").
        starts = not groups or bool(opening - previous and _kind(page_texts, groups[-1]).kind not in opening)
        if starts:
            groups.append([index])
        else:
            groups[-1].append(index)
        previous = opening
    return [
        Segment(group[0] + 1, group[-1] + 1, (result := _kind(page_texts, group)).kind, result.confidence, result.reasons)
        for group in groups
    ]


def classify_folder(parsed: Path) -> dict[str, dict[str, Any]]:
    """What each parsed file is, and the documents inside it.

    A file is named by its first document; ``documents`` lists every document with its pages.
    """
    types = {}
    for stem, document in documents(parsed):
        texts = [chr(10).join(block["text"] for block in page["blocks"]) for page in document["pages"]]
        found = segment(texts) or [Segment(1, 0, "unknown", 0.0, ())]
        first = found[0]
        types[stem] = {
            "kind": first.kind,
            "confidence": round(first.confidence, 3),
            "reasons": list(first.reasons),
            "documents": [{**asdict(part), "confidence": round(part.confidence, 3), "reasons": list(part.reasons)} for part in found],
        }
    return types
