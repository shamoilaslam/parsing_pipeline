# Evaluation policy

## Internal validation

Every canonical document reports:

- reading-order continuity
- header/footer consistency
- table rectangularity, fill, and cell bboxes
- page/block/span/character bbox containment
- paragraph geometry
- text continuity and duplicate blocks
- numbered paragraph continuity
- metadata presence and provenance

These are invariant checks. They do not prove that the PDF text itself is correct.

## Gold benchmark (primary release gate)

`specter/benchmark.py` scores the parser against the 191 manually reviewed LHC
pages in `artifacts/gold/final_review_corrections_v2.json`. Gold is recorded per
page, so the parser's blocks are concatenated before scoring: the metric measures
text fidelity and cannot be gamed by merging or splitting paragraphs.

```powershell
python -m specter benchmark --output artifacts/benchmark/latest.json
python -m specter benchmark --baseline artifacts/benchmark/baseline.json
```

Results are reported per slice, never blended. The slices fail for different
reasons and need different fixes; one average would hide that a scanned page has
no text layer at all while an ordinary page is already exact.

Baseline, 2026-08-19 (`artifacts/benchmark/baseline.json`, pre-Phase-2):

| Slice | Pages | CER mean | CER median | Exact |
| --- | --- | --- | --- | --- |
| regression | 106 | 0.0012 | 0.0000 | 82 |
| table | 38 | 0.0115 | 0.0027 | 5 |
| scanned | 32 | 0.9990 | 1.0000 | 0 |
| urdu | 8 | 0.1162 | 0.1014 | 0 |
| text_edit | 7 | 0.1928 | 0.1275 | 0 |

After Phase 2 (`artifacts/benchmark/phase2.json`: page-1 template classification,
cover-field extraction, ORDER SHEET header stripping):

| Slice | Pages | CER mean | CER median | Exact |
| --- | --- | --- | --- | --- |
| regression | 106 | 0.0011 | 0.0000 | 86 |
| table | 38 | 0.0121 | 0.0027 | 5 |
| scanned | 32 | 0.9990 | 1.0000 | 0 |
| urdu | 8 | 0.1162 | 0.1014 | 0 |
| text_edit | 7 | 0.1953 | 0.1314 | 0 |

| Gold table | Pages | Found | Columns match | Cell recall |
| --- | --- | --- | --- | --- |
| cover | 8 | 8 | 8 | 0.7974 |
| order_sheet | 34 | 29 | 0 | 0.0000 |

False positives: 4 of 149 non-table pages received a table (unchanged).

Things this table must not be misread as:

- `scanned` CER 0.999 is not a parser defect. Those 32 pages have no text layer,
  so the digital route correctly returns nothing; they are the OCR route's
  responsibility and exist here as ground truth for it.
- `order_sheet` "found" now counts `form_header` blocks, not `table` blocks:
  Phase 2 deliberately stopped classifying the ORDER SHEET header as a table
  (it is boilerplate, stripped from content) and instead extracts the one real
  datum in its body geometrically (`order_date`). Detection rate (29/34) is
  unchanged from baseline; `columns match`/`cell recall` are diagnostic only
  and expected to read near zero, because the gold wraps the proceedings body
  into the header's rows and that body is ordinary prose, not a table.
- `table`/`text_edit` moved by ~0.0005-0.0025 CER, all traced to a handful of
  pages where separating a case-caption's widely spaced fragments (fixing the
  root cause behind the cover/table work) exposed a pre-existing sort-order
  weakness for same-row content; see the reading-order fix below. No page
  lost or gained text; only token order shifted by one or two positions.
- `regression` improved (82 -> 86 exact matches): the coalescing and
  reading-order fixes also correct latent errors outside the pages Phase 2
  targeted.

After Phase 3 (`artifacts/benchmark/phase3.json`: span emphasis, markdown
rendering, de-hyphenation, heading levels, valid table cells) **every slice is
bit-identical to Phase 2** — CER 0.0011 / 0.0121 / 0.9990 / 0.1162 / 0.1953,
unchanged to four decimal places.

