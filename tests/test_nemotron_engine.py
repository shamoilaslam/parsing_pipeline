"""NemotronEngine: the model's page outputs behind the Engine protocol."""

import fitz
import pytest

from docintel.engines.base import Engine
from docintel.engines.nemotron_batch import append_raw
from docintel.engines.nemotron_engine import NemotronEngine, PagesPending, log_transport
from docintel.schema.specter_v1 import SpecterV1

CAPTION = (
    "IN THE LAHORE HIGH COURT, LAHORE\nJudicial Department\nW.P. No. 12345 of 2023\n"
    "Ali versus Province of Punjab\nDate of hearing: 12-03-2024\nJUDGMENT"
)
# A region covering the upper half of the page; the box is not what these tests study.
RAW = f"<x_0.2><y_0.1>{CAPTION}<x_0.8><y_0.4><class_Text>"


def make_pdf(path, pages=2):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    return path


def serving(raw=RAW):
    """A transport that has model output for every page it is asked about."""
    return lambda pdf, jobs: {job["page_number"]: raw for job in jobs}


def test_the_engine_returns_a_valid_specter_result_and_declares_its_policy(tmp_path):
    engine = NemotronEngine(serving())
    assert isinstance(engine, Engine)
    assert (engine.name, engine.policy) == ("nemotron-parse", "self_hosted")
    result = engine.parse(make_pdf(tmp_path / "scan.pdf"), tmp_path / "out")
    SpecterV1.model_validate(result)
    assert result["document"]["page_count"] == 2
    assert result["document"]["engine"] == "nemotron-3.5-parse"


def test_pages_the_transport_has_no_output_for_are_reported_not_emitted_empty(tmp_path):
    engine = NemotronEngine(lambda pdf, jobs: {1: RAW})
    with pytest.raises(PagesPending) as raised:
        engine.parse(make_pdf(tmp_path / "scan.pdf"), None)
    assert raised.value.pages == [2]


def test_case_metadata_is_read_from_the_models_text(tmp_path):
    result = NemotronEngine(serving()).parse(make_pdf(tmp_path / "scan.pdf"), None)
    metadata = result["document"]["metadata"]
    assert metadata["case_number"] == "W.P. No. 12345 of 2023"
    assert metadata["court_id"] == "lahore_high_court"
    assert metadata["decision_date_iso"] == "2024-03-12"


def test_each_metadata_field_points_back_at_the_region_it_was_read_from(tmp_path):
    result = NemotronEngine(serving()).parse(make_pdf(tmp_path / "scan.pdf"), None)
    provenance = result["document"]["metadata_provenance"]["case_number"]
    [block] = result["pages"][0]["blocks"]
    assert provenance["page"] == 1
    assert provenance["bbox"] == block["bbox"]


def test_a_log_copied_back_from_a_gpu_session_serves_the_engine(tmp_path):
    (tmp_path / "work").mkdir()
    for page in (1, 2):
        append_raw(tmp_path / "work" / "raw.jsonl", page, RAW)
    engine = NemotronEngine(log_transport(tmp_path / "work"))
    result = engine.parse(make_pdf(tmp_path / "scan.pdf"), None)
    assert result["document"]["metadata"]["case_number"] == "W.P. No. 12345 of 2023"


def test_only_the_requested_pages_are_read_and_the_rest_are_left_for_the_native_parse(tmp_path):
    asked = []

    def transport(pdf, jobs):
        asked.extend(job["page_number"] for job in jobs)
        return {job["page_number"]: RAW for job in jobs}

    result = NemotronEngine(transport).parse(make_pdf(tmp_path / "mixed.pdf"), None, pages=[2])
    SpecterV1.model_validate(result)
    assert asked == [2]
    first, second = result["pages"]
    assert (first["extraction"], first["blocks"]) == ("native_text_pending", [])
    assert second["extraction"] == "scanned" and second["blocks"]
    assert result["document"]["stats"]["blocks"] == len(second["blocks"])


def test_a_live_transport_reads_each_page_once_and_resumes_from_its_log(tmp_path):
    from docintel.engines.nemotron_engine import live_transport

    reads = []

    def read_page(image_path):
        reads.append(image_path.name)
        return RAW

    pdf = make_pdf(tmp_path / "scan.pdf")
    engine = NemotronEngine(live_transport(tmp_path / "work", read_page))
    engine.parse(pdf, tmp_path / "out")
    assert len(reads) == 2
    NemotronEngine(live_transport(tmp_path / "work", read_page)).parse(pdf, tmp_path / "out")
    assert len(reads) == 2  # the second run is served from the log


