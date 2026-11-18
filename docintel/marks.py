"""Find rubber stamps and pen writing on a page image, by the colour of their ink.

A stamp is red ink in a compact block; a pen note is blue ink in thin strokes.  Colour is the only
cue used, so a black or grey stamp, and handwriting in black pencil or pen, is not found: a miss, never a guess.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

SATURATION_MIN = 90  # paper and black ink are nearly colourless; stamp ink is not
VALUE_MIN = 90
MERGE_FRACTION = 0.02  # letters and border this close (as a share of page width) are one stamp
MIN_AREA = 0.002  # of the page: a speck is not a stamp
MAX_AREA = 0.35  # a stamp covering most of the page is a colour cast, not a stamp
PAD_FRACTION = 0.01
BLUE_HUE = (100, 135)  # OpenCV hue: pen ink
MAX_STROKE = 0.012  # of page width: thicker colour is a blob (glare, a logo), not a pen stroke
MIN_INK_PIXELS = 150
MIN_STAMP_INK = 0.0025  # of the page's area in stamp strokes: a real stamp measured 0.0041, glare at most 0.0020
INK_CONTRAST = 55  # how much darker than the paper local to it a pixel must be to be ink
EDGE_ZONE = 0.12  # of page width: left, right and top strips hold shadows and bands, not content
MAX_ELONGATION = 8  # a region this much taller than wide is a rule or a band; wide and thinner than a text line, a rule
MIN_LINE_HEIGHT = 0.014  # of page width: a line of print is at least this tall
MAX_DENSITY = 0.55  # more solid than this is a blob, not writing
MIN_UNACCOUNTED_AREA = 0.0008
COVER_PAD = 0.012
MAX_NOTE_AREA = 0.15


def _ink(image: np.ndarray, kind: str, saturation_min: int, value_min: int) -> np.ndarray:
    hue, saturation, value = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    in_hue = ((hue <= 10) | (hue >= 170)) if kind == "red" else ((hue >= BLUE_HUE[0]) & (hue <= BLUE_HUE[1]))
    return in_hue & (saturation >= saturation_min) & (value >= value_min)


def _thin(ink: np.ndarray, width: int) -> np.ndarray:
    """Only the strokes: colour in thicker blobs (glare, a tinted patch) is taken out."""
    stroke = max(3, int(width * MAX_STROKE))
    blobs = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((stroke, stroke), np.uint8))
    return ink & (1 - cv2.dilate(blobs, np.ones((3, 3), np.uint8)))


def find_stamps(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes ``(x0, y0, x1, y1)`` in pixels, top to bottom, of each red-inked stamp."""
    height, width = image.shape[:2]
    ink = _thin(_ink(image, "red", SATURATION_MIN, VALUE_MIN).astype(np.uint8), width)
    size = max(3, int(width * MERGE_FRACTION))
    merged = cv2.morphologyEx(ink * 255, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pad = int(width * PAD_FRACTION)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        on_the_page = x > 0 and y > 0 and x + w < width and y + h < height  # a surface running off the photo's edge is not paper
        if (
            on_the_page
            and MIN_AREA <= (w * h) / (width * height) <= MAX_AREA
            and ink[y : y + h, x : x + w].sum() >= MIN_STAMP_INK * width * height
        ):
            boxes.append((max(0, x - pad), max(0, y - pad), min(width, x + w + pad), min(height, y + h + pad)))
    return sorted(boxes, key=lambda box: (box[1], box[0]))


INK_SATURATION_MIN = 60  # looser than detection: it only has to keep the faint edges of letters
ENLARGE = 2


def find_handwriting(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes of pen writing: blue ink in thin strokes, words on a line merged into one note."""
    height, width = image.shape[:2]
    ink = _ink(image, "blue", SATURATION_MIN + 10, VALUE_MIN - 30).astype(np.uint8)
    thin = _thin(ink, width)
    merged = cv2.morphologyEx(thin * 255, cv2.MORPH_CLOSE, np.ones((max(3, int(width * 0.012)), max(3, int(width * 0.03))), np.uint8))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pad = int(width * PAD_FRACTION)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if thin[y : y + h, x : x + w].sum() >= MIN_INK_PIXELS and (w * h) / (width * height) <= MAX_NOTE_AREA:
            boxes.append((max(0, x - pad), max(0, y - pad), min(width, x + w + pad), min(height, y + h + pad)))
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def isolate_ink(crop: np.ndarray, kind: str = "red") -> np.ndarray:
    """The ink of one colour in black on white, enlarged: print under or around it is left out.

    Read as it lies on the page, a stamp over text is read with that text (a date came back as the
    wrong year); read alone, it comes back as it is.
    """
    ink = _ink(crop, kind, INK_SATURATION_MIN, VALUE_MIN - 30)
    ink = cv2.dilate(ink.astype(np.uint8), np.ones((2, 2), np.uint8))
    gray = np.where(ink > 0, 0, 255).astype(np.uint8)
    return cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), None, fx=ENLARGE, fy=ENLARGE, interpolation=cv2.INTER_CUBIC)


def find_unaccounted(image: np.ndarray, covered: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Ink on the page that none of the ``covered`` boxes (every block read, stamps and notes too) accounts for.

    The safety net under the colour rules: whatever the layout model left out, text it skipped or a
    mark of a kind nobody has met, shows up here.  Rules, solid blobs and shadows at the page edge
    are not content and are left out.
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    paper = cv2.medianBlur(gray, 51)
    ink = ((paper.astype(np.int16) - gray.astype(np.int16)) > INK_CONTRAST).astype(np.uint8)
    pad = int(width * COVER_PAD)
    for x0, y0, x1, y1 in covered:
        ink[max(0, y0 - pad) : y1 + pad, max(0, x0 - pad) : x1 + pad] = 0
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    size = max(3, int(width * 0.015))
    merged = cv2.morphologyEx(ink * 255, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    edge = int(width * EDGE_ZONE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        ink_pixels = int(ink[y : y + h, x : x + w].sum())
        in_strip = x + w <= edge or x >= width - edge or y + h <= edge
        if (
            (w * h) / (width * height) >= MIN_UNACCOUNTED_AREA
            and ink_pixels >= MIN_INK_PIXELS - 30
            and h <= MAX_ELONGATION * w
            and not (w > MAX_ELONGATION * h and h < width * MIN_LINE_HEIGHT)
            and ink_pixels / (w * h) <= MAX_DENSITY
            and not in_strip
        ):
            boxes.append((x, y, x + w, y + h))
    return sorted(boxes, key=lambda box: (box[1], box[0]))


SIGNATURE_CUE = re.compile(
    r"(?i)\bsignature\b|\bsigned\b|\bsd/|yours\s+(?:faithfully|sincerely|truly)|for\s+and\s+on\s+behalf"
    r"|authori[sz]ed\s+(?:signatory|representative)|\bwitness(?:ed)?\b|\bdeponent\b"
)
CUE_REACH_X = 0.35  # of page width: how far from the ink a signature label may sit
CUE_REACH_Y = 0.06  # of page height
RULE_BELOW = 0.04  # of page height: where to look for the line a signature sits on
RULE_COVER = 0.6  # of the ink's width the line must span


def _has_rule_below(image: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    paper = cv2.medianBlur(gray, 51)
    strip = ((paper.astype(np.int16) - gray.astype(np.int16)) > INK_CONTRAST)[y1 : y1 + int(height * RULE_BELOW), max(0, x0 - 20) : x1 + 20]
    return bool(strip.size) and bool((strip.sum(axis=1) >= RULE_COVER * (x1 - x0)).any())


def near_signature(image: np.ndarray, box: tuple[int, int, int, int], labels: list[tuple[tuple[int, int, int, int], str]]) -> bool:
    """Is this ink where a signature goes: over a line it sits on, or beside a word such as "Signature"?"""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = box
    for (lx0, ly0, lx1, ly1), text in labels:
        gap_x = max(lx0 - x1, x0 - lx1, 0)
        gap_y = max(ly0 - y1, y0 - ly1, 0)
        if SIGNATURE_CUE.search(text) and gap_x <= width * CUE_REACH_X and gap_y <= height * CUE_REACH_Y:
            return True
    return _has_rule_below(image, box)
