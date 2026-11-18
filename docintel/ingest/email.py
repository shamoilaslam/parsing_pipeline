"""E-mail as a PDF the pipeline already reads.

The headers and the body are drawn as real text on pages, so the native parser reads them with no OCR.
Each attachment the pipeline can read is saved as a file of its own beside the PDF and listed; one it
cannot read is listed too, never dropped.  The headers are kept exactly as the message gave them (no
date or address is normalised) in ``<pdf>.email.json``, which the fact-intelligence layer reads.
"""

from __future__ import annotations

import email
import email.policy
import json
import textwrap
from pathlib import Path
from typing import Any

import fitz

from docintel.ingest.image import image_to_pdf
from docintel.ingest.office import office_to_pdf

PAGE = (595, 842)
MARGIN = 56
FONT_SIZE = 10
LINE_HEIGHT = 13
WRAP = 95
HEADERS = ("From", "To", "Cc", "Date", "Subject")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
OFFICE_SUFFIXES = {".docx", ".doc", ".odt", ".rtf"}
# Fonts that draw more than Latin text, tried in order.
FALLBACK_FONTS = (
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def sidecar_path(pdf: Path) -> Path:
    return pdf.with_name(f"{pdf.name}.email.json")


def _addresses(value: str | None) -> list[str]:
    return [part.strip() for part in value.replace("\n", " ").split(",") if part.strip()] if value else []


def _draw(lines: list[str], out_pdf: Path) -> list[str]:
    """Draw the lines on pages.  Returns warnings: text no font here could draw is left out and said so."""
    warnings = []
    fallback = next((path for path in FALLBACK_FONTS if Path(path).exists()), None)
    latin = fitz.Font("helv")
    doc = fitz.open()
    per_page = (PAGE[1] - 2 * MARGIN) // LINE_HEIGHT
    for start in range(0, max(len(lines), 1), per_page):
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        for row, line in enumerate(lines[start : start + per_page]):
            point = (MARGIN, MARGIN + row * LINE_HEIGHT)
            if all(latin.has_glyph(ord(char)) or char.isspace() for char in line):
                page.insert_text(point, line, fontsize=FONT_SIZE, fontname="helv")
            elif fallback:
                page.insert_text(point, line, fontsize=FONT_SIZE, fontfile=fallback, fontname="fallback")
            else:
                warnings.append("non_latin_text_not_drawn")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_pdf)
    return sorted(set(warnings))


def _attachments(message: email.message.EmailMessage, out_pdf: Path) -> list[dict[str, Any]]:
    folder = out_pdf.parent / f"{out_pdf.name[: -len('.pdf')]}.attachments"
    listed = []
    for part in message.iter_attachments():
        name = Path(part.get_filename() or "attachment").name
        entry: dict[str, Any] = {"name": name, "content_type": part.get_content_type(), "file": None, "status": "not_read"}
        suffix = Path(name).suffix.lower()
        data = part.get_payload(decode=True) or b""
        if suffix == ".pdf" or suffix in IMAGE_SUFFIXES or suffix in OFFICE_SUFFIXES:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (name if suffix == ".pdf" else f"{name}.pdf")
            if suffix == ".pdf":
                target.write_bytes(data)
            else:
                raw = folder / name
                raw.write_bytes(data)
                try:
                    (image_to_pdf if suffix in IMAGE_SUFFIXES else office_to_pdf)(raw, target)
                except Exception:  # the attachment is listed as unread, the message still converts
                    raw.unlink(missing_ok=True)
                    listed.append(entry)
                    continue
                raw.unlink(missing_ok=True)
            entry.update(file=str(target.relative_to(out_pdf.parent)).replace("\\", "/"), status="read")
        listed.append(entry)
    return listed


def email_to_pdf(source: Path, out_pdf: Path) -> dict[str, Any]:
    message = email.message_from_binary_file(source.open("rb"), policy=email.policy.default)
    body_part = message.get_body(("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""
    meta: dict[str, Any] = {
        "from": message["From"],
        "to": _addresses(message["To"]),
        "cc": _addresses(message["Cc"]),
        "date": message["Date"],
        "subject": message["Subject"],
    }
    lines = [f"{name}: {message[name]}" for name in HEADERS if message[name]] + [""]
    for paragraph in str(body).splitlines():
        lines += textwrap.wrap(paragraph, WRAP) or [""]
    meta["warnings"] = _draw(lines, out_pdf)
    meta["body_text"] = str(body)
    meta["attachments"] = _attachments(message, out_pdf)
    sidecar_path(out_pdf).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
