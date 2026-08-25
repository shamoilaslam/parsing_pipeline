"""Render deterministic page-by-page layout overlays.

Green rectangles are reference blocks, blue rectangles are predictions, and
predictions are outlined orange when their matched IoU is below 0.5.  Labels
carry the page-local reading-order index; matched pairs also get an IoU label.
The evaluator's structural block projection is used, while the source PDF is
rendered unchanged underneath it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw

from specter.evaluate_layout import _predictions_for_reference, gt_blocks, match_blocks, merge_reference_blocks, predicted_blocks


def _xy(value: list[float], scale: float) -> tuple[float, float, float, float]:
    return tuple(float(item) * scale for item in value)  # type: ignore[return-value]


def render(pdf_path: Path, prediction: dict[str, Any], truth: dict[str, Any], output_dir: Path, threshold: float = 0.10, scale: float = 1.5) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = merge_reference_blocks(gt_blocks(truth))
    predicted = _predictions_for_reference(prediction, expected)
    decorations = predicted_blocks(prediction, include_decorative=True)
    matches = match_blocks(predicted, expected, threshold)
    matched_by_prediction = {
        item["prediction_index"]: item for item in matches if item["prediction_index"] is not None
    }
    summary = []
    doc = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            page_no = page_index + 1
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            page_truth = [(index, item) for index, item in enumerate(expected) if item["page"] == page_no]
            page_pred = [(index, item) for index, item in enumerate(predicted) if item["page"] == page_no]
            page_matches = [item for item in matches if item["truth"]["page"] == page_no]

            # Reference first, prediction second, so the predicted geometry is
            # visible on top when the two regions are close.
            for _, item in page_truth:
                rect = _xy(item["bbox"], scale)
                draw.rectangle(rect, outline=(0, 175, 0, 220), width=3)
                draw.text((rect[0] + 2, rect[1] + 2), f"GT{item['order']}", fill=(0, 120, 0, 255))
            for prediction_index, item in page_pred:
                match = matched_by_prediction.get(prediction_index)
                overlap = float(match["iou"]) if match else 0.0
                color = (20, 90, 220, 230) if overlap >= 0.5 else (235, 120, 20, 240)
                rect = _xy(item["bbox"], scale)
                draw.rectangle(rect, outline=color, width=3)
                draw.text((rect[0] + 2, max(0, rect[1] - 14)), f"P{item['order']}", fill=color)
            for item in decorations:
                if item["page"] == page_no and item.get("decorative"):
                    rect = _xy(item["bbox"], scale)
                    draw.rectangle(rect, outline=(110, 110, 110, 180), width=2)
                    draw.text((rect[0] + 2, rect[1] + 2), f"D{item['order']}", fill=(90, 90, 90, 255))

            for match in page_matches:
                if match["prediction"] is None:
                    continue
                left = match["truth"]["bbox"]
                right = match["prediction"]["bbox"]
                x1 = ((left[0] + left[2]) / 2) * scale
                y1 = ((left[1] + left[3]) / 2) * scale
                x2 = ((right[0] + right[2]) / 2) * scale
                y2 = ((right[1] + right[3]) / 2) * scale
                draw.line((x1, y1, x2, y2), fill=(120, 0, 150, 180), width=2)
                draw.text(((x1 + x2) / 2, (y1 + y2) / 2), f"IoU {match['iou']:.2f}", fill=(120, 0, 150, 255))

            legend = "GT green | P blue >= .50 | P orange < .50 | D gray | line: IoU"
            draw.rectangle((8, 8, min(image.width - 8, 430), 32), fill=(255, 255, 255, 220), outline=(40, 40, 40, 220))
            draw.text((14, 14), legend, fill=(20, 20, 20, 255))
            rendered = Image.alpha_composite(image, overlay).convert("RGB")
            path = output_dir / f"page_{page_no:03d}.png"
            rendered.save(path)
            summary.append({
                "page": page_no,
                "path": str(path),
                "ground_truth_blocks": len(page_truth),
                "predicted_blocks": len(page_pred),
                "matched_blocks": sum(item["prediction"] is not None for item in page_matches),
                "matches": [
                    {
                        "gt_order": item["truth"]["order"],
                        "prediction_order": item["prediction"]["order"] if item["prediction"] else None,
                        "iou": item["iou"],
                    }
                    for item in page_matches
                ],
            })
    finally:
        doc.close()
    index = {"pdf": str(pdf_path), "scale": scale, "pages": summary}
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Render page-by-page GT/predicted layout overlays.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("prediction", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/digital/overlays/2024LHC6559"))
    parser.add_argument("--iou-threshold", type=float, default=0.10)
    parser.add_argument("--scale", type=float, default=1.5)
    args = parser.parse_args()
    index = render(
        args.pdf,
        json.loads(args.prediction.read_text(encoding="utf-8")),
        json.loads(args.ground_truth.read_text(encoding="utf-8")),
        args.output,
        args.iou_threshold,
        args.scale,
    )
    print(f"rendered={len(index['pages'])} pages output={args.output}")


if __name__ == "__main__":
    main()
