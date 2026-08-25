import tempfile
import unittest
from pathlib import Path

import fitz

from specter.ingest_pdfs import classify_pdf
from specter.specter_parser import PUA_BLOCK_RATIO, SpecterParser, _unreliable_text_reasons


ROOT = Path(__file__).resolve().parents[1]
BULLET = ""
PUA_RUN = ""


def spans(*fonts):
    return [{"font": font} for font in fonts]


class UnreliableTextTests(unittest.TestCase):
    def test_clean_latin_block_is_reliable(self):
        self.assertEqual(_unreliable_text_reasons(spans("TimesNewRomanPSMT"), "An ordinary paragraph."), [])

    def test_mostly_private_use_text_is_flagged(self):
        # InPage/Urdu PDFs re-emit their 8-bit ligature encoding into the
        # private-use area; the characters are unmappable, not merely unusual.
        self.assertEqual(
            _unreliable_text_reasons(spans("CIDFont+F9"), PUA_RUN),
            ["private_use_glyphs"],
        )

    def test_a_symbol_bullet_does_not_condemn_the_block(self):
        # Measured: 6 blocks across 250 documents contain any private-use
        # codepoint, all under 10% -- these are list bullets, not garbage.
        text = f"{BULLET} The petitioner filed an application before the trial court."
        self.assertEqual(_unreliable_text_reasons(spans("Arial"), text), [])

    def test_threshold_sits_between_the_two_populations(self):
        solid = 100
        below = "a" * solid + BULLET * int(solid * (PUA_BLOCK_RATIO - 0.1))
        above = "a" * solid + BULLET * int(solid * (PUA_BLOCK_RATIO + 0.4))
        self.assertEqual(_unreliable_text_reasons(spans("Arial"), below), [])
        self.assertEqual(_unreliable_text_reasons(spans("Arial"), above), ["private_use_glyphs"])

    def test_nastaleeq_fonts_are_flagged(self):
        # These render correct Urdu on screen but extract as transposed
        # ligature clusters, so the text is not citable.
        for font in (
            "Jameel Noori Nastaleeq",
            "ABCDEE+Jameel Noori Nastaleeq",
            "Jameel Noori Nastaleeq,I",
            "Alvi Nastaleeq",
            "AlQalam Nastaliq",
        ):
            self.assertEqual(
                _unreliable_text_reasons(spans(font), "جو جائیداد"),
                ["unreliable_font_encoding"],
                font,
            )

    def test_ordinary_fonts_carrying_urdu_are_not_flagged(self):
        # Measured across 70 RTL documents: every document with clean Urdu
        # used a non-Nastaleeq font.  Flagging these would waste VLM budget
        # re-reading text that is already correct.
        for font in ("Arial", "ArialMT", "Times New Roman", "TimesNewRomanPSMT", "Tahoma"):
            self.assertEqual(
                _unreliable_text_reasons(spans(font), "جو جائیداد"),
                [],
                font,
            )

    def test_one_bad_font_among_several_flags_the_block(self):
        self.assertIn(
            "unreliable_font_encoding",
            _unreliable_text_reasons(spans("Arial", "Jameel Noori Nastaleeq"), "mixed block"),
        )

    def test_both_reasons_can_apply(self):
        self.assertEqual(
            sorted(_unreliable_text_reasons(spans("Alvi Nastaleeq"), PUA_RUN)),
            ["private_use_glyphs", "unreliable_font_encoding"],
        )

    def test_empty_and_missing_inputs_do_not_crash(self):
        self.assertEqual(_unreliable_text_reasons([], ""), [])
        self.assertEqual(_unreliable_text_reasons([{}], "   "), [])


class RouterTests(unittest.TestCase):
    def test_a_page_with_neither_text_nor_image_is_not_digital(self):
        # The dominant-image test is deliberately strict, so a scan whose
        # pages fall just short of it used to be reported as digital and
        # silently produced an empty document instead of going to OCR.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.pdf"
            document = fitz.open()
            document.new_page()
            document.save(str(path))
            document.close()
            self.assertEqual(classify_pdf(path)["mode"], "scanned")

    def test_a_page_with_native_text_is_still_digital(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "IN THE LAHORE HIGH COURT")
            document.save(str(path))
            document.close()
            self.assertEqual(classify_pdf(path)["mode"], "digital")

    def test_known_digital_judgments_are_unaffected(self):
        for name in ("2024LHC6559.pdf", "2023LHC5924.pdf", "2025LHC5084.pdf"):
            self.assertEqual(classify_pdf(ROOT / "data" / "pdfs" / name)["mode"], "digital", name)


class RealDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2018LHC1986 (1).pdf")
        cls.blocks = [b for page in cls.result["pages"] for b in page["blocks"]]

    def test_broken_urdu_blocks_are_flagged(self):
        flagged = [b for b in self.blocks if b.get("text_status")]
        self.assertTrue(flagged)
        self.assertEqual(flagged[0]["text_status"]["status"], "unreliable_native")

    def test_flagging_discriminates_within_a_single_document(self):
        # 2025LHC495 quotes hadith in English, so nine of its ten
        # Arabic-script blocks are Times New Roman prose whose only RTL
        # character is the honorific U+FDFA -- which extracts perfectly.
        # Exactly one block is Nastaleeq Urdu (3 correct function words
        # against 31 reversed ones) and only that one may be condemned;
        # a blanket "contains RTL means unreadable" rule would quarantine
        # nine pages of sound English along with it.
        result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2025LHC495.pdf")
        rtl = [b for page in result["pages"] for b in page["blocks"] if b.get("contains_rtl")]
        flagged = [b for b in rtl if b.get("text_status")]
        self.assertEqual(len(flagged), 1)
        self.assertGreater(len(rtl), 5)
        self.assertEqual(flagged[0]["text_status"]["reasons"], ["unreliable_font_encoding"])

    def test_flagged_blocks_keep_their_text_and_geometry(self):
        # Quarantine marks; it never deletes.  The characters stay so the
        # block can be re-read later, and the bbox stays so a crop can be
        # rendered for OCR/VLM.
        for block in self.blocks:
            if block.get("text_status"):
                self.assertTrue(block["text"].strip())
                self.assertEqual(len(block["bbox"]), 4)

    def test_document_reports_the_count_and_warns(self):
        stats = self.result["document"]["stats"]
        self.assertGreater(stats["unreliable_text_blocks"], 0)
        self.assertTrue(any("unreliable native text" in w for w in self.result["document"]["warnings"]))

    def test_reliable_blocks_carry_no_status_field(self):
        # Only the exceptions are annotated, so the field stays a signal
        # rather than becoming noise on every block in the corpus.
        self.assertTrue([b for b in self.blocks if not b.get("text_status")])


if __name__ == "__main__":
    unittest.main()
