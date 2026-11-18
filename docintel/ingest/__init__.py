"""Adapters that turn other file formats into a PDF the pipeline already reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docintel.ingest.email import email_to_pdf
from docintel.ingest.image import image_to_pdf
from docintel.ingest.office import office_to_pdf

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
OFFICE_SUFFIXES = {".docx", ".doc", ".odt", ".rtf"}
EMAIL_SUFFIXES = {".eml"}


def normalize_folder(folder: Path, work: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    """Convert every image, Word file and e-mail under ``folder`` to a PDF under ``work``.

    Returns the folder holding the PDFs (None if there were none to convert) and
    one record per file that could not be converted.  PDFs are left where they are.
    """
    converted = False
    failures: list[dict[str, Any]] = []
    for source in sorted(folder.rglob("*")):
        suffix = source.suffix.lower()
        convert = (
            image_to_pdf if suffix in IMAGE_SUFFIXES
            else office_to_pdf if suffix in OFFICE_SUFFIXES
            else email_to_pdf if suffix in EMAIL_SUFFIXES
            else None
        )
        if convert is None or not source.is_file():
            continue
        # Keep the original suffix in the name so photo.png and photo.docx cannot collide.
        target = work / source.relative_to(folder).parent / f"{source.name}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            convert(source, target)
            converted = True
        except Exception as error:  # one unreadable file must not end a corpus run
            failures.append({"file": str(source), "error": f"{type(error).__name__}: {error}"})
    return (work if converted else None), failures
