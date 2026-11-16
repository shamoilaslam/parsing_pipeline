"""Read the output of NVIDIA Nemotron 3.5 Parse into page blocks.

The model returns one string per page image: for each region, its top-left
corner, its text, its bottom-right corner and its class.  Coordinates are
fractions of the page image, so a box in PDF points is a fraction times the page
size (checked against a real page: within 4 pt of the PDF's own text box).  This
module only interprets that string; running the model is somewhere else, so the
format can be tested without a GPU.
"""

from __future__ import annotations

import re
from typing import Any

from docintel.engines.native import _is_rtl, _script
from docintel.quality import text_quality

Block = dict[str, Any]

SOURCE = "nemotron-3.5-parse"

_NUMBER = r"(\d+(?:\.\d+)?)"
# A region cut off by the token limit has no closing class tag and is not matched.
_REGION = re.compile(rf"<x_{_NUMBER}><y_{_NUMBER}>(.*?)<x_{_NUMBER}><y_{_NUMBER}><class_([^>]+)>", re.DOTALL)

# Nemotron's layout classes onto specter block types.  A class with no entry
# (Picture, Formula, Caption, ...) is read as text and keeps its class name in
# ``layout_class`` so nothing about it is lost.
_BLOCK_TYPE = {
    "Text": "text",
    "Title": "heading",
    "Section-header": "heading",
    "List-item": "list",
    "Table": "table",
    "Page-header": "header",
    "Page-footer": "footer",
    "Footnote": "footnote",
}


def parse_output(raw: str, *, page_size: tuple[float, float]) -> list[Block]:
    page_w, page_h = page_size
    blocks = []
    for order, (x1, y1, text, x2, y2, layout_class) in enumerate(_REGION.findall(raw)):
        bbox = [float(x1) * page_w, float(y1) * page_h, float(x2) * page_w, float(y2) * page_h]
        blocks.append(
            {
                "type": _BLOCK_TYPE.get(layout_class, "text"),
                "layout_class": layout_class,
                "text": text,
                "bbox": bbox,
                "reading_order": order,
            }
        )
    return blocks


URDU_QUALITY_CAP = 0.2  # measured: the model's Urdu is letter-reversed


def _confidence(text: str, has_rtl: bool) -> dict[str, Any]:
    score, reasons = text_quality(text)
    reasons = ["engine_reports_no_confidence", *reasons]
    if has_rtl:
        score = min(score, URDU_QUALITY_CAP)
        reasons.append("urdu_unreliable_for_engine")
    return {"score": score, "reasons": reasons}


def _page_confidence(blocks: list[dict[str, Any]]) -> float:
    weight = sum(len(block["text"]) for block in blocks)
    if not weight:
        return 0.0
    return round(sum(block["confidence"]["score"] * len(block["text"]) for block in blocks) / weight, 3)


def build_page(raw: str, *, page_number: int, page_size: tuple[float, float]) -> dict[str, Any]:
    """One ``specter.v1`` page from the model's output for that page's image."""
    blocks = []
    for region in parse_output(raw, page_size=page_size):
        text = region["text"]
        has_rtl = any(_is_rtl(char) for char in text)
        blocks.append(
            {
                **region,
                "id": f"p{page_number}_b{region['reading_order']}",
                "markdown": text,
                "language": _script(text),
                "source": SOURCE,
                "contains_rtl": has_rtl,
                # The model reports no confidence, so the score is computed from the text and says so.
                "confidence": _confidence(text, has_rtl),
                # Measured: its Urdu comes back letter-reversed.  The region and box are
                # right; the text is a placeholder until the Urdu stage reads the crop.
                "needs_review": has_rtl,
                "rtl": {"contains_rtl": has_rtl, "text_status": "logical_model"},
            }
        )
    return {
        "page_number": page_number,
        "width": page_size[0],
        "height": page_size[1],
        "rotation": 0,
        "blocks": blocks,
        "markdown": "\n\n".join(block["markdown"] for block in blocks if block["markdown"].strip()),
        "confidence": _page_confidence(blocks),
        "extraction": "scanned",
    }
