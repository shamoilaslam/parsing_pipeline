"""Word-processor files as PDFs.

LibreOffice renders the file, so the PDF has the document's exact text layer and
real page geometry: the native parser then reads it with no OCR, and its boxes
point at the rendered page a reviewer would be shown.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path


class ConverterMissing(RuntimeError):
    """LibreOffice is not installed on this machine."""


class ConversionFailed(RuntimeError):
    """LibreOffice ran but produced no PDF."""


def _find_libreoffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, timeout=300)


_PROPERTIES = {
    "title": "dc:title", "subject": "dc:subject", "creator": "dc:creator", "keywords": "cp:keywords",
    "description": "dc:description", "last_modified_by": "cp:lastModifiedBy", "revision": "cp:revision",
    "created": "dcterms:created", "modified": "dcterms:modified",
}


def docx_sidecar(pdf: Path) -> Path:
    return pdf.with_name(f"{pdf.name}.docx.json")


def docx_properties(document: Path) -> dict[str, str]:
    """The properties stored in the file (author, dates, title), exactly as stored.  Empty if there are none to read."""
    try:
        with zipfile.ZipFile(document) as archive:
            xml = archive.read("docProps/core.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, OSError, UnicodeDecodeError):
        return {}
    found = {}
    for name, tag in _PROPERTIES.items():
        match = re.search(rf"<{tag}(?:\s[^>]*)?>([^<]*)</{tag}>", xml)
        if match and match.group(1).strip():
            found[name] = match.group(1).strip()
    return found


def office_to_pdf(
    document: Path,
    out_pdf: Path,
    *,
    find: Callable[[], str | None] = _find_libreoffice,
    run: Callable[[list[str]], None] = _run,
) -> Path:
    soffice = find()
    if soffice is None:
        raise ConverterMissing("LibreOffice (soffice) is needed to read Word files and is not installed")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        run([soffice, "--headless", "--convert-to", "pdf", "--outdir", scratch, str(document)])
        produced = Path(scratch) / f"{document.stem}.pdf"
        if not produced.exists():
            raise ConversionFailed(f"LibreOffice produced no PDF for {document.name}")
        shutil.move(produced, out_pdf)
    if (properties := docx_properties(document)):
        docx_sidecar(out_pdf).write_text(json.dumps(properties, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_pdf
