import tempfile
import unittest
from pathlib import Path

import fitz

from specter.ingest_pdfs import _merge_native_pages, _page_needs_ocr
from specter.scanned_parser import ScannedParser, _detect_blue_slip
from specter.specter_parser import _cover_fields_from_table, _narrow_to_spans


ROOT = Path(__file__).resolve().parents[1]


def single_page(source: Path, index: int, destination: Path) -> Path:
    """Copy one page into its own PDF so a test can OCR it in isolation."""
    out = fitz.open()
    with fitz.open(source) as src:
        out.insert_pdf(src, from_page=index, to_page=index)
    out.save(str(destination))
    out.close()
    return destination


class BlueSlipTests(unittest.TestCase):
    """A blue slip is the court's routing form, not case content."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        page = single_page(ROOT / "data" / "pdfs" / "2013LHC4394.pdf", 0, tmp / "slip.pdf")
        cls.result = ScannedParser().parse(page, tmp / "out")
        cls.page = cls.result["pages"][0]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_page_is_dropped_from_content(self):
        self.assertTrue(self.page["excluded"])
        self.assertEqual(self.page["exclusion_reason"], "blue_slip")
        self.assertEqual(self.page["blocks"], [])
        self.assertEqual(self.page["markdown"], "")

    def test_the_exclusion_is_reported_not_silent(self):
        # The page still appears in the output with its geometry and reason,
        # so a dropped page is never mistaken for one that failed to parse.
        self.assertEqual(self.page["page_number"], 1)
        self.assertGreater(self.page["width"], 0)
        stats = self.result["document"]["stats"]
        self.assertEqual(stats["blue_slip_pages"], 1)
        self.assertEqual(stats["excluded_pages"], 1)

    def test_detection_only_fires_on_the_first_page(self):
        # A later page mentioning the form in passing is ordinary content.
        self.assertTrue(_detect_blue_slip("BLUE SLIP", 1))
        self.assertFalse(_detect_blue_slip("BLUE SLIP", 2))

    def test_detection_survives_ocr_noise_between_the_words(self):
        # Regression: OCR reads this page's title as "BLUE'SLIP", which a
        # whitespace-only pattern misses -- so the form was not being
        # recognised, and therefore not dropped, on the very document the
        # rule exists for.
        for reading in ("BLUE SLIP", "BLUESLIP", "BLUE'SLIP", "BLUE-SLIP", "blue . slip"):
            self.assertTrue(_detect_blue_slip(reading, 1), reading)

    def test_detection_does_not_reach_across_words(self):
        self.assertFalse(_detect_blue_slip("BLUE PAPER SLIP", 1))


class ScannedContractTests(unittest.TestCase):
    """The OCR route must satisfy the same document contract as the digital one."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        page = single_page(ROOT / "data" / "pdfs" / "2013LHC4394.pdf", 2, tmp / "body.pdf")
        cls.result = ScannedParser().parse(page, tmp / "out")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_schema_matches_the_digital_parser(self):
        self.assertEqual(self.result["schema_version"], "specter.v1")
        self.assertIn("pages", self.result)
        self.assertIn("stats", self.result["document"])

    def test_every_block_carries_geometry(self):
        for page in self.result["pages"]:
            for block in page["blocks"]:
                self.assertEqual(len(block["bbox"]), 4, block.get("id"))

    def test_reading_order_is_contiguous(self):
        for page in self.result["pages"]:
            orders = [block["reading_order"] for block in page["blocks"]]
            self.assertEqual(orders, list(range(len(orders))))

    def test_no_page_is_silently_dropped(self):
        # Every page of the source must appear in the output, whatever its
        # kind; a missing page number is indistinguishable from a parse that
        # never ran.
        with fitz.open(ROOT / "data" / "pdfs" / "2013LHC4394.pdf") as src:
            self.assertGreater(len(src), 0)
        self.assertEqual([p["page_number"] for p in self.result["pages"]], [1])


