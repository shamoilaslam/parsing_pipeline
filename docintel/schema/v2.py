"""``specter.v2``: the additions on top of ``specter.v1``, applied after a parse.

v2 is a strict superset of v1 (ADR 0001): nothing is renamed, retyped or
removed.  The fields are derived from what the engines and enrichers already
produced, in one place, so both engines get them identically and an engine
never has to know the schema revision.  One function per added field, each
landed as its own snapshot-changing commit.
"""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Any

# Length of the document id kept in a block's uid; 16 hex chars is 64 bits,
# far past any collision a corpus of tens of millions of documents can reach.
UID_PREFIX = 16


def document_id(pdf: Path) -> str:
    """SHA-256 of the file's bytes: the same file has the same id in every run and on every machine."""
    digest = hashlib.sha256()
    with pdf.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_identity(result: dict[str, Any], pdf: Path) -> None:
    """``document.id`` and, on every block, ``uid`` = ``<id prefix>/<block id>``.

    A block's own ``id`` is only unique within its document; ``uid`` is the
    address a fact can cite from anywhere.
    """
    document = result["document"]
    document["id"] = document_id(pdf)
    prefix = document["id"][:UID_PREFIX]
    for page in result.get("pages", []):
        for block in page.get("blocks", []):
            block["uid"] = f"{prefix}/{block['id']}"


# The Arabic-script blocks Urdu is set in, including its presentation forms.
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def _is_arabic_letter(char: str) -> bool:
    cp = ord(char)
    return char.isalpha() and any(start <= cp <= end for start, end in _ARABIC_RANGES)


def _is_latin_letter(char: str) -> bool:
    if not char.isalpha():
        return False
    return char.isascii() or unicodedata.name(char, "").startswith("LATIN")


def script_and_direction(text: str) -> tuple[str, str]:
    """ISO 15924 script and base direction, from the letters of ``text``.

    Only letters decide: digits and punctuation exist in both scripts, so a
    block of ``12.3`` has no script of its own.  ``Zyyy`` (common) is also what
    a block with both scripts gets, because no single 15924 code names a mix;
    its ``direction`` says ``mixed`` so the mix is not hidden.
    """
    arabic = any(_is_arabic_letter(char) for char in text)
    latin = any(_is_latin_letter(char) for char in text)
    if arabic and latin:
        return "Zyyy", "mixed"
    if arabic:
        return "Arab", "rtl"
    if latin:
        return "Latn", "ltr"
    return "Zyyy", "ltr"


def add_script(result: dict[str, Any]) -> None:
    """``script`` and ``direction`` on every block."""
    for page in result.get("pages", []):
        for block in page.get("blocks", []):
            block["script"], block["direction"] = script_and_direction(block["text"])


def add_readings(result: dict[str, Any]) -> None:
    """``readings[]``: every candidate transcription of a block, and why one was accepted.

    A block leaves a parse with the one reading its engine produced.  ``engine``
    is the block's ``source`` verbatim, so it names what actually read the
    text; ``policy`` is ``local`` because every engine that writes a block
    today runs in this process.  ``checks`` is empty until something verifies
    the reading.  Later readings (a vision model's, a human's) are appended by
    the step that makes them; ``text`` always equals the accepted reading.
    """
    for page in result.get("pages", []):
        for block in page.get("blocks", []):
            block["readings"] = [{
                "text": block["text"],
                "engine": block["source"],
                "policy": "local",
                "checks": [],
                "accepted": True,
            }]


def add_scanned_cell_positions(result: dict[str, Any]) -> None:
    """``row`` and ``column`` on scanned table cells, as native cells already have.

    The ruled-grid detector emits cells row by row, so a cell's position
    follows from the lengths of the table's ``rows``.  A table whose cell count
    does not match its rows is left alone rather than guessed at.
    """
    for page in result.get("pages", []):
        for block in page.get("blocks", []):
            cells, rows = block.get("cells"), block.get("rows")
            if not cells or not rows or "row" in cells[0]:
                continue
            if len(cells) != sum(len(row) for row in rows):
                continue
            positions = [(r, c) for r, row in enumerate(rows) for c in range(len(row))]
            for cell, (row, column) in zip(cells, positions, strict=True):
                cell["row"], cell["column"] = row, column


def upgrade(result: dict[str, Any], pdf: Path) -> dict[str, Any]:
    add_identity(result, pdf)
    add_script(result)
    add_readings(result)
    add_scanned_cell_positions(result)
    return result
