# Architecture and ownership

## Corpus entry point

`python -m specter parse <folder> --out <dir>` is the production ingestion
command. Per document it writes `<stem>.json` (canonical), `<stem>.md`, and
`metadata/<stem>.json`; per run it writes `manifest.json`.

Three properties exist because the run is unattended over tens of thousands of
documents:

* **A failed document does not end the run.** The exception is captured into
  `manifest.json` under `failures` and the loop continues.
* **Output names are assigned before any work is handed out.** A filename that
  is distinct across the run is kept; any other is replaced by one built from
  its folder and a digest of its path, and listed under
  `renamed_for_uniqueness`. Nothing is skipped for sharing a name -- 21,712 of
  IHC's 60,529 PDFs are called `judgment.pdf`.
* **`--skip-existing` resumes** without re-parsing, and rebuilds the manifest
  rows for already-finished documents from their metadata files, so a resumed
  run still describes the whole corpus rather than only the remainder.

JSON and Markdown stay at the output root rather than in `json/` and
`markdown/` sub-folders: block Markdown references `assets/` with relative
links, which a sub-folder would break. Those links are percent-encoded
(`_md_link`) because corpus filenames contain spaces and parentheses, and an
unencoded `)` terminates a Markdown link early.

## Components

| Component | Module | Responsibility |
| --- | --- | --- |
| Router | `specter.ingest_pdfs` | Classify digital, scanned, or mixed using page image coverage |
| Digital parser | `specter.specter_parser` | Native characters, spans, words, bboxes, paragraphs, tables, metadata |
| Scanned parser | `specter.scanned_parser` | Local rendering, preprocessing, OCR, tables, exclusions, confidence |
| Urdu adapters | `specter.urdu_ocr`, `specter.urdu_vision` | Optional isolated Urdu transcription; never required for English/native parsing |
| Validators | `specter.validate_document` | Read-only component diagnostics and retry recommendations |
| Evaluators | `specter.evaluate_*` | CER/WER, IoU, layout, reading order, OCR reports |
| Renderers | `specter.render_*` | Visual QA overlays and page inspection artifacts |

## Metadata on the scanned route

A scanned document has no native text layer, so its metadata is read from the
OCR output -- both the recognised text and the recognised page structure. The
digital route's extractors are reused rather than duplicated:
`_cover_fields_from_blocks` and `_cover_fields_from_table` need only a block's
`type`, `text`, and `bbox`, all of which OCR blocks carry, so `counsel` and
`hearing_date` come from the cover's own rows instead of a regex over the page.

`_metadata_provenance` runs on the scanned route too. Where a digital block
narrows a field's box to the spans carrying it, an OCR block has no style
spans, so `_narrow_to_spans` falls back to the per-line OCR boxes -- the finest
granularity the engine actually reports. The soundness rule is unchanged: a
field is only given a box when the region genuinely contains its value.

Consequence worth knowing: OCR line boxes are reported in PDF points, like
every other bbox in the document. They were previously emitted in render-pixel
space (`render_scale` times too large), which put any highlight drawn from
`line_results` off the page.

## Court registry

`specter/courts.py` holds one entry per court and is the only place that knows
what differs between them:

* `court_id` -- the canonical, filterable identifier
* `name` / `spellings` -- the canonical name and the variants seen on the page
* `path_pattern` / `path_from` -- where a corpus states the case in its path,
  and whether that is the filename or the folder above it
* `judge_from_folder` -- where it files by judge
* `sidecar` -- where it publishes a metadata record beside each document
* `case_number_format` -- how this court writes a case number

`canonical_name`, `court_id` and `path_labels` all dispatch through it, so
adding a court is an entry in `COURTS` rather than an edit in the parser, the
router and the benchmark. LHC, SC and IHC all go through it today; the statute
corpus is a document *kind* rather than a court.

## Supreme Court generalisation

The SC corpus lives at `<root>/<judge>/SCP_<type>.<number>_<year>_<date>.pdf`,
so the path itself states the case, its decision date and the judge for 789 of
790 files. The registry entry's `path_pattern` reads that.

Those labels are used two ways, and the split matters:

* `benchmark --sc-metadata` scores extraction **against** them, which is how SC
  metadata became measurable without waiting on annotation.
* `apply_path_labels` (called by the CLI, deliberately **not** by `parse_pdf`)
  fills a field only when extraction found nothing, tags it
  `source="filename"` with an empty bbox, and records every disagreement under
  `document.label_check`. Folding it into `parse_pdf` would make the benchmark
  score the filename against itself.

A label never overwrites a value found on the page. The scraper can be wrong
too, and a disagreement is more useful reported than silently resolved.

### Dates

`_decision_date` asks in order of how firmly the text states a date: an explicit
date of judgment, then pronouncement wording, then the hearing date. The hearing
date ranks that high on evidence -- in 30 of the 34 SC documents whose true
decision date appears in the text at all, it appears as the Date of Hearing.
This also corrected LHC, where the old rule picked the right year on only 33.7%
of documents.

