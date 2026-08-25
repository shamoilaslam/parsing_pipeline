import json
import unittest
from pathlib import Path

from specter.evaluate_layout import evaluate


ROOT = Path(__file__).resolve().parents[1]


class LayoutEvaluationTests(unittest.TestCase):
    def test_sample_layout_report(self):
        prediction = json.loads((ROOT / "artifacts/digital/2024LHC6559.json").read_text(encoding="utf-8"))
        truth = json.loads((ROOT / "data/references/llamaparse/2024LHC6559.json").read_text(encoding="utf-8"))
        report = evaluate(prediction, truth)
        self.assertGreater(report["ground_truth_blocks"], 0)
        self.assertGreater(report["matched_blocks"], 0)
        self.assertEqual(report["table_count"]["ground_truth"], 1)
        self.assertEqual(report["table_count"]["predicted"], 1)
        self.assertGreater(report["reading_order_kendall_tau"], 0.95)
        self.assertGreater(report["layout_f1_at_0_5"], 0.50)
        self.assertGreater(report["bbox_iou_mean"], 0.60)


if __name__ == "__main__":
    unittest.main()
