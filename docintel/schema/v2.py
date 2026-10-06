"""``specter.v2``: the additions on top of ``specter.v1``, applied after a parse.

v2 is a strict superset of v1 (ADR 0001): nothing is renamed, retyped or
removed.  The fields are derived from what the engines and enrichers already
produced, in one place, so both engines get them identically and an engine
never has to know the schema revision.  One function per added field, each
landed as its own snapshot-changing commit.
"""

from __future__ import annotations

import hashlib
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


def upgrade(result: dict[str, Any], pdf: Path) -> dict[str, Any]:
    add_identity(result, pdf)
    return result
