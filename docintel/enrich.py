"""The seam between layout and domain knowledge.

An engine says what is printed on a page.  An enricher says what it means:
what kind of document this is, which authorities it cites, what the corpus
already knows about it.  Enrichers run after the engine and only add to or
correct the metadata; they never re-read the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Enricher(Protocol):
    name: str

    def enrich(self, result: dict[str, Any], pdf: Path) -> dict[str, Any]:
        """Return ``result`` (a ``specter.v1`` payload) with this enricher's fields applied."""
        ...