A bare `dated <date>` was the last rung, on the reasoning that a document
stating one date states its own. It does not. On the 152 documents where it was
the rung that decided -- 23 at SC, 129 at IHC -- it was right once; what it
finds is the impugned order, an FIR, or an agreement recited in the facts. It
also pre-empted the ORDER SHEET's own structural date, which the page states
correctly in its form body. Removing it took IHC decision dates from 49.6% to
74.4%, and to 93.5% among the documents that state one at all.

A field left empty is what lets the corpus's own label fill it; a confidently
wrong value blocks the label and ships as fact.

### Consolidated case numbers

`CASE_RE` follows connector runs (`No.101 & 102-P of 2011`, `No.43 to 46/2023`)
so the year stays attached to the number. Without it a case number was ambiguous
across years.

## Islamabad High Court generalisation

The IHC corpus is `<root>/<year>_OfficialSite/<judge>/<case>/` holding
`judgment.pdf`, any interim `order_<ddmmyyyy>_<n>.pdf`, and a `meta.json`. The
record is far richer than a filename -- parties, bench, author, filing
category, and every hearing with its short order -- and it is treated as
exactly the same thing: a label. It fills a gap, tagged `source="sidecar"` with
no bbox; it never overwrites the page; disagreements go to `label_check`.

The whole record is preserved under `document.source_metadata`, verbatim, so
what the pipeline does not model yet is not lost.

Three IHC-specific decisions:

* **`document_kind` separates orders from judgments.** Two thirds of the PDFs
  are interim orders.
* **Party names are filled but never checked.** The corpus writes a display
  title -- "FOP etc", "MD, OGDCL etc" -- where the cause title prints the name
  in full. Comparing them reported a disagreement on 176 of 281 documents, none
  of them actionable. A check that fires on half a corpus is not a check.
* **Output names are assigned before anything is written.** 21,712 of 60,529
  IHC PDFs are called `judgment.pdf`. The router used to skip repeats and
  report them, which is honest but would have parsed an eighth of the corpus.
  `output_stems` keeps a filename that is already distinct -- so no LHC or SC
  path moves -- and otherwise builds one from the case folder plus a digest of
  the path, because the same case number recurs under different judges 7,544
  times.

Judges come from three places now, in order: the cover's `Present:` list, the
`NAME, J.-` (LHC, SC) or `NAME, J:-` (IHC) attribution that opens the opinion,
and the `(NAME)` / `JUDGE` signature at the foot. The signature was added
because on an order sheet it is the only statement of who decided -- there is
no cover and no attribution -- and without it the judge was unknown on 95% of
IHC documents.

## Statutes

A statute is a different kind of document, not another court. An Act has no
parties, no bench and no decision date; it has a title, an enactment number in
Roman numerals, a commencement date, a preamble, and a numbered run of
sections. `specter/statutes.py` reads those, and the router (`ingest_pdfs`)
decides the kind and drops the judgment-only fields rather than reporting them
empty -- a consumer should not have to know which fields are meaningless for
which kind of document.

Sections are the unit that matters: a lawyer cites section 302 of the Penal
Code, not page 14 of it. Each detected section, chapter, part and schedule
carries its page and bbox, so a citation resolves to a region of the page.

### Measuring it without annotation

A statute numbers its sections 1..N with no gaps, so a hole in the run is a
miss. That gives a corpus-wide correctness signal for free, the same way the
corpus file paths give free metadata labels. Over 60 statutes, complete runs
went 72% -> 83% -> 90% -> 93% -> **98%** as the causes below were fixed, and
the single remaining hole is a section the printed Act omits.

Three of those causes were not detection failures at all but classification
ones, and each was found by the metric rather than by reading code:

* **A schedule's own entries were counted as sections.** They restart at 1 and
  run past the last section, so the Provident Funds Act reported 11 missing
  sections that were listed banks. Numbered lines after a schedule heading are
  now `schedule_item` -- unless the heading precedes section 1 of the body, in
  which case it is the contents page announcing the schedule, and acting on it
  swallowed whole statutes.
* **A heading can start part-way through a block.** Section 28 of the
  Co-operative Societies Act follows section 27's closing words in the same
  text flow. Missed, its text is folded into section 27 and a reader asking for
  section 28 gets the wrong law. The page still marks it: the heading opens an
  emphasised run where what precedes it is plain, and the box recorded covers
  that run alone.
* **Two headings can share one line**, where a section is omitted in print and
  the next begins immediately (`9. 4[10. Power to make rules`).

The enactment number went from 60% to 93% on one character: the line is
routinely set as `1ACT No. LXIV OF 1975`, the `1` being a footnote marker, and
`` does not match between two word characters.

### Why detection reads the spans, not the text

An amended section is printed with its footnote marker hard against the number,
and every form of it was a real corpus failure:

| printed | read as | what separates them |
|---|---|---|
| `²11.` | section 211 | marker plain, number bold |
| `⁷19.` | section 719 | marker 8pt, number 12pt |
| `1[2.` | section lost | marker closes a bracket |
| `*11.` | section lost | symbol set in the same run |

