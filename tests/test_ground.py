"""Grounding: a model's claim survives only if its quote is in the parsed page, and then it carries the page box."""

from docintel.agent.ground import ground

PAGES = [
    {"page_number": 1, "blocks": [
        {"id": "p1_b0", "text": "PROPOSAL FOR NOTIFIED ORDERS", "bbox": [10, 10, 200, 30]},
        {"id": "p1_b1", "text": "16 September 1993\nTo: The Managing Director", "bbox": [10, 40, 200, 80]},
    ]},
    {"page_number": 2, "blocks": [{"id": "p2_b0", "text": "Yours faithfully, Director", "bbox": [10, 500, 200, 520]}]},
]


def test_a_quote_found_in_a_block_returns_its_page_block_and_box():
    found = ground("16 September 1993", PAGES)
    assert (found.page, found.block_id, found.bbox) == (1, "p1_b1", [10, 40, 200, 80])


def test_case_spacing_and_quote_marks_do_not_matter():
    assert ground("proposal  for notified   ORDERS", PAGES).block_id == "p1_b0"


def test_a_quote_that_is_not_on_any_page_is_rejected():
    assert ground("Dear Sir, we are pleased to enclose", PAGES) is None


def test_a_one_letter_ocr_slip_still_grounds_but_a_different_sentence_does_not():
    assert ground("Yours faithfuly, Director", PAGES).page == 2
    assert ground("Yours sincerely, Chairman", PAGES) is None


def test_a_quote_too_short_to_mean_anything_is_rejected():
    assert ground("of", PAGES) is None
    assert ground("", PAGES) is None


def test_a_quote_may_be_limited_to_the_pages_it_is_expected_on():
    assert ground("Yours faithfully", PAGES, pages_allowed=[1]) is None
