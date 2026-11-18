"""The fact-intelligence layer: one JSON per file, per-page text, boxes as page fractions, honest about what was not read."""

import fitz
import pytest

from docintel.facts import facts_file


def block(text, bbox=(60.0, 79.2, 300.0, 158.4), type="text", source="pymupdf-native", language="en", score=0.88,
          order=0, reasons=(), **extra):
    return {"id": f"p1_b{order}", "type": type, "text": text, "bbox": list(bbox), "source": source, "language": language,
            "reading_order": order, "contains_rtl": language == "ur",
            "confidence": {"score": score, "reasons": list(reasons)}, **extra}


def page(blocks, number=1, extraction="digital", **extra):
    return {"page_number": number, "width": 600.0, "height": 792.0, "rotation": 0, "extraction": extraction,
            "blocks": blocks, **extra}


def result(*pages):
    return {"document": {"id": "abc123", "source_name": "bundle.pdf", "page_count": len(pages), "engine": "x"},
            "pages": list(pages)}


def facts(*pages, **kwargs):
    return facts_file(result(*pages), **kwargs)


def test_the_file_block_says_what_was_read_and_by_which_parser():
    out = facts(page([block("x")]), seconds=1.5)
    assert out["file"]["name"] == "bundle.pdf" and out["file"]["sha256"] == "abc123"
    assert out["file"]["pages"] == 1 and out["file"]["seconds"] == 1.5
    assert out["file"]["parser"] == "docintel" and out["file"]["parser_version"]
    assert out["metadata"] == {"email": None, "docx": None}


def test_a_digital_page_is_text_layer_and_a_read_page_is_ocr_with_the_engine_named():
    digital, scanned = facts(
        page([block("Digital")]),
        page([block("Scanned", source="nemotron-3.5-parse")], number=2, extraction="scanned"),
    )["pages"]
    assert digital["source"] == "text_layer" and scanned["source"] == "ocr"
    assert scanned["engine"] == "nemotron-3.5-parse" and "engine" not in digital


def test_boxes_are_fractions_of_the_page_as_x_y_width_height_from_the_top_left():
    [one] = facts(page([block("x", bbox=(60.0, 79.2, 300.0, 158.4))]))["pages"][0]["blocks"]
    assert one["bbox"] == pytest.approx([0.1, 0.1, 0.4, 0.1])


def test_pages_keep_their_physical_order_and_numbers():
    out = facts(page([block("a")], number=1), page([block("b")], number=2))
    assert [p["page"] for p in out["pages"]] == [1, 2]


def test_headers_and_footers_are_reported_apart_and_leave_the_body_text():
    body = page([block("Header text", type="header", order=0), block("The body.", order=1),
                 block("Page 4", type="footer", order=2)])
    [out] = facts(body)["pages"]
    assert out["header_text"] == "Header text" and out["footer_text"] == "Page 4"
    assert out["printed_page_number"] == "4"
    assert out["text"] == "The body." and [b["text"] for b in out["blocks"]] == ["The body."]


def test_block_types_use_the_fact_pipelines_names():
    kinds = {"heading": "heading", "text": "paragraph", "list": "list_item", "footnote": "footnote", "table": "table"}
    blocks = [block("t", type=kind, order=i) for i, kind in enumerate(kinds)]
    [out] = facts(page(blocks))["pages"]
    assert [b["type"] for b in out["blocks"]] == list(kinds.values())


def test_markdown_the_model_added_is_removed_and_the_words_are_untouched():
    [out] = facts(page([block("**Dear Sir,** the sum is 1,250,000.", source="nemotron-3.5-parse")], extraction="scanned"))["pages"]
    assert out["blocks"][0]["text"] == "Dear Sir, the sum is 1,250,000."


def test_a_markdown_table_becomes_rows_of_cells():
    table = "| Date | Amount |\n| --- | --- |\n| 12.03.2004 | 50,000 |"
    [out] = facts(page([block(table, type="table", source="nemotron-3.5-parse")], extraction="scanned"))["pages"]
    [one] = out["blocks"]
    assert [[cell["text"] for cell in row] for row in one["rows"]] == [["Date", "Amount"], ["12.03.2004", "50,000"]]


def test_urdu_the_model_cannot_read_is_marked_illegible_with_its_position_and_no_text():
    unread = block("ﻡﻼﺳ", language="ur", source="nemotron-3.5-parse", score=0.2, reasons=["urdu_unreliable_for_engine"])
    [out] = facts(page([unread], extraction="scanned"))["pages"]
    [one] = out["blocks"]
    assert (one["type"], one["text"]) == ("illegible", "") and one["bbox"]
    assert out["text"] == "" and "urdu_unread" in out["quality"]["flags"]


def test_urdu_read_by_the_vision_stage_is_kept_in_nfc():
    read = block("کی", language="ur", source="nemotron-3.5-parse", reasons=["urdu_unreliable_for_engine", "gemini_urdu_vision"],
                 rtl={"vision": {"provider": "gemini"}})
    [out] = facts(page([read], extraction="scanned"))["pages"]
    assert out["blocks"][0]["type"] == "paragraph" and out["blocks"][0]["text"] == "کی"
    assert out["language"]["ur"] == 1.0


