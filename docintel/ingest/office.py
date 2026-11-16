"""Word-processor files as PDFs.

LibreOffice renders the file, so the PDF has the document's exact text layer and
real page geometry: the native parser then reads it with no OCR, and its boxes
point at the rendered page a reviewer would be shown.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
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
    return out_pdf
