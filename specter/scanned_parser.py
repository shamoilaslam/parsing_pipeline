"""Image-first OCR parser for scanned legal PDFs.

The scanned path deliberately stays small and measurable:

* PyMuPDF renders the page and remains the source of page geometry.
* RapidOCR's bundled CPU detector/English recognizer finds text lines.
* The existing local PP-OCR Arabic/Urdu recognizer is run only on uncertain
  lines, then selected using script and confidence evidence.
* OpenCV handles scan cleanup, blue-slip/blank-page detection, and ruled-table
  geometry.  No new model is downloaded by this module.

The result uses the same ``specter.v1`` contract as the digital parser.  OCR
line polygons and provenance are retained because line-level boxes are the
reliable granularity exposed by the lightweight engine; exact character boxes
are not fabricated.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from specter.specter_parser import (
    DATE_RE,
    _area,
    _bbox,
    _clean,
    _cover_fields_from_blocks,
    _cover_fields_from_table,
    _cover_metadata,
    _extract_metadata,
    _metadata_provenance,
    _is_page_number,
    _is_rtl,
    _reading_order,
    _script,
    _table_html,
    write_result,
)


LIST_RE = re.compile(r"^\s*(?:(?:\d+|[A-Z])\s*[.)]|[-*])(?:\s+|$)")
# OCR routinely drops or mangles the gap between the two words -- "BLUESLIP"
# and "BLUE'SLIP" both occur in this corpus -- so a little punctuation noise
# is tolerated between them.  Letters and digits are not, which keeps the
# match from reaching across into neighbouring words.
BLUE_SLIP_RE = re.compile(r"\bBLUE[^A-Za-z0-9]{0,3}SLIP\b", re.I)
KNOWN_JOINED = {
    "JUDICIALDEPARTMENT": "JUDICIAL DEPARTMENT",
    "INTHE": "IN THE",
    "COURTOF": "COURT OF",
    "DATEOFHEARING": "DATE OF HEARING",
    "ORDERoftheCOURT".upper(): "ORDER OF THE COURT",
}


def _polygon_bbox(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
        return _bbox([[float(p[0]), float(p[1]), float(p[0]), float(p[1])] for p in value])
    return list(map(float, value)) if value else [0.0, 0.0, 0.0, 0.0]


def _polygon(value: Any) -> list[list[float]]:
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
        return [[round(float(point[0]), 3), round(float(point[1]), 3)] for point in value]
    x0, y0, x1, y1 = map(float, value or [0, 0, 0, 0])
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _normal_key(text: str) -> str:
    value = re.sub(r"\d+", "#", text.casefold())
    value = re.sub(r"[^\w\u0600-\u06ff#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _text_correction(text: str) -> tuple[str, list[str]]:
    """Apply only deterministic, low-risk OCR cleanup."""
    original = text
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text).strip()
    joined_key = re.sub(r"[^A-Za-z]", "", text).upper()
    if joined_key in KNOWN_JOINED:
        text = KNOWN_JOINED[joined_key]
    reasons = []
    if text != original:
        reasons.append("unicode_and_whitespace_normalization")
    if joined_key in KNOWN_JOINED and text != original:
        reasons.append("known_legal_heading_spacing")
    return text, reasons


def _render_page(page: fitz.Page, scale: float) -> np.ndarray:
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    channels = 3 if pix.n < 4 else 4
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, channels)
    if channels == 4:
        image = image[:, :, :3]
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _preprocess(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Keep the background grayscale rather than aggressively binarizing: this
    # retains faint Urdu strokes and lets RapidOCR use its own thresholding.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced = cv2.normalize(enhanced, None, 0, 255, cv2.NORM_MINMAX)
    clean = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    ink = cv2.threshold(enhanced, 205, 255, cv2.THRESH_BINARY_INV)[1]
    ink_ratio = float(np.count_nonzero(ink)) / float(max(1, ink.size))
    return clean, enhanced, {"method": "grayscale+CLAHE+normalization", "ink_ratio": round(ink_ratio, 6)}


def preprocess_variants(image: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray, dict[str, Any]]]:
    """Return small, independently benchmarkable CPU preprocessing variants.

    Variants are intentionally single-stage. Combining every denoiser and
    threshold creates a combinatorial search with little evidence of benefit.

    Measured over the 32 scanned gold pages, plain ``grayscale`` -- normalize
    only, no enhancement -- beat every other variant on mean CER, median CER,
    worst page, and speed, so it is the default.  Enhancement was actively
    harmful: CLAHE amplifies scan noise along with the ink, and the two pages
    it damaged worst (CER 0.478 and 0.468) both fall to about 0.06 without it.
    See ``docs/evaluation.md`` for the full table.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    variants: dict[str, np.ndarray] = {
        "grayscale": gray,
        "clahe": clahe,
        "otsu": cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        "adaptive": cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11),
    }
    mean = cv2.boxFilter(clahe, cv2.CV_32F, (31, 31), normalize=True)
    sqmean = cv2.boxFilter(np.square(clahe.astype(np.float32)), cv2.CV_32F, (31, 31), normalize=True)
    std = np.sqrt(np.maximum(0.0, sqmean - np.square(mean)))
    sauvola_threshold = mean * (1.0 + 0.20 * (std / 128.0 - 1.0))
    variants["sauvola"] = np.where(clahe.astype(np.float32) > sauvola_threshold, 255, 0).astype(np.uint8)
    foreground = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    points = np.column_stack(np.where(foreground > 0))
    angle = 0.0
    if len(points) > 100:
        rect = cv2.minAreaRect(points.astype(np.float32))
        angle = float(rect[-1])
        if angle < -45:
            angle += 90
        if abs(angle) <= 4.0 and abs(angle) >= 0.15:
            center = (gray.shape[1] / 2.0, gray.shape[0] / 2.0)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            variants["deskew_clahe"] = cv2.warpAffine(clahe, matrix, (gray.shape[1], gray.shape[0]), borderValue=255)
        else:
            variants["deskew_clahe"] = clahe
    else:
        variants["deskew_clahe"] = clahe
    output = {}
    for name, variant in variants.items():
        clean = cv2.cvtColor(variant, cv2.COLOR_GRAY2BGR)
        ink_ratio = float(np.count_nonzero(variant < 205)) / float(max(1, variant.size))
        output[name] = (clean, variant, {"method": name, "estimated_skew_degrees": round(angle, 3), "ink_ratio": round(ink_ratio, 6)})
    return output


