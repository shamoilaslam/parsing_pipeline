"""Read-only access to parsed corpora.

The parsed runs under ``specter_data`` are inputs, not workspaces: nothing in
this package writes beside them.  ``check_output`` is called before any run
writes a byte, and refuses an output folder inside any input.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.getenv("SPECTER_DATA_ROOT", r"E:\Shamoil_Data\specter_data"))

# Corpus name -> parsed run folder.  The raw PDF folders are inputs too (they
# are hashed for document ids), so they are protected the same way.
PARSED = {
    "LHC": DATA_ROOT / "parsed_LHC",
    "SC": DATA_ROOT / "parsed_SC",
    "pakistancode": DATA_ROOT / "parsed_pakistancode",
}
RAW = {
    "LHC": DATA_ROOT / "LHC",
    "SC": DATA_ROOT / "SC",
    "pakistancode": DATA_ROOT / "pakistancode",
}


class OutputInsideInput(ValueError):
    pass


def check_output(out: Path, inputs: list[Path]) -> Path:
    """Refuse an output folder that is, or is inside, any input folder."""
    resolved = out.resolve()
    for root in inputs:
        root = root.resolve()
        if resolved == root or root in resolved.parents:
            raise OutputInsideInput(f"{out} is inside the input {root}; outputs must go to a folder of their own")
    return resolved


@dataclass(frozen=True)
class ParsedDocument:
    corpus: str
    stem: str
    json_path: Path
    metadata_path: Path

    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def payload(self) -> dict[str, Any]:
        return json.loads(self.json_path.read_text(encoding="utf-8"))


def documents(corpus: str, root: Path | None = None) -> Iterator[ParsedDocument]:
    """Every parsed document in a run that has both its canonical JSON and its metadata record."""
    root = root or PARSED[corpus]
    for metadata_path in sorted((root / "metadata").glob("*.json")):
        json_path = root / metadata_path.name
        if json_path.exists():
            yield ParsedDocument(corpus, metadata_path.stem, json_path, metadata_path)
