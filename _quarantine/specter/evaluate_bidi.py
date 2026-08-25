"""Compare native Urdu text with python-bidi on the same aligned blocks."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from bidi.algorithm import get_display

from specter.evaluate_urdu import evaluate, gt_blocks, predicted_blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure whether python-bidi helps or hurts this PDF.")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    expected = gt_blocks(truth)

    native = evaluate(predicted_blocks(prediction, "native"), expected)
    bidi_prediction = copy.deepcopy(prediction)
    for page in bidi_prediction.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("rtl", {}).get("contains_rtl"):
                block["text"] = get_display(block.get("text", ""))
    bidi = evaluate(predicted_blocks(bidi_prediction, "native"), expected)
    report = {
        "native": {key: native[key] for key in ("coverage", "cer", "wer", "mean_block_cer", "mean_block_wer")},
        "python_bidi_get_display": {key: bidi[key] for key in ("coverage", "cer", "wer", "mean_block_cer", "mean_block_wer")},
        "delta": {"cer": bidi["cer"] - native["cer"], "wer": bidi["wer"] - native["wer"]},
        "recommendation": "do_not_apply_automatically" if bidi["cer"] > native["cer"] else "requires_more_validation",
    }
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"native CER={native['cer']:.4f} WER={native['wer']:.4f}")
    print(f"python-bidi CER={bidi['cer']:.4f} WER={bidi['wer']:.4f}")
    print(report["recommendation"])


if __name__ == "__main__":
    main()
