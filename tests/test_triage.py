"""The triage seam: a PDF page in, a PageProfile of cheap facts out."""

import fitz
import pytest

from docintel.triage import profile_page, sensitivity
from tests.pua_font import build_pua_font


def make_page(draw=lambda page: None):
    """A one-page PDF; ``draw`` puts whatever the test studies on the page."""
    doc = fitz.open()
    page = doc.new_page()
    draw(page)
    return doc, page


def write_text(text):
    return lambda page: page.insert_text((72, 100), text, fontsize=11)


def test_a_digital_page_reports_its_text_and_nothing_alarming():
    doc, page = make_page(write_text("The petitioner prays that the order be set aside."))
    profile = profile_page(page)
    assert profile.text_chars == len("The petitioner prays that the order be set aside.")
    assert not profile.dominant_image
    assert profile.pua_ratio == 0.0


def test_a_page_covered_by_an_image_is_flagged_even_with_a_text_layer():
    def draw(page):
        pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 60, 80), False)
        pixmap.clear_with(200)
        page.insert_image(page.rect, pixmap=pixmap)
        write_text("An earlier scanner's OCR text")(page)

    doc, page = make_page(draw)
    assert profile_page(page).dominant_image


def test_private_use_glyphs_are_reported_as_a_share_of_the_non_space_text(tmp_path):
    text = " ab"
    font = build_pua_font(tmp_path / "pua.ttf", [ord(char) for char in text])

    def draw(page):
        page.insert_font("pua", str(font))
        page.insert_text((72, 100), text, fontname="pua", fontsize=11)

    doc, page = make_page(draw)
    assert profile_page(page).pua_ratio == pytest.approx(4 / 6)


def test_a_cnic_number_marks_the_text_sensitive():
    assert sensitivity("Complainant CNIC No. 35202-1234567-1, resident of Lahore") == ("cnic",)


def test_ordinary_case_text_is_not_sensitive():
    assert sensitivity("W.P. No. 12345/2023 was decided on 12-03-2024 by the Division Bench.") == ()


def test_a_long_reference_number_is_not_mistaken_for_a_cnic():
    assert sensitivity("Diary reference 1234567890123456789") == ()


@pytest.mark.parametrize("number", ["0300-1234567", "03001234567", "+92 300 1234567", "+923001234567"])
def test_a_mobile_number_marks_the_text_sensitive(number):
    assert sensitivity(f"Contact the complainant on {number}.") == ("phone",)


@pytest.mark.parametrize(
    "text",
    ["Medico-Legal Certificate No. 44", "Post-mortem report of the deceased", "Autopsy findings and forensic analysis"],
)
def test_medical_and_forensic_papers_are_sensitive(text):
    assert sensitivity(text) == ("medical",)


def test_victim_wording_is_sensitive():
    assert sensitivity("The victim, a minor, was seen by a counsellor.") == ("victim",)


def test_a_page_whose_text_layer_carries_a_cnic_is_profiled_sensitive():
    doc, page = make_page(write_text("Complainant CNIC 35202-1234567-1"))
    assert profile_page(page).sensitive


def test_a_clean_digital_page_is_not_sensitive():
    doc, page = make_page(write_text("The petition is dismissed."))
    assert not profile_page(page).sensitive


@pytest.mark.parametrize(
    ("text", "flag"),
    [
        ("میڈیکو لیگل رپورٹ", "medical"),
        ("پوسٹ مارٹم کی رپورٹ", "medical"),
        ("فرانزک تجزیہ", "medical"),
        ("متاثرہ لڑکی کا بیان", "victim"),
    ],
)
def test_urdu_medical_and_victim_wording_is_sensitive(text, flag):
    assert sensitivity(text) == (flag,)
