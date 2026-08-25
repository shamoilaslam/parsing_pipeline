import unittest

import fitz

from specter.scanned_parser import _detect_blue_slip, _detect_table_grids, _preprocess, _render_page
from specter.ingest_pdfs import classify_pdf


class ScannedPipelineTests(unittest.TestCase):
    def test_blue_slip_title_is_page_one_only(self):
        self.assertTrue(_detect_blue_slip("BLUESLIP", 1))
        self.assertTrue(_detect_blue_slip("BLUE SLIP", 1))
        self.assertFalse(_detect_blue_slip("BLUE SLIP", 2))

    def test_ruled_table_geometry_is_detected_without_form_false_positive(self):
        doc = fitz.open("data/pdfs/2014LHC4829.pdf")
        try:
            form, _, _ = _preprocess(_render_page(doc[0], 1.5))
            judgment, gray, _ = _preprocess(_render_page(doc[1], 1.5))
            self.assertEqual(_detect_table_grids(cv2_gray(form)), [])
            self.assertEqual(len(_detect_table_grids(gray)), 1)
        finally:
            doc.close()

    def test_ingest_router_uses_image_coverage_not_hidden_text(self):
        self.assertEqual(classify_pdf("data/pdfs/2013LHC4394.pdf")["mode"], "scanned")
        self.assertEqual(classify_pdf("data/pdfs/2014LHC4829.pdf")["mode"], "scanned")
        self.assertEqual(classify_pdf("data/pdfs/2024LHC6559.pdf")["mode"], "digital")


def cv2_gray(image):
    import cv2

    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


if __name__ == "__main__":
    unittest.main()
