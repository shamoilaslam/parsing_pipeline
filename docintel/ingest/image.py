"""Photos and scans as PDF pages.

The pipeline reads PDFs, so an image is wrapped in a PDF with one page per frame:
the scan route then handles it like any scanned page, and its boxes are in the
PDF's points.  A photo of a page on a desk is straightened and cropped to the
paper first.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image, ImageOps, ImageSequence

# Pixels to points when the file does not say its own resolution.
ASSUMED_DPI = 150

# A sheet seen against a background fills a good part of the frame but not all of
# it.  Outside this range the "sheet" is the whole image (a flat scan) or noise,
# and cropping would cut real content.
SHEET_AREA = (0.30, 0.92)


def _upright_frames(image: Path) -> Iterator[Image.Image]:
    """Each frame as a person would see it: EXIF rotation applied."""
    with Image.open(image) as source:
        for frame in ImageSequence.Iterator(source):
            yield ImageOps.exif_transpose(frame)


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left."""
    by_sum = points.sum(axis=1)
    by_difference = np.diff(points, axis=1).ravel()
    return np.float32(
        [
            points[by_sum.argmin()],
            points[by_difference.argmin()],
            points[by_sum.argmax()],
            points[by_difference.argmax()],
        ]
    )


def _straighten(frame: Image.Image) -> Image.Image:
    """Crop a photographed sheet to the paper and undo its perspective; else return the frame."""
    pixels = np.array(frame.convert("L"))
    blurred = cv2.GaussianBlur(pixels, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return frame
    largest = max(contours, key=cv2.contourArea)
    share = cv2.contourArea(largest) / pixels.size
    quad = cv2.approxPolyDP(largest, 0.02 * cv2.arcLength(largest, True), True)
    if len(quad) != 4 or not SHEET_AREA[0] <= share <= SHEET_AREA[1]:
        return frame
    corners = _order_corners(quad.reshape(4, 2).astype(np.float32))
    top_left, top_right, bottom_right, bottom_left = corners
    width = int(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
    height = int(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(corners, target)
    return Image.fromarray(cv2.warpPerspective(np.array(frame.convert("RGB")), matrix, (width, height)))


def image_to_pdf(image: Path, out_pdf: Path) -> Path:
    doc = fitz.open()
    for frame in _upright_frames(image):
        frame = _straighten(frame)
        width, height = frame.size
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        page = doc.new_page(width=width * 72 / ASSUMED_DPI, height=height * 72 / ASSUMED_DPI)
        page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(out_pdf)
    return out_pdf