That is the intended result, not a null one. Phase 3 enriches `markdown` and
`spans[].emphasis` and leaves `text` byte-verbatim, and the benchmark scores
`text` (see the `text`/`markdown` contract in `architecture.md`). The change is
verified by separate checks rather than by CER:

| Check | Result |
| --- | --- |
| Documents carrying emphasis spans | 240 of 241 |
| Emphasis silently dropped by the round-trip guard | 0 of 391 emphasised blocks |
| `markdown` vs `text` content divergence | 0 |
| Markup leaked into `text` | 0 (4 blocks contain literal `*` that is genuinely in the PDF) |
| Table rows invalid as markdown | 0 (was: every multi-line cell) |
| Headings emitting both levels | yes (40 h1 / 17 h2 across 30 documents) |

Deliberately deferred: the footnote/`footer` block class. **0 of 25 gold
documents contain a single superscript span** — inlined footnote markers were a
trait of `2024LHC6559.pdf`, not of the LHC corpus — so footnote-boundary logic
would have been built for no measurable gain.

Also settled on evidence: **`text` must not be de-hyphenated.** Gold contains 16
occurrences of `word- word` after normalisation and the parser produces 15; they
agree. Gold preserves the source's line break, so rejoining in `text` would
diverge from ground truth. The rejoined form lives in `markdown` instead.

After the text-layer trust gate (`artifacts/benchmark/phase1gate.json`) **every
slice is again bit-identical** — the gate annotates blocks and never edits text.
Verified separately:

| Check | Result |
| --- | --- |
| Gold Urdu pages carrying a flag | **8 of 8** — every page where the parser output is known garbage |
| Known-broken documents flagged (4 sampled) | every RTL block: 18/18, 28/28, 36/36, 1/1 |
| Known-clean documents flagged (4 sampled) | 0 of 5, 2, 7, 3 blocks — no over-flagging |
| `urdu_2021LHC2033.pdf` (86% PUA) | 3,062 of 3,467 blocks flagged |
| Discrimination inside one document | `2025LHC495.pdf`: 1 of 10 RTL blocks flagged; the other 9 are English quoting `ﷺ` |
| Scanned gold documents routed correctly | 9 of 9 (was 5 of 9) |
| Flagged blocks that lost text or bbox | 0 |
| Corpus-wide flag rate | ~495 documents, 5.2% of the corpus |

The router fix is worth noting separately: `classify_pdf` returned `digital`
whenever no page-sized image was found, **even with zero extractable text**, so
a scan whose pages fell just short of the image test produced a confidently
empty document. Four of the nine scanned gold documents hit this. The planned
CamScanner-stub fix turned out unnecessary — 0 stub-only pages across 250
documents, and `2019LHC5053.pdf` already routed correctly via image coverage.

After Phase 4 (`artifacts/benchmark/phase4.json`: structure-first metadata with
verified bbox provenance) **every slice is again bit-identical** — metadata is
not scored by the text benchmark.

Note that the gold set carries page transcriptions, not metadata annotations,
so per-field precision/recall against a gold answer is not available. Two
checks stand in for it, and both are stronger than they sound because neither
can be satisfied by a plausible-looking wrong answer:

1. **Provenance soundness** — extract the text at each reported bbox and
   confirm it contains the value. Self-verifying, no annotation needed.
2. **Value grounding** — confirm the value also appears in the human-verified
   gold page text, so a hallucinated field cannot pass.

Across the 76 gold documents:

| field | present | bbox contains value | median box height | value found in gold text |
| --- | --- | --- | --- | --- |
| court | 54 | 54 | 1.1 lines | 46/54 |
| bench | 33 | 33 | 1.0 | 29/33 |
| case_number | 65 | 65 | 1.0 | 61/65 |
| decision_date | 62 | 62 | 1.0 | 54/62 |
| hearing_date | 22 | 22 | 1.0 | 21/22 |
| judges | 13 | 13 | 0.9 | 12/13 |
| parties | 65 | 65 | 3.1 | 56/65 |

Before this phase, `parties` resolved to a correct box in **5 of 66** cases and
`decision_date` boxes were whole paragraphs. Corpus-wide on 250 documents:
**1,833 fields located, 0 with a box that does not contain its value**, 293
counsel rows all carrying geometry, 0 errors. 257 fields are present but
unlocated and honestly report `no_source_span` rather than a guessed box.

