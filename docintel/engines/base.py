"""The seam between the pipeline and whatever reads a page.

An engine turns a PDF into a ``specter.v1`` result.  The pipeline picks an
engine and never names a concrete parser, so a self-hosted or third-party
engine can be added without touching routing (ADR 0001, decisions 3 and 5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

# Where a page's pixels and text may go while this engine reads them.
#   local        -- stays in this process
#   self_hosted  -- leaves the process but stays on infrastructure we control
#   third_party  -- sent to someone else's API
# Confidential client documents may only use the first two.
Policy = Literal["local", "self_hosted", "third_party"]


@runtime_checkable
class Engine(Protocol):
    name: str
    policy: Policy

    def parse(
        self,
        pdf: Path,
        output_dir: Path | None,
        *,
        stem: str | None = None,
        pages: list[int] | None = None,
    ) -> dict[str, Any]:
        """Read ``pdf`` and return a ``specter.v1`` result.

        ``pages`` lists the pages the caller needs from this engine.  An engine
        that can skip the rest does; one that cannot reads them anyway, and the
        caller discards what it did not ask for.
        """
        ...