class ScannedMetadataTests(unittest.TestCase):
    """A scanned document has no text layer, so metadata comes from the OCR."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        # Page 3 of this document is its cover sheet.
        page = single_page(ROOT / "data" / "pdfs" / "2013LHC4394.pdf", 2, tmp / "cover.pdf")
        cls.result = ScannedParser().parse(page, tmp / "out")
        cls.document = cls.result["document"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_counsel_is_recovered_from_the_ocr_cover(self):
        # Counsel comes from cover structure, which the scanned route did not
        # read at all before: every scanned document reported zero counsel.
        counsel = self.document["metadata"]["counsel"]
        self.assertTrue(counsel)
        self.assertTrue(all(row["role"] and row["names"] for row in counsel))

    def test_metadata_fields_are_located_on_the_page(self):
        provenance = self.document["metadata_provenance"]
        self.assertTrue(provenance)
        located = [field for field in provenance.values() if field["bbox"]]
        self.assertTrue(located, "no metadata field could be located in the OCR text")

    def test_a_located_box_really_contains_its_value(self):
        # A box that does not contain what it points at is worse than no box.
        page = self.result["pages"][0]
        for field in self.document["metadata_provenance"].values():
            if not field["bbox"] or field["page"] != page["page_number"]:
                continue
            x0, y0, x1, y1 = field["bbox"]
            self.assertLessEqual(x1, page["width"] + 1)
            self.assertLessEqual(y1, page["height"] + 1)
            self.assertLess(x0, x1)
            self.assertLess(y0, y1)

    def test_ocr_line_boxes_use_the_same_units_as_every_other_box(self):
        # Regression: line_results carried render-pixel boxes while every
        # other bbox was in PDF points, so a consumer drawing them landed at
        # render_scale x the correct position, off the page.
        page = self.result["pages"][0]
        for block in page["blocks"]:
            for line in (block.get("ocr") or {}).get("line_results") or []:
                self.assertLessEqual(line["bbox"][2], page["width"] + 1)
                self.assertLessEqual(line["bbox"][3], page["height"] + 1)


class PageRoutingTests(unittest.TestCase):
    """Routing is decided per page, and no page may fall between the cases."""

    class FakePage:
        def __init__(self, text="", image_fraction=0.0, width=600.0, height=800.0):
            self.rect = fitz.Rect(0, 0, width, height)
            self._text = text
            self._fraction = image_fraction

        def get_text(self, _kind):
            return self._text

        def get_image_info(self):
            if not self._fraction:
                return []
            side = (self.rect.width * self.rect.height * self._fraction) ** 0.5
            return [{"bbox": (0, 0, side, side)}]

    def test_a_page_with_no_text_and_a_partial_image_is_sent_to_ocr(self):
        # Regression: this page matched neither "dominant image" nor "has
        # text", so it was in neither list and came out empty.  Measured at
        # 190 pages across 23 SC documents.
        self.assertTrue(_page_needs_ocr(self.FakePage(text="", image_fraction=0.30)))

    def test_a_page_with_no_text_and_no_image_is_sent_to_ocr(self):
        # Nothing to extract either way; the scanned parser drops it as blank.
        self.assertTrue(_page_needs_ocr(self.FakePage(text="")))

    def test_a_page_sized_image_wins_over_its_hidden_text(self):
        # Deliberate and load-bearing: text sitting on a full-page scan is
        # another scanner's OCR, and trusting it was measured to be worse.
        self.assertTrue(_page_needs_ocr(self.FakePage(text="hidden ocr text", image_fraction=0.95)))

    def test_private_use_text_counts_as_unreadable(self):
        # Legacy InPage encodings render correctly but extract as garbage.
        self.assertTrue(_page_needs_ocr(self.FakePage(text=" x")))

    def test_ordinary_digital_text_stays_native(self):
        self.assertFalse(_page_needs_ocr(self.FakePage(text="IN THE SUPREME COURT OF PAKISTAN")))
        self.assertFalse(_page_needs_ocr(self.FakePage(text="Judgment text", image_fraction=0.10)))


class CoverFieldsFromOcrTableTests(unittest.TestCase):
    """Cover extraction must accept OCR tables, not just digital ones."""

    def ocr_table(self):
        # An OCR table cell has a bbox but no row/column index; a digital one
        # has both.  Reading cell["row"] directly crashed the whole parse on
        # the first SC document that had a two-column scanned cover.
        return {
            "bbox": [0.0, 0.0, 400.0, 100.0],
            "rows": [["For the Petitioner", "Mr. Khadim Hussain Qaiser, ASC"]],
            "cells": [
                {"bbox": [0.0, 0.0, 200.0, 100.0], "text": "For the Petitioner"},
                {"bbox": [200.0, 0.0, 400.0, 100.0], "text": "Mr. Khadim Hussain Qaiser, ASC"},
            ],
        }

    def test_an_ocr_table_yields_its_cover_field(self):
        fields = _cover_fields_from_table(self.ocr_table())
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["label"], "For the Petitioner")

    def test_the_value_falls_back_to_the_table_box(self):
        # Without a row/column index there is no cell box to narrow to, so the
        # table's own box is reported rather than a wrong one.
        self.assertEqual(_cover_fields_from_table(self.ocr_table())[0]["bbox"], [0.0, 0.0, 400.0, 100.0])

    def test_an_indexed_cell_still_narrows(self):
        table = self.ocr_table()
        table["cells"][1].update({"row": 0, "column": 1})
        self.assertEqual(_cover_fields_from_table(table)[0]["bbox"], [200.0, 0.0, 400.0, 100.0])


class NarrowToOcrLinesTests(unittest.TestCase):
    """Provenance must narrow to a line when there are no style spans."""

    def block(self):
        return {
            "text": "Date of hearing 11.02.2020 before the Bench",
            "bbox": [10.0, 10.0, 400.0, 90.0],
            "ocr": {"line_results": [
                {"text": "Date of hearing 11.02.2020", "bbox": [10.0, 10.0, 300.0, 40.0]},
                {"text": "before the Bench", "bbox": [10.0, 50.0, 200.0, 90.0]},
            ]},
        }

    def test_the_box_tightens_to_the_line_holding_the_value(self):
        self.assertEqual(_narrow_to_spans(self.block(), "11.02.2020"), [10.0, 10.0, 300.0, 40.0])

    def test_a_value_absent_from_every_line_keeps_the_block_box(self):
        self.assertEqual(_narrow_to_spans(self.block(), "no such value"), [10.0, 10.0, 400.0, 90.0])

    def test_style_spans_still_win_when_present(self):
        block = self.block()
        block["spans"] = [{"text": "11.02.2020", "bbox": [1.0, 2.0, 3.0, 4.0]}]
        self.assertEqual(_narrow_to_spans(block, "11.02.2020"), [1.0, 2.0, 3.0, 4.0])


def doc(pages):
    return {"document": {"stats": {}, "extraction_mode": "scanned"}, "pages": pages, "markdown": ""}


def scan_page(number, text):
    return {"page_number": number, "markdown": text, "blocks": [{"text": text, "document_reading_order": 0}]}


class MixedRoutingTests(unittest.TestCase):
    """A mixed PDF must not have its readable pages re-read by OCR."""

    def setUp(self):
        self.scanned = doc([scan_page(1, "ocr guess"), scan_page(2, "ocr guess two"), scan_page(3, "real scan")])
        self.digital = doc([scan_page(1, "exact native"), scan_page(2, "exact native two")])

    def test_pages_with_a_text_layer_come_from_the_digital_parse(self):
        merged = _merge_native_pages(self.scanned, self.digital, [1, 2])
        self.assertEqual([p["markdown"] for p in merged["pages"]], ["exact native", "exact native two", "real scan"])

    def test_genuinely_scanned_pages_are_left_to_ocr(self):
        merged = _merge_native_pages(self.scanned, self.digital, [1, 2])
        self.assertEqual(merged["pages"][2]["markdown"], "real scan")

    def test_document_reading_order_is_renumbered_across_the_merge(self):
        # Blocks arrive from two engines, each numbering from zero; without
        # renumbering the document order would restart mid-document.
        merged = _merge_native_pages(self.scanned, self.digital, [1, 2])
        orders = [b["document_reading_order"] for p in merged["pages"] for b in p["blocks"]]
        self.assertEqual(orders, list(range(len(orders))))

    def test_the_merge_is_recorded(self):
        merged = _merge_native_pages(self.scanned, self.digital, [1, 2])
        self.assertEqual(merged["document"]["extraction_mode"], "mixed_native_and_ocr")
        self.assertEqual(merged["document"]["stats"]["native_pages_kept"], 2)

    def test_a_page_missing_from_the_digital_parse_keeps_its_ocr(self):
        # Defensive: the two parses must stay aligned, but a gap must not
        # drop the page.
        merged = _merge_native_pages(self.scanned, doc([]), [1, 2])
        self.assertEqual(len(merged["pages"]), 3)


if __name__ == "__main__":
    unittest.main()
