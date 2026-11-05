"""The ``specter.v1`` output contract, written down.

Until now the contract was whatever the two parsers happened to build.  This
model states the part of it every consumer relies on -- the fields ``rag``, the
benchmark, the inspector and the metadata writer read -- and checks their types.

Strictness is chosen per level, on what varies legitimately:

* **Leaves are closed** (``extra="forbid"``): a span, a table cell, a
  confidence record.  Each has one constructor, so an unknown key there is a
  bug, not a variant.
* **Blocks, pages and the document are open** (``extra="allow"``): enrichment
  adds keys to them by design -- ``structure`` and ``citations`` on the
  document, ``rtl.ocr`` on a block after ``specter urdu``, ``scan`` on a
  scanned page.  Closing them would turn every enrichment into a schema break.

Nothing here changes output.  It validates what is written; it does not build it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# [x0, y0, x1, y1] in PDF points, origin top-left (PyMuPDF's convention).
BBox = Annotated[list[float], Field(min_length=4, max_length=4)]

BlockType = Literal["text", "list", "heading", "header", "footer", "footnote", "table", "form_header"]
PageExtraction = Literal["digital", "scanned_or_image_only", "scanned", "excluded", "native_text_pending"]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Emphasis(_Closed):
    # Only the flags that are set are stored.
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    superscript: bool | None = None


class Span(_Closed):
    """A styled run from the native text layer (``_span_record``)."""

    text: str
    bbox: BBox
    font: str | None
    size: float
    flags: int | None
    script: str
    emphasis: Emphasis | None = None
    char_bboxes: list[BBox] | None = None  # right-to-left spans only


class Word(_Closed):
    text: str
    bbox: BBox


class NativeCell(_Closed):
    """A cell from PyMuPDF's table finder (``_table_cells``)."""

    row: int
    column: int
    row_span: int
    column_span: int
    bbox: BBox
    text: str
    words: list[Word]


class ScannedCell(_Closed):
    """A cell from the ruled-grid detector on a scan (``ScannedParser._table_blocks``).

    It carries no row or column index -- a known inconsistency with
    ``NativeCell`` that v2 removes.
    """

    bbox: BBox
    text: str
    confidence: float
    model: str


class Confidence(_Closed):
    score: float
    reasons: list[str]


class TextStatus(_Closed):
    status: Literal["unreliable_native"]
    reasons: list[str]


class Block(_Open):
    id: str
    type: BlockType
    text: str  # verbatim: the citation source of truth, never markup
    markdown: str  # derived, readable view
    bbox: BBox
    reading_order: int
    document_reading_order: int | None = None
    language: str
    source: str
    confidence: Confidence
    rtl: dict[str, Any]
    contains_rtl: bool | None = None
    decorative: bool | None = None
    spans: list[Span] | None = None
    line_bboxes: list[BBox] | None = None
    heading_level: int | None = None
    text_status: TextStatus | None = None
    start_index: int | None = None
    end_index: int | None = None
    rows: list[list[str]] | None = None
    cells: list[NativeCell | ScannedCell] | None = None


class Page(_Open):
    page_number: int
    width: float
    height: float
    rotation: int
    blocks: list[Block]
    markdown: str
    confidence: float
    extraction: PageExtraction


class Document(_Open):
    source_file: str
    source_name: str
    engine: str
    extraction_mode: str
    page_count: int
    metadata: dict[str, Any]
    confidence: float
    warnings: list[str]


class SpecterV1(_Closed):
    schema_version: Literal["specter.v1"]
    document: Document
    pages: list[Page]
    markdown: str
