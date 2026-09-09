"""Chunking: a text algorithm over the flattened document, never over geometry.

Two strategies, chosen by what the document actually offers.

**A statute already states its own chunk boundaries.** A section is
self-contained, citable, and is the unit a judgment cites -- "302 PPC", not
"page 106". Measured over the 30,995 sections of the parsed federal code:
median 589 characters (~147 tokens), p95 2,854, and exactly one section over
8,192 tokens. So the section *is* the chunk, and only the 9.6% over the token
budget are split further, on their own sub-section markers.

**A judgment states boundaries less often.** 55% of Lahore High Court judgments
number their paragraphs, and where a number is printed it is both the natural
boundary and the citable unit ("para 15"). Where it is not, the text is packed
to a budget, broken at the latest sentence end that fits.

Both produce the same record, and both carry a `prefix` -- the act and section
heading, or the case and court -- which is prepended *for embedding only*. The
legal-RAG literature calls this summary-augmented chunking and finds simple
document context enough; it costs nothing, where late chunking would mean an
8,192-token forward pass per document on a CPU with no GPU. The prefix is kept
out of `text` so what is quoted back to a user remains what the page says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from rag.acts import court_name, short_form as act_short_form, short_form_from_slug
from rag.normalise import DocumentText

# A judgment argues in numbered paragraphs and a lawyer cites them by number.
PARAGRAPH_NUMBER = re.compile(r"(?:(?<=\s)|^)(\d{1,3})\.\s+(?=[A-Z“\"(])")
# Sentence ends, in preference order, for when no paragraph number is printed.
SENTENCE_END = re.compile(r"[.;:]\s+(?=[A-Z“\"(])")
# A statute's own sub-divisions, used only to split a section that is too long.
SUBSECTION = re.compile(r"(?:(?<=\s)|^)(?:\(\d{1,2}\)|\([a-z]{1,2}\))\s+")

DEFAULT_BUDGET = 512          # tokens; BGE-M3 accepts 8192, but see the module docs
DEFAULT_OVERLAP = 64
CHARS_PER_TOKEN = 4.0         # replaced by a real tokenizer when one is available


def approximate_tokens(text: str) -> int:
    """Token count without loading a tokenizer.

    Deliberately crude and deliberately explicit: every budget in this module is
    stated in tokens, and swapping in the BGE-M3 tokenizer changes only this.
    """
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass
class Chunk:
    """A retrievable passage with the provenance to cite it."""

    id: str
    document_id: str
    source: str
    document_kind: str
    text: str
    prefix: str                  # context for the embedder, not part of the law
    citation: str | None
    title: str | None
    structure: dict[str, Any]
    page_start: int | None
    page_end: int | None
    bboxes: list[list[float]]
    block_ids: list[str]
    flags: dict[str, Any]
    order: int
    char_start: int
    char_end: int

    def embedding_text(self) -> str:
        """What the embedder sees: the passage under its document's context."""
        return f"{self.prefix}\n\n{self.text}".strip() if self.prefix else self.text

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def boundaries(text: str, pattern: re.Pattern[str]) -> list[int]:
    """Character offsets where `pattern` allows a split."""
    return [match.start() for match in pattern.finditer(text)]


def pack(text: str, budget: int, overlap: int,
         stops: list[int], tokens: Callable[[str], int]) -> list[tuple[int, int]]:
    """Greedily fill chunks up to `budget`, breaking at the latest stop that fits.

    `stops` are offsets where a break is allowed, best first in the caller's
    order of preference. A passage with no stop inside the budget is cut at the
    budget rather than left oversized -- an over-long chunk is silently
    truncated by the model, which loses text without saying so.
    """
    if not text:
        return []
    limit = max(1, int(budget * CHARS_PER_TOKEN))
    step = max(1, limit - int(overlap * CHARS_PER_TOKEN))
    ordered = sorted(set(stops))
    out: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        if tokens(text[start:]) <= budget:
            out.append((start, len(text)))
            break
        hard = start + limit
        candidates = [stop for stop in ordered if start + 1 < stop <= hard]
        end = candidates[-1] if candidates else hard
        out.append((start, end))
        start = max(end, start + step) if not candidates else end
    return [(a, b) for a, b in out if text[a:b].strip()]


def _chunk_id(document_id: str, order: int) -> str:
    return f"{document_id}#{order:04d}"


def _emit(doc: DocumentText, start: int, end: int, order: int, *, prefix: str,
          citation: str | None, title: str | None, structure: dict[str, Any],
          flags: dict[str, Any]) -> Chunk:
    where = doc.provenance(start, end)
    return Chunk(
        id=_chunk_id(doc.document_id, order), document_id=doc.document_id,
        source=doc.source, document_kind=doc.document_kind,
        text=doc.text[start:end].strip(), prefix=prefix, citation=citation,
        title=title, structure=structure, flags=flags, order=order,
        char_start=start, char_end=end, **where)


