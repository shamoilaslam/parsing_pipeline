"""The second look at a page: stamps, pen notes, and ink no block accounts for.

Works on any page dict with blocks (read by Nemotron, or by the PDF's own text layer) and the page's
image, so a digital page that carries stamps or pasted scans gets the same treatment as a scan.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2

from docintel.engines.nemotron_parse import add_marks, add_unaccounted, parse_output
from docintel.marks import find_handwriting, find_stamps, find_unaccounted, isolate_ink, near_signature

MarkReader = Callable[[Path], str]


def _text(reader: MarkReader, crop: Path, size: tuple[int, int]) -> str:
    regions = parse_output(reader(crop), page_size=size)
    return " ".join(region["text"].strip() for region in regions if region["text"].strip())


def _px(bbox: list[float], scale: tuple[float, float]) -> tuple[int, int, int, int]:
    return int(bbox[0] / scale[0]), int(bbox[1] / scale[1]), int(bbox[2] / scale[0]), int(bbox[3] / scale[1])


def read_marks(page: dict[str, Any], job: dict[str, Any], reader: MarkReader) -> None:
    """Add stamps, pen notes, recovered text, signatures and illegible ink to ``page``.

    ``job`` holds the page image (``image_path``) and the page's size in points (``page_size``).
    """
    image = cv2.imread(job["image_path"])
    if image is None:
        return
    scale = (job["page_size"][0] / image.shape[1], job["page_size"][1] / image.shape[0])
    stem = Path(job["image_path"])
    marks = []
    for kind, ink, find in (("stamp", "red", find_stamps), ("handwriting", "blue", find_handwriting)):
        for index, (x0, y0, x1, y1) in enumerate(find(image)):
            crop = stem.with_name(f"{stem.stem}_{kind[0]}{index}.png")
            isolated = isolate_ink(image[y0:y1, x0:x1], ink)
            cv2.imwrite(str(crop), isolated)
            marks.append((kind, _text(reader, crop, (isolated.shape[1], isolated.shape[0])), [x0 * scale[0], y0 * scale[1], x1 * scale[0], y1 * scale[1]]))
    add_marks(page, marks)

    covered = [_px(block["bbox"], scale) for block in page["blocks"]]
    labels = [(_px(block["bbox"], scale), block["text"]) for block in page["blocks"]]
    found = []
    for index, (x0, y0, x1, y1) in enumerate(find_unaccounted(image, covered)):
        crop = stem.with_name(f"{stem.stem}_r{index}.png")
        cv2.imwrite(str(crop), image[y0:y1, x0:x1])
        found.append((_text(reader, crop, (x1 - x0, y1 - y0)), [x0 * scale[0], y0 * scale[1], x1 * scale[0], y1 * scale[1]],
                      near_signature(image, (x0, y0, x1, y1), labels)))
    add_unaccounted(page, found)
