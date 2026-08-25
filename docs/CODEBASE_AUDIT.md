> Historical: a point-in-time audit from before the SC work and the repository
> clean-up. Module names here may no longer exist. Current layout is in `README.md`.

# Specter Parsing Pipeline — Codebase & Data Audit

**Date:** 2026-08-18
**Purpose:** Ground-truth picture of what exists, what actually works, what's
documentation vs. reality, and a precise diagnosis of the table-parsing
problem, before any further build decisions.

---

## 1. TL;DR

- The repo is **not junk**. The `specter/` package is a coherent, small
  (~4,800 LOC), deterministic PDF parser with real tests, real docs
  (`docs/architecture.md`, `docs/evaluation.md`, `docs/PROJECT_REPORT.md`,
  `docs/GOLD_DATASET.md`), and an honest self-assessment of its own
  weaknesses. Whoever (Codex) built this was disciplined about scope and
  about not overclaiming.
- The **junk is confined to `artifacts/dump/` and `artifacts/legacy/`**
  (194 MB combined) — scratch batches from the gold-review process and
  superseded experiment runs. Safe to archive; nothing load-bearing lives
  only there (verified below).
- **The full corpus is not in this repo.** `data/pdfs/` holds 19 sample
  PDFs. The real corpus lives at `D:\Shamoil Data\specter_data\LHC`
  (9,512 files, confirmed present on disk) and an IHC/SC corpus that was
  profiled (19,726 files) but is **not currently at a locatable path** —
  worth confirming with you before planning IHC/SC work.
- **The gold benchmark is real but narrower than `docs/GOLD_DATASET.md`
  promises.** `artifacts/gold/final_review_corrections_v2.json` is
  **192 reviewed pages across 76 LHC documents, text-only** (corrected
  transcription + a discard flag). There is **no bbox, no table-cell, no
  reading-order ground truth anywhere in the repo yet** — the ambitious
  annotation schema in `GOLD_DATASET.md` was designed but never executed.
  This matters a lot for what you can and can't claim right now.
- **I found and confirmed the exact table bug you described**, with two
  concrete documents as evidence (see §4). It's not random — it's a
  specific, recurring LHC template (the "ORDER SHEET" proceedings table)
  that PyMuPDF's `find_tables()` handles differently from the ordinary
  "Date of hearing / For the Appellant / For the State" cover table.

---

## 2. What's actually built

### 2.1 Code inventory (`specter/`, ~4,800 lines)

| Module | Role |
|---|---|
| `ingest_pdfs.py` | Router: image-coverage based digital/scanned/mixed classification |
| `specter_parser.py` (857 lines) | Digital path: PyMuPDF `rawdict` → lines → paragraphs → tables → metadata → canonical JSON |
| `scanned_parser.py` (734 lines) | Scanned path: page render → RapidOCR (English) + local PP-OCR Arabic/Urdu model → tables via OpenCV ruled-line detection |
| `urdu_vision.py` (455 lines) | Budgeted VLM fallback for Urdu crops only (Gemini → OpenRouter free tier → LlamaParse), with local quota/queue files so it's resumable and never re-parses the PDF |
| `urdu_ocr.py` | Local Urdu OCR adapter |
| `validate_document.py` (290 lines) | Read-only diagnostics: reading order, header/footer consistency, table geometry, bbox containment, numbering continuity, metadata provenance — reports PASS/REVIEW, never rewrites text |
| `evaluate_document.py`, `evaluate_layout.py`, `evaluate_bidi.py`, `evaluate_scanned.py`, `evaluate_urdu.py` | CER/WER, IoU, layout F1, Kendall Tau, RTL/bidi checks |
| `build_gold_manifest.py` (326 lines) | Stratified sampler over the full corpus (by court/route/language/table/template/year) for gold-set selection |
| `render_layout_overlays.py`, `render_scanned_overlays.py` | Visual QA overlays for manual review |
| `inject_cells.py` | Post-hoc table-cell repair tool (see §4.3) |
| `benchmark_scanned_ocr.py`, `verify.py` | OCR preprocessing benchmarking, corpus verification |

**Design rules actually enforced in code** (confirmed by reading, not just
docs): native PDF text is authoritative for digital docs; no VLM in the
digital path; OCR is isolated to scanned/Urdu; nothing is deleted without
provenance; LlamaParse output is never treated as ground truth in the
evaluator.

### 2.2 Tests

