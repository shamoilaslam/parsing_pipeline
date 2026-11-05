"""The Nemotron Parse output seam: model output string in, typed page blocks out.

The token grammar (`<x_a><y_b>TEXT<x_c><y_d><class_NAME>`) and the resize-and-pad
rule are NVIDIA's, from the postprocessing helpers shipped with
nvidia/NVIDIA-Nemotron-Parse-v1.2.  Fixtures are written to that grammar, not
captured from a model run.
"""

import pytest

from docintel.engines.nemotron_parse import build_page, parse_output
from docintel.schema.specter_v1 import Page

CANVAS = (1664, 2048)  # the model's padded input; an image of this size is not padded
PAGE = (600, 800)  # PDF points


def test_a_region_is_returned_with_its_text_and_a_box_in_pdf_points():
    raw = "<x_0.1><y_0.2>Hello<x_0.5><y_0.3><class_Text>"
    [block] = parse_output(raw, image_size=CANVAS, page_size=PAGE)
    assert block["text"] == "Hello"
    assert block["bbox"] == pytest.approx([60, 160, 300, 240])


def test_a_small_image_is_centred_on_the_canvas_not_scaled_up():
    # 832x1024 is half the canvas each way: padded 416 left/right and 512 top/bottom,
    # so canvas fractions 0.25 and 0.75 are the image's own edges.
    raw = "<x_0.25><y_0.25>Whole page<x_0.75><y_0.75><class_Text>"
    [block] = parse_output(raw, image_size=(832, 1024), page_size=PAGE)
    assert block["bbox"] == pytest.approx([0, 0, 600, 800])


def test_a_tall_image_is_shrunk_to_the_canvas_height_and_padded_sideways():
    # 2000x4096 shrinks to 1000x2048; padding is (1664-1000)//2 = 332 on each side.
    # Canvas x 0.25 -> (416-332)/1000 and 0.75 -> (1248-332)/1000 of the page width.
    raw = "<x_0.25><y_0.0>Tall<x_0.75><y_1.0><class_Text>"
    [block] = parse_output(raw, image_size=(2000, 4096), page_size=(500, 1024))
    assert block["bbox"] == pytest.approx([42, 0, 458, 1024])


def test_a_wide_image_is_capped_by_height_then_again_by_width():
    # 4000x3000 -> height cap gives 2730x2048 -> width cap gives 1664x1248,
    # leaving (2048-1248)//2 = 400 of padding above and below.
    raw = "<x_0.0><y_0.1953125>Wide<x_1.0><y_0.8046875><class_Text>"
    [block] = parse_output(raw, image_size=(4000, 3000), page_size=(400, 300))
    assert block["bbox"] == pytest.approx([0, 0, 400, 300])


def test_regions_keep_the_models_order_and_multiline_text_stays_verbatim():
    raw = (
        "<x_0.1><y_0.1>First line\nsecond line<x_0.9><y_0.2><class_Text>\n"
        "<x_0.1><y_0.3>Second<x_0.9><y_0.4><class_Text>"
    )
    first, second = parse_output(raw, image_size=CANVAS, page_size=PAGE)
    assert first["text"] == "First line\nsecond line"
    assert (first["reading_order"], second["reading_order"]) == (0, 1)


def one_region(class_name):
    raw = f"<x_0.1><y_0.1>x<x_0.2><y_0.2><class_{class_name}>"
    [block] = parse_output(raw, image_size=CANVAS, page_size=PAGE)
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
    arguments = {"page_number": 3, "image_size": CANVAS, "page_size": PAGE}
    arguments.update(overrides)
    return build_page(raw, **arguments)


def test_a_built_page_satisfies_the_specter_v1_page_contract():
    page = build("<x_0.1><y_0.1>Hello<x_0.5><y_0.2><class_Text>")
    Page.model_validate(page)
    assert (page["page_number"], page["width"], page["height"]) == (3, 600, 800)
    [block] = page["blocks"]
    assert block["id"] == "p3_b0"
    assert block["source"] == "nemotron-parse-v1.2"


def test_urdu_text_is_marked_rtl_so_the_urdu_stage_will_pick_it_up():
    [block] = build("<x_0.1><y_0.1>جو جائیداد<x_0.5><y_0.2><class_Text>")["blocks"]
    assert (block["language"], block["contains_rtl"], block["rtl"]["contains_rtl"]) == ("ur", True, True)


def test_page_markdown_joins_the_blocks_in_order():
    raw = "<x_0.1><y_0.1>One<x_0.5><y_0.2><class_Text><x_0.1><y_0.3>Two<x_0.5><y_0.4><class_Text>"
    assert build(raw)["markdown"] == "One\n\nTwo"
