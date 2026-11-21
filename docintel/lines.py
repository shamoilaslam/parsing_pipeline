"""The boxes of the lines of text inside one block, found from its image.

Rows of ink are grouped into lines.  The result is used only when it finds exactly as many lines as the
block's text has; otherwise the answer is None and the caller falls back to an even split, marked approximate.
"""

from __future__ import annotations

import cv2
import numpy as np

INK_CONTRAST = 55
ROW_THRESHOLD = 0.06  # of the busiest row's ink: below this a row is a gap between lines
MIN_LINE_HEIGHT = 4
MERGE_GAP = 2  # rows: a gap this small (a dotted i, an accent) does not split a line


def line_boxes(crop: np.ndarray, expected: int | None) -> list[tuple[int, int, int, int]] | None:
    """``(x0, y0, x1, y1)`` per line, in the crop's pixels.  With ``expected``, None unless exactly that many are found."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    ink = (gray.astype(np.int16) < int(np.median(gray)) - INK_CONTRAST).astype(np.uint8)
    rows = ink.sum(axis=1)
    if rows.max(initial=0) == 0:
        return None
    busy = rows > ROW_THRESHOLD * rows.max()
    bands: list[list[int]] = []
    for y, on in enumerate(busy):
        if not on:
            continue
        if bands and y - bands[-1][1] <= MERGE_GAP + 1:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    bands = [band for band in bands if band[1] - band[0] + 1 >= MIN_LINE_HEIGHT]
    if not bands or (expected is not None and len(bands) != expected):
        return None
    boxes = []
    for top, bottom in bands:
        columns = np.nonzero(ink[top : bottom + 1].sum(axis=0))[0]
        boxes.append((int(columns[0]), int(top), int(columns[-1]) + 1, int(bottom) + 1))
    return boxes
