import json
import tempfile
import unittest
from pathlib import Path

from specter.specter_parser import SpecterParser


ROOT = Path(__file__).resolve().parents[1]


class SpecterParserTests(unittest.TestCase):
    def test_digital_judgment_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = SpecterParser(rtl_mode="image").parse(ROOT / "data" / "pdfs" / "2024LHC6559.pdf", tmp)
            self.assertEqual(result["schema_version"], "specter.v1")
            self.assertEqual(result["document"]["extraction_mode"], "digital")
            self.assertEqual(result["document"]["page_count"], 19)
            self.assertGreater(result["document"]["stats"]["tables"], 0)
            self.assertGreater(result["document"]["stats"]["rtl_blocks"], 0)
            self.assertTrue(result["markdown"])
            self.assertTrue(list(Path(tmp).rglob("*.png")))

            first_page = result["pages"][0]
            self.assertTrue(any(block["type"] == "table" for block in first_page["blocks"]))
            self.assertTrue(all("bbox" in block and len(block["bbox"]) == 4 for page in result["pages"] for block in page["blocks"]))
            self.assertTrue(any("case_number" in result["document"]["metadata"] for _ in [0]))

    def test_scanned_or_image_only_is_explicit(self):
        result = SpecterParser().parse(ROOT / "data" / "pdfs" / "cp_159_2021.pdf")
        self.assertIn(result["document"]["extraction_mode"], {"scanned_or_image_only", "mixed"})
        self.assertTrue(result["document"]["warnings"])


if __name__ == "__main__":
    unittest.main()
