import json
import unittest
from pathlib import Path

from specter.evaluate_urdu import evaluate, gt_blocks, predicted_blocks


ROOT = Path(__file__).resolve().parents[1]


class UrduEvaluationTests(unittest.TestCase):
    def test_sample_has_urdu_matches_and_metrics(self):
        prediction = json.loads((ROOT / "artifacts/digital/2024LHC6559.json").read_text(encoding="utf-8"))
        truth = json.loads((ROOT / "data/references/llamaparse/2024LHC6559.json").read_text(encoding="utf-8"))
        report = evaluate(predicted_blocks(prediction), gt_blocks(truth))
        self.assertGreater(report["ground_truth_blocks"], 0)
        self.assertGreater(report["matched_blocks"], 0)
        self.assertIsNotNone(report["cer"])
        self.assertIsNotNone(report["wer"])


if __name__ == "__main__":
    unittest.main()
