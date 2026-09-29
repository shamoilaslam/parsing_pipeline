"""The written-down ``specter.v1`` contract matches what the parser writes."""

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from docintel.schema import SpecterV1
from specter.ingest_pdfs import _parse_one

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "pdfs" / "2021LHC9943.pdf"      # 2 pages, digital: fast
SCHEMA_FILE = ROOT / "docintel" / "schema" / "specter.v1.schema.json"


class SpecterV1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        out = Path(cls._tmp.name)
        # The CLI's own path, so the document carries everything enrichment adds.
        row, failure = _parse_one((SAMPLE, out, "auto", "image", 2.0, "grayscale", 0.45, None, False))
        assert failure is None, failure
        cls.result = json.loads((out / f"{SAMPLE.stem}.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_parsed_document_validates(self):
        SpecterV1.model_validate(self.result)

    def test_unknown_key_on_a_leaf_is_rejected(self):
        # A span has one constructor, so a key it never writes is a bug.
        broken = json.loads(json.dumps(self.result))
        span_block = next(b for p in broken["pages"] for b in p["blocks"] if b.get("spans"))
        span_block["spans"][0]["colour"] = "red"
        with self.assertRaises(ValidationError):
            SpecterV1.model_validate(broken)

    def test_enrichment_keys_on_a_block_are_allowed(self):
        # Blocks are open by design: `specter urdu` adds to them after parsing.
        enriched = json.loads(json.dumps(self.result))
        enriched["pages"][0]["blocks"][0]["vision"] = {"provider": "gemini"}
        SpecterV1.model_validate(enriched)

    def test_scanned_table_cells_validate_without_row_and_column(self):
        cell = {"bbox": [0.0, 0.0, 1.0, 1.0], "text": "x", "confidence": 0.9, "model": "rapidocr"}
        table = {**self.result["pages"][0]["blocks"][0], "type": "table", "cells": [cell], "rows": [["x"]]}
        doc = json.loads(json.dumps(self.result))
        doc["pages"][0]["blocks"].append(table)
        SpecterV1.model_validate(doc)

    def test_exported_json_schema_is_current(self):
        # Non-Python consumers read the file, so it must not drift from the model.
        exported = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(exported, SpecterV1.model_json_schema(),
                         "regenerate docintel/schema/specter.v1.schema.json from SpecterV1")


if __name__ == "__main__":
    unittest.main()