def test_documents_sharing_a_page_number_do_not_share_model_output(tmp_path):
    from docintel.engines.nemotron_engine import live_transport

    transport = live_transport(tmp_path / "work", lambda image_path: f"<x_0.1><y_0.1>{image_path.stem}<x_0.9><y_0.2><class_Text>")
    first = NemotronEngine(transport).parse(make_pdf(tmp_path / "a.pdf", 1), tmp_path / "out")
    second = NemotronEngine(transport).parse(make_pdf(tmp_path / "b.pdf", 1), tmp_path / "out")
    assert first["pages"][0]["blocks"][0]["text"] == "a_p1"
    assert second["pages"][0]["blocks"][0]["text"] == "b_p1"


STAMP_RAW = "<x_0.05><y_0.2>RECEIVED<x_0.9><y_0.5><class_Text><x_0.05><y_0.55>06 OCT 1993<x_0.9><y_0.9><class_Text>"


def stamped_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    # A stamp: an outline with lettering in red ink (a solid red block would be glare, not a stamp).
    page.draw_rect(fitz.Rect(330, 40, 570, 120), color=(0.8, 0.1, 0.1), width=3)
    page.insert_text((345, 80), "RECEIVED", fontsize=24, color=(0.8, 0.1, 0.1))
    page.insert_text((345, 108), "06 OCT 1993", fontsize=18, color=(0.8, 0.1, 0.1))
    doc.save(path)
    return path


def test_a_red_stamp_is_read_on_its_own_and_kept_apart_from_the_body(tmp_path):
    seen = []

    def read_stamp(image_path):
        seen.append(image_path.name)
        return STAMP_RAW

    result = NemotronEngine(serving(), mark_reader=read_stamp).parse(stamped_pdf(tmp_path / "s.pdf"), tmp_path / "out")
    blocks = result["pages"][0]["blocks"]
    [stamp] = [block for block in blocks if block["type"] == "stamp"]
    assert stamp["text"] == "RECEIVED 06 OCT 1993"
    x0, y0, x1, y1 = stamp["bbox"]
    assert x0 <= 330 and y0 <= 40 and x1 >= 570 and y1 >= 120 and x1 - x0 < 300  # page points, around the stamp
    assert any(block["type"] != "stamp" for block in blocks) and len(seen) == 1
    assert "RECEIVED" not in result["pages"][0]["markdown"]


def test_without_a_mark_reader_no_stamps_are_added(tmp_path):
    result = NemotronEngine(serving()).parse(stamped_pdf(tmp_path / "s.pdf"), tmp_path / "out")
    assert all(block["type"] != "stamp" for block in result["pages"][0]["blocks"])


def noted_pdf(path):
    """A page with a blue pen note drawn as strokes, as handwriting is."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for row in range(3):
        y = 400 + row * 6
        page.draw_polyline([(100, y), (160, y - 14), (220, y + 4), (280, y - 14), (340, y + 4), (400, y - 10)],
                           color=(0.1, 0.25, 0.8), width=2)
    doc.save(path)
    return path


def test_pen_handwriting_is_read_on_its_own_as_a_handwriting_block_with_low_confidence(tmp_path):
    result = NemotronEngine(serving(), mark_reader=lambda image_path: "<x_0.1><y_0.1>check with Khalid<x_0.9><y_0.9><class_Text>").parse(
        noted_pdf(tmp_path / "n.pdf"), tmp_path / "out"
    )
    [note] = [block for block in result["pages"][0]["blocks"] if block["type"] == "handwriting"]
    assert note["text"] == "check with Khalid"
    assert note["confidence"]["score"] <= 0.5 and "handwriting_unreliable" in note["confidence"]["reasons"]
    assert note["bbox"][0] <= 100 and note["bbox"][2] >= 400


def page_with_text_the_model_skipped(path):
    """The model's output covers the top of the page; more text sits lower down that it left out."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for row in range(4):
        page.insert_text((80, 560 + row * 22), "The Manager shall maintain separate and proper books and accounts.", fontsize=11)
    doc.save(path)
    return path


SKIPPED = "<x_0.1><y_0.05>Covered text at the top.<x_0.9><y_0.2><class_Text>"


