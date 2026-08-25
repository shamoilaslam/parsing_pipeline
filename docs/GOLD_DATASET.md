# Specter gold dataset workflow

Do not manually annotate all 30,000 PDFs. Build a small, representative,
document-level gold set and use the remaining corpus for weak labels and
active-learning selection.

## Sampling plan

1. Profile every PDF with `build_gold_manifest.py`. The profiler records court,
   year, digital/scanned route, page geometry, RTL presence, table signals,
   template family, and available parser confidence.
2. Stratify by court, route, language, table presence, page template, and year
   bucket. Split by document, never by page, so pages from the same form do not
   leak between evaluation sets.
3. Select approximately 150-250 documents initially, with 3-5 pages per
   document. Fully annotate 15-25 difficult documents so paragraph merging and
   document reading order are tested end-to-end.
4. Add a hard-case set of 400-800 pages selected from low confidence,
   preprocessing disagreement, tables, Urdu, unusual geometry, blue-slip
   forms, blank/noisy scans, and long legal lists.
5. Hold out 20% of selected documents as a locked evaluation set. Never tune a
   threshold on the locked set.

The resulting manual workload is usually around 1,000-2,000 pages, not 30,000
documents. After the first release, rerun the sampler against new parser
outputs and annotate only newly discovered failure clusters.

## What an annotator records

For every selected page:

- page width/height and whether the page is excluded as a blue slip or blank;
- ordered blocks: `header`, `footer`, `heading`, `text`, `list`, or `table`;
- exact verified transcription and `[x0,y0,x1,y1]` block bbox;
- page-local reading order;
- table cells with row/column, row/column span, bbox, and exact text;
- RTL text as logical Unicode, while retaining the source crop reference.

Metadata and semantic fields should be annotated only for a smaller subset of
documents. They are derived labels, not a substitute for page-level text and
geometry truth.

Double-annotate 10-20% of pages, adjudicate disagreements, and keep an audit
trail of every correction. The gold JSON must never be overwritten by parser
output.

## Generate a manifest

```powershell
python build_gold_manifest.py data `
  --documents 250 `
  --pages-per-document 5 `
  --full-documents 20 `
  --output artifacts/gold/gold_manifest.json
```

Use `--predictions-root` after a parser run to boost low-confidence or
disagreement pages. Use `--render-selected` only for the selected pages, not
the complete corpus.

## Stage design

The proposed stages are useful, but they should be deterministic components,
not free-form agents:

- Structure normalization: high-precision fixes for repeated headers/footers,
  paragraph/list fragmentation, and heading levels; every change is logged.
- Table extraction: detect grid/rows/columns, OCR each cell, preserve spans,
  and never let table OCR replace surrounding body text.
- OCR retry: retry only low-quality regions with a different preprocessing
  variant; replace a block only when the candidate improves quality.
- Metadata extraction: regex/rule extractors with source spans and confidence.
- Semantic validation: validate and normalize citations, sections, FIRs,
  dates, and case numbers; never invent a value that is not present.

Raw OCR confidence should not be the sole selector. Text density is also not a
quality signal by itself because hallucinated or merged text can be dense. Use
confidence plus stability and geometry, and use dictionary/citation checks as
bounded validators:

```text
quality = 0.45 * OCR confidence
        + 0.20 * preprocessing/engine agreement
        + 0.15 * line continuity
        + 0.10 * language/script validity
        + 0.10 * text stability
```

Calibrate these weights on the gold set. A dictionary score should be capped
or used as a validation flag because party names, citations, and Urdu words
are routinely out of vocabulary.
