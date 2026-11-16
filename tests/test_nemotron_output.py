"""The Nemotron 3.5 Parse output seam: model output string in, typed page blocks out.

The grammar is `<x_a><y_b>TEXT<x_c><y_d><class_NAME>`: the region's top-left corner,
its text, its bottom-right corner and its class, with coordinates as fractions of
the page image.  The real-output test uses a page the model read on CPU
(tests/fixtures/nemotron35_page1.txt); the rest are written to the same grammar.
"""

from pathlib import Path

import pytest

from docintel.engines.nemotron_parse import build_page, parse_output
from docintel.schema.specter_v1 import Page

PAGE = (600, 800)  # PDF points
FIXTURE = Path(__file__).parent / "fixtures" / "nemotron35_page1.txt"


def test_a_region_is_returned_with_its_text_and_a_box_in_pdf_points():
    raw = "<x_0.1><y_0.2>Hello<x_0.5><y_0.3><class_Text>"
    [block] = parse_output(raw, page_size=PAGE)
    assert block["text"] == "Hello"
    assert block["bbox"] == pytest.approx([60, 160, 300, 240])


def test_regions_keep_the_models_order_and_multiline_text_stays_verbatim():
    raw = (
        "<x_0.1><y_0.1>First line\nsecond line<x_0.9><y_0.2><class_Text>\n"
        "<x_0.1><y_0.3>Second<x_0.9><y_0.4><class_Text>"
    )
    first, second = parse_output(raw, page_size=PAGE)
    assert first["text"] == "First line\nsecond line"
    assert (first["reading_order"], second["reading_order"]) == (0, 1)


def test_a_page_the_model_read_gives_boxes_where_the_pdfs_own_text_is():
    # 2024LHC1690.pdf page 1 is 612x1008 pt; PyMuPDF puts "Form No: HCJD/C-121" at (162.0, 99.6, 299.7, 115.1).
    # The model's output stops mid-table (token limit), so only the four complete regions come back.
    blocks = parse_output(FIXTURE.read_text(encoding="utf-8"), page_size=(612, 1008))
    assert [block["layout_class"] for block in blocks] == ["Page-header", "Title", "Text", "Text"]
    assert blocks[0]["bbox"] == pytest.approx([162.0, 99.6, 299.7, 115.1], abs=4)
    assert "Civil Revision No.21823 of 2024" in blocks[2]["text"]


def one_region(class_name):
    raw = f"<x_0.1><y_0.1>x<x_0.2><y_0.2><class_{class_name}>"
    [block] = parse_output(raw, page_size=PAGE)
    return block


@pytest.mark.parametrize(
    ("layout_class", "block_type"),
    [
        ("Text", "text"),
        ("Title", "heading"),
        ("Section-header", "heading"),
        ("List-item", "list"),
        ("Table", "table"),
        ("Page-header", "header"),
        ("Page-footer", "footer"),
        ("Footnote", "footnote"),
    ],
)
def test_each_known_class_maps_to_a_specter_block_type(layout_class, block_type):
    assert one_region(layout_class)["type"] == block_type


def test_a_class_with_no_specter_type_is_kept_and_read_as_text():
    block = one_region("Picture")
    assert (block["type"], block["layout_class"]) == ("text", "Picture")


def build(raw, **overrides):
    arguments = {"page_number": 3, "page_size": PAGE}
    arguments.update(overrides)
    return build_page(raw, **arguments)


def test_a_built_page_satisfies_the_specter_v1_page_contract():
    page = build("<x_0.1><y_0.1>Hello<x_0.5><y_0.2><class_Text>")
    Page.model_validate(page)
    assert (page["page_number"], page["width"], page["height"]) == (3, 600, 800)
    [block] = page["blocks"]
    assert block["id"] == "p3_b0"
    assert block["source"] == "nemotron-3.5-parse"


def test_urdu_text_is_marked_rtl_so_the_urdu_stage_will_pick_it_up():
    [block] = build("<x_0.1><y_0.1>جو جائیداد<x_0.5><y_0.2><class_Text>")["blocks"]
    assert (block["language"], block["contains_rtl"], block["rtl"]["contains_rtl"]) == ("ur", True, True)


def test_page_markdown_joins_the_blocks_in_order():
    raw = "<x_0.1><y_0.1>One<x_0.5><y_0.2><class_Text><x_0.1><y_0.3>Two<x_0.5><y_0.4><class_Text>"
    assert build(raw)["markdown"] == "One\n\nTwo"


def test_an_urdu_region_is_kept_but_flagged_because_the_model_cannot_read_urdu():
    page = build_page("<x_0.1><y_0.1>یہ ایک جملہ ہے<x_0.9><y_0.2><class_Text>", page_number=1, page_size=(600, 800))
    [block] = page["blocks"]
    assert block["contains_rtl"] and block["needs_review"]
    assert "urdu_unreliable_for_engine" in block["confidence"]["reasons"]


def test_an_english_region_is_not_flagged():
    [block] = build_page("<x_0.1><y_0.1>Dear Sir<x_0.9><y_0.2><class_Text>", page_number=1, page_size=(600, 800))["blocks"]
    assert not block.get("needs_review")


def test_regions_carry_a_computed_quality_and_the_page_their_character_weighted_mean():
    raw = (
        "<x_0.1><y_0.1>The petition is dismissed for the reasons recorded above in this order.<x_0.9><y_0.2><class_Text>"
        "<x_0.1><y_0.3>the the the the the the<x_0.9><y_0.4><class_Text>"
    )
    page = build_page(raw, page_number=1, page_size=(600, 800))
    good, looping = page["blocks"]
    assert good["confidence"]["score"] > 0.8 and "engine_reports_no_confidence" in good["confidence"]["reasons"]
    assert looping["confidence"]["score"] < 0.6 and "repeated_tokens" in looping["confidence"]["reasons"]
    assert looping["confidence"]["score"] < page["confidence"] < good["confidence"]["score"]


def test_an_urdu_region_never_scores_above_the_floor_for_unreadable_text():
    [block] = build_page("<x_0.1><y_0.1>یہ ایک جملہ ہے<x_0.9><y_0.2><class_Text>", page_number=1, page_size=(600, 800))["blocks"]
    assert block["confidence"]["score"] <= 0.2