Coverage also improved where structure beat the regex: `decision_date` 57 → 62
(the ORDER SHEET states its date in the form body, not in the "decided on"
phrasing), `hearing_date` 18 → 22 with cover-row agreement rising 16/21 → 20/21,
and `counsel` is new — 139 of 240 documents, 293 rows.

Four real bugs surfaced during verification, each found by a measurement rather
than by reading code: `parties` synthesised the connector `"Versus"` so the
value could never be quoted or located; a bare `v.` deep in the body was read as
the caption (a cited case, not this case's parties); a list field's box could
belong to a different element than the one reported; and substring matching
located section `13` inside the year `2013`.

## Statutes, scored by their own numbering

A statute needs no annotation either, and for a stronger reason than the
label corpora: an Act numbers its sections 1..N with no gaps, so a hole in the
run is a detection miss and nothing else can produce one. The check cannot be
satisfied by a plausible-looking wrong answer.

Over 60 statutes sampled from `pakistancode`, 0 parse errors:

| field | rate |
| --- | --- |
| title | 93% |
| statute_kind | 97% |
| act_number | 93% |
| commencement_date | 90% |
| long_title | 95% |
| has_preamble | 85% |
| **complete 1..N section run** | **57 of 58 = 98%** |

The one remaining hole is section 1 of the Works of Defence Act 1903, which the
published text does not contain.

Complete runs went 72% -> 83% -> 90% -> 93% -> 98%. The last two steps were
classification rather than detection, and both were found by this metric:
schedule entries were being counted as sections (the Provident Funds Act
reported 11 missing sections that were listed banks), and a section heading can
begin part-way through a block or share a line with the previous one.

`act_number` went 60% -> 93% on one character: the line is routinely printed as
`1ACT No. LXIV OF 1975` -- the `1` is a footnote marker -- and `` does not
match between two word characters, so every statute setting it that way was
skipped silently.

## Corpora with no gold: scoring against the labels the corpus states

There is no annotated gold for SC or IHC, and there does not have to be for
metadata. Both corpora state the answer about themselves somewhere outside the
page: SC names the case, its decision date and the judge in the file path; IHC
publishes a `meta.json` beside every judgment naming the parties, the bench,
the author, the filing category and the date of the order.

```powershell
python -m specter benchmark --sc-metadata  --limit 150 --output artifacts/benchmark/sc.json
python -m specter benchmark --ihc-metadata --limit 250 --shuffle --output artifacts/benchmark/ihc.json
```

`--limit` reads the corpus in path order, so a report stays comparable with
every earlier one -- changing which documents a metric reads is a silent way to
move it. `--shuffle` draws from across the corpus with a fixed seed, and IHC
needs it: filed by year and judge, its first 250 documents are all 2014 and one
judge.

Both score extraction **before** labels are applied. `apply_path_labels` runs in
the CLI and deliberately not in `parse_pdf`, because folding it in earlier would
score the corpus against itself.

Two things the report separates, because they are different failures:

* `decision_date` -- correct, out of every document the labels date.
* `decision_date_when_stated` -- correct, out of the documents where extraction
  produced anything. A wrong value ships as fact **and** blocks the label from
  filling the gap; an empty one does neither.

Interim orders are reported apart from judgments. Two thirds of IHC's PDFs are
orders, and an order names neither a bench nor a disposition, so blending them
reads as a parser defect where there is none.

### IHC, 250 digital documents

| field | before | after |
| --- | --- | --- |
| judge | 4.8% | 66.4% |
| decision_date | 49.6% | 74.4% |
| decision_date, among those stated | — | 93.5% |
| case_number | 61.6% | 73.2% |

Judgments alone: case_number 94.2%, judge 82.6%, decision_date 78.3%.

Three measured changes produced that, each of which also had to leave LHC's
five gold slices bit-identical:

1. **The signature is read.** `(NAME)` over `JUDGE` at the foot is the only
   statement of who decided on an order sheet, which has no cover and no
   `NAME, J.-` attribution. Judges were unknown on 95% of IHC documents.
2. **`NAME, J:-` is an attribution too.** IHC writes a colon where LHC and SC
   write a period.
3. **A bare `dated` is no longer read as the court's own date.** Right once in
   152 documents where it decided; see `docs/architecture.md`.

### The same changes on SC

Every metadata rule here is shared between courts, so each change had to be
shown not to cost SC or LHC anything. On the same 40 SC documents as the
previous report:

| field | before | after |
| --- | --- | --- |
| case_number | 75.0% | **82.5%** |
| decision_date | 62.5% | 62.5% |
| judge | 95.0% | 95.0% |
| court coverage | 100% | 100% |
| counsel coverage | 42.5% | 42.5% |

Nothing moved down. Over a wider 150-document draw the same code reads
case_number 87.3%, judge 98.7%, court 99.3% and counsel 74% -- higher because
the first 40 in path order are one judge's files, which is the reason
`--shuffle` exists.

The LHC gold set stayed bit-identical across all five slices (regression
0.0011, table 0.0093, scanned 0.0901, urdu 0.1162, text_edit 0.1652), as it
must: none of this touches page text.

### What the labels cannot score

`petitioner` sits at 75% on judgments and `respondent` at 22%, and both
understate the parser. The corpus writes a display title -- `FOP etc`,
`MD, OGDCL etc`, `ECP, etc.` -- where the cause title prints
`Federation of Pakistan and others`. The extraction is usually the better of
the two. For the same reason party names are filled from the record but never
checked against it: comparing them reported a disagreement on 176 of 281
documents and not one was actionable.

`counsel` reaches 29% on IHC judgments against 62% on LHC, and that is a real
gap with a known cause: an IHC order-sheet judgment has no counsel row at all,
because it has no cover table -- counsel appear as prose inside the first
proceedings entry. Extracting them there is a new capability, and there is no
label to measure it against, so it has not been built.

### As delivered

Extraction plus labels, over 281 IHC documents parsed end to end:

| field | present | agrees with the record |
| --- | --- | --- |
| case_number | 100% | 73% |
| decision_date | 100% | 95% |
| judges | 100% | 91% |
| court_id / petitioner | 100% | not checked (above) |

Of the case numbers that disagree, 48 of 60 sampled are the document's own
caption naming a different case from the folder it was filed under -- the
corpus disagreeing with itself. `label_check` reports those rather than
resolving them.

## The scanned route, measured for the first time

The benchmark previously ran the *digital* parser over every gold page,
including the 32 with no text layer, so the `scanned` slice read CER 0.999 and
said nothing about OCR quality. It now routes each document through
`ingest_pdfs.classify_pdf` and uses `ScannedParser` where that says scanned, so
the slice reports what the OCR route actually produces:

| route on the 32 scanned gold pages | CER mean | CER median | WER mean |
| --- | --- | --- | --- |
| digital parser (what was measured before) | 0.9990 | 1.0000 | 0.9992 |
| OCR route, `clahe` preprocessing (first measurement) | 0.1337 | 0.0911 | 0.2436 |
| **OCR route, `grayscale` preprocessing (current default)** | **0.0901** | **0.0611** | 0.1784 |

`--no-scanned` skips the OCR route for fast iteration, at the cost of making
that slice meaningless again.

OCR costs roughly 10s per page on CPU, so the full benchmark now takes about 11
minutes rather than 3. That is the price of the number being real.

One slice improved as a side effect of routing a document to the engine that
can actually read it, wherever the text layer was partial: `table` 0.0121 →
0.0072 → 0.0093 (the last step from the preprocessing change below, traced and
explained there). `text_edit` moved from 0.1953 → 0.1578 → 0.1652 for the same
reason.

### Preprocessing variant, chosen on evidence

`ScannedParser` renders each page and cleans it before OCR. Six single-stage
CPU variants exist in `preprocess_variants`; the parser had defaulted to
`clahe` (contrast-limited adaptive histogram equalization) since the route was
built, chosen by assumption and never tested against the alternatives. All six
were swept over the 32 scanned gold pages end to end (full OCR, not a proxy
metric):

| variant | CER mean | CER median | worst page | pages < 0.05 | sec/page |
| --- | --- | --- | --- | --- | --- |
| **grayscale (new default)** | **0.0901** | **0.0611** | **0.3186** | **13** | 10.6 |
| sauvola | 0.1147 | 0.0968 | 0.4129 | 8 | 9.8 |
| deskew_clahe | 0.1278 | 0.0904 | 0.4738 | 9 | 12.4 |
| otsu | 0.1300 | 0.0876 | 0.4210 | 6 | 11.9 |
| clahe (old default) | 0.1337 | 0.0911 | 0.4781 | 9 | 11.8 |
| adaptive | 0.1468 | 0.1178 | 0.3969 | 7 | 10.8 |

Plain `grayscale` — intensity normalization only, no contrast enhancement —
won on every metric, including speed. CLAHE was actively harmful: it boosts
local contrast, which amplifies scan noise along with the ink. The two worst
pages in the corpus, `2018LHC1351.pdf` p1 and `2020LHC3788.pdf` p1 (CER 0.478
and 0.468 under `clahe`), fall to about 0.06 under plain `grayscale`. The
result is not one lucky page: `grayscale` is better on 18 of 32 pages and
worse on 8, but the losses are small (largest +0.056) against gains as large
as −0.42.

This same trade-off shows up once more outside the official 32-page slice:
`2019LHC4817.pdf` is a fully-scanned document whose *embedded scanner OCR*
text layer (not ours) is bad enough that the benchmark's slice classifier
buckets two of its pages as `table`/`text_edit` rather than `scanned` (native
length is not short enough to trip that rule, even though `classify_pdf`
correctly routes the whole document to our OCR). `grayscale` is worse on those
two pages while being far better everywhere else, which is the source of the
small `+0.0021`/`+0.0074` CER moves on those slices above — not a routing
defect, the same known variance at a different accounting boundary.

**Mixed documents keep the native text of their readable pages.** Routing a
whole mixed PDF to OCR was measurably harmful: `2020LHC3741.pdf` is 27 pages of
which only 26–27 are scans, and OCR-ing the other 25 turned four gold pages
from CER 0.0 into 0.02–0.10. `ingest_pdfs.parse_pdf` now takes each page from
the engine that can read it, which restored the regression slice to 0.0011 and
86 exact matches. Both parsers run for such documents, which is acceptable
because they are rare (1 of 9,512 LHC documents).

**Blue-slip and blank pages are excluded from content by design** — a blue slip
is the court's routing form, not part of the judgment. The page still appears in
the output carrying its geometry and `exclusion_reason`, so a dropped page is
never mistaken for one that failed to parse. This costs exactly one gold page
(`2019LHC4817.pdf` p1, whose transcription includes the slip) which now scores
CER 1.0 in the `text_edit` slice: a deliberate product decision, recorded rather
than hidden.

Detection of those slips had been silently failing. OCR reads the title as
`BLUE'SLIP`, and the pattern allowed only whitespace between the two words, so
on `2013LHC4394.pdf` — the document the rule exists for — the form was never
recognised and never dropped. The pattern now tolerates punctuation between the
words but not letters.

Table false positives rose 4 → 6 when the scanned route was first measured;
both new ones were on `route=scanned` pages, from the OCR route's own
ruled-line table detector. The four on digital pages were unchanged. Switching
the preprocessing default to `grayscale` (below) incidentally dropped this to
4, the same as the digital-only baseline.

Two defects were found and fixed during Phase 2 that are not table-shaped at
all, both in `specter/specter_parser.py`:

1. `_coalesce_lines` joined same-baseline PDF line fragments purely on
   vertical proximity, with no horizontal-gap check. A label and its value in
   different columns of a form share a baseline and were glued into one
   fragment, spanning the blank gutter between them -- the root cause of
   borderless cover pages (~30% of the corpus) yielding nothing.
2. `_reading_order`'s two-column fallback clustered on left-edge proximity
   only. A short signature block (2-3 names) could satisfy the "two columns
   with >=3 items" check by coincidence and be read column-by-column instead
   of top-to-bottom by position. Fixed by requiring each candidate column to
   independently span a large fraction of the page height -- a fix validated
   against both a real regressed document and a synthetic case that showed
   the first (ratio-based) attempt only worked by coincidence.

Normalisation is applied to both sides before scoring: whitespace is collapsed
(mandatory, because the gold was captured under an older PyMuPDF that emitted
space-only lines), private-use glyphs are dropped, and the synthetic
`Court:`/`Case No:`/`Parties:` annotation lines left in three records are removed.

## Urdu recovery, measured

The 8 Urdu gold pages (`2022LHC1355.pdf`, `2025LHC3906.pdf`) both use the
`Jameel Noori Nastaleeq` font and are correctly flagged `unreliable_font_encoding`
by the Phase 1 trust gate — the pipeline already knows this text is not
citable, which is the safety property that matters most.

**Diagnosing the corruption.** Character-multiset comparison against gold
shows 99.87% overlap: essentially every character the page contains is
present in the extracted text. Two more targeted checks ruled out the obvious
explanations: reordering characters by their own geometry changes CER by only
−0.0025 (not a visual-vs-logical bidi problem), and reversing each word
whole makes things worse (−0.0232, inconsistent). Word-level diffing against
gold showed the real pattern: the letters of a word survive but are
**transposed within it** — `انتقال` extracts as `ااقتنل`, `میں` as `ںیم`. This
is the Nastaleeq ligature-cluster corruption identified during Phase 0/1
(E3/E4); it has no invertible rule, confirming deterministic recovery is not
possible.

**Page-level CER understates the damage and nearly produced the wrong
conclusion.** These pages are mixed English/Urdu; English on them is ~95-100%
accurate and dominates page length, so CER (0.1162) mostly measures English
fidelity. Scored on Urdu words alone, the native scrambled text recovers only
**6.9%** of them correctly (40 of 576).

**Local RapidOCR-Arabic was tested and made things worse**, recovering 0.2%
of Urdu words (1 of 576) — worse than the scrambled text it would replace.
The cause is not a tuning problem: `models/rapidocr_arabic/dict.txt` contains
Arabic-style letterforms (e.g. Arabic kaf `ك`) rather than Urdu ones (`ک`), and
the model was trained on Naskh-style Arabic, which is visually a different
script from Nastaleeq. One representative failure: gold `وصولی اپنے ہاتھ سے
تحریر کر کے دستخط اور انگوٹھاجات ثبت کر دیے۔` (~14 words) is read back as
`ظشكرتتابالوصو` (13 characters, the entire line collapsed). No amount of image
preprocessing fixes a model reading the wrong alphabet shape. This model is
ruled out for Urdu; local recognition as a category is not — see below.

**Not yet tested: TrOCR-Urdu**, already stubbed as `TrOCRUrdu` in
`specter/urdu_ocr.py`, a transformer trained specifically on Urdu rather than
Arabic. It is free and CPU-capable but requires downloading roughly 1.5GB of
weights from Hugging Face on first use, which needs explicit sign-off before
running. Until it is tried, "local Urdu recovery" is not settled — one model
has been ruled out, not the whole approach.

**Design, independent of that outcome:** try local recognition first and fall
back to the free-tier VLM only below a confidence threshold. At LHC's
observed Urdu rate (roughly 3% of documents, 250–370 of 9,512), even an
all-API fallback is cheap; the ratio should be re-measured against the IHC/SC
corpus once it arrives, since heavier reliance on Urdu there would change the
economics of that decision.

## External comparison

`evaluate_document.py` compares a prediction with a reference and reports:

- CER and WER
- block match coverage
- mean bbox IoU
- Layout F1 at IoU 0.5
- Kendall Tau for reading order
- per-type/script metrics
- table count agreement

LlamaParse outputs are currently regression references only. A strict block metric can penalize a semantically better parser when one system merges two legal paragraphs and the other keeps them separate. Production claims require independently annotated text, structure, and geometry.

## Release gate

Use the following as a gold-set gate, not as an unconditional reference-output gate:

```text
reading_order_tau > 0.95
layout_f1       > 0.90
mean_iou        > 0.85
```

Metadata completeness, table quality, and text CER/WER should be reported separately from layout quality.
