"""Read the output of NVIDIA Nemotron Parse into page blocks.

The model returns one string per page image: for each region, its top-left
corner, its text, its bottom-right corner and its class.  This module only
interprets that string; running the model is somewhere else, so the format can
be tested without a GPU.
"""

from __future__ import annotations

import re
from typing import Any

from docintel.engines.native import _is_rtl, _script

Block = dict[str, Any]

SOURCE = "nemotron-parse-v1.2"

# The model reads a fixed canvas; coordinates in its output are fractions of it.
CANVAS_W, CANVAS_H = 1664, 2048

_NUMBER = r"(\d+(?:\.\d+)?)"
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


def _letterbox(image_size: tuple[int, int]) -> tuple[float, float, int, int]:
    """Where the image sits on the model's canvas: its size there and its padding.

    The model shrinks an image to fit (height first, then width) and never
    enlarges it, then centres it with floor-division padding.
    """
    width, height = image_size
    if height > CANVAS_H:
        width, height = int(CANVAS_H * width / height), CANVAS_H
    if width > CANVAS_W:
        width, height = CANVAS_W, int(CANVAS_W * height / width)
    return width, height, (CANVAS_W - width) // 2, (CANVAS_H - height) // 2


def parse_output(raw: str, *, image_size: tuple[int, int], page_size: tuple[float, float]) -> list[Block]:
    placed_w, placed_h, pad_left, pad_top = _letterbox(image_size)
    page_w, page_h = page_size

    def point_x(fraction: str) -> float:
        return (float(fraction) * CANVAS_W - pad_left) / placed_w * page_w

    def point_y(fraction: str) -> float:
        return (float(fraction) * CANVAS_H - pad_top) / placed_h * page_h

    blocks = []
    for order, (x1, y1, text, x2, y2, layout_class) in enumerate(_REGION.findall(raw)):
        bbox = [point_x(x1), point_y(y1), point_x(x2), point_y(y2)]
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


def build_page(
    raw: str, *, page_number: int, image_size: tuple[int, int], page_size: tuple[float, float]
) -> dict[str, Any]:
    """One ``specter.v1`` page from the model's output for that page's image."""
    blocks = []
    for region in parse_output(raw, image_size=image_size, page_size=page_size):
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
                # The model reports no confidence; 0.0 says so rather than claiming certainty.
                "confidence": {"score": 0.0, "reasons": ["engine_reports_no_confidence"]},
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
        "confidence": 0.0,
        "extraction": "scanned",
    }
