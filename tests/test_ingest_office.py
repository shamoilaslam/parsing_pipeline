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


CORE = (
    '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
    "<dc:title>Letter on royalty terms</dc:title><dc:creator>A. Counsel</dc:creator><cp:lastModifiedBy>B. Clerk</cp:lastModifiedBy>"
    '<cp:revision>3</cp:revision><dcterms:created xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">2008-10-20T09:00:00Z</dcterms:created>'
    "</cp:coreProperties>"
)


def docx_with_properties(path):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("docProps/core.xml", CORE)
        archive.writestr("word/document.xml", "<w:document/>")
    return path


def test_the_documents_own_properties_are_read_exactly_as_stored(tmp_path):
    from docintel.ingest.office import docx_properties

    assert docx_properties(docx_with_properties(tmp_path / "a.docx")) == {
        "title": "Letter on royalty terms", "creator": "A. Counsel", "last_modified_by": "B. Clerk",
        "revision": "3", "created": "2008-10-20T09:00:00Z",
    }


def test_a_file_with_no_readable_properties_gives_an_empty_record(tmp_path):
    from docintel.ingest.office import docx_properties

    broken = tmp_path / "b.docx"
    broken.write_bytes(b"not a zip")
    assert docx_properties(broken) == {}


def test_a_converted_word_file_leaves_its_properties_beside_the_pdf(tmp_path):
    import json

    from docintel.ingest.office import docx_sidecar

    out = tmp_path / "out" / "a.docx.pdf"
    docx = docx_with_properties(tmp_path / "a.docx")
    out.parent.mkdir()
    office_to_pdf(docx, out, find=lambda: "soffice", run=fake_libreoffice([]))
    assert json.loads(docx_sidecar(out).read_text(encoding="utf-8"))["creator"] == "A. Counsel"
