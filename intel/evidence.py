"""The evidence record every fact carries (ADR 0002, Decision 2), and the quote check.

A fact is stored only with evidence: where on which page it was printed.  A
fact a model produced is accepted only if its quote is found in the document's
own text; ``locate`` is that check, and it maps the quote back to pages and
boxes through the offset map ``rag.normalise`` already builds.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from rag.normalise import DocumentText

Method = Literal["deterministic", "label", "linkage", "model", "human"]
ReviewState = Literal["unreviewed", "accepted", "corrected", "rejected"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Closed):
    document_id: str
    page: int | None = None
    bboxes: list[list[float]] = []
    block_ids: list[str] = []
    char_start: int | None = None
    char_end: int | None = None
    quote: str | None = None
    # Where a label came from when it has no page region: a filename, a meta.json.
    source: str | None = None


class Review(_Closed):
    state: ReviewState = "unreviewed"
    by: str | None = None
    at: str | None = None


class Fact(_Closed):
    value: Any
    method: Method
    extractor: str
    extractor_version: str
    confidence: float
    evidence: list[Evidence]
    review: Review = Review()


def locate(text: DocumentText, quote: str) -> list[Evidence]:
    """Every place ``quote`` occurs in the document, as evidence; empty if it does not occur.

    Whitespace is the one thing allowed to differ: the flattened text joins
    blocks with single spaces and a quote may wrap differently.  Everything
    else -- spelling, punctuation, case -- must match, or the quote is not
    the page's words.
    """
    words = quote.split()
    if not words:
        return []
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words))
    found = []
    for match in pattern.finditer(text.text):
        where = text.provenance(match.start(), match.end())
        found.append(Evidence(
            document_id=text.document_id,
            page=where["page_start"],
            bboxes=where["bboxes"],
            block_ids=where["block_ids"],
            char_start=match.start(),
            char_end=match.end(),
            quote=match.group(0),
        ))
    return found