7 unit tests, all passing (`test_parser.py`, `test_scanned_pipeline.py`,
`test_layout_evaluation.py`, `test_urdu_evaluation.py`). They are **contract
tests on synthetic fixtures** (e.g. "digital judgment has the expected
top-level keys," "blue slip title only appears on page 1," "ruled table
geometry is detected without form false-positive"). None of them assert
against a real corpus PDF, and **none test table cell/column correctness on
a real document** — so the bug in §4 would not have been caught by CI.

### 2.3 Data reality (this is the part that changes planning)

| Location | What it actually is |
|---|---|
| `data/pdfs/` | 19 sample PDFs (mixed LHC/other), used for dev iteration |
| `data/references/llamaparse/` | Only **2** LlamaParse reference JSONs exist (`2013LHC4394`, `2024LHC6559`) — not a broad reference set |
| `D:\Shamoil Data\specter_data\LHC\` | **9,512 real LHC PDFs**, confirmed present, profiled 100% (`gold_manifest.json` corpus_summary) |
| IHC/SC corpus | **19,726 PDFs profiled** per `gold_manifest_IHC_SC.json`, but the source path isn't currently mounted/found — flag this, don't assume it's available |
| `artifacts/gold/gold_manifest.json` / `gold_manifest LHC.json` / `gold_manifest_IHC_SC.json` / `gold_50.json` | Stratified sampling manifests (document + page selection with `hardness_score`, `template_key`, `stratum`), **not annotations** — `annotation.status` is `"pending"` / `gold_json: null` for the entries I checked |
| `artifacts/gold/final_review_corrections_v2.json` | **The actual gold data.** 192 pages, 76 LHC docs, fields: `text`, `original_text`, `discard`, `page_number`, `source_name`. Text-only. |
| `artifacts/dump/*` (batch1-4 html/json) | Scratch outputs from the manual review pass that produced the file above — a review UI export and its accept/correct batches |

**Corpus-wide profiling facts worth knowing** (from `gold_manifest.json`,
computed over all 9,512 LHC PDFs):

- 9,182 digital / 330 scanned / 1 mixed
- **8,238 of 9,512 (≈87%) are flagged `digital | no_urdu | table`** — tables
  on the digital path are the *majority* case, not an edge case, confirming
  your "almost always present" observation.
- IHC/SC skews much more scanned: 8,226 of 19,726 (42%) scanned vs. LHC's
  3.5% — if IHC/SC is in scope soon, your OCR/VLM cost model needs a
  different assumption than LHC.

**Gap vs. the plan in `docs/GOLD_DATASET.md`:** that document specifies
page-level bbox annotation, ordered blocks, table cells with row/column
spans and bboxes, RTL logical-vs-crop pairing, double-annotation of
10-20%, and a locked 20% holdout. **None of that was executed.** What
exists is a lighter text-correctness pass. This is not a criticism of past
work — stratified sampling + text correctness is a reasonable first slice —
but it means **no metric in this repo can currently support a claim about
bbox accuracy or table accuracy**, only about raw text CER/WER on the 76
sampled LHC documents. `docs/PROJECT_REPORT.md` already says this plainly
("table metrics are structural only... there is no independent table
ground truth") — the code's self-assessment and my independent read agree.

---

## 3. Architecture as it actually runs

```
PDF
 └─ ingest_pdfs.classify_pdf()      -- image-coverage per page
     ├─ digital   → SpecterParser (PyMuPDF rawdict, no API calls)
     ├─ scanned   → ScannedParser (RapidOCR local, no API calls)
     └─ mixed     → routed WHOLE document to ScannedParser (safety-first;
                     see §5.1, this is a real cost/quality tradeoff)
                     ↳ Urdu-script blocks only → urdu_vision.py budgeted
                       VLM fallback (Gemini → OpenRouter free → LlamaParse),
                       gated by local quota files, resumable, never touches
                       non-Urdu text
```

This matches what you described: PyMuPDF does the heavy lifting for
digital, OCR/VLM is reserved for scanned pages and the (small) Urdu
fraction, and nothing calls a paid model on the bulk digital corpus.

---

## 4. The table problem — diagnosed with evidence

### 4.1 Current strategy

`specter_parser.py` calls PyMuPDF's `page.find_tables()`, then applies a
fill-ratio gate: a candidate table is rejected (falls back to being read as
prose) if fewer than 65% of its detected grid cells contain text. This gate
exists specifically because PyMuPDF's table detector misreads aligned prose
as a table in some 2018-era documents — it's a deliberate, documented
tradeoff, not an oversight.

### 4.2 What's actually happening (confirmed on real files)

There are (at least) **two distinct first-page table templates** in the LHC
corpus, and PyMuPDF's grid detector treats them very differently:

**Template A — the bordered "cover" table** (`Date of hearing / For the
Appellant / For the State / For the Complainant`), seen in
`2024LHC6559.pdf`, `2015LHC625.pdf`, `2024LHC5682.pdf`, `2024LHC2628.pdf`.
This one works well — `find_tables()` returns the full multi-row grid, and
`_table_cells()` correctly attaches word-level geometry per cell. **This is
not the broken case.**

**Template B — the "ORDER SHEET" proceedings log**
(`Sr.No. of order/proceeding | Date of order/proceeding | Order with the
signature of the Judge...`), seen in `2025LHC5084.pdf` and
`2024LHC1690.pdf`. Here `find_tables()` **only recovers the header row** as
a table (1 row, 3 columns). Everything below the header — the actual
dated log entries, which are visually two/three columns wrapping across
many lines — is **not part of the detected table at all**, so it falls
through to ordinary line-based text extraction. Confirmed output for
`2025LHC5084.pdf`, page 1:

```
table  → ['S.No. of order/\nproceeding', 'Date of order/\nproceeding', 'Order with the signature...']   (header only, 1 row)
text   → '28.04.2025 Qazi Zafar Ullah Khan, Advocate, assisted by M/s Shahid'
text   → 'Mir, Asad Iqbal, and Qazi Shahid Rashid, Advocates, for the Petitioner.'
text   → 'Mr. Muhammad Amjad Pervaiz, Advocate General Punjab,'
...
```

The date column (`28.04.2025`, x≈144) and the order-text column
(x≈216, wraps for many lines) get flattened into single left-aligned text
blocks by y-position, with the column boundary lost entirely — this is
exactly the symptom in your screenshot: text that is visually a table
column ends up outside the table boundary, read row-wise by line position
instead of column-wise by structure.

**Root cause:** PyMuPDF's `find_tables()` is a ruled-line/whitespace-grid
detector. The order-sheet template usually has a ruled box around the
*header* but the body rows are separated by thin or partial rules (or none)
because each entry can be an arbitrary number of wrapped lines — so the
grid detector either doesn't extend the table past row 1, or the fill-ratio
gate rejects the larger candidate before it ever reaches `_table_cells()`.
Since this is the *first page* of nearly every judgment (matching your "99-100% on page 1" observation), it's high-value to fix specifically, not
generically.

### 4.3 A second, smaller finding: cell text and cell geometry already
disagree internally

In `_table_cells()` (`specter_parser.py:325-361`), word-level bboxes are
already computed correctly by geometric containment (`cell_words`), but the
canonical `text` field for each cell is taken from
`table.extract()`'s own row/column grouping, not from the geometrically
verified words. When PyMuPDF's internal `extract()` misassigns a word to
the wrong logical row/column (which is a known PyMuPDF behavior on
multi-line cells), the JSON's cell `text` can disagree with its own
`words` list, silently. This is a small, targeted, high-confidence fix
once the bigger template-B problem is addressed — worth fixing in the same
pass rather than separately. (`inject_cells.py` in the repo already exists
as a post-hoc cell-repair script, suggesting this was noticed before but
not folded back into the main parser.)

### 4.4 What this means for the fix (not doing it yet, just scoping)

- This is **not** "PyMuPDF tables are broken," it's "PyMuPDF tables are
  reliable for bordered key-value grids and unreliable for the specific
  order-sheet log template." A template-aware second pass (detect the
  `Sr.No./S.No. ... Order with the signature` header pattern, then build
  the table geometrically from column x-bands of the text below it, the
  same way `_table_cells()` already does word-to-cell assignment) is a
  bounded, deterministic fix — no VLM required, consistent with your
  budget constraint.
- Because tables are ~87% present and almost always page-1-only, a
  template-specific fallback is worth far more than a generic
  borderless-table algorithm right now.

---

## 5. Other confirmed weaknesses (cross-checked against `docs/PROJECT_REPORT.md`)

The project's own report already lists these honestly; I verified them
rather than just trusting the doc:

1. **No multi-page table continuation.** `2024LHC4594.pdf`'s
   "Appellant by / Respondent by" table splits across pages 1-2 and is
   emitted as two unrelated table blocks — confirmed in the JSON.
2. **Merged cells always `row_span=1, column_span=1`** — confirmed in
   `_table_cells()`; no spanning logic exists.
3. **Metadata extraction is regex/rule-based**, openly heuristic, not
   calibrated. Confirmed in `_extract_metadata()` — court/judge/party/date
   extraction is pattern matching over the first ~12,000 characters, no
   source-span-verified linking beyond a best-effort anchor.
4. **Mixed-mode PDFs are routed entirely to the scanned/OCR path**, even
   for their digital pages (`ingest_pdfs.classify_pdf`, `mode = "mixed_scanned_first"`) — a deliberate safety choice, but it means any digital
   page in a mixed doc loses native-text fidelity. Worth revisiting once
   volume increases, since only 1 of 9,512 LHC docs is currently "mixed,"
   but IHC/SC's much higher scanned rate may make this matter more there.
5. **No independent bbox or reading-order ground truth exists** (see §2.3)
   — so `layout_f1`/`mean_iou` release gates defined in
   `docs/evaluation.md` cannot currently be evaluated against anything
   except the two LlamaParse reference files, which the project's own
   evaluator explicitly says are not ground truth.

---

## 6. Junk vs. signal in `artifacts/`

| Path | Size | Verdict |
|---|---|---|
| `artifacts/digital/` | 20 MB | **Current** — keep, this is the live digital batch output |
| `artifacts/scanned/` | 80 MB | **Current** — keep, includes `final/` (production) and `experiments/` (preprocessing variant tests, e.g. otsu/sauvola — useful provenance for OCR tuning, low priority to clean) |
| `artifacts/gold/` | 49 MB | **Keep everything** — manifests, benchmark, and the corrections file are the only real ground truth you have |
| `artifacts/legacy/` | 114 MB | **Safe to archive.** Contains `digital_final`, `specter`, `specter_batch`, `urdu_rapid`, `loose`, `page_images` — all superseded by `artifacts/digital/` and `artifacts/scanned/`. `docs/PROJECT_REPORT.md` confirms only disposable pre-production dirs were already deleted; this is what's left and it's provenance, not current state. |
| `artifacts/dump/` | 80 MB | **Safe to archive.** Batch review HTML exports and intermediate correction batches that were already merged into `final_review_corrections_v2.json`. Keep zipped somewhere for audit trail of the annotation process, but it shouldn't stay in the active working set. |

Nothing in `dump/` or `legacy/` is a dependency of any code path — I
checked imports/usages, not just directory names.

---

## 7. Cost model sanity check (matches your stated constraints)

- Digital path: zero API cost, zero GPU (PyMuPDF only).
- Scanned path: zero API cost, CPU-only (RapidOCR + a local Arabic/Urdu
  PP-OCR model in `models/rapidocr_arabic/`).
- Urdu-in-digital fallback: budgeted against **free tiers only**
  (Gemini free tier, OpenRouter free router, LlamaParse as a last resort),
  with local quota-tracking files so a run can be resumed without
  re-spending budget. This is consistent with "we cannot afford VLM calls
  on the full corpus" — the only paid/VLM surface is intentionally the
  smallest one (Urdu blocks only, ~153 blocks in the 13-doc sample run).

No part of the current design calls a VLM for table repair, scanned
routing, or digital extraction — so fixing the table issue deterministically
(§4.4) keeps that cost profile intact. Introducing VLM-based table repair
would be the first thing to break that budget, which is presumably why it
hasn't been done.

---

## 8. Open questions for you before deciding next steps

1. Where does the IHC/SC raw PDF corpus (19,726 files, already profiled)
   actually live right now? It wasn't at the path recorded in
   `gold_manifest_IHC_SC.json` when I checked.
2. Is LHC-only production readiness the near-term goal, with IHC/SC
   deferred, or do you want table/metadata fixes designed to generalize to
   IHC/SC templates from the start?
3. For the table fix: are you OK with a template-detection approach (a
   handful of known first-page table shapes get dedicated geometric
   handling) rather than a fully generic table algorithm? Given tables are
   ~87% present but concentrated on page 1 with a small number of recurring
   templates, template-aware beats generic-but-imperfect for your
   cost/time budget.
4. Do you want to invest in bbox/table-cell gold annotation next (even a
   small stratified set, per `GOLD_DATASET.md`'s own plan) before or after
   the table-parsing fix — right now there's no way to *measure* whether a
   table fix actually improved bbox accuracy, only to eyeball it.

This report makes no code changes. Once you've reviewed it, tell me which
thread to pull first (table fix, bbox gold set, metadata/citation
grounding, or the IHC/SC corpus question) and I'll scope that
specifically.
