"""Find rubber stamps on a page image.

A stamp is red ink in a compact block, set apart from black print.  Colour is the only cue used,
so a black or grey stamp, and one in blue ink, is not found: a miss, never a guess.
"""

from __future__ import annotations

import cv2
import numpy as np

SATURATION_MIN = 90  # paper and black ink are nearly colourless; stamp ink is not
VALUE_MIN = 90
MERGE_FRACTION = 0.02  # letters and border this close (as a share of page width) are one stamp
MIN_AREA = 0.002  # of the page: a speck is not a stamp
MAX_AREA = 0.35  # a stamp covering most of the page is a colour cast, not a stamp
PAD_FRACTION = 0.01


def find_stamps(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes ``(x0, y0, x1, y1)`` in pixels, top to bottom, of each red-inked stamp."""
    height, width = image.shape[:2]
    hue, saturation, value = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    ink = ((hue <= 10) | (hue >= 170)) & (saturation >= SATURATION_MIN) & (value >= VALUE_MIN)
    size = max(3, int(width * MERGE_FRACTION))
    merged = cv2.morphologyEx(ink.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pad = int(width * PAD_FRACTION)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if MIN_AREA <= (w * h) / (width * height) <= MAX_AREA:
            boxes.append((max(0, x - pad), max(0, y - pad), min(width, x + w + pad), min(height, y + h + pad)))
    return sorted(boxes, key=lambda box: (box[1], box[0]))


INK_SATURATION_MIN = 60  # looser than detection: it only has to keep the faint edges of letters
ENLARGE = 2


def isolate_ink(crop: np.ndarray) -> np.ndarray:
    """The stamp's red ink in black on white, enlarged: print under or around it is left out.

    Read as it lies on the page, a stamp over text is read with that text (a date came back as the
    wrong year); read alone, it comes back as it is.
    """
    hue, saturation, value = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    ink = ((hue <= 10) | (hue >= 170)) & (saturation >= INK_SATURATION_MIN) & (value >= VALUE_MIN - 30)
    ink = cv2.dilate(ink.astype(np.uint8), np.ones((2, 2), np.uint8))
    gray = np.where(ink > 0, 0, 255).astype(np.uint8)
    return cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), None, fx=ENLARGE, fy=ENLARGE, interpolation=cv2.INTER_CUBIC)
