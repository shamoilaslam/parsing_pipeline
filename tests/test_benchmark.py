import unittest

from specter.benchmark import (
    classify,
    error_rates,
    gold_tables,
    gold_text,
    max_columns,
    normalise,
    table_kind,
)


COVER = "[TABLE]\nDate of hearing | 08.09.2016.\nPetitioner by | Mr. Abdul Rashid Awan, Advocate.\n[/TABLE]"
ORDER_SHEET = "[TABLE]\nS.No. of order/ proceeding | Date of order/ Proceeding | Order with signature of Judge\n | 04.12.2013 | Counsel for the petitioner.\n[/TABLE]"


class NormalisationTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        # Gold was captured under an older PyMuPDF that emitted space-only
        # lines; without this the comparison reports differences that do not
        # exist in the source.
        self.assertEqual(normalise("a \n \n  b\tc "), "a b c")

    def test_private_use_glyphs_are_dropped(self):
        self.assertEqual(normalise("bullet  item"), "bullet item")

    def test_synthetic_metadata_line_is_removed_from_gold(self):
        record = {"text": "JUDICIAL DEPARTMENT\nCase No: F.A.O. 352 of 2013\nGulbaz Amin"}
        self.assertEqual(gold_text(record), "JUDICIAL DEPARTMENT Gulbaz Amin")

    def test_table_markers_are_stripped_from_gold_text(self):
        self.assertNotIn("[TABLE]", gold_text({"text": COVER}))


class TableParsingTests(unittest.TestCase):
    def test_pipe_rows_become_cells(self):
        tables = gold_tables({"text": COVER})
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0], ["Date of hearing", "08.09.2016."])
        self.assertEqual(max_columns(tables), 2)

    def test_no_marker_yields_no_tables(self):
        self.assertEqual(gold_tables({"text": "ordinary prose"}), [])

    def test_cover_and_order_sheet_are_distinguished(self):
        # These need different targets: cover cells should be reproduced, while
        # the order-sheet body is prose and must not be scored as table cells.
        self.assertEqual(table_kind(gold_tables({"text": COVER})), "cover")
        self.assertEqual(table_kind(gold_tables({"text": ORDER_SHEET})), "order_sheet")


class ErrorRateTests(unittest.TestCase):
    def test_identical_text_scores_zero(self):
        self.assertEqual(error_rates("abc def", "abc def"), (0.0, 0.0))

    def test_missing_prediction_scores_one(self):
        self.assertEqual(error_rates("abc", ""), (1.0, 1.0))

    def test_both_empty_scores_zero(self):
        self.assertEqual(error_rates("", ""), (0.0, 0.0))


class SliceTests(unittest.TestCase):
    def test_empty_text_layer_is_scanned(self):
        self.assertEqual(classify({"text": "x"}, "a" * 500, ""), "scanned")

    def test_urdu_outranks_table(self):
        gold = "مدعیہ جو جائیداد"
        self.assertEqual(classify({"text": COVER}, gold, gold), "urdu")

    def test_exact_native_match_is_regression(self):
        self.assertEqual(classify({"text": "abc"}, "abc", "abc"), "regression")

    def test_edited_gold_is_text_edit(self):
        self.assertEqual(classify({"text": "abc"}, "abc def", "abc"), "text_edit")


if __name__ == "__main__":
    unittest.main()
