"""The ``specter.v2`` additions: present, derived deterministically, and a strict superset of v1."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from docintel.pipeline import _parse_one
from docintel.schema import SpecterV1, SpecterV2
from docintel.schema.v2 import UID_PREFIX, document_id, script_and_direction

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "pdfs" / "2021LHC9943.pdf"      # 2 pages, digital: fast


class SpecterV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        out = Path(cls._tmp.name)
        row, failure = _parse_one((SAMPLE, out, "auto", "image", 2.0, "grayscale", 0.45, None, False))
        assert failure is None, failure
        cls.result = json.loads((out / f"{SAMPLE.stem}.json").read_text(encoding="utf-8"))
        cls.metadata = json.loads((out / "metadata" / f"{SAMPLE.stem}.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_output_is_valid_v1_and_v2(self):
        SpecterV1.model_validate(self.result)
        SpecterV2.model_validate(self.result)

    def test_document_id_is_the_sha256_of_the_file(self):
        expected = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
        self.assertEqual(document_id(SAMPLE), expected)
        self.assertEqual(self.result["document"]["id"], expected)
        self.assertEqual(self.metadata["id"], expected)

    def test_block_uid_is_unique_and_addresses_its_document(self):
        prefix = self.result["document"]["id"][:UID_PREFIX]
        uids = [block["uid"] for page in self.result["pages"] for block in page["blocks"]]
        self.assertTrue(uids)
        self.assertEqual(len(uids), len(set(uids)))
        self.assertTrue(all(uid.startswith(prefix + "/") for uid in uids))
        first = self.result["pages"][0]["blocks"][0]
        self.assertEqual(first["uid"], f"{prefix}/{first['id']}")

    def test_every_block_has_a_script_and_direction(self):
        blocks = [block for page in self.result["pages"] for block in page["blocks"]]
        self.assertTrue(all(block["script"] in {"Latn", "Arab", "Zyyy"} for block in blocks))
        self.assertTrue(all(block["direction"] in {"ltr", "rtl", "mixed"} for block in blocks))


class ScriptAndDirectionTests(unittest.TestCase):
    def test_cases(self):
        cases = {
            "The petitioner appeals": ("Latn", "ltr"),
            "درخواست گزار": ("Arab", "rtl"),
            "Section 497 کے تحت": ("Zyyy", "mixed"),
            "12.3 (4)": ("Zyyy", "ltr"),
            "": ("Zyyy", "ltr"),
            "۱۲۳": ("Zyyy", "ltr"),       # digits carry no script of their own
            "école": ("Latn", "ltr"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(script_and_direction(text), expected)
