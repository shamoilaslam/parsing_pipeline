"""Unified text/layout evaluation against a LlamaParse-style reference.

The report is a comparison metric, not a claim of ground-truth accuracy. It
uses the same geometry matching as the digital layout gate and adds CER/WER
for matched blocks, grouped by script and structural type.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import jiwer

from specter.evaluate_layout import _predictions_for_reference, evaluate as evaluate_layout, gt_blocks, match_blocks, merge_reference_blocks


def _distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _normal(text: str) -> str:
    value = str(text or "")
    # Compare table cell content rather than Markdown decoration. The parser
    # stores rows/cells; LlamaParse stores a pipe-delimited Markdown table.
    value = re.sub(r"\*{1,2}", "", value)
    value = re.sub(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$", "", value, flags=re.MULTILINE)
    value = value.replace("|", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _script(text: str) -> str:
    rtl = sum(0x0600 <= ord(char) <= 0x08FF for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    return "mixed" if rtl and latin else ("ur" if rtl else "en")


def evaluate_document(prediction: dict[str, Any], reference: dict[str, Any], threshold: float = 0.10) -> dict[str, Any]:
    layout = evaluate_layout(prediction, reference, threshold)
    expected = merge_reference_blocks(gt_blocks(reference))
    predicted = _predictions_for_reference(prediction, expected)
    matches = match_blocks(predicted, expected, threshold)
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"truth": [], "prediction": []})
    all_truth: list[str] = []
    all_prediction: list[str] = []
    for item in matches:
        if not item["prediction"]:
            continue
        truth_text = _normal(item["truth"].get("text", ""))
        pred_text = _normal(item["prediction"].get("text", ""))
        key = f"{item['truth'].get('type', 'unknown')}:{_script(truth_text)}"
        group = groups[key]
        group["truth"].append(truth_text)
        group["prediction"].append(pred_text)
        all_truth.append(truth_text)
        all_prediction.append(pred_text)
    grouped = {}
    for key, value in groups.items():
        grouped[key] = {
            "matched_blocks": len(value["truth"]),
            "cer": jiwer.cer("\n".join(value["truth"]), "\n".join(value["prediction"])) if value["truth"] else 0.0,
            "wer": jiwer.wer("\n".join(value["truth"]), "\n".join(value["prediction"])) if value["truth"] else 0.0,
        }
    all_pairs = [item for item in matches if item["prediction"]]
    return {
        "reference_note": "LlamaParse output; not ground truth",
        "layout": {key: layout[key] for key in ["ground_truth_blocks", "predicted_blocks", "matched_blocks", "match_coverage", "bbox_iou_mean", "layout_f1_at_0_5", "reading_order_kendall_tau", "table_count", "quality_gate"]},
        "text": {"matched_blocks": len(all_pairs), "cer": jiwer.cer("\n".join(all_truth), "\n".join(all_prediction)) if all_truth else 0.0, "wer": jiwer.wer("\n".join(all_truth), "\n".join(all_prediction)) if all_truth else 0.0, "by_type_and_script": grouped},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate text and layout together.")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_document(json.loads(args.prediction.read_text(encoding="utf-8")), json.loads(args.reference.read_text(encoding="utf-8")))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
