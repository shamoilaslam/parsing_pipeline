"""Document understanding by a language model that may select but not write.

The model reads the opening of every page and answers in a fixed shape: where each
document starts, what kind it is (from a closed list) and the quote that shows it,
plus a date and the parties, each with its quote.  Nothing it says is kept unless its
quote is found in the parsed page text (``docintel.agent.ground``); what fails is
listed under ``rejected`` and the document is marked ``needs_review``.  An unusable
answer degrades to one unknown document, never to a guess.
"""

from __future__ import annotations

from typing import Any, Protocol

from docintel.agent.ground import ground
from docintel.understand.classify import CUES

KINDS = (*CUES, "other")
# Characters of each page the model is shown, and pages it is shown at most.
OPENING_CHARS = 350
MAX_PAGES = 60

PROMPT = """You are reading a legal case file that may hold several documents, one after another.
Below is the opening of every page.  Answer with JSON only:
{{"documents": [{{"first_page": <int>, "kind": <one of: {kinds}>,
  "kind_quote": <words copied exactly from the document that show its kind>,
  "date_quote": <its date, copied exactly, or null>,
  "parties": [{{"quote": <a person or organisation, copied exactly>, "role": <their role in the document>}}]}}]}}
Rules: the first document starts on page 1; list documents in page order; every quote must be copied
exactly from the text below, never reworded; if you are not sure, use "other" and omit the quote.

{pages}
"""


class Brain(Protocol):
    def json(self, prompt: str) -> dict[str, Any] | None: ...


def _opening(page: dict[str, Any]) -> str:
    return " ".join(block["text"] for block in page["blocks"])[:OPENING_CHARS]


def _evidence(quote: Any, pages: list[dict[str, Any]], span: list[int]) -> dict[str, Any] | None:
    found = ground(quote, pages, pages_allowed=span) if isinstance(quote, str) else None
    return found and {"text": quote, "page": found.page, "block_id": found.block_id, "bbox": found.bbox}


def _document(entry: dict[str, Any], pages: list[dict[str, Any]], first: int, last: int) -> dict[str, Any]:
    span = list(range(first, last + 1))
    rejected = []
    kind = entry.get("kind") if entry.get("kind") in KINDS else "unknown"
    kind_evidence = _evidence(entry.get("kind_quote"), pages, span)
    if kind != "unknown" and not kind_evidence:
        rejected.append({"field": "kind", "claimed": entry.get("kind"), "quote": entry.get("kind_quote")})
        kind = "unknown"
    date = _evidence(entry.get("date_quote"), pages, span)
    if entry.get("date_quote") and not date:
        rejected.append({"field": "date", "quote": entry["date_quote"]})
    parties = []
    for party in entry.get("parties") or []:
        evidence = _evidence(party.get("quote"), pages, span) if isinstance(party, dict) else None
        if evidence:
            parties.append({**evidence, "role": str(party.get("role", ""))})
        else:
            rejected.append({"field": "party", "quote": party.get("quote") if isinstance(party, dict) else party})
    return {
        "first_page": first, "last_page": last, "kind": kind, "kind_evidence": kind_evidence,
        "date": date, "parties": parties, "rejected": rejected,
        "needs_review": kind in ("unknown", "other") or bool(rejected),
    }


def _fallback(page_count: int) -> dict[str, Any]:
    return {"documents": [{"first_page": 1, "last_page": page_count, "kind": "unknown", "kind_evidence": None,
                           "date": None, "parties": [], "rejected": [], "needs_review": True}]}


def understand(pages: list[dict[str, Any]], brain: Brain) -> dict[str, Any]:
    shown = pages[:MAX_PAGES]
    text = "\n".join(f"[PAGE {page['page_number']}] {_opening(page)}" for page in shown)
    answer = brain.json(PROMPT.format(kinds=", ".join(KINDS), pages=text))
    entries = answer.get("documents") if isinstance(answer, dict) else None
    starts = [entry.get("first_page") for entry in entries or [] if isinstance(entry, dict)]
    valid = (
        entries and len(starts) == len(entries) and all(isinstance(start, int) for start in starts)
        and starts[0] == 1 and starts == sorted(set(starts)) and starts[-1] <= len(pages)
    )
    if not valid:
        return _fallback(len(pages))
    ends = [start - 1 for start in starts[1:]] + [len(pages)]
    return {"documents": [_document(entry, pages, start, end) for entry, start, end in zip(entries, starts, ends)]}
