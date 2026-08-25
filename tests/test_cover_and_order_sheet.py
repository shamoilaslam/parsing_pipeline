import unittest
from pathlib import Path

from specter.specter_parser import (
    SpecterParser,
    _coalesce_lines,
    _cover_fields_from_blocks,
    _cover_fields_from_table,
    _is_cover_label,
    _is_order_sheet_header,
    _reading_order,
)


ROOT = Path(__file__).resolve().parents[1]


def text_block(text, bbox, kind="text"):
    return {"type": kind, "text": text, "bbox": bbox}


def raw_line(text, x0, y0, x1, y1):
    step = (x1 - x0) / max(1, len(text))
    chars = [{"c": c, "bbox": [x0 + i * step, y0, x0 + (i + 1) * step, y1]} for i, c in enumerate(text)]
    return {"bbox": [x0, y0, x1, y1], "spans": [{"bbox": [x0, y0, x1, y1], "chars": chars}]}


def body_block(x0, y0, x1, y1):
    return {"type": "text", "bbox": [x0, y0, x1, y1]}


class CoverLabelTests(unittest.TestCase):
    def test_matches_observed_label_shapes(self):
        for label in (
            "Date of hearing:",
            "Dates of hearing:",
            "Date of Hearing.",
            "Petitioner by:",
            "Petitioners by:",
            "Respondents by:",
            "Appellant by:",
            "For the Appellant:",
            "For the State:",
            "For the Complainant:",
            "Petitioners in Crl.Rev.No.9027/2017 by:",
        ):
            self.assertTrue(_is_cover_label(label), label)

    def test_does_not_match_ordinary_prose(self):
        # These would false-positive on a naive "starts with a role word"
        # match; the label must be a short, terminated prefix, not just
        # contain a role word anywhere in a longer sentence.
        for sentence in (
            "State of facts is that the petitioner filed an application.",
            "Respondent No.2 filed an application before the trial court.",
            "The learned counsel for the petitioner argued the case.",
            "award should be made in a contracting state which would",
        ):
            self.assertFalse(_is_cover_label(sentence), sentence)


class CoverFieldsFromTableTests(unittest.TestCase):
    def test_two_column_rows_become_fields(self):
        table = {
            "bbox": [0, 0, 100, 100],
            "rows": [["Date of hearing:", "24.10.2024"], ["For the State:", "Mr. X, Advocate."]],
            "cells": [
                {"row": 0, "column": 1, "bbox": [10, 0, 20, 5], "text": "24.10.2024"},
                {"row": 1, "column": 1, "bbox": [10, 5, 20, 10], "text": "Mr. X, Advocate."},
            ],
        }
        fields = _cover_fields_from_table(table)
        self.assertEqual([f["label"] for f in fields], ["Date of hearing", "For the State"])
        self.assertEqual(fields[0]["value"], "24.10.2024")
        self.assertEqual(fields[0]["bbox"], [10, 0, 20, 5])

    def test_non_two_column_table_is_ignored(self):
        # A schedule or the ORDER SHEET header (3 columns) is not a cover
        # grid; only a table shaped like a label/value form qualifies.
        table = {"rows": [["S.No.", "Date", "Order text"]], "cells": []}
        self.assertEqual(_cover_fields_from_table(table), [])

    def test_empty_label_cell_is_skipped(self):
        table = {"rows": [["", "orphaned value"]], "cells": []}
        self.assertEqual(_cover_fields_from_table(table), [])


