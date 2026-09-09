"""One record shape, whatever court or code the document came from.

A retriever should not know that IHC publishes a ``meta.json``, that SC encodes
its case number in the filename, or that a statute has sections where a
judgment has paragraphs.

The central idea here is the **offset map**. A parsed document is flattened to
one string, and every character of it remembers which block, page and bounding
box it came from. Chunking then runs on plain text -- any algorithm, structural
or recursive or semantic -- and a chunk's character span maps back to exact page
regions.

That matters because of how the corpus is actually laid out. Justified text
makes PyMuPDF return a line as several blocks: a judgment line comes back as
'and' | 'recovery' | 'of' | 'certain'. Before the parser learned to join such
fragments, 16.8% of blocks shared a line with a neighbour and carried 4.9% of
all characters; it is 6.8% and 2.4% now, the rest being printed columns that
must not be joined. Chunking *on blocks* would therefore
cut sentences into single words, and an earlier attempt to repair that with
geometry -- same-line joins, indent and gap thresholds -- both misread
two-column tables and needed constants tuned against the corpus.

None of it is necessary. Concatenating block text with single spaces already
yields correct continuous prose, which is why the page-text benchmark is
unaffected by the fragmentation. The offset map keeps provenance without ever
asking geometry where a paragraph begins.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Blocks that are on the page but are not the document's own words.
NOT_CONTENT = frozenset({"header", "footer", "footnote"})
COVER_PAGE = 1
JOIN = " "


@dataclass(frozen=True)
class Segment:
    """A run of the flattened text, and where on the page it was printed."""

    start: int
    end: int
    block_id: str
    page: int
    bbox: tuple[float, float, float, float]
    block_type: str
    contains_rtl: bool = False


@dataclass
class DocumentText:
    """A document as one string, with every character's provenance."""

    text: str
    segments: list[Segment]
    document_id: str
    source: str
    document_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    _starts: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._starts = [segment.start for segment in self.segments]

    def spans(self, start: int, end: int) -> list[Segment]:
        """Every segment the character range [start, end) touches.

        Bisect rather than a scan: a chunker asks this once per chunk, and a
        long judgment has thousands of segments.
        """
        if start >= end or not self.segments:
            return []
        first = max(0, bisect_right(self._starts, start) - 1)
        last = bisect_left(self._starts, end)
        return [segment for segment in self.segments[first:last]
                if segment.start < end and segment.end > start]

    def provenance(self, start: int, end: int) -> dict[str, Any]:
        """Pages, boxes and block ids for a character range."""
        touched = self.spans(start, end)
        if not touched:
            return {"page_start": None, "page_end": None, "bboxes": [], "block_ids": []}
        return {
            "page_start": min(segment.page for segment in touched),
            "page_end": max(segment.page for segment in touched),
            # Every region the chunk covers, not a union box: a chunk crossing a
            # page break has no single rectangle, and a union would point at
            # text the chunk does not contain.
            "bboxes": [list(segment.bbox) for segment in touched],
            "block_ids": [segment.block_id for segment in touched],
        }


def source_of(path: Path) -> str:
    """Which corpus a parsed file came from, read from its own source path."""
    parts = {part.lower() for part in path.parts}
    for name in ("pakistancode", "lhc", "sc", "ihc"):
        if name in parts:
            return "pakistancode" if name == "pakistancode" else name.upper()
    return "unknown"


def content_blocks(page: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The blocks on a page that are the document's own words.

    Headers, footers and footnotes are excluded: a page footer inside a section
    of the Penal Code would be quoted back as law, and an amendment note names
    the amending Act rather than stating the rule.
    """
    for block in page.get("blocks", []):
        if block.get("type") in NOT_CONTENT or block.get("decorative"):
            continue
        if not (block.get("text") or "").strip():
            continue
        yield block


def flatten(payload: dict[str, Any], document_id: str, source: str,
            skip_cover: bool = False) -> DocumentText:
    """A parsed document as one string that remembers where each part was printed."""
    document = payload.get("document") or {}
    pieces: list[str] = []
    segments: list[Segment] = []
    cursor = 0
    for page in payload.get("pages", []):
        if skip_cover and page["page_number"] == COVER_PAGE:
            continue
        for block in content_blocks(page):
            text = (block.get("text") or "").strip()
            box = block.get("bbox") or [0.0, 0.0, 0.0, 0.0]
            if pieces:
                pieces.append(JOIN)
                cursor += len(JOIN)
            segments.append(Segment(start=cursor, end=cursor + len(text),
                                    block_id=block.get("id", ""),
                                    page=page["page_number"],
                                    bbox=tuple(float(v) for v in box[:4]),
                                    block_type=block.get("type", "text"),
                                    contains_rtl=bool(block.get("contains_rtl"))))
            pieces.append(text)
            cursor += len(text)
    return DocumentText(text="".join(pieces), segments=segments, document_id=document_id,
                        source=source, document_kind=document.get("document_kind", "unknown"),
                        metadata=document.get("metadata") or {})
