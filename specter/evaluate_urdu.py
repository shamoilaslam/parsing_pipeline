"""Measure Urdu CER/WER against a LlamaParse-style ground-truth JSON.

Usage:
    python -m specter.evaluate_urdu artifacts/digital/2024LHC6559.json data/references/llamaparse/2024LHC6559.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from jiwer import cer, wer


def _rtl(text: str) -> bool:
    return any(0x0600 <= ord(char) <= 0x06FF or 0x0750 <= ord(char) <= 0x077F or 0xFB50 <= ord(char) <= 0xFEFF for char in text)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def to_box(box: Any) -> list[float]:
    if isinstance(box, dict):
        return [float(box.get("x", 0)), float(box.get("y", 0)), float(box.get("x", 0)) + float(box.get("w", 0)), float(box.get("y", 0)) + float(box.get("h", 0))]
    return [float(value) for value in box]


def iou(left: list[float], right: list[float]) -> float:
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def gt_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for page in data.get("pages", []):
        for item in page.get("items", []):
            text = item.get("value") or item.get("md", "")
            if not _rtl(text):
                continue
            boxes = item.get("bbox", [])
            if boxes:
                output.append({"page": page.get("page_number"), "text": normalize(text), "bbox": to_box(boxes[0])})
    return output


def predicted_blocks(data: dict[str, Any], field: str = "native") -> list[dict[str, Any]]:
    output = []
    for page in data.get("pages", []):
        for block in page.get("blocks", []):
            rtl = block.get("rtl", {})
            if not rtl.get("contains_rtl"):
                continue
            text = rtl.get("native_text", block.get("text", "")) if field == "native" else block.get("text", "")
            if field == "ocr":
                text = rtl.get("ocr", {}).get("logical_text", "") or text
            if field == "vision":
                text = rtl.get("vision", {}).get("text", "") or text
            output.append({"page": page.get("page_number"), "text": normalize(text), "bbox": to_box(block.get("bbox", []))})
    return output


def evaluate(predicted: list[dict[str, Any]], truth: list[dict[str, Any]], min_iou: float = 0.05) -> dict[str, Any]:
    rows = []
    matched_truth: set[int] = set()
    # A semantic Urdu paragraph may contain several LlamaParse reference
    # blocks.  Match one prediction to all sufficiently contained reference
    # blocks, then compare the concatenated paragraph once.
    for actual in sorted(predicted, key=lambda item: (item["page"], item["bbox"][1], item["bbox"][0])):
        candidates = []
        for index, expected in enumerate(truth):
            if index in matched_truth or actual["page"] != expected["page"]:
                continue
            overlap = iou(expected["bbox"], actual["bbox"])
            intersection_x0 = max(expected["bbox"][0], actual["bbox"][0])
            intersection_y0 = max(expected["bbox"][1], actual["bbox"][1])
            intersection_x1 = min(expected["bbox"][2], actual["bbox"][2])
            intersection_y1 = min(expected["bbox"][3], actual["bbox"][3])
            intersection = max(0.0, intersection_x1 - intersection_x0) * max(0.0, intersection_y1 - intersection_y0)
            expected_area = max(1e-9, (expected["bbox"][2] - expected["bbox"][0]) * (expected["bbox"][3] - expected["bbox"][1]))
            containment = intersection / expected_area
            if overlap >= min_iou or containment >= 0.60:
                candidates.append((index, overlap, containment, expected))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        expected_text = " ".join(row[3]["text"] for row in candidates)
        matched_truth.update(row[0] for row in candidates)
        min_overlap = min(row[1] for row in candidates)
        rows.append({
            "page": actual["page"],
            "matched": True,
            "truth_blocks": len(candidates),
            "truth": expected_text,
            "prediction": actual["text"],
            "iou": round(min_overlap, 4),
            "cer": cer(expected_text, actual["text"]),
            "wer": wer(expected_text, actual["text"]),
        })
    for index, expected in enumerate(truth):
        if index not in matched_truth:
            rows.append({"page": expected["page"], "matched": False, "truth_blocks": 1, "truth": expected["text"], "prediction": "", "iou": 0.0})
    matched = [row for row in rows if row["matched"]]
    truth_text = "\n".join(row["truth"] for row in matched)
    prediction_text = "\n".join(row["prediction"] for row in matched)
    return {
        "ground_truth_blocks": len(truth),
        "predicted_blocks": len(predicted),
        "matched_blocks": sum(row.get("truth_blocks", 1) for row in matched),
        "coverage": sum(row.get("truth_blocks", 1) for row in matched) / len(truth) if truth else 0.0,
        "cer": cer(truth_text, prediction_text) if matched else None,
        "wer": wer(truth_text, prediction_text) if matched else None,
        "mean_block_cer": sum(row["cer"] for row in matched) / len(matched) if matched else None,
        "mean_block_wer": sum(row["wer"] for row in matched) / len(matched) if matched else None,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Urdu CER/WER against LlamaParse ground truth.")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--field", choices=["native", "vision", "ocr"], default="native")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this file")
    args = parser.parse_args()
    predicted = json.loads(args.prediction.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    report = evaluate(predicted_blocks(predicted, args.field), gt_blocks(truth))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        # PowerShell's legacy console encoding cannot print Urdu safely;
        # JSON escapes preserve the exact characters for redirection/parsing.
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"field={args.field} coverage={report['coverage']:.3f} matched={report['matched_blocks']}/{report['ground_truth_blocks']}")
        print(f"CER={report['cer']:.4f} WER={report['wer']:.4f} mean_block_CER={report['mean_block_cer']:.4f} mean_block_WER={report['mean_block_wer']:.4f}")


if __name__ == "__main__":
    main()
