"""``specter.v2``: ``specter.v1`` plus the fields ``docintel.schema.v2`` adds.

Each subclass only adds; nothing from v1 is changed.  Fields are optional
here until every commit of the v2 series has landed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from docintel.schema.specter_v1 import Block, Document, NativeCell, Page, ScannedCell, SpecterV1


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str | None = None


class Reading(BaseModel):
    """One candidate transcription of a block."""

    model_config = ConfigDict(extra="forbid")

    text: str
    engine: str
    policy: Literal["local", "self_hosted", "third_party"]
    checks: list[Check]
    accepted: bool


class ScannedCellV2(ScannedCell):
    row: int | None = None
    column: int | None = None


class BlockV2(Block):
    uid: str | None = None
    script: Literal["Latn", "Arab", "Zyyy"] | None = None
    direction: Literal["ltr", "rtl", "mixed"] | None = None
    readings: list[Reading] | None = None
    cells: list[NativeCell | ScannedCellV2] | None = None  # type: ignore[assignment]


class PageV2(Page):
    blocks: list[BlockV2]  # type: ignore[assignment]


class DocumentV2(Document):
    id: str | None = None


class SpecterV2(SpecterV1):
    document: DocumentV2  # type: ignore[assignment]
    pages: list[PageV2]  # type: ignore[assignment]
