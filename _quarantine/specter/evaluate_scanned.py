"""Evaluate scanned OCR against a LlamaParse comparison reference.

This is intentionally separate from the production gate: LlamaParse output
is useful for regression comparison, but it is not labelled ground truth.
Excluded blue-slip/blank pages are removed from both sides before matching.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

from specter.evaluate_layout import (
    _predictions_for_reference,
    evaluate as evaluate_layout,
    gt_blocks,
    match_blocks,
    merge_reference_blocks,
)


def _levenshtein(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _filter_excluded(prediction: dict[str, Any], reference: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], set[int]]:
    excluded = {int(page["page_number"]) for page in prediction.get("pages", []) if page.get("excluded")}
    pred = dict(prediction)
    pred["pages"] = [page for page in prediction.get("pages", []) if int(page.get("page_number", 0)) not in excluded]
    truth = dict(reference)
    truth["pages"] = [page for page in reference.get("pages", []) if int(page.get("page_number", 0)) not in excluded]
    return pred, truth, excluded


def evaluate_scanned(prediction: dict[str, Any], reference: dict[str, Any], threshold: float = 0.10) -> dict[str, Any]:
    prediction, reference, excluded = _filter_excluded(prediction, reference)
    report = evaluate_layout(prediction, reference, threshold)
    expected = merge_reference_blocks(gt_blocks(reference))
    predicted = _predictions_for_reference(prediction, expected)
    matches = match_blocks(predicted, expected, threshold)
    char_distance = char_total = word_distance = word_total = 0
    text_pairs = 0
    for item in matches:
        if not item["prediction"]:
            continue
        truth_text = _norm(item["truth"].get("text", ""))
        pred_text = _norm(item["prediction"].get("text", ""))
        truth_chars, pred_chars = list(truth_text), list(pred_text)
        truth_words, pred_words = truth_text.split(), pred_text.split()
        char_distance += _levenshtein(truth_chars, pred_chars)
        char_total += len(truth_chars)
        word_distance += _levenshtein(truth_words, pred_words)
        word_total += len(truth_words)
        text_pairs += 1
    report["comparison_reference"] = "LlamaParse output; not ground truth"
    report["excluded_pages"] = sorted(excluded)
    report["text"] = {
        "matched_block_pairs": text_pairs,
        "cer": char_distance / char_total if char_total else 0.0,
        "wer": word_distance / word_total if word_total else 0.0,
        "note": "Text CER/WER is only a regression signal because the reference contains OCR/annotation errors and different block granularity.",
    }
    report["ocr_models"] = prediction.get("document", {}).get("stats", {}).get("models", [])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare scanned OCR/layout to a LlamaParse reference.")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--iou-threshold", type=float, default=0.10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_scanned(json.loads(args.prediction.read_text(encoding="utf-8")), json.loads(args.reference.read_text(encoding="utf-8")), args.iou_threshold)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["comparison_reference", "excluded_pages", "ground_truth_blocks", "predicted_blocks", "match_coverage", "bbox_iou_mean", "layout_f1_at_0_5", "reading_order_kendall_tau", "text", "ocr_models"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
