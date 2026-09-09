"""The CLI's corpus-level behaviour: what enters the index, and what does not."""

import json
import tempfile
import unittest
from pathlib import Path

from rag.__main__ import _chunk_dir, main


def document(text, kind="judgment", metadata=None):
    return {"pages": [{"page_number": 2, "blocks": [
                {"id": "p2_b0", "type": "text", "text": text,
                 "bbox": [72.0, 100.0, 540.0, 114.0]}]}],
            "document": {"document_kind": kind, "metadata": metadata or {"court_id": "lahore_high_court"},
                         "structure": []}}


class DeduplicationTests(unittest.TestCase):
    """The Lahore corpus holds 9,512 PDFs and 8,243 unique documents.

    1,269 are byte-identical second copies, mostly named "2011LHC4385 (1).pdf".
    Indexed as they stand, a search returns the same judgment twice and pushes a
    real second result off the page.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, name, text):
        (self.root / f"{name}.json").write_text(json.dumps(document(text)), encoding="utf-8")

    def test_a_second_copy_of_a_judgment_is_not_indexed_twice(self):
        body = "The appeal is allowed and the impugned decree is set aside accordingly."
        self.write("2011LHC4385", body)
        self.write("2011LHC4385 (1)", body)
        self.write("2011LHC9999", "A different judgment about a different dispute entirely.")
        chunks, duplicates = _chunk_dir(self.root, "LHC", None)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len({c["document_id"] for c in chunks}), 2)

    def test_a_copy_saved_under_an_unrelated_name_is_still_caught(self):
        # Matching on the filename would miss this; matching on the text does not.
        body = "The petition is dismissed for want of jurisdiction in these circumstances."
        self.write("2011LHC4385", body)
        self.write("some_other_name", body)
        _, duplicates = _chunk_dir(self.root, "LHC", None)
        self.assertEqual(duplicates, 1)

    def test_two_genuinely_different_judgments_both_survive(self):
        self.write("a", "The appeal is allowed and the decree is set aside on these facts.")
        self.write("b", "The revision is dismissed as the concurrent findings disclose no error.")
        chunks, duplicates = _chunk_dir(self.root, "LHC", None)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len({c["document_id"] for c in chunks}), 2)

    def test_duplicates_can_be_kept_deliberately(self):
        body = "The appeal is allowed and the impugned decree is set aside accordingly."
        self.write("2011LHC4385", body)
        self.write("2011LHC4385 (1)", body)
        chunks, duplicates = _chunk_dir(self.root, "LHC", None, deduplicate=False)
        self.assertEqual(duplicates, 0)
        self.assertEqual(len({c["document_id"] for c in chunks}), 2)

    def test_the_command_reports_what_it_skipped(self):
        body = "The appeal is allowed and the impugned decree is set aside accordingly."
        self.write("2011LHC4385", body)
        self.write("2011LHC4385 (1)", body)
        out = self.root / "chunks.jsonl"
        self.assertEqual(main(["chunk", str(self.root), "--out", str(out), "--source", "LHC"]), 0)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()


class SearchTests(unittest.TestCase):
    """A hit has to say where it is printed and which leg found it."""

    def setUp(self):
        from rag.retrieve import HybridIndex

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.index_dir = Path(self._dir.name) / "index"
        chunks = [
            {"id": "s302", "document_id": "ppc", "citation": "302 PPC", "prefix": "THE PAKISTAN PENAL CODE",
             "text": "302. Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished with death as qisas.",
             "page_start": 106, "page_end": 106, "bboxes": [[72.0, 100.0, 540.0, 140.0]],
             "block_ids": ["p106_b3"], "document_kind": "statute", "source": "pakistancode"},
            {"id": "s497", "document_id": "crpc", "citation": "497 CRPC", "prefix": "CODE OF CRIMINAL PROCEDURE",
             "text": "497. When bail may be taken in case of non-bailable offence.",
             "page_start": 167, "page_end": 167, "bboxes": [[72.0, 200.0, 540.0, 240.0]],
             "block_ids": ["p167_b1"], "document_kind": "statute", "source": "pakistancode"}]
        HybridIndex(chunks).save(self.index_dir)

    def test_a_search_reports_the_citation_page_and_boxes(self):
        code = main(["search", "bail non-bailable offence", "--index", str(self.index_dir),
                     "--embedder", "hashing", "-k", "2"])
        self.assertEqual(code, 0)

    def test_the_json_form_carries_the_boxes_a_reader_would_cite(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["search", "qatl-i-amd qisas", "--index", str(self.index_dir),
                  "--embedder", "hashing", "--json", "-k", "1"])
        hit = json.loads(out.getvalue())[0]
        self.assertEqual(hit["citation"], "302 PPC")
        self.assertEqual(hit["pages"], [106, 106])
        self.assertTrue(hit["bboxes"])
        self.assertIn("bm25", hit["legs"])
