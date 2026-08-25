> Historical: a point-in-time report. Current architecture is in
> `docs/architecture.md`, current numbers in `docs/evaluation.md`.

# Specter project report

## Scope of this run

This run processed only fully digital PDFs in `data/pdfs/`. The router classified 13 PDFs as digital, 5 as scanned, and 1 as mixed. The scanned and mixed documents were not sent through the digital parser.

The digital batch contains:

- 287 pages
- 2,464 semantic blocks
- 13 accepted tables
- 4 low-fill table candidates rejected as likely aligned prose
- 153 Arabic/Urdu-script blocks retained as native text plus rendered source crops
- 509 numbered blocks checked
- 12 documents with `PASS` diagnostics
- 1 document with `REVIEW` diagnostics because its source does not state a court name: `2025PCrlj57.pdf`

The generated batch is under `artifacts/digital/`. Every processed PDF has a JSON file, Markdown file, and validation report. `corpus_summary.json` is the batch manifest.

## Current architecture

```text
PDF
 |
 +- image-coverage router
 |    +- digital -> PyMuPDF native parser
 |    +- scanned -> local OCR route
 |    `- mixed -> scanned-first safety route
 |
 +- document fingerprint
 |    +- page size and variants
 |    +- estimated margins and text density
 |    +- font distribution
 |    +- repeated header/footer bands
 |    +- column estimate
 |    +- image/logo presence
 |    `- stable template key
 |
 +- native digital extraction
 |    +- rawdict characters, spans, fonts, and bboxes
 |    +- line primitives
 |    +- semantic paragraph merger
 |    +- numbered paragraph protection
 |    +- deterministic reading order
 |    +- repeated-band header/footer labels
 |    +- PyMuPDF native table extraction
 |    `- word and cell geometry
 |
 +- diagnostics and validators
 |    +- reading order
 |    +- header/footer consistency
 |    +- table geometry and fill
 |    +- block/span/character bbox containment
 |    +- paragraph geometry
 |    +- text continuity and duplicates
 |    +- numbered paragraph continuity
 |    `- metadata presence and provenance
 |
 `- canonical JSON + Markdown
      `- chunking is allowed only after the quality profile is accepted
```

The “agentic” part is intentionally a small deterministic controller. It reports the failed component and allows that component to be retried. It does not rewrite legal text, call a VLM, or silently repair a document.

## Output contract

Each JSON document contains:

- page and block geometry
- character bboxes for RTL spans and span/font metadata
- line bboxes and paragraph bboxes
- page and document reading-order indices
- block type, language, source, and heuristic confidence
- table rows, cells, cell bboxes, cell words, and Markdown/HTML forms
- document fingerprint and template key
- extracted metadata and metadata provenance
- router decision and extraction mode
- diagnostics in this form:

```json
{
  "status": "PASS",
  "failed_components": [],
  "quality_score": 0.99,
  "quality_profile": {
    "reading_order": 1.0,
    "headers_footers": 1.0,
    "tables": 1.0,
    "bboxes": 1.0,
    "text_continuity": 1.0,
    "paragraph_geometry": 1.0,
    "numbering": 1.0,
    "metadata": 1.0
  }
}
```

Metadata provenance currently includes `value`, `page`, `bbox`, `confidence`, and `source`. If a normalized value crosses multiple PDF blocks, the provenance layer uses an anchor and records that limitation instead of claiming an exact substring match.

## Corpus validation results

The following internal invariants passed for all 13 digital PDFs:

- reading-order indices are contiguous within each page and document
- paragraph bboxes enclose their line bboxes
- block, span, and character geometry remains inside page bounds
- text indices are monotonic
- repeated header/footer classification is based on cross-page evidence
- accepted tables are rectangular and have cell bboxes

Numbering is checked conservatively. Quoted judgments, statutory lists, and nested enumerations may restart numbering; those are grouped by indentation and font size. Four documents still have numbering warnings in their diagnostics, but their overall numbering score remains above the retry threshold. This is a warning channel, not an automatic text rewrite.

## Main weaknesses that remain

### 1. Tables are structurally improved but not generalized yet

The current primary strategy is PyMuPDF `find_tables()` with a fill-ratio gate. The gate removed four false table detections from the 2018 judgment where aligned prose had been interpreted as a table.

Remaining gaps:

- no independent whitespace-alignment fallback
- no borderless-table strategy
- no reliable multi-page table continuation model
- merged cells are currently represented conservatively as `row_span: 1` and `column_span: 1`
- table semantics are not understood; a schedule, party table, form, and judgment header are all just tables
- table metrics are structural only for this corpus; there is no independent table ground truth

