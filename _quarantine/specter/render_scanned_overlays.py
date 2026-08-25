"""Render OCR/layout overlays for scanned pages.

Predicted blocks are blue and include reading order, model, and confidence.
When a comparison reference is supplied, reference blocks are green and each
matched prediction is labelled with IoU.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw

from specter.evaluate_layout import _predictions_for_reference, gt_blocks, match_blocks, merge_reference_blocks, predicted_blocks
from specter.evaluate_scanned import _filter_excluded


def _xy(box: list[float], scale: float) -> tuple[float, float, float, float]:
    return tuple(float(value) * scale for value in box)  # type: ignore[return-value]


def render(pdf: Path, prediction: dict[str, Any], output_dir: Path, reference: dict[str, Any] | None = None, scale: float = 1.5) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if reference is not None:
        prediction, reference, excluded = _filter_excluded(prediction, reference)
        expected = merge_reference_blocks(gt_blocks(reference))
        predicted = _predictions_for_reference(prediction, expected)
        matches = match_blocks(predicted, expected, 0.10)
    else:
        excluded = {int(page["page_number"]) for page in prediction.get("pages", []) if page.get("excluded")}
        expected, matches = [], []
        predicted = predicted_blocks(prediction, include_decorative=True)
    matched_by_prediction = {item["prediction_index"]: item for item in matches if item.get("prediction_index") is not None}
    predicted_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in predicted:
        predicted_by_page.setdefault(int(item["page"]), []).append(item)
    expected_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in expected:
        expected_by_page.setdefault(int(item["page"]), []).append(item)
    summary = []
    doc = fitz.open(pdf)
    try:
        for index, page in enumerate(doc):
            page_no = index + 1
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            for item in expected_by_page.get(page_no, []):
                rect = _xy(item["bbox"], scale)
                draw.rectangle(rect, outline=(0, 170, 0, 220), width=3)
                draw.text((rect[0] + 2, rect[1] + 2), f"GT{item['order']}", fill=(0, 120, 0, 255))
            page_pred = predicted_by_page.get(page_no, [])
            for pred_index, item in enumerate(page_pred):
                global_index = predicted.index(item)
                match = matched_by_prediction.get(global_index)
                iou = float(match["iou"]) if match else None
                color = (20, 90, 220, 235) if iou is None or iou >= 0.5 else (235, 120, 20, 240)
                rect = _xy(item["bbox"], scale)
                draw.rectangle(rect, outline=color, width=3)
                confidence = next((b.get("ocr", {}).get("confidence") for p in prediction.get("pages", []) if p.get("page_number") == page_no for b in p.get("blocks", []) if b.get("bbox") == item["bbox"]), None)
                label = f"P{item['order']}"
                if confidence is not None:
                    label += f" c{float(confidence):.2f}"
                if iou is not None:
                    label += f" IoU {iou:.2f}"
                draw.text((rect[0] + 2, max(0, rect[1] - 14)), label, fill=color)
            if page_no in excluded:
                draw.rectangle((8, 8, min(image.width - 8, 470), 38), fill=(255, 235, 210, 230), outline=(170, 80, 0, 230))
                reason = next((p.get("exclusion_reason", "excluded") for p in prediction.get("pages", []) if p.get("page_number") == page_no), "excluded")
                draw.text((14, 16), f"EXCLUDED: {reason}", fill=(150, 60, 0, 255))
            else:
                legend = "GT green | OCR blue/orange | label: order confidence IoU"
                draw.rectangle((8, 8, min(image.width - 8, 500), 32), fill=(255, 255, 255, 220), outline=(40, 40, 40, 220))
                draw.text((14, 14), legend, fill=(20, 20, 20, 255))
            rendered = Image.alpha_composite(image, overlay).convert("RGB")
            path = output_dir / f"page_{page_no:03d}.png"
            rendered.save(path)
            summary.append({"page": page_no, "path": str(path), "predicted_blocks": len(page_pred), "reference_blocks": len(expected_by_page.get(page_no, []))})
    finally:
        doc.close()
    index = {"pdf": str(pdf), "pages": summary, "reference": bool(reference), "excluded_pages": sorted(excluded)}
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Render scanned OCR overlays.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("prediction", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/scanned/overlays"))
    parser.add_argument("--scale", type=float, default=1.5)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
    result = render(args.pdf, prediction, args.output, reference, args.scale)
    print(f"rendered={len(result['pages'])} output={args.output}")


if __name__ == "__main__":
    main()