On a flat string the marker and the number are indistinguishable. The spans
carry weight and size, so the rule is exact -- and it refuses to strip where
nothing distinguishes the digits, because a number merely split across spans is
not a footnote and section 12 must not become section 2.

The same styling requirement is what stops ordinary numbered prose ("3. is the
number of copies required") and the table of contents from being read as
sections.

## Canonical data flow

1. The router inspects page-sized image coverage. Hidden OCR text does not turn a scan into a digital document, and a document with neither a page-sized image nor any extractable text is routed to OCR rather than reported as an empty digital parse.
2. The digital parser reads PyMuPDF `rawdict`, preserving native text and geometry.
3. Line primitives are coalesced, then semantic paragraphs are merged. Distinct numbered paragraph prefixes are never merged.
4. Repeated top/bottom signatures are required before a running header/footer is classified.
5. Native table candidates are accepted only when their structure and fill are plausible. Low-fill aligned prose is left as ordinary text.
6. Page-one templates are classified, not treated as one generic "table" problem: a bordered cover grid or a borderless label/value layout both become `document.cover_fields` (label, value, bbox, page); an ORDER SHEET proceedings-log header is marked `form_header`, excluded from prose/markdown, and carries the one datum in its unruled body (`order_date`) recovered from the header's own column geometry, not by segmenting the body.
7. Span styling is carried through as `spans[].emphasis` (`bold`, `italic`, `underline`, `superscript`) and rendered into `markdown`. Underline comes from PyMuPDF's `char_flags`, the other three from `flags`; LHC judgments underline case citations, so this is the primary structural signal for citation extraction, and each emphasised span keeps its own bbox for grounding.
8. Every document receives a fingerprint, metadata provenance, block confidence, and validator profile.
9. Validators report `PASS` or `REVIEW`; they do not rewrite text.

## Text-layer trust

Native text is authoritative only where it is actually readable. Two failure
modes produce characters that look valid but are not, and a block hit by either
carries `text_status: {"status": "unreliable_native", "reasons": [...]}`:

- `private_use_glyphs` — the block is ≥30% Private Use Area codepoints. InPage
  and similar Urdu tools re-emit their own 8-bit ligature encoding into the PUA,
  so the characters are unmappable. The threshold clears symbol-font bullets,
  which measured under 10% in every case across 250 documents.
- `unreliable_font_encoding` — a span uses a Nastaleeq-family font. These render
  correct Urdu on screen but extract as transposed ligature clusters. Measured
  across 70 RTL documents: all 33 with broken Urdu use such a font and none of
  the 15 with clean Urdu does.

The flag never edits text. The characters and bbox are retained so the region
can be re-read by OCR/VLM later; the block simply must not be indexed or cited
until it is. Roughly 5% of the LHC corpus is affected. Ordinary fonts carrying
Urdu are deliberately left unflagged — quarantining text that is already correct
would waste VLM budget and lose real content.

## Metadata provenance

Every metadata field carries `{value, matched_value, page, bbox, confidence,
source}`, and the box is verified rather than assumed: it is only reported when
the region it names genuinely contains the value.

- The box is narrowed from the block to the **spans** actually carrying the
  value, so a date resolves to a single line rather than a whole paragraph.
- A value the extractor assembled from separate blocks (`X Versus Y`) is
  located by its parts and reported as their union, extended to include the
  separator so the box covers the whole caption.
- Matching is boundary-aware: section `13` will not match inside `2013`.
- A list field reports many values but one box, so `matched_value` names which
  one the geometry belongs to; `occurrences` carries the rest.
- A value that cannot be located reports `source: "no_source_span"` and an
  empty box. **A box that does not contain what it points at is worse than no
  box**, so nothing is guessed.

`cover_fields` feeds this directly: a labelled row states *which* date is the
hearing date, where a regex over the page can only guess among every date on
it. Counsel are read off the same rows as `{role, names, page, bbox}`. The
semantic filter for which rows count as metadata lives here rather than in the
structural extraction, because `cover_fields` legitimately reports every
two-column table it finds — including deposition tables that are structure but
not case metadata.

## The `text` / `markdown` contract

Every block carries both, and they are not interchangeable:

- **`text` is verbatim.** It reproduces the PDF's own characters and line breaks, including a word broken across a line (`sub- section`). It is the citation source of truth, the field the gold benchmark scores, and it never contains markup. Enrichment must never touch it.
- **`markdown` is the derived, readable view.** It carries emphasis markers, rejoins line-broken words (`sub-section`), flattens newlines inside table cells so rows stay valid, and prefixes headings by level.

This split is why the emphasis work costs zero CER: the benchmark reads `text`. It is also deliberate on the evidence — the gold transcription preserves the source's line breaks exactly, so a de-hyphenated `text` would diverge from ground truth. A consumer that wants clean tokens for embedding should read `markdown`; one that needs to quote or locate the source should read `text` and the span bboxes.

## Design rules

- Native PDF text is authoritative for digital documents.
- VLMs are not part of the digital parser.
- OCR is local and isolated to scanned/explicit Urdu fallback paths.
- No heuristic may delete a region without provenance.
- A reference output is not ground truth.
- Chunking must consume only a validated canonical document.