def _dominant_page_image(page: fitz.Page) -> bool:
    page_area = float(page.rect.width * page.rect.height)
    if not page_area:
        return False
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    return any(_area(list(map(float, info.get("bbox", (0, 0, 0, 0))))) >= page_area * 0.72 for info in infos)


def _detect_blue_slip(text: str, page_no: int) -> bool:
    # The form is called a blue slip even when the scan itself is monochrome;
    # use its printed title rather than a fragile color heuristic.
    return page_no == 1 and bool(BLUE_SLIP_RE.search(text))


def _long_segments(binary: np.ndarray, horizontal: bool) -> list[tuple[int, int, int, int]]:
    height, width = binary.shape[:2]
    kernel_size = max(25, int((width if horizontal else height) * (0.08 if horizontal else 0.035)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, 1) if horizontal else (1, kernel_size))
    mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    segments = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if horizontal and w >= width * 0.18 and h <= max(12, height * 0.01):
            segments.append((x, y, w, h))
        elif not horizontal and h >= height * 0.035 and w <= max(12, width * 0.01):
            segments.append((x, y, w, h))
    return sorted(segments, key=lambda item: (item[1], item[0]))


def _dedupe_positions(values: list[int], tolerance: int = 8) -> list[int]:
    result: list[int] = []
    for value in sorted(values):
        if not result or value - result[-1] > tolerance:
            result.append(value)
        else:
            result[-1] = int((result[-1] + value) / 2)
    return result


