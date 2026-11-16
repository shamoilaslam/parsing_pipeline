"""Reading a parsed folder: the documents in it, without the run's own bookkeeping files."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Top-level JSON files a run writes that are not parsed documents.
NOT_DOCUMENTS = {"manifest.json", "report.json", "document_types.json", "ingest_failures.json"}


def documents(parsed: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Each parsed document as ``(output stem, payload)``, in name order."""
    for path in sorted(parsed.glob("*.json")):
        if path.name not in NOT_DOCUMENTS:
            yield path.stem, json.loads(path.read_text(encoding="utf-8"))
