"""Grounding: the rule that keeps a language model from putting words in a document.

A model may label and select; it may not write.  Anything it claims about a document
must come with a quote, and the claim stands only if that quote is found in the parsed
text of a page.  The result carries the page and the block's box, so every field points
back at the pixels it was read from.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

# A quote must match this closely (a single OCR slip in a sentence still passes) and carry
# enough characters to be evidence of anything.
MIN_MATCH = 92
MIN_QUOTE_CHARS = 4


@dataclass(frozen=True)
class Grounding:
    page: int
    block_id: str
    bbox: list[float]
    score: float


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w%$']+", " ", text.lower())).strip()


def ground(quote: str, pages: Sequence[dict[str, Any]], *, pages_allowed: Sequence[int] | None = None) -> Grounding | None:
    wanted = _norm(quote)
    if len(wanted.replace(" ", "")) < MIN_QUOTE_CHARS:
        return None
    best: Grounding | None = None
    for page in pages:
        if pages_allowed is not None and page["page_number"] not in pages_allowed:
            continue
        for block in page["blocks"]:
            score = fuzz.partial_ratio(wanted, _norm(block["text"]))
            if score >= MIN_MATCH and (best is None or score > best.score):
                best = Grounding(page["page_number"], block.get("id", ""), list(block["bbox"]), score)
    return best
