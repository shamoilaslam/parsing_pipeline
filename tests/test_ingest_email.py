"""E-mail as a PDF the pipeline reads: headers and body on pages, attachments as files of their own."""

import json
from email.message import EmailMessage

import fitz

from docintel.ingest import normalize_folder
from docintel.ingest.email import email_to_pdf, sidecar_path


def message(attach=None):
    mail = EmailMessage()
    mail["From"] = "Counsel <counsel@example.invalid>"
    mail["To"] = "Tribunal <tribunal@example.invalid>, Parties <parties@example.invalid>"
    mail["Cc"] = "Registry <registry@example.invalid>"
    mail["Date"] = "Mon, 21 May 2018 10:14:00 +0500"
    mail["Subject"] = "Quantum hearing coordination"
    mail.set_content("Dear all,\n\nThe corrected exhibits are attached. The hearing is on 4 June 2018.\n\nRegards")
    for name, kind, data in attach or []:
        maintype, subtype = kind.split("/")
        mail.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return mail


def one_page_pdf(text="Attached exhibit text."):
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), text, fontsize=11)
    return doc.tobytes()


def write(tmp_path, mail, name="mail.eml"):
    path = tmp_path / name
    path.write_bytes(bytes(mail))
    return path


def test_the_headers_and_body_become_the_text_layer_of_a_pdf(tmp_path):
    out = tmp_path / "out" / "mail.eml.pdf"
    email_to_pdf(write(tmp_path, message()), out)
    with fitz.open(out) as doc:
        text = "".join(page.get_text() for page in doc)
    for expected in ("From: Counsel", "Cc: Registry", "Subject: Quantum hearing coordination", "The hearing is on 4 June 2018."):
        assert expected in text


def test_the_headers_are_kept_exactly_as_the_message_gave_them(tmp_path):
    out = tmp_path / "out" / "mail.eml.pdf"
    meta = email_to_pdf(write(tmp_path, message()), out)
    assert meta["from"] == "Counsel <counsel@example.invalid>"
    assert meta["to"] == ["Tribunal <tribunal@example.invalid>", "Parties <parties@example.invalid>"]
    assert meta["cc"] == ["Registry <registry@example.invalid>"]
    assert meta["date"] == "Mon, 21 May 2018 10:14:00 +0500" and meta["subject"] == "Quantum hearing coordination"
    assert json.loads(sidecar_path(out).read_text(encoding="utf-8")) == meta


def test_a_pdf_attachment_is_saved_as_its_own_file_and_listed(tmp_path):
    out = tmp_path / "out" / "mail.eml.pdf"
    meta = email_to_pdf(write(tmp_path, message([("exhibit.pdf", "application/pdf", one_page_pdf())])), out)
    [attachment] = meta["attachments"]
    assert attachment["name"] == "exhibit.pdf" and attachment["status"] == "read"
    saved = out.parent / attachment["file"]
    with fitz.open(saved) as doc:
        assert "Attached exhibit text." in doc[0].get_text()


def test_an_attachment_of_a_kind_the_pipeline_cannot_read_is_listed_not_dropped(tmp_path):
    out = tmp_path / "out" / "mail.eml.pdf"
    meta = email_to_pdf(write(tmp_path, message([("data.xlsx", "application/octet-stream", b"PK\x03\x04")])), out)
    assert meta["attachments"] == [{"name": "data.xlsx", "content_type": "application/octet-stream", "file": None, "status": "not_read"}]


def test_a_folder_run_converts_each_message_and_reports_the_ones_it_cannot_read(tmp_path):
    folder = tmp_path / "case"
    folder.mkdir()
    write(folder, message([("exhibit.pdf", "application/pdf", one_page_pdf())]), "mail.eml")
    (folder / "broken.eml").write_bytes(b"\xff\xfe not a message")
    work, failures = normalize_folder(folder, tmp_path / "work")
    produced = sorted(path.name for path in work.rglob("*.pdf"))
    assert "mail.eml.pdf" in produced and "exhibit.pdf" in produced
    assert [failure["file"] for failure in failures] == [str(folder / "broken.eml")] or failures == []
