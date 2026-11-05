"""parse_pdf with the Nemotron engine: scan pages from the model, readable pages from the PDF."""

import json
import sys

import fitz
import pytest

from docintel.engines.nemotron_batch import append_raw
from docintel.pipeline import main, parse_pdf

SCAN_TEXT = "W.P. No. 12345 of 2023"
RAW = f"<x_0.2><y_0.1>{SCAN_TEXT}<x_0.8><y_0.2><class_Text>"


def mixed_pdf(path):
    """Page 1 has a text layer; page 2 is one page-sized image, as a scan is."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Digital page one.", fontsize=11)
    scan = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 60, 80), False)
    pixmap.clear_with(220)
    scan.insert_image(scan.rect, pixmap=pixmap)
    doc.save(path)
    return path


def test_a_scan_page_is_read_by_the_model_and_a_readable_page_stays_native(tmp_path):
    pdf = mixed_pdf(tmp_path / "mixed.pdf")
    (tmp_path / "work").mkdir()
    append_raw(tmp_path / "work" / "raw.jsonl", 2, RAW)
    result = parse_pdf(pdf, tmp_path / "out", engine="nemotron", nemotron_work=tmp_path / "work")
    first, second = result["pages"]
    assert "Digital page one." in first["blocks"][0]["text"]
    assert second["blocks"][0]["text"] == SCAN_TEXT
    assert second["blocks"][0]["source"] == "nemotron-parse-v1.2"
    assert result["document"]["extraction_mode"] == "mixed_native_and_ocr"
    assert result["document"]["router"]["selected"] == "nemotron"


def test_the_command_line_parses_a_corpus_with_the_nemotron_engine(tmp_path, monkeypatch):
    pdf = mixed_pdf(tmp_path / "mixed.pdf")
    (tmp_path / "work").mkdir()
    append_raw(tmp_path / "work" / "raw.jsonl", 2, RAW)
    out = tmp_path / "out"
    argv = [
        "parse",
        str(pdf),
        "--out",
        str(out),
        "--quiet",
        "--engine",
        "nemotron",
        "--nemotron-work",
        str(tmp_path / "work"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    main()
    written = json.loads((out / "mixed.json").read_text(encoding="utf-8"))
    assert written["pages"][1]["blocks"][0]["source"] == "nemotron-parse-v1.2"


def test_the_nemotron_engine_refuses_to_run_without_a_work_folder(tmp_path):
    with pytest.raises(ValueError, match="nemotron_work"):
        parse_pdf(mixed_pdf(tmp_path / "mixed.pdf"), tmp_path / "out", engine="nemotron")