This is the largest remaining digital weakness.

### 2. Metadata and legal semantics are still rule-based

The parser can extract likely courts, case numbers, dates, judges, parties, citations, statutes, and sections using regular expressions and local source text. This is useful, auditable, and non-hallucinatory, but it is not deep legal document understanding.

Specifically, it does not yet reliably determine:

- which date is judgment date versus hearing date, filing date, incident date, or cited-case date
- which person is judge, counsel, petitioner, respondent, complainant, or witness in every layout
- whether a citation is a holding, a quoted authority, or merely mentioned
- which section belongs to which statute when multiple statutes occur nearby
- normalized court, judge, party, and citation identities
- procedural stage, relief, issues, findings, ratio, or final disposition
- relationships between metadata entities and their source spans

The current confidence values are heuristic. They are not calibrated probabilities and should not be interpreted as legal truth. Metadata fields need a source-span matcher with word-level anchors and a rule-based semantic validator before they are used as authoritative search facets.

`2025PCrlj57.pdf` is intentionally marked `REVIEW`: the source carries a `[Balochistan]` digest tag but does not explicitly state a court name. The parser does not invent “Balochistan High Court.”

### 3. Fingerprints are descriptive, not yet strategy selectors

The fingerprint now records page geometry, margins, fonts, text density, columns, images, and repeated bands. It produces a stable template key, but the key is not yet used to select or reuse a court/template-specific strategy. That should be added only after enough labeled examples exist; otherwise similar-looking templates could cause silent overfitting.

### 4. Header/footer handling has safe defaults, not complete semantic classification

Repeated signatures and page bands are reliable for running headers, running footers, and page numbers. Page-one titles and page-specific annotations are deliberately retained rather than deleted.

Remaining cases include footnotes, legal annotations at the bottom of a page, appendices with a different header, and a body paragraph that happens to begin near a page band. These require a gold set and template-aware rules. They should never be removed solely because they appear near a margin.

### 5. Reading order is strong on this corpus, but not universal

The current geometry order passed all internal checks. The two-column fallback is deterministic, but unusual layouts can still require work:

- marginal notes and sidebars
- footnotes interleaved with body text
- rotated blocks
- nested quotations with independent indentation
- multi-column tables and forms
- documents containing several judgments or orders in one PDF

The current result proves ordering invariants, not universal semantic reading order. Independent page-level ground truth is still required.

### 6. Confidence is not calibrated

Block confidence is derived from native extraction, source type, layout classification, and table detection. Document confidence is an aggregate heuristic. It should eventually be calibrated against annotated errors and reported separately from the validator quality profile.

### 7. Evaluation coverage is incomplete

`2024LHC6559.json` from LlamaParse remains a reference output, not ground truth. The other 12 digital PDFs currently have no independent English ground truth, so corpus-wide CER, WER, and bbox IoU cannot honestly be reported yet. The gold manifest and annotation process are therefore prerequisites for production claims.

For transparency, the final 2024 output was compared with the existing LlamaParse reference. Reading-order Tau is `1.0` and match coverage is `0.978`, but strict one-to-one block metrics are `layout F1 0.695`, `mean IoU 0.796`, `CER 0.310`, and `WER 0.324`. This regression is primarily a segmentation mismatch: the corrected parser keeps adjacent numbered legal paragraphs separate, while the reference sometimes groups them. It is evidence that the reference is not a reliable gold segmentation target, not evidence that the parser should merge legally distinct paragraphs. A segmentation-aware evaluator and independent gold annotations are required before using these metrics as release gates.

## Recommended next implementation order

1. Build a small, stratified digital gold set across courts, templates, tables, quotations, Urdu blocks, and multi-document PDFs.
2. Add exact word/span provenance for metadata and normalize dates, parties, courts, citations, statutes, and sections.
3. Add merged-cell inference and a validator-gated whitespace/borderless table fallback.
4. Add template-key strategy selection only when repeated fingerprints have confirmed examples.
5. Calibrate block and component confidence against the gold set.
6. Add chunking only after a document passes the required reading-order, bbox, table, and metadata gates.

## Reproducible commands

```powershell
python -m specter digital --input data\pdfs --out artifacts\digital --rtl-mode image
python -m specter validate artifacts\digital\2024LHC6559.json
python -m unittest -v
```

## Cleanup performed

Only disposable generated experiment directories from the pre-production layout were removed after verifying their exact locations:

- `digital_check`
- `digital_check2`
- `digital_improved`
- `digital_improved2`
- `digital_improved3`

Source PDFs, parser engines, tests, gold manifests, scanned outputs, the main `specter` artifacts, and the finalized `digital_corpus` were preserved.
