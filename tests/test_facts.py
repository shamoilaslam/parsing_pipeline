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


def test_a_stamp_is_its_own_block_and_page_flag_and_stays_out_of_the_body_text():
    stamp = block("RECEIVED 06 OCT 1993", type="stamp", order=1, source="nemotron-3.5-parse")
    [out] = facts(page([block("The body text.", source="nemotron-3.5-parse"), stamp], extraction="scanned"))["pages"]
    assert [b["type"] for b in out["blocks"]] == ["paragraph", "stamp"]
    assert out["blocks"][1]["text"] == "RECEIVED 06 OCT 1993"
    assert out["text"] == "The body text." and "stamp" in out["quality"]["flags"]


def test_handwriting_is_its_own_block_with_a_flag_and_stays_out_of_the_body_text():
    note = block("check with Khalid", type="handwriting", order=1, source="nemotron-3.5-parse", score=0.41)
    [out] = facts(page([block("The body text.", source="nemotron-3.5-parse"), note], extraction="scanned"))["pages"]
    assert out["blocks"][1]["type"] == "handwriting" and out["blocks"][1]["confidence"] == 0.41
    assert out["text"] == "The body text." and "handwriting" in out["quality"]["flags"]


def test_ink_no_block_accounted_for_is_an_illegible_block_with_its_own_page_flag():
    unread = block("", type="illegible", order=1, source="nemotron-3.5-parse", score=0.0, reasons=["unaccounted_ink"])
    [out] = facts(page([block("Body.", source="nemotron-3.5-parse"), unread], extraction="scanned"))["pages"]
    assert [b["type"] for b in out["blocks"]] == ["paragraph", "illegible"]
    assert out["blocks"][1]["text"] == "" and "unaccounted_ink" in out["quality"]["flags"]
    assert "urdu_unread" not in out["quality"]["flags"]


def test_text_recovered_from_a_second_look_is_ordinary_body_text():
    recovered = block("The Manager shall keep the books.", order=1, source="nemotron-3.5-parse", score=0.9,
                      reasons=["recovered_from_unaccounted_ink"])
    [out] = facts(page([block("First.", source="nemotron-3.5-parse"), recovered], extraction="scanned"))["pages"]
    assert out["text"] == "First.\n\nThe Manager shall keep the books."


def test_a_signature_is_its_own_block_with_a_flag_and_no_text_in_the_body():
    sign = block("", type="signature", order=1, source="nemotron-3.5-parse", score=0.0, reasons=["signature_ink"])
    [out] = facts(page([block("For and on behalf of the Authority. Signature:", source="nemotron-3.5-parse"), sign], extraction="scanned"))["pages"]
    assert [b["type"] for b in out["blocks"]] == ["paragraph", "signature"]
    assert out["blocks"][1]["text"] == "" and "signature" in out["quality"]["flags"]
    assert out["text"] == "For and on behalf of the Authority. Signature:"


def test_a_digital_page_with_a_stamp_the_model_read_is_mixed_and_names_the_engine_for_the_ocr_part():
    stamp = block("RECEIVED 06 OCT 1993", type="stamp", order=1, source="nemotron-3.5-parse")
    digital, mixed = facts(page([block("Text layer words.")]), page([block("Text layer words."), stamp], number=2))["pages"]
    assert digital["source"] == "text_layer" and "engine" not in digital
    assert mixed["source"] == "mixed" and mixed["engine"] == "nemotron-3.5-parse"
    assert mixed["text"] == "Text layer words."


def test_markdown_is_stripped_only_from_what_a_model_read_not_from_the_text_layer():
    native = block("A **real** asterisk pair in the document")
    read = block("**Dear Sir**", order=1, source="nemotron-3.5-parse")
    [out] = facts(page([native, read]))["pages"]
    assert [b["text"] for b in out["blocks"]] == ["A **real** asterisk pair in the document", "Dear Sir"]