class CoverFieldsFromBlocksTests(unittest.TestCase):
    def test_inline_label_and_value(self):
        blocks = [text_block("Date of hearing: 15.11.2023.", [158, 354, 333, 370])]
        fields = _cover_fields_from_blocks(blocks)
        self.assertEqual(fields, [{"label": "Date of hearing", "value": "15.11.2023.", "bbox": [158, 354, 333, 370], "source": "cover_text"}])

    def test_label_alone_absorbs_the_next_block_as_value(self):
        blocks = [
            text_block("Petitioner by:", [158, 379, 237, 394]),
            text_block("M/s. Dr. Malik Muhammad Hafeez, Advocate.", [266, 379, 543, 394]),
        ]
        fields = _cover_fields_from_blocks(blocks)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["label"], "Petitioner by")
        self.assertEqual(fields[0]["value"], "M/s. Dr. Malik Muhammad Hafeez, Advocate.")

    def test_value_wraps_across_several_blocks_until_next_label(self):
        blocks = [
            text_block("Respondents by:", [158, 427, 255, 443]),
            text_block("Mr. Mahmood Ahmad Bhatti, Advocate.", [266, 427, 498, 443]),
            text_block("Miss Riffat Yasmeen, Assistant Attorney General.", [266, 443, 543, 459]),
            text_block("For the Complainant:", [158, 460, 260, 476]),
            text_block("Mr. Azhar Iqbal, Advocate.", [266, 460, 450, 476]),
        ]
        fields = _cover_fields_from_blocks(blocks)
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["label"], "Respondents by")
        self.assertEqual(fields[0]["value"], "Mr. Mahmood Ahmad Bhatti, Advocate. Miss Riffat Yasmeen, Assistant Attorney General.")
        self.assertEqual(fields[1]["label"], "For the Complainant")

    def test_value_absorption_stops_at_the_judgment_opening_line(self):
        blocks = [
            text_block("Petitioner by:", [158, 379, 237, 394]),
            text_block("M/s. Dr. Malik Muhammad Hafeez, Advocate.", [266, 379, 543, 394]),
            text_block("MUHAMMAD SAJID MEHMOOD SETHI, J.- Through instant petition ...", [194, 556, 544, 571]),
            text_block("the petitioner has challenged the correspondence.", [158, 580, 548, 595]),
        ]
        fields = _cover_fields_from_blocks(blocks)
        self.assertEqual(len(fields), 1)
        self.assertNotIn("SETHI", fields[0]["value"])

    def test_non_text_block_stops_absorption(self):
        blocks = [
            text_block("Petitioner by:", [158, 379, 237, 394]),
            text_block("1. This is actually a numbered paragraph.", [266, 379, 543, 394], kind="list"),
        ]
        fields = _cover_fields_from_blocks(blocks)
        self.assertEqual(fields, [])

    def test_no_label_yields_no_fields(self):
        blocks = [text_block("This is an ordinary paragraph of judgment text.", [158, 400, 500, 415])]
        self.assertEqual(_cover_fields_from_blocks(blocks), [])


class OrderSheetHeaderTests(unittest.TestCase):
    def test_single_short_row_with_signature_text_is_a_header(self):
        table = {"bbox": [0, 0, 500, 50], "rows": [["S.No. of order/proceeding", "Date of order/proceeding", "Order with the signature of the Judge"]]}
        self.assertTrue(_is_order_sheet_header(table))

    def test_tall_box_is_not_a_header(self):
        # A cover table can coincidentally be short-and-wide too; height is
        # what separates the two-row-max order sheet header from a real grid.
        table = {"bbox": [0, 0, 500, 80], "rows": [["S.No. of order/proceeding", "Date", "Order with the signature of the Judge"]]}
        self.assertFalse(_is_order_sheet_header(table))

    def test_multi_row_table_is_not_a_header(self):
        table = {"bbox": [0, 0, 500, 50], "rows": [["a", "b", "c"], ["d", "e", "f"]]}
        self.assertFalse(_is_order_sheet_header(table))

    def test_bordered_cover_table_is_not_a_header(self):
        table = {"bbox": [0, 0, 500, 50], "rows": [["Date of hearing:", "24.10.2024"]]}
        self.assertFalse(_is_order_sheet_header(table))


class RealDocumentRegressionTests(unittest.TestCase):
    """Fixed real documents pinned as regression tests, per E5/E6 findings."""

    def test_order_sheet_header_is_stripped_and_date_extracted(self):
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2025LHC5084.pdf")
        page1 = result["pages"][0]
        headers = [b for b in page1["blocks"] if b["type"] == "form_header"]
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0]["order_date"]["value"], "28.04.2025")
        self.assertNotIn("S.No. of order", result["pages"][0]["markdown"])
        # The order-sheet body text must still be readable as ordinary prose.
        self.assertIn("Qazi Zafar Ullah Khan", result["pages"][0]["markdown"])

    def test_order_sheet_header_is_stripped_on_every_page_it_recurs(self):
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2013LHC3273.pdf")
        self.assertEqual(result["document"]["stats"]["form_headers_stripped"], 2)
        dates = {
            page["page_number"]: block["order_date"]["value"]
            for page in result["pages"]
            for block in page["blocks"]
            if block["type"] == "form_header"
        }
        self.assertEqual(dates, {1: "02.12.2013", 6: "28.01.2013"})

    def test_borderless_cover_fields_are_recovered(self):
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2023LHC5924.pdf")
        fields = {f["label"]: f["value"] for f in result["document"]["cover_fields"]}
        self.assertEqual(fields["Date of hearing"], "15.11.2023.")
        self.assertIn("M/s. Dr. Malik Muhammad Hafeez", fields["Petitioner by"])
        self.assertIn("Malik Khaleel Ahmad Mamra, Advocates.", fields["Petitioner by"])
        self.assertIn("Miss Riffat Yasmeen", fields["Respondents by"])

    def test_bordered_cover_table_still_yields_fields(self):
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2024LHC6559.pdf")
        fields = {f["label"]: f["value"] for f in result["document"]["cover_fields"]}
        self.assertEqual(fields["Date of hearing"], "24.10.2024")
        self.assertIn("Malik Muhammad Usman Bhatti", fields["For the Appellant"])

    def test_case_caption_line_keeps_left_to_right_order(self):
        # Regression: separating a case-caption's widely spaced fragments
        # (E5/E6's own fix) exposed a sub-point baseline-jitter weakness in
        # the plain y-then-x sort, which put "The State etc." before
        # "Versus" because its y0 was 0.2pt lower than its neighbours'.
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2021LHC9943.pdf")
        texts = [b["text"] for b in result["pages"][0]["blocks"]]
        self.assertLess(texts.index("Bilal Moeen Butt alias Bilal"), texts.index("Versus"))
        self.assertLess(texts.index("Versus"), texts.index("The State etc."))


