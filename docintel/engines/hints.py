"""The legal knowledge an engine's metadata pass leans on, passed in rather than imported.

Four of Specter's metadata rules need to know a court's canonical name, its id,
whether it sits in benches, and what a citation looks like.  They are injected
so the engine layer states what it needs and ``docintel.legal.hints`` supplies
it (ADR 0001, decision 2); a non-legal deployment passes its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegalHints:
    canonical_court: Callable[[str], str]
    court_id: Callable[[str], str | None]
    sits_in_benches: Callable[[str], bool]
    find_citations: Callable[[str], list[dict[str, Any]]]