def test_language_is_the_share_of_text_in_each_language():
    [out] = facts(page([block("a" * 30), block("ب" * 10, language="ur", order=1)]))["pages"]
    assert out["language"] == {"en": 0.75, "ur": 0.25}


def test_quality_has_a_score_the_ocr_confidence_and_flags_for_what_was_seen():
    [text_page, blank] = facts(
        page([block("Some text on a page")]),
        page([], number=2, extraction="scanned", scan={"preprocessing": {"estimated_skew_degrees": 3.2}}),
    )["pages"]
    assert text_page["quality"]["score"] == 0.88 and text_page["quality"]["ocr_confidence"] is None
    assert set(blank["quality"]["flags"]) == {"blank", "skewed"} and blank["status"] == "ok"


def test_an_unread_page_fails_alone_with_a_reason():
    ok, failed = facts(page([block("fine")]), page([], number=2, extraction="native_text_pending"))["pages"]
    assert ok["status"] == "ok"
    assert failed["status"] == "failed" and failed["reason"]


def test_the_page_image_is_written_when_the_source_pdf_is_given(tmp_path):
    pdf = tmp_path / "in.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=792)
    doc.save(pdf)
    out = facts(page([block("x")]), pdf=pdf, folder=tmp_path / "facts")
    assert out["pages"][0]["image"] == "pages/0001.png"
    assert (tmp_path / "facts" / "pages" / "0001.png").exists()


def _parsed_folder(tmp_path, blocks):
    import json

    pdf = tmp_path / "in.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=792)
    doc.save(pdf)
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    payload = result(page(blocks, extraction="scanned"))
    payload["document"]["source_file"] = str(pdf)
    (parsed / "in.json").write_text(json.dumps(payload), encoding="utf-8")
    return parsed


URDU = dict(language="ur", source="nemotron-3.5-parse", reasons=["urdu_unreliable_for_engine"])


def test_the_folder_writer_gives_each_file_its_own_folder_and_leaves_the_parsed_files_alone(tmp_path):
    import json

    from docintel.facts import write_folder

    parsed = _parsed_folder(tmp_path, [block("Plain English text", source="nemotron-3.5-parse")])
    before = (parsed / "in.json").read_bytes()
    write_folder(parsed, tmp_path / "facts", urdu_ceiling="local")
    written = json.loads((tmp_path / "facts" / "in" / "in.json").read_text(encoding="utf-8"))
    assert written["pages"][0]["text"] == "Plain English text"
    assert (tmp_path / "facts" / "in" / "pages" / "0001.png").exists()
    assert (parsed / "in.json").read_bytes() == before  # the legal-research layer is not touched


def test_urdu_goes_to_the_vision_route_on_a_copy_and_only_when_the_policy_allows(tmp_path, monkeypatch):
    import json

    from docintel import facts
    from docintel.engines import urdu_vision

    calls = []

    def fake_run(pdf, prediction, output_dir, **kwargs):
        calls.append(kwargs["ceiling"])
        data = json.loads(prediction.read_text(encoding="utf-8"))
        block0 = data["pages"][0]["blocks"][0]
        block0["text"], block0["rtl"] = "اردو", {"vision": {"provider": "gemini"}}
        prediction.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(urdu_vision, "run", fake_run)
    parsed = _parsed_folder(tmp_path, [block("ﻡﻼﺳ", **URDU)])
    facts.write_folder(parsed, tmp_path / "local", urdu_ceiling="local")
    facts.write_folder(parsed, tmp_path / "third", urdu_ceiling="third_party")
    local = json.loads((tmp_path / "local" / "in" / "in.json").read_text(encoding="utf-8"))
    third = json.loads((tmp_path / "third" / "in" / "in.json").read_text(encoding="utf-8"))
    assert calls == ["third_party"]
    assert local["pages"][0]["blocks"][0]["type"] == "illegible"
    assert third["pages"][0]["blocks"][0]["text"] == "اردو"


def test_a_printed_page_number_leading_a_running_header_is_found():
    [out] = facts(page([block("2 W.P No.10464 of 2021", type="header"), block("Body text")]))["pages"]
    assert out["printed_page_number"] == "2"
    [out] = facts(page([block("W.P No.10464 of 2021", type="header"), block("Body text")]))["pages"]
    assert out["printed_page_number"] is None  # a year or case number is not a page number


def test_a_page_number_glued_to_the_footer_words_by_ocr_is_still_found():
    [out] = facts(page([block("Body text"), block("Notarealrecord.Page12", type="footer", order=1)]))["pages"]
    assert out["printed_page_number"] == "12"


def test_a_latex_table_the_model_wrote_becomes_rows_with_its_escapes_removed():
    latex = (
        "\\begin{tabular}{cc}\n"
        "\\thead{Party} & \\thead{Initial Percentage Interest}\\\\\n"
        "Balochistan Development Authority & 50\\%\\\\\n"
        "\\end{tabular}"
    )
    [out] = facts(page([block(latex, type="table", source="nemotron-3.5-parse")], extraction="scanned"))["pages"]
    [one] = out["blocks"]
    assert [[cell["text"] for cell in row] for row in one["rows"]] == [
        ["Party", "Initial Percentage Interest"],
        ["Balochistan Development Authority", "50%"],
    ]