def _detect_table_grids(gray: np.ndarray) -> list[dict[str, Any]]:
    binary = cv2.threshold(gray, 205, 255, cv2.THRESH_BINARY_INV)[1]
    horizontals = _long_segments(binary, True)
    verticals = _long_segments(binary, False)
    if len(horizontals) < 2 or len(verticals) < 2:
        return []
    height, width = gray.shape[:2]
    interior_verticals = [item for item in verticals if item[0] > width * 0.04 and item[0] + item[2] < width * 0.96]
    if len(interior_verticals) < 2:
        return []
    xs = _dedupe_positions([x + w // 2 for x, _, w, _ in interior_verticals])
    vertical_y0 = min(y for _, y, _, _ in interior_verticals)
    vertical_y1 = max(y + h for _, y, _, h in interior_verticals)
    relevant_horizontals = [item for item in horizontals if vertical_y0 - 18 <= item[1] + item[3] // 2 <= vertical_y1 + 18]
    ys = _dedupe_positions([y + h // 2 for _, y, _, h in relevant_horizontals])
    if len(xs) < 3 or len(ys) < 2:
        return []
    grids: list[dict[str, Any]] = []
    for y0, y1 in zip(ys, ys[1:]):
        near_h = [item for item in horizontals if abs(item[1] + item[3] // 2 - y0) <= 10 or abs(item[1] + item[3] // 2 - y1) <= 10]
        if len(near_h) < 2:
            continue
        for x0, x1 in zip(xs, xs[1:]):
            if x1 - x0 < 30 or y1 - y0 < 15:
                continue
            # A grid cell is enough evidence; neighbouring cells are later
            # coalesced into one table block.
            grids.append({"bbox": [float(x0), float(y0), float(x1), float(y1)], "x0": x0, "x1": x1, "y0": y0, "y1": y1})
    if not grids:
        return []
    x0 = min(item["x0"] for item in grids)
    x1 = max(item["x1"] for item in grids)
    y0 = min(item["y0"] for item in grids)
    y1 = max(item["y1"] for item in grids)
    if (x1 - x0) * (y1 - y0) < gray.shape[0] * gray.shape[1] * 0.003:
        return []
    row_positions = _dedupe_positions([item["y0"] for item in grids] + [item["y1"] for item in grids])
    col_positions = _dedupe_positions([item["x0"] for item in grids] + [item["x1"] for item in grids])
    cells = []
    for top, bottom in zip(row_positions, row_positions[1:]):
        row = []
        for left, right in zip(col_positions, col_positions[1:]):
            if right - left >= 30 and bottom - top >= 15:
                row.append([float(left), float(top), float(right), float(bottom)])
        if row:
            cells.append(row)
    return [{"bbox": [float(x0), float(y0), float(x1), float(y1)], "cells": cells}]


def _overlap_ratio(inner: list[float], outer: list[float]) -> float:
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / max(1.0, _area(inner))


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [[_clean(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
    return "\n".join(
        ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    )


class ScannedParser:
    def __init__(
        self,
        render_scale: float = 2.0,
        use_urdu_router: bool = True,
        urdu_model_dir: str | Path = "models/rapidocr_arabic",
        preprocess_variant: str = "grayscale",
        retry_threshold: float = 0.45,
        save_page_images: bool = False,
    ):
        from rapidocr_onnxruntime import RapidOCR

        self.render_scale = render_scale
        self.use_urdu_router = use_urdu_router
        # The rendered page is OCR input, not output.  Saved, it was 63% of a
        # corpus run's bytes and nothing read it back -- ``specter inspect``
        # re-renders from the source PDF, which is the same picture.  Off by
        # default; turn it on for a sample you want to keep beside the parse.
        self.save_page_images = save_page_images
        if preprocess_variant not in {"clahe", "grayscale", "otsu", "adaptive", "sauvola", "deskew_clahe"}:
            raise ValueError("Unknown preprocess_variant")
        self.preprocess_variant = preprocess_variant
        self.retry_threshold = retry_threshold
        self.general_model = "rapidocr-onnx-general-cpu"
        self.urdu_model = "rapidocr-arabic-urdu-cpu"
        self.ocr = RapidOCR()
        self.urdu_ocr = None
        if use_urdu_router:
            try:
                from specter.urdu_ocr import RapidArabicOCR

                self.urdu_ocr = RapidArabicOCR(urdu_model_dir)
            except (FileNotFoundError, ImportError, RuntimeError):
                self.urdu_ocr = None

    def _ocr_page(self, image: np.ndarray, clean: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        result, elapsed = self.ocr(clean)
        lines: list[dict[str, Any]] = []
        for item in result or []:
            if len(item) < 3:
                continue
            polygon, raw_text, raw_score = item[0], str(item[1] or ""), float(item[2] or 0.0)
            text, corrections = _text_correction(raw_text)
            bbox = _polygon_bbox(polygon)
            if not text or _area(bbox) < 4:
                continue
            lines.append({
                "bbox": bbox,
                "polygon": _polygon(polygon),
                "raw_text": raw_text,
                "text": text,
                "score": raw_score,
                "model": self.general_model,
                "provider": "local",
                "corrections": corrections,
            })

        routed = 0
        region_retries = 0
        if self.retry_threshold > 0 and lines:
            retry_indices = [index for index, line in enumerate(lines) if line["score"] < self.retry_threshold]
            if retry_indices:
                retry_clean = preprocess_variants(image)["otsu"][0]
                for index in retry_indices:
                    x0, y0, x1, y1 = map(int, lines[index]["bbox"])
                    pad_x = max(8, int((x1 - x0) * 0.04))
                    pad_y = max(8, int((y1 - y0) * 0.8))
                    crop = retry_clean[max(0, y0 - pad_y):min(retry_clean.shape[0], y1 + pad_y), max(0, x0 - pad_x):min(retry_clean.shape[1], x1 + pad_x)]
                    retry_result, _ = self.ocr(crop) if crop.size else ([], [])
                    retry_candidates = [item for item in (retry_result or []) if len(item) >= 3 and str(item[1] or "").strip()]
                    if not retry_candidates:
                        continue
                    best = max(retry_candidates, key=lambda item: float(item[2] or 0.0))
                    retry_text, retry_corrections = _text_correction(str(best[1] or ""))
                    retry_score = float(best[2] or 0.0)
                    if retry_text and retry_score > lines[index]["score"]:
                        lines[index]["raw_text"] = lines[index]["text"]
                        lines[index]["text"] = retry_text
                        lines[index]["score"] = retry_score
                        lines[index]["preprocessing"] = "otsu-region-retry"
                        lines[index]["corrections"] = retry_corrections
                        lines[index]["retry"] = True
                        region_retries += 1
        if self.urdu_ocr and lines:
            candidates = [index for index, line in enumerate(lines) if line["score"] < 0.80 or any(_is_rtl(ch) for ch in line["text"])]
            # English scans frequently score below .80. Probe a small sample
            # first; only fan out to every uncertain line if the sample
            # produces a plausible Arabic-script result. Explicit RTL output
            # is always probed and never suppressed by the sample limit.
            probe_indices = [index for index in candidates if any(_is_rtl(ch) for ch in lines[index]["text"])]
            probe_indices += [index for index in candidates if index not in probe_indices][:4]
            height, width = clean.shape[:2]

            def crops_for(indices: list[int]) -> list[np.ndarray]:
                crops: list[np.ndarray] = []
                for index in indices:
                    x0, y0, x1, y1 = map(int, lines[index]["bbox"])
                    pad_x = max(8, int((x1 - x0) * 0.04))
                    pad_y = max(8, int((y1 - y0) * 0.8))
                    crop = clean[max(0, y0 - pad_y):min(height, y1 + pad_y), max(0, x0 - pad_x):min(width, x1 + pad_x)]
                    if crop.size:
                        crops.append(crop)
                return crops

            def apply_results(indices: list[int], candidates_result: list[dict[str, Any]]) -> bool:
                accepted = False
                for index, candidate in zip(indices, candidates_result):
                    arabic_text, corrections = _text_correction(str(candidate.get("text") or ""))
                    arabic_score = float(candidate.get("score") or 0.0)
                    has_rtl = any(_is_rtl(ch) for ch in arabic_text)
                    if sum(_is_rtl(ch) for ch in arabic_text) >= 2 and has_rtl and (arabic_score >= 0.45 or arabic_score >= lines[index]["score"] * 0.85):
                        lines[index]["raw_text"] = lines[index]["text"]
                        lines[index]["text"] = arabic_text
                        lines[index]["score"] = arabic_score
                        lines[index]["model"] = self.urdu_model
                        lines[index]["candidates"] = [{"model": self.general_model, "text": lines[index]["raw_text"]}, {"model": self.urdu_model, "text": arabic_text, "score": arabic_score}]
                        lines[index]["corrections"] = corrections
                        accepted = True
                return accepted

            probe_crops = crops_for(probe_indices)
            probe_results = self.urdu_ocr.recognize_images(probe_crops) if probe_crops else []
            if apply_results(probe_indices, probe_results):
                remaining = [index for index in candidates if index not in probe_indices]
                remaining_crops = crops_for(remaining)
                remaining_results = self.urdu_ocr.recognize_images(remaining_crops) if remaining_crops else []
                apply_results(remaining, remaining_results)
            routed = sum(line["model"] == self.urdu_model for line in lines)
        return lines, {"elapsed": [float(value) for value in (elapsed or [])], "routed_urdu_lines": routed, "region_retries": region_retries, "detected_lines": len(lines)}

    @staticmethod
    def _merge_lines(lines: list[dict[str, Any]], page_width: float) -> list[dict[str, Any]]:
        if not lines:
            return []
        lines = sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        heights = [max(1.0, item["bbox"][3] - item["bbox"][1]) for item in lines]
        median_height = statistics.median(heights)
        blocks: list[dict[str, Any]] = []
        for line in lines:
            if not blocks:
                blocks.append({"lines": [line]})
                continue
            previous = blocks[-1]
            last = previous["lines"][-1]
            gap = line["bbox"][1] - last["bbox"][3]
            x_delta = abs(line["bbox"][0] - last["bbox"][0])
            wide = (line["bbox"][2] - line["bbox"][0] > page_width * 0.70 and last["bbox"][2] - last["bbox"][0] > page_width * 0.70)
            same_indent = x_delta <= max(18.0, median_height * 1.4) or wide
            separate_list_items = bool(LIST_RE.match(line["text"])) and bool(LIST_RE.match(last["text"]))
            # OCR polygons are often taller than the visible glyphs and can
            # overlap the next line by several pixels. Treat that overlap as
            # normal line leading, not a paragraph boundary.
            if -24.0 <= gap <= median_height * 1.55 and same_indent and not separate_list_items:
                previous["lines"].append(line)
            else:
                blocks.append({"lines": [line]})

        result = []
        for group in blocks:
            group_lines = group["lines"]
            bbox = _bbox([line["bbox"] for line in group_lines])
            scores = [float(line["score"]) for line in group_lines]
            text = " ".join(line["text"] for line in group_lines if line["text"])
            text, corrections = _text_correction(text)
            models = sorted(set(line["model"] for line in group_lines))
            contains_rtl = any(_is_rtl(ch) for ch in text)
            language_chars = [char for char in text if not char.isspace()]
            valid_chars = sum(char.isalnum() or char in ".,;:!?()[]{}%/+-_\"'&=<>" or _is_rtl(char) for char in language_chars)
            language_confidence = valid_chars / len(language_chars) if language_chars else 0.0
            gaps = [group_lines[index + 1]["bbox"][1] - group_lines[index]["bbox"][3] for index in range(len(group_lines) - 1)]
            continuity = statistics.mean(1.0 if -24.0 <= gap <= median_height * 1.55 else 0.0 for gap in gaps) if gaps else 1.0
            agreement = 0.5 if len(models) == 1 else 0.85
            ocr_confidence = statistics.mean(scores)
            quality_score = 0.55 * ocr_confidence + 0.15 * agreement + 0.15 * language_confidence + 0.15 * continuity
            result.append({
                "text": text,
                "bbox": bbox,
                "line_bboxes": [line["bbox"] for line in group_lines],
                "line_polygons": [line["polygon"] for line in group_lines],
                "line_ocr": [{"text": line["text"], "raw_text": line["raw_text"], "bbox": line["bbox"], "score": round(float(line["score"]), 4), "model": line["model"], "preprocessing": line.get("preprocessing", "primary"), "retry": bool(line.get("retry")), "corrections": line.get("corrections", [])} for line in group_lines],
                "confidence_score": max(0.0, min(1.0, quality_score)),
                "ocr_confidence": max(0.0, min(1.0, ocr_confidence)),
                "agreement": agreement,
                "language_confidence": language_confidence,
                "line_continuity": continuity,
                "models": models,
                "contains_rtl": contains_rtl,
                "language": _script(text),
                "post_corrections": corrections,
            })
        return result

    def _table_blocks(self, grids: list[dict[str, Any]], clean: np.ndarray) -> list[dict[str, Any]]:
        tables = []
        for grid in grids:
            rows: list[list[str]] = []
            cells: list[dict[str, Any]] = []
            for row in grid["cells"]:
                values = []
                for cell in row:
                    x0, y0, x1, y1 = map(int, cell)
                    crop = clean[max(0, y0 + 2):min(clean.shape[0], y1 - 2), max(0, x0 + 2):min(clean.shape[1], x1 - 2)]
                    result, _ = self.ocr(crop) if crop.size else ([], [])
                    value = " ".join(_text_correction(str(item[1]))[0] for item in (result or []) if len(item) >= 2 and str(item[1]).strip())
                    values.append(value)
                    cells.append({"bbox": list(map(float, cell)), "text": value, "confidence": round(statistics.mean([float(item[2]) for item in (result or []) if len(item) >= 3]) if result else 0.0, 4), "model": self.general_model})
                rows.append(values)
            if rows:
                tables.append({
                    "bbox": grid["bbox"],
                    "rows": rows,
                    "cells": cells,
                    "text": "\n".join(" | ".join(row) for row in rows),
                    "markdown": _markdown_table(rows),
                    "html": _table_html(rows),
                    "confidence": round(statistics.mean([cell["confidence"] for cell in cells]) if cells else 0.0, 4),
                })
        return tables

    @staticmethod
    def _classify(block: dict[str, Any], top_keys: set[str], bottom_keys: set[str], page_height: float, page_width: float) -> tuple[str, list[str]]:
        text = block["text"]
        key = _normal_key(text)
        y0 = block["bbox"][1]
        centered = abs((block["bbox"][0] + block["bbox"][2]) / 2 - page_width / 2) < page_width * 0.16
        if key in top_keys or (y0 <= page_height * 0.10 and ("JUDGMENT" in text.upper() or _is_page_number(text))):
            return "header", ["cross_page_repetition_or_top_band"]
        if key in bottom_keys or _is_page_number(text):
            return "footer", ["cross_page_repetition_or_page_number"]
        if LIST_RE.match(text):
            return "list", ["numbered_or_bulleted_prefix"]
        if len(text) <= 100 and centered and text.isupper():
            return "heading", ["centered_heading_shape"]
        return "text", ["ocr_semantic_paragraph"]

    def parse(self, pdf_path: str | Path, output_dir: str | Path = "outputs/specter_scanned",
              ocr_pages: list[int] | None = None, stem: str | None = None) -> dict[str, Any]:
        """Parse a scanned document.

        ``ocr_pages`` restricts OCR to the pages that actually need it.  In a
        mixed document the router already knows which pages have readable
        native text, and those are taken from the digital parse afterwards --
        OCR-ing them costs about 30s each and the result is discarded.  Skipped
        pages are still emitted, with their geometry, so the merge has
        something to replace and no page disappears.
        """
        pdf_path = Path(pdf_path)
        output_dir = Path(output_dir)
        asset_dir = output_dir / "assets" / (stem or pdf_path.stem) / "scanned"
        if self.save_page_images:
            asset_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        raw_pages: list[list[dict[str, Any]]] = []
        page_infos: list[dict[str, Any]] = []
        try:
            wanted = set(ocr_pages) if ocr_pages is not None else None
            for page_index, page in enumerate(doc):
                page_no = page_index + 1
                if wanted is not None and page_no not in wanted:
                    page_infos.append({"page": page, "page_no": page_no, "skipped": True})
                    continue
                image = _render_page(page, self.render_scale)
                if self.preprocess_variant == "clahe":
                    clean, gray, prep = _preprocess(image)
                else:
                    clean, gray, prep = preprocess_variants(image)[self.preprocess_variant]
                page_path = asset_dir / f"page_{page_no:03d}.png"
                if self.save_page_images:
                    cv2.imwrite(str(page_path), image)
                native_text = page.get_text("text") or ""
                lines, ocr_stats = self._ocr_page(image, clean)
                combined_text = " ".join(line["text"] for line in lines)
                blue_slip = _detect_blue_slip(f"{native_text} {combined_text}", page_no)
                blank = prep["ink_ratio"] < 0.003
                page_infos.append({
                    "page": page,
                    "page_no": page_no,
                    "image": image,
                    "clean": clean,
                    "gray": gray,
                    "prep": prep,
                    "native_text": native_text,
                    "lines": lines,
                    "ocr_stats": ocr_stats,
                    "blue_slip": blue_slip,
                    "blank": blank,
                    "dominant_image": _dominant_page_image(page),
                    # Only claimed where the file is actually there: a path to
                    # something that does not exist is worse than no path.
                    "asset": (str(page_path.relative_to(output_dir)).replace("\\", "/")
                              if self.save_page_images else None),
                })

            # Merge before header/footer inference so repeated running regions
            # are compared at semantic-block granularity.
            for info in page_infos:
                if info.get("skipped"):
                    continue
                info["blocks"] = self._merge_lines(info["lines"], float(info["page"].rect.width) * self.render_scale)
            top_keys: dict[str, set[int]] = defaultdict(set)
            bottom_keys: dict[str, set[int]] = defaultdict(set)
            for info in page_infos:
                if info.get("skipped") or info["blue_slip"] or info["blank"]:
                    continue
                height = float(info["clean"].shape[0])
                for block in info["blocks"]:
                    key = _normal_key(block["text"])
                    if len(key) < 3:
                        continue
                    if block["bbox"][1] <= height * 0.16:
                        top_keys[key].add(info["page_no"])
                    if block["bbox"][3] >= height * 0.84:
                        bottom_keys[key].add(info["page_no"])
            repeated_top = {key for key, seen in top_keys.items() if len(seen) >= 2}
            repeated_bottom = {key for key, seen in bottom_keys.items() if len(seen) >= 2}

            pages: list[dict[str, Any]] = []
            body_text_parts: list[str] = []
            all_models: set[str] = set()
            total_blocks = 0
            table_count = 0
            for info in page_infos:
                page = info["page"]
                page_no = info["page_no"]
                if info.get("skipped"):
                    # Geometry only: the merge replaces this with the digital
                    # parse of the same page, which can actually read it.
                    pages.append({
                        "page_number": page_no,
                        "width": float(page.rect.width),
                        "height": float(page.rect.height),
                        "rotation": int(page.rotation),
                        "blocks": [], "markdown": "", "confidence": 0.0,
                        "extraction": "native_text_pending",
                    })
                    continue
                page_height = float(info["clean"].shape[0])
                page_width = float(info["clean"].shape[1])
                # A blue slip is the court's routing form, not case content,
                # so it is dropped rather than indexed.  A blank scan is
                # dropped for the same reason: neither is part of the
                # judgment.  The page still appears in the output with its
                # geometry and reason, so nothing disappears silently.
                excluded_reason = "blue_slip" if info["blue_slip"] else ("blank_scan" if info["blank"] else None)
                if excluded_reason:
                    pages.append({
                        "page_number": page_no,
                        "width": float(page.rect.width),
                        "height": float(page.rect.height),
                        "rotation": int(page.rotation),
                        "blocks": [],
                        "markdown": "",
                        "confidence": 0.0,
                        "extraction": "excluded",
                        "excluded": True,
                        "exclusion_reason": excluded_reason,
                        "scan": {"rendered_image": info["asset"], "preprocessing": info["prep"], "ocr": info["ocr_stats"]},
                    })
                    continue

                grids = _detect_table_grids(info["gray"])
                table_blocks = self._table_blocks(grids, info["clean"]) if grids else []
                table_count += len(table_blocks)
                candidates: list[dict[str, Any]] = []
                for block in info["blocks"]:
                    if any(_overlap_ratio(block["bbox"], table["bbox"]) >= 0.20 for table in table_blocks):
                        continue
                    kind, reasons = self._classify(block, repeated_top, repeated_bottom, page_height, page_width)
                    score = float(block["confidence_score"])
                    all_models.update(block["models"])
                    candidates.append({
                        "id": "",
                        "type": kind,
                        "text": block["text"],
                        "markdown": ("# " + block["text"] if kind == "heading" else block["text"]),
                        "bbox": [round(value / self.render_scale, 3) for value in block["bbox"]],
                        "line_bboxes": [[round(value / self.render_scale, 3) for value in bbox] for bbox in block["line_bboxes"]],
                        "line_polygons": [[[round(value / self.render_scale, 3) for value in point] for point in polygon] for polygon in block["line_polygons"]],
                        "reading_order": 0,
                        "document_reading_order": 0,
                        "decorative": bool(_is_page_number(block["text"]) and block["bbox"][1] < page_height * 0.12),
                        "language": block["language"],
                        "contains_rtl": block["contains_rtl"],
                        "source": "pymupdf-render+rapidocr",
                        "ocr": {
                            "models": block["models"],
                            "provider": "local",
                            "ocr_confidence": round(block["ocr_confidence"], 4),
                            "agreement": round(block["agreement"], 4),
                            "language_confidence": round(block["language_confidence"], 4),
                            "line_continuity": round(block["line_continuity"], 4),
                            "quality_score": round(score, 4),
                            "confidence": round(score, 4),
                            "line_results": [{**line, "bbox": [round(value / self.render_scale, 3) for value in line["bbox"]]} for line in block["line_ocr"]],
                            "preprocessing": info["prep"],
                            "bbox_granularity": "line",
                            "character_bboxes_available": False,
                        },
                        "confidence": {"score": round(score, 3), "reasons": reasons + ["ocr_quality_score"] + block["post_corrections"]},
                        "rtl": {"contains_rtl": block["contains_rtl"], "text_status": "ocr_logical" if block["contains_rtl"] else "logical_ocr"},
                    })
                    body_text_parts.append(block["text"])
                for table_index, table in enumerate(table_blocks):
                    score = float(table["confidence"])
                    candidates.append({
                        "id": "",
                        "type": "table",
                        "text": table["text"],
                        "markdown": table["markdown"],
                        "bbox": [round(value / self.render_scale, 3) for value in table["bbox"]],
                        "rows": table["rows"],
                        "cells": [{**cell, "bbox": [round(value / self.render_scale, 3) for value in cell["bbox"]]} for cell in table["cells"]],
                        "html": table["html"],
                        "reading_order": 0,
                        "language": "mixed",
                        "source": "opencv-ruled-table+rapidocr",
                        "ocr": {"models": [self.general_model], "provider": "local", "confidence": score, "bbox_granularity": "cell", "character_bboxes_available": False},
                        "confidence": {"score": round(score, 3), "reasons": ["ruled_table_geometry", "cell_ocr"]},
                        "rtl": {"contains_rtl": False, "text_status": "logical_ocr"},
                    })
                    body_text_parts.append(table["text"])
                ordered = _reading_order(candidates, float(page.rect.width))
                for order, block in enumerate(ordered):
                    block["reading_order"] = order
                    block["id"] = f"p{page_no}_b{order}"
                page_markdown = "\n\n".join(block["markdown"] for block in ordered if block.get("markdown", "").strip())
                page_score = statistics.mean([float(block["confidence"]["score"]) for block in ordered]) if ordered else 0.0
                total_blocks += len(ordered)
                pages.append({
                    "page_number": page_no,
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "rotation": int(page.rotation),
                    "blocks": ordered,
                    "markdown": page_markdown,
                    "confidence": round(page_score, 3),
                    "extraction": "scanned",
                    "scan": {"rendered_image": info["asset"], "preprocessing": info["prep"], "ocr": info["ocr_stats"], "dominant_page_image": info["dominant_image"]},
                })

            # Metadata comes from the OCR text and the OCR page structure --
            # a scanned document has no native text layer to read it from.
            # ``_cover_fields_from_*`` need only type/text/bbox, all of which
            # OCR blocks carry, so the digital route's structure-first
            # extraction is reused rather than duplicated.
            cover_fields: list[dict[str, Any]] = []
            for page in pages:
                for table_block in [item for item in page["blocks"] if item["type"] == "table"]:
                    cover_fields.extend({**field, "page": page["page_number"]} for field in _cover_fields_from_table(table_block))
                cover_fields.extend({**field, "page": page["page_number"]} for field in _cover_fields_from_blocks(page["blocks"]))
            metadata = _extract_metadata("\n".join(body_text_parts), dict(doc.metadata or {}))
            structured = _cover_metadata(cover_fields, [])
            metadata["counsel"] = structured["counsel"]
            if structured.get("hearing_date"):
                metadata["hearing_date"] = structured["hearing_date"]

            document_order = 0
            for page in pages:
                for block in page["blocks"]:
                    block["document_reading_order"] = document_order
                    document_order += 1
            document_markdown = "\n\n---\n\n".join(page["markdown"] for page in pages if page["markdown"])
            scores = [page["confidence"] for page in pages if page["blocks"]]
            result = {
                "schema_version": "specter.v1",
                "document": {
                    "source_file": str(pdf_path),
                    "source_name": pdf_path.name,
                    "engine": "pymupdf-render+rapidocr",
                    "extraction_mode": "scanned",
                    "page_count": len(pages),
                    "processed_page_count": sum(bool(page["blocks"]) for page in pages),
                    "pdf_metadata": dict(doc.metadata or {}),
                    "metadata": metadata,
                    "metadata_provenance": _metadata_provenance(metadata, pages),
                    "cover_fields": cover_fields,
                    "confidence": round(statistics.mean(scores), 3) if scores else 0.0,
                    "confidence_policy": "quality score = OCR confidence + engine agreement + language validity + line continuity; low-confidence lines may be locally retried",
                    "stats": {"blocks": total_blocks, "tables": table_count, "excluded_pages": sum(bool(page.get("excluded")) for page in pages), "blue_slip_pages": sum(page.get("exclusion_reason") == "blue_slip" for page in pages), "blank_pages": sum(page.get("exclusion_reason") == "blank_scan" for page in pages), "region_retries": sum(int(page.get("scan", {}).get("ocr", {}).get("region_retries", 0)) for page in pages), "preprocess_variant": self.preprocess_variant, "models": sorted(all_models)},
                    "warnings": ["OCR output is line-box accurate; exact character boxes are not available from the lightweight runtime and are not fabricated.", "LlamaParse output is a comparison reference, not ground truth."],
                },
                "pages": pages,
                "markdown": document_markdown,
            }
            return result
        finally:
            doc.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse scanned legal PDFs with local RapidOCR and layout-aware provenance.")
    parser.add_argument("pdf", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/scanned"))
    parser.add_argument("--render-scale", type=float, default=2.0)
    parser.add_argument("--no-urdu-router", action="store_true")
    parser.add_argument("--urdu-model-dir", type=Path, default=Path("models/rapidocr_arabic"))
    parser.add_argument("--preprocess-variant", choices=["clahe", "grayscale", "otsu", "adaptive", "sauvola", "deskew_clahe"], default="grayscale")
    parser.add_argument("--retry-threshold", type=float, default=0.45)
    args = parser.parse_args()
    for pdf in args.pdf:
        result = ScannedParser(
            render_scale=args.render_scale,
            use_urdu_router=not args.no_urdu_router,
            urdu_model_dir=args.urdu_model_dir,
            preprocess_variant=args.preprocess_variant,
            retry_threshold=args.retry_threshold,
        ).parse(pdf, args.out)
        json_path, md_path = write_result(result, args.out)
        print(json.dumps({"pdf": str(pdf), "json": str(json_path), "markdown": str(md_path), "confidence": result["document"]["confidence"], "stats": result["document"]["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