def test_text_the_model_skipped_is_read_from_the_page_and_put_in_reading_order(tmp_path):
    reader = lambda image_path: "<x_0.0><y_0.0>The Manager shall maintain separate and proper books.<x_1.0><y_1.0><class_Text>"
    result = NemotronEngine(serving(SKIPPED), mark_reader=reader).parse(page_with_text_the_model_skipped(tmp_path / "g.pdf"), tmp_path / "out")
    blocks = sorted(result["pages"][0]["blocks"], key=lambda block: block["reading_order"])
    assert [block["text"] for block in blocks][0] == "Covered text at the top."
    recovered = blocks[1]
    assert recovered["text"].startswith("The Manager shall maintain") and recovered["type"] == "text"
    assert "recovered_from_unaccounted_ink" in recovered["confidence"]["reasons"] and recovered["bbox"][1] > 400


def test_unaccounted_ink_that_cannot_be_read_is_an_illegible_block_not_guessed_text(tmp_path):
    result = NemotronEngine(serving(SKIPPED), mark_reader=lambda image_path: "").parse(
        page_with_text_the_model_skipped(tmp_path / "g.pdf"), tmp_path / "out"
    )
    illegible = [block for block in result["pages"][0]["blocks"] if block["type"] == "illegible"]
    assert illegible and all(block["text"] == "" and "unaccounted_ink" in block["confidence"]["reasons"] for block in illegible)


def test_a_page_the_model_covered_fully_gets_nothing_added(tmp_path):
    blocks = NemotronEngine(serving(), mark_reader=lambda image_path: "x").parse(make_pdf(tmp_path / "e.pdf"), tmp_path / "out")["pages"][0]["blocks"]
    assert [block["type"] for block in blocks] == ["text"]


def test_recovered_text_goes_after_the_last_block_above_it_even_when_the_models_own_order_is_not_top_to_bottom():
    from docintel.engines.nemotron_parse import add_unaccounted, build_page

    raw = (
        "<x_0.1><y_0.05>Top.<x_0.9><y_0.1><class_Text>"
        "<x_0.1><y_0.9>Footer page 2<x_0.9><y_0.95><class_Page-footer>"
        "<x_0.1><y_0.6>Late paragraph the model put last.<x_0.9><y_0.65><class_Text>"
    )
    page = build_page(raw, page_number=1, page_size=(600, 800))
    add_unaccounted(page, [("The recovered paragraph is read from the page.", [60.0, 560.0, 540.0, 600.0], False)])
    ordered = [block["text"] for block in sorted(page["blocks"], key=lambda block: block["reading_order"])]
    assert ordered.index("The recovered paragraph is read from the page.") > ordered.index("Late paragraph the model put last.") - 1
    assert ordered[0] == "Top."
    # the recovered block (y=560) sits below the late paragraph (y=480), so it follows it
    assert ordered.index("The recovered paragraph is read from the page.") == ordered.index("Late paragraph the model put last.") + 1


def signature_page(path, signed=True):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((60, 600), "Signature:", fontsize=11)
    page.draw_line((130, 604), (330, 604), color=(0, 0, 0), width=1)
    if signed:
        page.draw_polyline([(140, 598), (160, 575), (178, 600), (200, 570), (220, 598), (250, 580), (290, 600)], color=(0.1, 0.1, 0.1), width=2.5)
    doc.save(path)
    return path


SIG_COVERED = "<x_0.05><y_0.72><x_0.2><y_0.78>Signature:<class_Text>"
SIGNATURE_LABEL = "<x_0.08><y_0.73>Signature:<x_0.2><y_0.76><class_Text>"


def test_a_signature_over_the_signature_line_is_a_signature_block_with_no_text(tmp_path):
    result = NemotronEngine(serving(SIGNATURE_LABEL), mark_reader=lambda image_path: "").parse(signature_page(tmp_path / "s.pdf"), tmp_path / "out")
    [signature] = [block for block in result["pages"][0]["blocks"] if block["type"] == "signature"]
    assert signature["text"] == "" and 130 <= signature["bbox"][0] <= 150 and signature["bbox"][2] >= 280


def test_an_unsigned_signature_line_adds_nothing(tmp_path):
    result = NemotronEngine(serving(SIGNATURE_LABEL), mark_reader=lambda image_path: "").parse(
        signature_page(tmp_path / "u.pdf", signed=False), tmp_path / "out"
    )
    assert [block["type"] for block in result["pages"][0]["blocks"]] == ["text"]