def test_the_folder_writer_gives_a_digital_page_with_an_image_the_second_look_and_leaves_plain_pages_alone(tmp_path):
    import json

    import cv2
    import numpy as np

    from docintel.facts import write_folder

    pdf = tmp_path / "in.pdf"
    doc = fitz.open()
    with_image = doc.new_page(width=600, height=800)
    with_image.insert_text((60, 100), "Digital text layer on the page.", fontsize=11)
    red = np.full((90, 260, 3), 255, np.uint8)
    cv2.rectangle(red, (4, 4), (255, 85), (40, 40, 200), 4)  # an outline with lettering, as a stamp is
    cv2.putText(red, "RECEIVED", (14, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (40, 40, 200), 3)
    cv2.putText(red, "06 OCT 1993", (14, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 200), 3)
    png = tmp_path / "stamp.png"
    cv2.imwrite(str(png), red)
    with_image.insert_image(fitz.Rect(300, 40, 560, 130), filename=str(png))
    plain = doc.new_page(width=600, height=800)
    plain.insert_text((60, 100), "Another digital page.", fontsize=11)
    doc.save(pdf)
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    pages = [
        page([block("Digital text layer on the page.", bbox=(60, 90, 250, 104))]),
        page([block("Another digital page.", bbox=(60, 90, 200, 104))], number=2),
    ]
    payload = result(*pages)
    payload["document"]["source_file"] = str(pdf)
    (parsed / "in.json").write_text(json.dumps(payload), encoding="utf-8")
    seen = []

    def reader(image_path):
        seen.append(image_path.name)
        return "<x_0.0><y_0.0>RECEIVED<x_1.0><y_1.0><class_Text>"

    write_folder(parsed, tmp_path / "facts", urdu_ceiling="local", mark_reader=reader)
    written = json.loads((tmp_path / "facts" / "in" / "in.json").read_text(encoding="utf-8"))
    first, second = written["pages"]
    assert first["source"] == "mixed" and any(b["type"] == "stamp" and b["text"] == "RECEIVED" for b in first["blocks"])
    assert second["source"] == "text_layer" and len(seen) == 1


def test_an_email_file_carries_its_headers_and_attachments_in_metadata_email(tmp_path):
    import json

    from docintel.facts import write_folder
    from docintel.ingest.email import sidecar_path

    pdf = tmp_path / "mail.eml.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_text((60, 100), "From: A", fontsize=11)
    doc.save(pdf)
    meta = {"from": "A <a@x.invalid>", "to": ["B <b@y.invalid>"], "cc": [], "date": "Mon, 21 May 2018 10:14:00 +0500",
            "subject": "Hearing", "body_text": "Dear all", "warnings": [],
            "attachments": [{"name": "e.pdf", "content_type": "application/pdf", "file": "mail.eml.attachments/e.pdf", "status": "read"}]}
    sidecar_path(pdf).write_text(json.dumps(meta), encoding="utf-8")
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    payload = result(page([block("From: A", bbox=(60, 90, 140, 104))]))
    payload["document"].update(source_file=str(pdf), source_name="mail.eml.pdf")
    (parsed / "mail.eml.json").write_text(json.dumps(payload), encoding="utf-8")
    write_folder(parsed, tmp_path / "facts", urdu_ceiling="local")
    written = json.loads((tmp_path / "facts" / "mail.eml" / "mail.eml.json").read_text(encoding="utf-8"))
    assert written["file"]["name"] == "mail.eml" and written["file"]["mime"] == "message/rfc822"
    assert written["metadata"]["email"] == {k: meta[k] for k in ("from", "to", "cc", "date", "subject", "attachments")}
    assert written["metadata"]["docx"] is None


def test_a_local_urdu_reader_reads_crops_under_a_local_ceiling_so_nothing_is_illegible(tmp_path, monkeypatch):
    import json

    from docintel import facts
    from docintel.engines import urdu_vision

    seen = {}

    def fake_run(pdf, prediction, output_dir, **kwargs):
        seen.update(kwargs)
        data = json.loads(prediction.read_text(encoding="utf-8"))
        block0 = data["pages"][0]["blocks"][0]
        block0["text"], block0["rtl"] = "اردو", {"vision": {"provider": "local"}}
        prediction.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(urdu_vision, "run", fake_run)
    parsed = _parsed_folder(tmp_path, [block("ﻡﻼﺳ", **URDU)])
    reader = lambda image: "x"
    facts.write_folder(parsed, tmp_path / "facts", urdu_ceiling="local", urdu_reader=reader)
    written = json.loads((tmp_path / "facts" / "in" / "in.json").read_text(encoding="utf-8"))
    assert seen["ceiling"] == "local" and seen["local_reader"] is reader
    assert written["pages"][0]["blocks"][0]["text"] == "اردو"


def test_language_is_counted_from_the_letters_in_the_text_not_from_a_stale_label():
    urdu_with_latin = block("اردو" * 25 + " BDA", language="mixed", order=0)  # the label is stale; the text is Urdu
    [out] = facts(page([urdu_with_latin, block("English words here", order=1)]))["pages"]
    assert out["language"]["ur"] > 0.7 and out["language"]["en"] < 0.3
    assert out["blocks"][0]["language"] == "ur" and out["blocks"][1]["language"] == "en"


def test_a_tilted_scan_gets_its_measured_skew_and_the_skewed_flag(tmp_path):
    import cv2
    import numpy as np

    tilted = np.full((1200, 900, 3), 235, np.uint8)
    for row in range(26):
        cv2.putText(tilted, "The Manager shall maintain separate and proper books and accounts of the venture", (70, 90 + row * 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (30, 30, 30), 1)
    tilted = cv2.warpAffine(tilted, cv2.getRotationMatrix2D((450, 600), 2.0, 1.0), (900, 1200), borderValue=(235, 235, 235))
    png = tmp_path / "scan.png"
    cv2.imwrite(str(png), tilted)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_image(fitz.Rect(0, 0, 600, 800), filename=str(png))
    doc.save(pdf)
    straight = page([block("Text.", source="nemotron-3.5-parse")], extraction="scanned")
    out = facts_file(result(straight), pdf=pdf, folder=tmp_path / "facts")
    quality = out["pages"][0]["quality"]
    assert abs(quality["skew_degrees"] - 2.0) <= 0.3 and "skewed" in quality["flags"]


def test_a_text_layer_page_is_not_measured_for_skew(tmp_path):
    pdf = tmp_path / "t.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_text((60, 100), "Text layer.", fontsize=11)
    doc.save(pdf)
    out = facts_file(result(page([block("Text layer.")])), pdf=pdf, folder=tmp_path / "facts")
    assert out["pages"][0]["quality"]["skew_degrees"] is None


def test_a_page_the_model_failed_on_is_failed_with_the_models_reason_and_the_others_are_fine():
    failed = page([], number=2, extraction="failed", failure="RuntimeError: CUDA out of memory")
    ok, bad = facts(page([block("Fine.")]), failed)["pages"]
    assert ok["status"] == "ok" and bad["status"] == "failed" and "CUDA out of memory" in bad["reason"]


def test_a_block_lists_its_lines_with_boxes_from_the_text_layer_when_the_counts_match():
    native = block("First line\nSecond line\nThird line", bbox=(60.0, 100.0, 300.0, 136.0), line_bboxes=[
        [60.0, 100.0, 200.0, 112.0], [60.0, 112.0, 220.0, 124.0], [60.0, 124.0, 180.0, 136.0]])
    [out] = facts(page([native]))["pages"]
    lines = out["blocks"][0]["lines"]
    assert [line["text"] for line in lines] == ["First line", "Second line", "Third line"]
    assert lines[1]["bbox"] == pytest.approx([0.1, 112 / 792, 160 / 600, 12 / 792], abs=1e-3)
    assert not any(line.get("approximate") for line in lines)


def test_a_one_line_block_has_one_line_with_the_blocks_own_box():
    [out] = facts(page([block("Only line", bbox=(60.0, 79.2, 300.0, 158.4))]))["pages"]
    assert out["blocks"][0]["lines"] == [{"text": "Only line", "bbox": out["blocks"][0]["bbox"]}]


def test_without_line_boxes_or_an_image_the_lines_are_an_even_split_marked_approximate():
    [out] = facts(page([block("a one\na two", bbox=(60.0, 100.0, 300.0, 140.0), source="nemotron-3.5-parse")], extraction="scanned"))["pages"]
    lines = out["blocks"][0]["lines"]
    assert [line["approximate"] for line in lines] == [True, True]
    assert lines[0]["bbox"][1] < lines[1]["bbox"][1]


def test_lines_are_measured_from_the_page_image_when_it_shows_as_many_lines_as_the_text(tmp_path):
    import cv2
    import numpy as np

    image = np.full((1200, 900, 3), 235, np.uint8)
    for row in range(3):
        cv2.putText(image, "The Manager shall maintain proper books", (100, 300 + row * 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
    png = tmp_path / "scan.png"
    cv2.imwrite(str(png), image)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_image(fitz.Rect(0, 0, 600, 800), filename=str(png))
    doc.save(pdf)
    text = "The Manager shall maintain proper books\n" * 3
    scanned = page([block(text.strip(), bbox=(60.0, 180.0, 400.0, 280.0), source="nemotron-3.5-parse")], extraction="scanned")
    [out] = facts_file(result(scanned), pdf=pdf, folder=tmp_path / "facts")["pages"]
    lines = out["blocks"][0]["lines"]
    assert len(lines) == 3 and not any(line.get("approximate") for line in lines)
    ys = [line["bbox"][1] for line in lines]
    assert ys == sorted(ys) and 0.2 < ys[0] < 0.3  # a first line near y=300px of 1200


def test_one_long_line_of_text_over_several_visual_lines_is_split_by_width_without_losing_or_changing_a_word(tmp_path):
    import cv2
    import numpy as np

    image = np.full((1200, 900, 3), 235, np.uint8)
    for row in range(3):
        cv2.putText(image, "The Manager shall maintain proper books", (100, 300 + row * 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
    png = tmp_path / "scan.png"
    cv2.imwrite(str(png), image)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_image(fitz.Rect(0, 0, 600, 800), filename=str(png))
    doc.save(pdf)
    one_line = " ".join(["The Manager shall maintain proper books"] * 3)  # the model gave it as one line
    scanned = page([block(one_line, bbox=(60.0, 180.0, 400.0, 280.0), source="nemotron-3.5-parse")], extraction="scanned")
    [out] = facts_file(result(scanned), pdf=pdf, folder=tmp_path / "facts")["pages"]
    lines = out["blocks"][0]["lines"]
    assert len(lines) == 3 and all(line["approximate"] for line in lines)
    assert " ".join(line["text"] for line in lines) == one_line


def test_a_mixed_page_reports_the_ocr_confidence_of_the_part_a_model_read():
    stamp = block("RECEIVED 06 OCT 1993", type="stamp", order=1, source="nemotron-3.5-parse", score=0.6)
    [out] = facts(page([block("Text layer words that are long enough.", score=0.88), stamp]))["pages"]
    assert out["source"] == "mixed" and out["quality"]["ocr_confidence"] == 0.6


def test_a_word_file_carries_its_document_properties_in_metadata_docx(tmp_path):
    import json

    from docintel.facts import write_folder
    from docintel.ingest.office import docx_sidecar

    pdf = tmp_path / "letter.docx.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800).insert_text((60, 100), "Letter.", fontsize=11)
    doc.save(pdf)
    props = {"title": "Letter", "creator": "A. Counsel", "created": "2008-10-20T09:00:00Z"}
    docx_sidecar(pdf).write_text(json.dumps(props), encoding="utf-8")
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    payload = result(page([block("Letter.", bbox=(60, 90, 100, 104))]))
    payload["document"].update(source_file=str(pdf), source_name="letter.docx.pdf")
    (parsed / "letter.docx.json").write_text(json.dumps(payload), encoding="utf-8")
    write_folder(parsed, tmp_path / "facts", urdu_ceiling="local")
    written = json.loads((tmp_path / "facts" / "letter.docx" / "letter.docx.json").read_text(encoding="utf-8"))
    assert written["metadata"]["docx"] == props and written["metadata"]["email"] is None
    assert written["file"]["name"] == "letter.docx" and "wordprocessingml" in written["file"]["mime"]
