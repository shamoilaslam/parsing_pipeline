"""`docintel run <folder>`: point at a folder of PDFs, get a parsed folder back."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from docintel.run import main

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "pdfs" / "2021LHC9943.pdf"  # 2 pages, digital: fast


@pytest.fixture
def corpus(tmp_path):
    folder = tmp_path / "judgments"
    (folder / "2024").mkdir(parents=True)
    shutil.copy(SAMPLE, folder / "a.pdf")
    shutil.copy(SAMPLE, folder / "2024" / "b.pdf")
    return folder


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["docintel run", *map(str, argv)])
    return main()


def test_pointing_at_a_folder_writes_a_parsed_folder_beside_it(corpus, monkeypatch):
    assert run(monkeypatch, corpus) == 0
    parsed = corpus.parent / "judgments_parsed"
    assert sorted(path.name for path in parsed.glob("*.json")) == ["a.json", "b.json", "manifest.json", "report.json"]
    assert sorted(path.name for path in (parsed / "metadata").glob("*.json")) == ["a.json", "b.json"]
    assert json.loads((parsed / "manifest.json").read_text(encoding="utf-8"))["parsed"] == 2


def test_a_second_run_resumes_instead_of_parsing_again(corpus, monkeypatch):
    run(monkeypatch, corpus)
    parsed = corpus.parent / "judgments_parsed"
    before = (parsed / "a.json").stat().st_mtime_ns
    assert run(monkeypatch, corpus) == 0
    assert (parsed / "a.json").stat().st_mtime_ns == before
    assert json.loads((parsed / "manifest.json").read_text(encoding="utf-8"))["parsed"] == 2


def test_a_corrupt_pdf_is_reported_and_the_rest_are_still_parsed(corpus, monkeypatch):
    (corpus / "bad.pdf").write_bytes(b"this is not a pdf")
    assert run(monkeypatch, corpus) == 1
    manifest = json.loads((corpus.parent / "judgments_parsed" / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["parsed"], manifest["failed"]) == (2, 1)


def test_a_path_that_is_not_a_folder_is_refused(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as refused:
        run(monkeypatch, tmp_path / "missing")
    assert refused.value.code == 2


def test_out_overrides_where_the_parsed_folder_goes(corpus, monkeypatch, tmp_path):
    run(monkeypatch, corpus, "--out", tmp_path / "elsewhere")
    assert (tmp_path / "elsewhere" / "manifest.json").exists()
    assert not (corpus.parent / "judgments_parsed").exists()


def test_the_docintel_command_dispatches_run(corpus, monkeypatch):
    from docintel.__main__ import main as docintel

    monkeypatch.setattr(sys, "argv", ["docintel", "run", str(corpus), "--workers", "1"])
    docintel()
    assert (corpus.parent / "judgments_parsed" / "report.json").exists()