def chunk_statute(doc: DocumentText, payload: dict[str, Any], *,
                  budget: int = DEFAULT_BUDGET, overlap: int = DEFAULT_OVERLAP,
                  tokens: Callable[[str], int] = approximate_tokens) -> list[Chunk]:
    """One chunk per section; a section over budget splits on its sub-sections."""
    document = payload.get("document") or {}
    metadata = document.get("metadata") or {}
    act = metadata.get("title") or doc.document_id
    short = act_short_form(metadata.get("title")) or short_form_from_slug(doc.document_id)
    # A section's span runs from its own marker block to the next marker's.
    at = {segment.block_id: segment.start for segment in doc.segments}
    markers = [entry for entry in (document.get("structure") or [])
               if entry.get("kind") in {"section", "definitions"} and entry.get("block_id") in at]
    markers.sort(key=lambda entry: at[entry["block_id"]])
    chunks: list[Chunk] = []
    for index, marker in enumerate(markers):
        start = at[marker["block_id"]]
        end = at[markers[index + 1]["block_id"]] if index + 1 < len(markers) else len(doc.text)
        body = doc.text[start:end]
        heading = (marker.get("title") or "").strip()
        # A section is cited as "302 PPC" where the act has a conventional
        # abbreviation, and by name where it does not.  The parsed document does
        # not carry the abbreviation -- it is a property of the act, not of the
        # page -- so it is resolved from the printed title.
        citation = (f"{marker.get('number')} {short}" if short
                    else f"section {marker.get('number')} of {act}")
        prefix = f"{act} — section {marker.get('number')}" + (f": {heading}" if heading else "")
        stops = [start + offset for offset in boundaries(body, SUBSECTION)] or \
                [start + offset for offset in boundaries(body, SENTENCE_END)]
        for piece_start, piece_end in pack(body, budget, overlap,
                                           [s - start for s in stops], tokens):
            chunks.append(_emit(
                doc, start + piece_start, start + piece_end, len(chunks),
                prefix=prefix, citation=citation, title=act,
                structure={"kind": "section", "number": marker.get("number"),
                           "heading": heading or None,
                           "chapter": marker.get("chapter"), "part": marker.get("part")},
                flags={"sections_complete": metadata.get("sections_complete"),
                       "numbering_restarts": metadata.get("numbering_restarts")}))
    return chunks


def chunk_judgment(doc: DocumentText, payload: dict[str, Any], *,
                   budget: int = DEFAULT_BUDGET, overlap: int = DEFAULT_OVERLAP,
                   tokens: Callable[[str], int] = approximate_tokens) -> list[Chunk]:
    """Pack the judgment to budget, breaking at paragraph numbers where printed."""
    document = payload.get("document") or {}
    metadata = document.get("metadata") or {}
    case = metadata.get("case_number") or doc.document_id
    # The printed court name has six spellings in 300 Lahore judgments -- "IN THE
    # LAHORE HIGH COURT", "LAHORE HIGH COURT", "IN THE LAHORE HIGH COURT," --
    # and embedding all six spends the prefix on noise while making the field
    # useless as a filter.  `court_id` is the canonical form the router already
    # resolved; the readable name comes from the court registry.
    court_id = metadata.get("court_id")
    court = court_name(court_id) or metadata.get("court") or doc.source
    decided = metadata.get("decision_date_iso") or metadata.get("decision_date") or ""
    prefix = " — ".join(part for part in (court, case, decided) if part)
    numbers = boundaries(doc.text, PARAGRAPH_NUMBER)
    stops = numbers or boundaries(doc.text, SENTENCE_END)
    at_number = {offset: match.group(1)
                 for match, offset in ((m, m.start()) for m in PARAGRAPH_NUMBER.finditer(doc.text))}
    chunks: list[Chunk] = []
    for start, end in pack(doc.text, budget, overlap, stops, tokens):
        chunks.append(_emit(
            doc, start, end, len(chunks), prefix=prefix,
            # A judgment's citable name is its neutral citation; the reported
            # citation belongs to a private publisher and the court's own PDF
            # does not carry one, so it is not invented here.
            citation=metadata.get("neutral_citation"),
            title=metadata.get("case_title") or case,
            structure={"kind": "paragraph", "number": at_number.get(start),
                       "case_number": case, "court": court_id or doc.source},
            flags={"engine": document.get("engine"),
                   # Urdu is routed separately by the parser and a chunk that
                   # contains it needs saying so.  This read `block_type ==
                   # "table"` and called the result "rtl", which flagged tables
                   # and no Urdu at all.
                   "rtl": any(segment.contains_rtl for segment in doc.spans(start, end))}))
    return chunks


def chunk_document(payload: dict[str, Any], document_id: str, source: str,
                   **kwargs: Any) -> list[Chunk]:
    """Chunk a parsed document by what kind of document it is."""
    from rag.normalise import flatten

    kind = (payload.get("document") or {}).get("document_kind")
    doc = flatten(payload, document_id, source)
    if kind == "statute":
        return chunk_statute(doc, payload, **kwargs)
    return chunk_judgment(doc, payload, **kwargs)