class CoalesceLinesTests(unittest.TestCase):
    def test_small_gap_same_row_fragments_are_joined(self):
        # A font/style change mid-sentence: two rawdict lines, same baseline,
        # touching edges -- must stay one fragment.
        lines = [raw_line("Petitioner ", 100, 200, 150, 214), raw_line("by:", 150, 200, 168, 214)]
        result = _coalesce_lines(lines)
        self.assertEqual(len(result), 1)

    def test_wide_gap_same_row_fragments_stay_separate(self):
        # A label and a value in different columns of a form: same baseline,
        # tens of points apart.  Joining these was the root cause behind
        # E5/E6's Template C failure -- the fused bbox spanned the blank
        # gutter and the two could never be told apart again downstream.
        lines = [raw_line("Petitioner by:", 158, 379, 238, 394), raw_line("M/s. Dr. Malik", 266, 379, 360, 394)]
        result = _coalesce_lines(lines)
        self.assertEqual(len(result), 2)


class ReadingOrderColumnGuardTests(unittest.TestCase):
    PAGE_WIDTH = 612.0
    PAGE_HEIGHT = 1000.0

    def test_short_signature_cluster_reads_top_to_bottom_not_column_by_column(self):
        # A 3-item left column and a 3-item right column confined to the last
        # 8% of the page (2025LHC290.pdf p3's exact shape) must not be read
        # as two independent columns; a human reads it top-to-bottom by y.
        blocks = [
            body_block(157, 918, 210, 933),  # left col, bottom
            body_block(193, 900, 231, 918),  # left col, middle
            body_block(229, 837, 359, 851),  # left col, top
            body_block(418, 791, 537, 806),  # right col, top
            body_block(445, 807, 491, 821),  # right col, middle
            body_block(445, 867, 491, 881),  # right col, bottom
        ]
        for block, order in zip(blocks, range(6)):
            block["order"] = order
        ordered = _reading_order(blocks, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        self.assertEqual([b["order"] for b in ordered], sorted(range(6), key=lambda i: blocks[i]["bbox"][1]))

    def test_genuine_full_page_two_column_body_is_read_column_by_column(self):
        # Two columns each spanning nearly the full page height, several
        # rows apiece: a real newspaper-style layout, which the fallback
        # exists to handle.
        left = [body_block(60, 80 + i * 100, 280, 90 + i * 100) for i in range(6)]
        right = [body_block(340, 80 + i * 100, 560, 90 + i * 100) for i in range(6)]
        blocks = left + right
        ordered = _reading_order(blocks, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        # Every left-column block must precede every right-column block.
        split = ordered.index(right[0])
        self.assertTrue(all(b in left for b in ordered[:split]))
        self.assertTrue(all(b in right for b in ordered[split:]))

    def test_same_row_baseline_jitter_does_not_reorder_left_to_right(self):
        # A case caption split into 3 fragments with a sub-point y0 offset
        # between them (E5/E6): must not let jitter outrank x0 order.
        blocks = [
            body_block(86.1, 216.0, 246.6, 231.5),
            body_block(404.6, 216.0, 484.7, 231.5),
            body_block(283.6, 216.2, 328.4, 231.8),  # 0.2pt lower baseline
        ]
        ordered = _reading_order(blocks, self.PAGE_WIDTH, self.PAGE_HEIGHT)
        self.assertEqual([b["bbox"][0] for b in ordered], [86.1, 283.6, 404.6])


if __name__ == "__main__":
    unittest.main()
