"""``specter.v2``: ``specter.v1`` plus the fields ``docintel.schema.v2`` adds.

Each subclass only adds; nothing from v1 is changed.  Fields are optional
here until every commit of the v2 series has landed.
"""

from __future__ import annotations

from docintel.schema.specter_v1 import Block, Document, Page, SpecterV1


class BlockV2(Block):
    uid: str | None = None


class PageV2(Page):
    blocks: list[BlockV2]  # type: ignore[assignment]


class DocumentV2(Document):
    id: str | None = None


class SpecterV2(SpecterV1):
    document: DocumentV2  # type: ignore[assignment]
    pages: list[PageV2]  # type: ignore[assignment]
