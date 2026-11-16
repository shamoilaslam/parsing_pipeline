"""The Word adapter: a document becomes a PDF with an exact text layer and real boxes.

LibreOffice does the conversion and is not installed on every machine, so the
tests drive the adapter through a fake one.
"""

from pathlib import Path

import pytest

from docintel.ingest.office import ConverterMissing, ConversionFailed, office_to_pdf


def fake_libreoffice(commands):
    """Records the command and writes the PDF LibreOffice would write beside --outdir."""

    def run(command):
        commands.append(command)
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / f"{Path(command[-1]).stem}.pdf").write_bytes(b"%PDF-1.4 fake")

    return run


def test_a_word_file_is_converted_headlessly_and_returned_at_the_requested_path(tmp_path):
    docx = tmp_path / "nda.docx"
    docx.write_bytes(b"not read by the fake")
    commands = []
    result = office_to_pdf(docx, tmp_path / "out" / "nda.pdf", find=lambda: "soffice", run=fake_libreoffice(commands))
    assert result == tmp_path / "out" / "nda.pdf"
    assert result.read_bytes() == b"%PDF-1.4 fake"
    assert commands[0][:4] == ["soffice", "--headless", "--convert-to", "pdf"]
    assert commands[0][-1] == str(docx)


def test_a_machine_without_libreoffice_gets_a_clear_error_and_nothing_is_run(tmp_path):
    commands = []
    with pytest.raises(ConverterMissing, match="LibreOffice"):
        office_to_pdf(tmp_path / "nda.docx", tmp_path / "nda.pdf", find=lambda: None, run=fake_libreoffice(commands))
    assert commands == []


def test_libreoffice_exiting_cleanly_without_a_pdf_is_reported_not_returned(tmp_path):
    with pytest.raises(ConversionFailed, match="nda.docx"):
        office_to_pdf(tmp_path / "nda.docx", tmp_path / "nda.pdf", find=lambda: "soffice", run=lambda command: None)
