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
* `has_benches` -- whether this court sits in more than one place, which is
  what decides if a "BENCH" line on the cover is its own seat or the court
  below

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

## Citations

A citation is the only part of a judgment that points outside it, and across a
corpus it is the only structure connecting judgments to each other. Pakistan
writes them in two grammars, both present in the corpus:

* **Year first** -- `2008 SCMR 598`. The reporter implies the court, so none is
  printed. This is the common form.
* **Reporter first** -- `PLD 2015 SC 123`. PLD, PLJ, NLR and KLR span every
  court, so the court is named between the year and the page.

`specter/citations.py` matches both against a **whitelist of reporters** rather
than against a general shape, and reads them from blocks rather than from the
flat text, so every citation carries the page and box it was printed in.

```powershell
python -m specter graph artifacts/run
```

The graph has one node per authority -- whether or not the corpus holds it --
and one edge per "this judgment relied on that one". Most cited authorities are
**not** in the corpus, and that is the honest state rather than a defect: a
judgment downloaded from a court website carries no reported citation of its
own, because the reporter assigns one only on publication. Two corpora state
one anyway -- LHC in every filename, as a *neutral* citation, and IHC in the
230 case folders (of 30,226) whose reported citation the registry has filled
in -- and those are the documents an edge can resolve to.

The edges are worth having regardless. "Which of our judgments rely on 2008
SCMR 598" is answerable from the citing side alone, and it is the question a
lawyer actually asks.

---

# Decision log

Every entry is a decision that changed behaviour, the measurement that drove
it, and what it cost. Entries are kept after the fact is superseded, because
the reason a thing was tried is worth as much as the outcome -- several of
these record a change that was made, measured, and then reverted.

The rule this log enforces: **no decision without a number, and no number
without the sample it came from.**

### Preprocessing variant: grayscale, not CLAHE

Swept six variants over the scanned gold pages. `grayscale` CER **0.0901**,
`clahe` **0.1337**, and grayscale was also faster. CLAHE was amplifying scan
noise. Default changed. *Open:* none.

### Urdu: neither local route is usable

Native text extracts as transposed Nastaleeq ligature clusters (99.87%
character overlap with the truth, words are anagrams). The local
RapidOCR-Arabic recogniser scores **1 of 576** Urdu words; the scrambled
native text scores **40 of 576**. Both unusable, so Urdu paragraphs are
rendered as crops and routed to a vision model (`specter urdu`).
*Open:* that stage has never been run at corpus scale.

### Routing is decided per page, not per document

A document-level decision emitted **190 SC pages across 23 documents** as
empty: they had neither a dominant image nor a text layer, so they matched
neither branch. Every page now lands in exactly one of `scan_pages` /
`native_pages`.

### A page-sized image beats a hidden text layer

Tried trusting an existing text layer over the image test. Worse: that text is
some other scanner's OCR. The two signals are a union, not a precedence.
Pinned by `test_ingest_router_uses_image_coverage_not_hidden_text`.

### The hearing date *is* the decision date at SC

In **30 of the 34** SC documents whose true decision date appears in the text
at all, it appears as `Date of Hearing`. Ranking it above a bare `dated` took
SC label agreement 1.0% to 56.7%, and LHC year-agreement 33.7% to 68.4%. This
was the opposite of what the plan had assumed.

### An "impugned date" guard was measured and deleted

Skipping dates that follow `against` / `impugned` / `passed by` was tried:
identical on SC, **worse on LHC (64.9% vs 68.4%)**. Removed rather than kept as
inert complexity.

### A bare `dated` is not the court's own date

It was the last rung of the date ladder, on the reasoning that a document
stating one date states its own. Measured on the **152 documents where it was
the rung that decided** -- 23 at SC, 129 at IHC -- it was right **once**. What
it finds is the impugned order, an FIR, or an agreement recited in the facts.
It also pre-empted the ORDER SHEET's own structural date, which the page states
correctly. Removing it took IHC decision dates 49.6% to **74.4%**, and to
**93.5%** among the documents that state one at all. LHC loses coverage on 36%
of documents, which is the honest state: those judgments do not state a date.

### A label fills a gap; it never overwrites the page

Filenames (SC), folder names (IHC) and `meta.json` (IHC) are labels produced by
a scraper. They fill a field extraction left empty, tagged with their source
and an empty bbox, and every disagreement goes to `label_check`. Applied by the
CLI and deliberately **not** by `parse_pdf`, or the benchmark would score the
corpus against itself.

### A neutral citation is not a case number

`2013LHC3273` is how a judgment is *cited*, not the number the court gave the
case. Reading it as one produced `None.5924/2023` in the metadata and **16
false disagreements across 22 LHC documents** while the extraction (`Writ
Petition No.23303/13`) was right throughout. Modelled as `neutral_citation`.

### Statutes are a document *kind*, not another court

An Act has no parties, no bench and no decision date, and has what a judgment
does not: a numbered run of sections. Judgment-only fields are dropped rather
than reported empty.

### Section runs are a free correctness metric

A statute numbers its sections 1..N, so a hole is a miss and needs no
annotation. Three defects found this way, all fixed:

| defect | evidence |
| --- | --- |
| Schedule entries counted as sections | the Provident Funds Act reported **11 missing sections** that were listed banks in its schedule |
| The contents page opened a schedule | all four sections of the Commercial Documents Evidence Act read as schedule entries; **5 statutes lost every section** |
| A heading part-way through a block was missed | section 28 of the Co-operative Societies Act follows section 27's prose in one block, so its text was folded into section 27 |

Complete section runs went **93% to 98%** (57 of 58). `act_number` went 60% to
**93%** after two fixes: the number is often set mid-line, and `\b` does not
exist between the footnote marker and the word in `1ACT No. LXIV OF 1975`.

### Party names are filled from a label but never checked against one

The IHC record writes a display title -- `FOP etc`, `MD, OGDCL etc`, `Toyata
Islamabad Moters` -- where the cause title prints the name in full, and the
page is usually the better of the two. Comparing them reported a disagreement
on **176 of 281 documents** and not one was actionable. A check that fires on
half a corpus is not a check. Party extraction is still *measured* against
these labels in the benchmark, where the caveat can be stated.

### Output names are assigned before anything is written

**21,712 of IHC's 60,529 PDFs are called `judgment.pdf`.** The router used to
skip repeats and report them, which is honest but would have parsed an eighth
of the corpus. A filename distinct across the run is kept -- so no LHC or SC
path moves -- and any other is rebuilt from its case folder plus a digest of
its path, because the same case number recurs under different judges **7,544
times**.

### Scanned page images are not saved by default

They are OCR *input*. Saved, they were **63% of a run's bytes** and nothing
read them back: `specter inspect` re-renders from the source PDF, which is the
same picture. The corpus output estimate fell **24 GB to 9 GB**. Verified on
four scanned documents: identical text, 5.63 MB to 0.69 MB, and the JSON
reports `rendered_image: null` rather than a path to a file that is not there.
Urdu crops are different and are always written: the text under them is
unrecoverable, so the crop *is* the data.

### Benchmark sampling made explicit after it was changed silently

Adding IHC introduced a shuffled `--limit` draw, because IHC is filed by year
and judge and its first 250 documents are all 2014 and one judge. That silently
changed which documents every metric read, making the stored baselines
non-comparable -- the SC figures reported alongside it were not a delta at all.
Path order is the default again; `--shuffle` is opt-in and recorded.

### Citations: a reporter whitelist, not a general shape

The pattern in use required the reporter *before* the year. Pakistan writes the
year first, so it found **0 of the 17** authorities in the first judgment it
was tested against. A general `<year> <word> <number>` shape was rejected in
turn: the survey found it matching `2018 AND 3`, `Crl. Appeal No. 2014 ... 9`
and `Dated 2019 ... 5` in quantity. Pakistan's law reports are a closed set, so
naming them keeps precision high, and a wrong edge asserts an authority the
judge never relied on.

Two bugs found while building it, both silent:

* `str.rstrip` on a character class ate the last letter of every spelling that
  ended in one of its characters -- `Supreme Court Cases` matched only
  `...Case`, so **every Indian citation in the corpus was missed**.
* The volume was captured and then dropped from the identifier, merging
  `(2015) 11 SCC 493` and `(2015) 2 SCC 493` into one node.

Measured after the fixes: **2,516 citations across 450 documents**; 75% of LHC,
66% of SC and 36% of IHC judgments cite at least one authority.

*Precision:* thirty matches sampled with their surrounding text -- all thirty
genuine.

*Recall:* of the 1,062 reporter tokens (`SCMR`, `PLD`, `AIR`, ...) printed
across 150 LHC judgments, **96.6% fall inside a citation that was extracted**.
The remainder are almost all correct refusals: `air` used as an ordinary
English word ("blockage of air passages"), and OCR corruption where the page
itself is unreadable -- `2OlO SCMR 650`, `2O14 SCMR 7464`, a zero read as the
letter O. Guessing at those would invent an authority. Two classes *were* real
misses and were fixed: courts not on the list (`PLD 1960 West Pakistan 111`,
`PLJ 2014 Tax Cases (Kar.) 181`) and a missing space before the court
(`PLD 1996Supreme Court 543`), which took recall 96.0% to 96.6%.

### The corpus and the courts use two different namespaces

Measured on 231 parsed LHC judgments: **all 231 state a citation for
themselves** (the neutral citation their filename is built from), and **not one
of them cites another by neutral citation.** Courts cite the *reported*
citation -- `2008 SCMR 598`, `PLD 2015 SC 123` -- which a judgment downloaded
from a court website does not state about itself, because the reporter assigns
it only on publication.

So case-to-case resolution is 0% today, and not because of a defect: the corpus
keys documents in one namespace and judgments cite in another. Closing it needs
a neutral-to-reported concordance, which is exactly what a law *publisher* has
and a court website does not. That is why a published index can show resolved
citations both ways and this cannot.

What the graph gives without it is co-citation, and that is the query a lawyer
actually runs: "which of our judgments rely on 2008 SCMR 598" is answerable
from the citing side alone, with the page and box of every reliance.

### An impossible citation is reported, not corrected -- and it found a *date* bug

A judgment cannot rely on a case decided after it, so `cited_year >
citing_year` is a free correctness check needing no annotation.

The first measurement said **zero anachronisms in 1,332 citations**, and that
number was misleading: it could only run where the corpus states a decision
date in its path, which is SC and IHC. **LHC was skipped entirely** -- its
filename is a neutral citation, not a date.

Run over 400 parsed LHC judgments the check fired **36 times**, and the
citations were not the problem. `2014 LHC 3328` was extracted with a decision
date of `17.9.2004`; it cites 2010, 2011 and 2012 cases quite properly. Four
more of the worst:

| document | year in its own citation | date extracted |
| --- | --- | --- |
| `2022LHC1690` | 2022 | `9-10-1982` |
| `2018LHC2340` | 2018 | `26-6-1991` |
| `2025LHC2177` | 2025 | `28.06.2000` |
| `2024LHC850` | 2024 | `23.9.2013` |

The citation graph turned out to be a check on the date field -- the part of
LHC metadata that nothing else could measure.

### LHC decision dates are measurable after all

The neutral citation states the year, so `2014 LHC 3328` is a 2014 judgment.
That is a free year-level label for every LHC document -- 9,512 files, of
which **8,243 are unique**; see the duplication entry below -- and it had been
missed: the open items list said LHC decision dates could not be scored.

Measured on 400: **329 of 400 (82%) state a decision date at all**, and of
those **315 of 329 (95.7%) agree with the year in their own citation** (±1
year, since a December judgment can carry the next year's citation). Fourteen
are wrong by more than a year.

On IHC the same check fired three times in 157 documents, and one of them is
worth recording because a *second* free check agreed with it independently:
`Regular First Appeal-192-2015` was extracted with a decision date of
`18.09.2014` while citing a 2018 and a 2020 case, and its `meta.json` states
the order date as `07-APR-2025`. The anachronism and the label disagreement
point at the same field of the same document from opposite directions.

A smaller, separate class of anachronism is a misprint on the page itself --
`2077 SCMR 7354` for `2017 SCMR 1354`, a `1` read as a `7`. Neither class is
corrected: the check cannot tell them apart, and guessing would damage one to
flatter the other.

### The court publishes the reported citation -- for 0.7% of its cases

The namespace entry above said a court website does not state the reported
citation of its own judgments. On the full IHC corpus that is *almost* true,
and the exception is worth having. IHC names each case folder
`<case number> _ <citation>`, and once a law report assigns one the court
writes it there:

| IHC case folders | count |
| --- | --- |
| `_ Citation Awaited` | 29,970 |
| a real reported citation | **230** |
| other (registry text, `MISCELLENEOUS`) | 26 |

So 0.7% of IHC judgments are addressable by the citation another judgment would
use to cite them. That is a concordance -- small, but the only one in the corpus
that was not bought from a publisher, and it confirms the diagnosis rather than
overturning it: the gap is not that courts hide the citation, it is that the
reporter has not assigned one to 99% of the corpus yet.

Twenty-five of the 230 still do not parse, and all twenty-five should not:
sixteen are `2022 CLC 0`, `00` and `000`, a placeholder the registry writes
before the page number is known, and eight are a bare `2018 PLD 108`. PLD
paginates *per court*, so a PLD citation with no court names two different
cases at once. Reading it would invent one of them; leaving it unresolved is
the honest state.

### Four citation forms found by counting reporter tokens on the page

Every occurrence of `SCMR`, `PLD`, `CLC` and nine more was counted across 698
digital IHC judgments and checked for whether it fell inside an extracted
citation. That needs no annotation and it found four defects:

| defect | printed as | was |
| --- | --- | --- |
| A court annotated on a series that does not paginate by court | `1987 CLC [Karachi] 2185`, `2009 YLR 550-Karachi` | missed |
| The Notes section of a series | `2018 YLR Note 114`, `2022 PCRLJ-N 64` | missed |
| A bracketed court on a series that *does* | `PLD 2016 [Lahore] 383` | missed |
| Dots required where the page omits them | `2012 PLC CS 90` | missed |

The last was a latent bug: `_spelling_pattern` kept `.` as a literal even though
its own docstring said dots were dropped and allowed back as optional
separators, so `PLC C.S.` demanded its dots.

The first carries a correctness trap worth stating. CLC, YLR, MLD, PLC and the
rest run **one continuous pagination across every court**, so `1987 CLC
[Karachi] 2185` and `1987 CLC 2185` are the same case; putting the annotated
court in the identifier would have split one authority into two nodes. PLD, PLJ,
NLR and KLR paginate *per court*, so there the court is part of the identity.
That is what the `names_court` flag now decides, and both directions are
pinned by tests.

Recall over those 698 documents went **91.4% to 95.8%** (1,139 of 1,189 reporter
tokens inside an extracted citation), and no identifier moved: re-running the
extractor over 105 already-parsed documents left all 328 existing citations on
the same node and added one.

Most of what remains is not a pattern gap. `20'T..6 SCMR 1976`, `7997 SCMR
703`, `PLD 7973 SC 7` are the *source PDF's own* embedded text layer, mis-OCRed
by whoever made it. A citation that cannot be read cannot be extracted, and
inventing the digits would assert an authority the judge never relied on.

### A scanned cover bleeds into every field that reads it

One Supreme Court document reported its case number as `petitioner for matter
to police. He did not`. Chasing it found four separate defects, three of which
were never about the scanned route at all -- OCR only made them visible by
merging a whole cover panel into one block.

Measured over 120 documents (30 each from LHC, SC, IHC and the statute corpus),
with defects defined so they need no annotation: a case number with no digit in
it is not a case number, a bench that recites the impugned judgment is not a
bench, a counsel entry whose name is the next field's label is not a name.

| | scanned | digital |
| --- | --- | --- |
| documents with at least one defect, before | **8 of 17** | 4 of 71 |
| after | **2 of 17** | 1 of 71 |

The four causes:

* **`Nos?` matched the "no" inside "not".** So `petitioner ... He did not`
  parsed as a case number, and because a *wrong* value blocks the corpus label
  from filling the field, it also shipped as fact. "No" now has to be a whole
  word and what follows it has to contain a digit. The document's case number
  is now `J.P.217/2017`, filled from its filename -- the label mechanism was
  working all along, and the bad extraction was what stopped it.
* **`bench` took any line containing the word.** It was 14 of 14 wrong at the
  Supreme Court, which does not sit in benches: every "Bench" on its pages is
  the High Court below, recited in the `(Against the judgment ... Multan Bench)`
  line, or body prose about "the learned Bench". A bench is now read only at a
  court that `courts.py` records as sitting in more than one place, and only
  from a heading-shaped line. All 18 real LHC benches survive; all 15 wrong
  values are gone.
* **Parties swallowed the impugned-judgment recital.** A Supreme Court cover
  names the case, recites what it is an appeal from, then names the parties.
  On a scanned page all three are one line, so `petitioner` came back as the
  whole block. Three documents now name a person instead.
* **`Sr.No.3, 4, 6, 7, ...`** -- a serial number on an objections sheet -- ran
  the comma continuation across the whole list.

Twenty-five field values changed across the 120 documents and **every one is an
improvement**; no correct value moved. The one remaining flag on a scanned
document is my detector being wrong, not the pipeline: `M/s Islamabad Electric
Supply Company Limited through its Director Finance, Islamabad` really is the
party's name, and it is longer than the 150-character threshold the check uses.

The lesson worth keeping is the second-order one. Three of these four were
latent on digital pages too and simply had no evidence against them; the
scanned route did not introduce them, it made them *visible* by removing the
line breaks that had been hiding the ambiguity.

### Two more statute sections that the page states and the parser did not

Measured on 90 randomly drawn statutes: 71 have a complete section run, 5 are
one-line repeal stubs, and 14 have a hole. Classifying every hole against the
page -- does the print actually state that section, or does it say
`[Omitted]`? -- turns 110 holes into **98 correct and 12 real misses**. Two
causes, both fixed, and both invisible without the spans:

* **The footnote-marker stripper ate the section number.** It removes a plain
  run set before an emphasised heading, which is right when a marker precedes
  the number. But the Public Private Partnership Authority Act sets the number
  *itself* plain and only its title bold -- `7` then `.  Chief Executive
  Officer` -- so the number matched the marker shape and stripping it left
  `. Chief Executive Officer`, which is not a heading at all. A marker stands
  in front of a number, so what remains after stripping one must still begin
  with a number; that guard restores section 7 and leaves every case the
  stripper was built for untouched.
* **A number set alone reads as a page number.** Some prints put the number on
  its own line with no full stop -- `3` then `Establishment of the Authority.--
  (1) there shall be...` -- and because a lone number at the edge of the page
  is exactly what a page number looks like, the layout engine types it as a
  footer and the heading pattern never sees it. Recovering it is only safe
  because `section_sequence` already says which numbers are missing: the pass
  looks *only* for a number the run itself reports as a hole, and only accepts
  it when the next block is an emphasised heading. It can neither invent a
  section past the last nor overwrite one already found.

Together those took the real misses from 12 to 10, and two of the remaining ten
are the checker's own false positives (`19` matched against the `20.` that
follows it in the Railways Act contents). The rest are individual layout
oddities rather than a class.

The general lesson is the same one the scanned covers taught: the defect was
never in the section pattern. It was in a *neighbouring* decision -- what counts
as a footnote marker, what counts as a page number -- and the section run is
what made it visible, because a hole in 1..N needs no annotation to be wrong.

### The statutes a judgment relies on were a whitelist of five

`acts` matched six hardcoded names -- the Penal Code, the Anti-Terrorism Act,
the CrPC, the Qanun-e-Shahadat Order, the Constitution, the CNSA -- and nothing
else, whatever the judgment cited. Measured over 90 judgments it named a
statute on **19**, while **54 named one it could not see**: the Limitation Act,
the Contract Act, the Income Tax Ordinance, the Customs Act, the Guardians and
Wards Act, the Government of India Act. A closed list cannot cover a corpus
that holds 1,039 statutes.

A general `<Capitalised words> Act` shape was tried first and **rejected on
measurement**: it has no left boundary, so it ran back through whatever
sentence preceded the name and produced `Judge or pendency of petition under
the Guardians and Wards Act`, `Date of Order` and `If any provision of an Act`.

What works is reading the run *outward* from the word that says what kind of
instrument it is, because that word can open the name (`Code of Criminal
Procedure`) or close it (`Limitation Act`). Five rules, each added against a
named failure:

| rule | what it stopped |
| --- | --- |
| A name never runs back through a word that *introduces* a statute (`Schedule`, `Section`, `Date`, `under`, `Appendix`, `Preamble`) | `First Schedule to the Limitation Act` |
| The kind word must itself be capitalised | `No order`, `Against the order`, `This order` |
| Forward, a title continues only through `of` and `the` | `Code of Civil Procedure in the Court`, `Constitution the President` |
| No word of a title carries a digit | `C-121 ORDER` -- the form label above the word ORDER on every LHC and IHC judgment sheet, on 8 documents |
| A closing kind word is preceded by the title's own word, never by a joiner | `Pakistan the Act`, `However the Ordinance` |

A trailing full stop is also excluded from a word, or `Court.` and `Order` read
as one title across a sentence boundary.

Result over the same 90 judgments: **21% to 68%** of judgments naming at least
one statute, 121 distinct names, and **not one name the old whitelist found was
lost**. Forty extracted names were read against their page and about nine in ten
were exactly right; the rest are truncations (`Relief Act` for `Specific Relief
Act`) rather than inventions, which fragment the facet but assert nothing false.

Recall was deliberately traded down twice on the way -- 86% coverage before the
precision rules, 68% after -- for the same reason citations use a reporter
whitelist: a wrongly named act asserts a law the judgment never engaged.

### Statute linking, and the two things the corpus cannot tell you

`specter link <run> --statutes <root>` joins the statutes a judgment names to
the statutes the corpus holds. Judgments and statutes are counted apart, because a statute names other
statutes too -- the Companies Act, 2017 names the Ordinance it repealed -- and
blending the two makes the headline meaningless. Over a 120-document run:

| | judgments | statutes |
| --- | --- | --- |
| documents naming a statute | 74 | 28 |
| statute mentions | 197 | 198 |
| linked to one we hold | **104 (53%)** | 125 (63%) |
| linked, but our text is newer than the judgment | **59 of 104** | n/a |

`version_risk` cannot fire on a statute, which has no decision date, and
correctly reports zero there rather than a default.

The index is built from the text sidecar beside each statute PDF, which states
the title as printed. The filename slug is kept as a second haystack because
the scraper appended notes to it -- `pakistan penal code ppc1860 under review`,
`customs act 1969 same as on the official website of fbr dated 30 06 2025` --
and a name is matched as a **contiguous run of words** in either. A
subsequence would do: "companies act" appears in order inside "Companies
Profits Workers Participation Act", and is not that statute.

**Jurisdiction is why most misses are misses.** `pakistancode.gov.pk` is the
*federal* code: of its 1,039 statutes exactly **one** names Punjab and two name
Sindh. A Lahore High Court judgment applies Punjab law constantly. So an
unmatched name is usually a statute we do not have rather than a match that
failed, and saying so is the whole point -- guessing a federal statute for
`Punjab Pre-emption Act, 1991` would attach the wrong law to the judgment.
Every name carries the jurisdiction its own title states, and every miss
carries a reason:

| reason | count |
| --- | --- |
| `not_found_in_federal_code` | 115 |
| `not_held_for_this_jurisdiction` | 35 (Punjab, India, West Pakistan, Balochistan) |
| `subordinate_legislation_not_in_the_code` | 16 |

The third exists because the code publishes *primary* legislation. Police
Rules, Prison Rules and Import Policy Orders are made **under** an Act and are
not in it; counting them as a federal coverage gap would overstate one.

**Amendments are two different questions, and only one is answerable.**

*Which enactment.* The Companies Ordinance, 1984 and the Companies Act, 2017
are not versions of one statute -- the second repealed the first -- so the year
is part of the identity and a link is refused where both sides state one and
they differ. This was found as a live defect: before the rule, the index
answered `Companies Act, 1913` with the 2017 Act.

*Which version of that enactment.* What the corpus holds is the consolidated
current text. A 2010 judgment construed the section as it stood in 2010, and it
may since have been substituted. There is no version history here and none is
invented. What the statute does carry is its own currency: some copies state it
(`Updated till 7.10.2022`) and all of them carry amendment footnotes naming the
amending instrument (`Subs. by Act No. XLI of 2025, s.2`), so the later of the
two is reported as `text_current_to`. Where the judgment predates it the link
carries **`version_risk`** -- meaning *this is the right statute, but not
necessarily the words the judge read*. That fired on 59 of the 104 judgment links, which is
the honest state of a consolidated corpus and exactly the sort of thing a RAG
answer must not silently paper over.

One bug worth recording because it was invisible: reading the first line of the
sidecar as the title gave the Companies Act, 2017 the title `Updated till
7.10.2022` and therefore the year **2022**, so it refused to match a judgment
citing it by its own year. A title is now looked for by what a Pakistani
statute title *is* -- a line naming the kind of instrument, or one opening
`THE` and carrying a year -- and the currency line is read as currency.

### Amendment footnotes were being read as the statute itself

Reported from outside: a reviewer building a separate section extractor over
the same Penal Code text found footnotes ending up inside section bodies. Four
claims came with it. Checked one at a time against the actual output, three
were already handled and one was real -- and the real one was worse than
reported.

| claim | verdict |
| --- | --- |
| `structure` has only chapters, no sections | **False.** 636 sections, 47 chapters, 5 definitions; `section_count` 512, run complete. All of 34, 302, 324, 337A, 379, 392, 420, 489F, 506, 511, 295A resolve with correct titles. |
| Sub-lettered sections are typed `text`, not `list`, so filtering loses 489F | **Not applicable.** Headings are found from span emphasis, not block type. 126 sub-lettered sections found. |
| An amendment marker prefixes the number -- `4[489F.` -- so `^\d` misses it | **Already handled.** `_heading_line` strips a marker set plain before an emphasised heading; 489F is found with the title `Dishonestly issuing a cheque`. |
| Footnotes get swallowed into section bodies | **Real.** |

The last one is real because header and footer detection here works by
**repetition across pages**, and a footnote is different on every page. So it
never repeats, never looks like a running footer, and falls through to body
text. In the Penal Code **111 of 185 amendment notes were typed as content**,
and they were worse than mislabelled: the paragraph merger joined them to the
body text above, so `1Subs. by the Law Reforms Ordinance, 1972` arrived
*concatenated into the statute*.

A footnote here is small type, low on the page, opening with its own marker.
All three are required, because each alone is wrong: a contents entry is small
and low (`Penalty and procedure, etc.`), a schedule row is small and low (`Any
other toe`), and a list marker is both (`(i)`). None carries a marker or
amendment wording, so none is taken. The check runs *before* the list branch,
since `1Subs. by ...` reads as a numbered list item.

Two consequences that had to be handled together:

* A long note wraps into a second block carrying no marker of its own, so a
  run continues through small low blocks once it has started. Without that,
  `The Act has also been amended in its application to Baluchistan` stays in
  the body.
* Typing them apart from body text stopped them merging **at all**, and one
  note arrived as 25 fragments. `footnote` had to be added to the joinable
  pairs in the merger.

Measured on the Penal Code: **29,942 characters -- about 6% of the body --
left the statute text.** That is roughly five thousand words of `Subs. by
A. O., 1949` that a retrieval system would have quoted as law. The section run
is unchanged at 512 complete, and footnotes are **kept**, not dropped: they
name the amending Act, which is worth having, just not inside the section.

Across 60 statutes drawn at random: **41 carry amendment notes, and 143,431
characters -- 3.9% of all body text -- moved out of it.**

All five LHC gold slices stayed bit-identical, which is what says this did not
disturb judgments while fixing statutes.

### Section-addressable statutes, and two more bugs the reviewer's output exposed

`specter sections` turns a parsed statute into the shape a retrieval system
needs: one record per section, with its heading, its text, its chapter, and the
blocks it came from. Nobody asks for page 106 of the Penal Code; they ask for
section 302.

The reviewer's own builder was the useful part here -- not its claims, its
*output*. Checking their 26-section sample against the source found **7
contaminated sections**, and two of the causes were defects in this parser that
their builder had faithfully inherited:

| | theirs | ours |
| --- | --- | --- |
| sections carrying a page footer | 5 | 0 |
| sections carrying an amendment footnote | 1 | 0 |
| headings cut mid-phrase | 3 | 0 |
| section 109 filed under the wrong chapter | XVI | **V** |

**A chapter cross-reference in prose was read as a chapter heading.** Section
109 says "an offence referred to in Chapter XVI shall be liable to punishment
of ta'zir", and `CHAPTER_RE` matched it, so every abetment section was filed
under the homicide chapter. A chapter designation stands alone or carries a
title; it never runs on into a lowercase sentence. Note the guard has to scope
the ignore-case flag off -- under `(?i)`, `[a-z]` matches capitals too, so the
first version of the fix rejected every real titled chapter.

**Two genuine chapters were skipped for being page headers.** The Penal Code
prints CHAPTER V and CHAPTER XIX as running headers, and structure detection
skipped header blocks wholesale. A chapter designation is structural wherever
it is set. With both fixed the chapter run is monotonic for the first time --
I, II, III, IV, **V**, VI ... **XIX**, XX -- where before it read I, II, III,
IV, **XVI**, VI.

**A blank span defeated the footnote size test.** One note stayed in the body
while its neighbours left: `font_sizes` includes spans that print nothing, and
a stray 12pt space inside an 8pt footnote put its maximum at 12. The size is
now read from spans that actually print something.

Measured over 28 statutes and 511 sections: **0 page footers, 0 missing
headings, 25 of 28 complete runs**, and one residual footnote leak (0.2%). The
six sections under 40 characters are correct -- `4[Omitted]` and
`3[* * * * * * *]` are what the print says.

The heading is recovered from the section's own text rather than the structure
marker, because a heading wraps across *blocks* and the marker only sees the
line the number sits on. A first attempt read it from the whole block instead;
that changed nothing, because the wrap is between blocks, and it was reverted
rather than left in as inert complexity.

---

### The contents page numbers its sections 1..N, exactly as the body does

Running `specter sections` over a stratified sample of 77 statutes reported
**1,226 duplicate section numbers across six statutes** and 206 sections whose
text was a row of dot leaders. The Sales Tax Act contributed 90 of them: pages
2-6 are its table of contents, every entry set bold and typed `heading`, so
each read as a section heading and produced a chunk reading
`Definitions..............7-28`. The Penal Code leaked one entry the same way
(`120B` from page 6), and that single entry mattered out of proportion: it put
the running section maximum at 120 before section 1 of the body was reached, so
every real section after it looked like a step backwards.

Position on the page is not the discriminator -- a contents list is set exactly
like the headings it lists. What separates them is the **enacting formula**: a
statute prints its contents before the act number, the commencement date, the
long title and the preamble, and its sections after them. The last such anchor
that still leaves most of the section markers after it is taken as the start of
the body, and markers before it are index entries.

The share test is what makes the anchor safe. Several of those forms are also
printed on the cover, ahead of the contents, and a cover anchor leaves 100% of
the markers after it, so the later one wins. Measured over the sample, the cut
removes 466 markers -- 373 in Estacode, 91 in the Sales Tax Act, and one each in
the Cantonments Act and the Penal Code -- and touches **74 of 77 statutes not at
all**. Duplicate section numbers fell from 1,226 to 1, dot-leader sections from
206 to 2, and both survivors are dotted fill-lines in a schedule's affirmation
form rather than contents entries.

### A cross-reference that opens a block reads as a section heading

The Penal Code prints section 127 as

    127. Receiving property taken by war or depredation mentioned in sections 125 and
    126. Whoever receives any property knowing the same to have been taken...

The `126.` is the tail of "sections 125 and 126", but it begins a block and is
set like every other heading, so it was read as one. The damage is worse than a
spurious entry: it invented a *second* section 126 and took section 127's text
away from it, leaving 127 as its own heading and nothing else. Section 163/164
failed identically.

What settles it is that a statute numbers its sections upward. A number that
goes backwards over one already accepted is not a new section. Requiring the
*repeat* is what keeps the rule from throwing away a section genuinely printed
out of order -- a number never seen before is kept wherever it appears.

### Estacode is not a statute, and saying so is the fix

Estacode is 1,044 pages holding some sixty separate SROs and rule-sets, each
numbering its own sections from 1. Read as one Act it reported **1,179 sections
in which "section 1" resolved to 62 different texts** -- and `sections_complete`
was being asserted against a run that does not exist.

Counting how many times the run begins again at 1 separates it outright: after
the front-matter cut, 75 of the 77 sampled statutes have exactly one section 1,
two have none, and Estacode has 62. No threshold has to be chosen. A compendium
gets `sections_complete: null` rather than a fabricated verdict, its sections
are given **no citation** -- "section 1 of Estacode" names sixty texts -- and it
is named in the corpus index instead of being written out. The parsed document
is still there to be read whole; what is withheld is the claim that it is
section-addressable.

### The section number is set in the margin, and reading order puts it either side

The Penal Code prints section 1 as its heading at x=144 with `1.` at x=108 on
the same line, and PyMuPDF returns them in that order -- so the section began
*after* its own heading and lost it outright: `Title and extent of operation of
the Code. This Act shall be called the [Pakistan]` was simply absent from
section 1. Section 2, four lines below on the same page, comes back the other
way round. Seven statutes in the sample use the layout, 74 section markers in
all.

No text rule separates the two cases; the preceding block is a heading for some
and the previous section's last line for others. The geometry does: **the
heading is the block that shares the number's line and sits to its right**,
whichever side of it reading order puts it on. The section reaches back over it
when that holds, the previous section's end moves back with it so both cannot
claim the same line, and the number itself is dropped from the assembled text --
kept in `block_ids` as provenance -- so it does not land in the middle of the
sentence it interrupts.

### A statute set at 7pt gives the footnote rule nothing to work with

The parser types a footnote from its size relative to the page median and its
place down the page, and across the corpus that is decisive. The House Building
Finance Corporation Act is set at 7pt throughout, so its amendment notes are
*the same size as the law*. Seventeen of its sections ended with the note that
follows them.

The note still carries its own shape: a footnote marker opening the block, and
an amendment citation inside it. Requiring both catches the runs where several
notes are merged into one block and only the first begins with the verb
(`1 Ins. and subs. by...`, `1 Sub-section (1) was originally subs. by...`).

Two false positives had to be excluded and each named a real rule. `17A.
[Appointment of officer to exercise duties of King's Proctor.] Omitted by the
Divorce Act` is a lettered section, not a marker glued to a verb -- so the
capital must open a *word*. And `10 & 11. [Amendment of the Sea Customs Act,
1878.] Rep. by the Repealing Act, 1938` is a repealed section that reads exactly
like an amendment note -- so a block that *opens* a section is the law, whatever
else it resembles. This lives in `statute_sections` rather than in `_classify`:
it is a rule about what the law's text is, and keeping it out of the parser
means the judgment pipeline cannot move. All five gold slices are bit-identical.

### Not every statute prints a year after its name

`TITLE_RE` required one, so "THE PAKISTAN PENAL CODE" -- which prints none --
had no title at all and fell back to the filename slug. Every section of it was
labelled **"pakistan penal code ppc1860 under review"**, which is what a
retrieved chunk would have shown as the name of the Act. Ten of 77 statutes
were in that state.

Making the year optional, and allowing the trailing full stop that
"ORDINANCE, 1977." carries, recovers 7 of the 10 and **changes not one title
that was already found** -- checked by running both patterns over the same
parsed text before the change was made. The short form survives it too: the
Penal Code is still `PPC` and still cites as `302 PPC`, because `_short_form`
compares the slug against the *printed* title and "ppc" is not a word of "THE
PAKISTAN PENAL CODE".

### The PDF's own title names a different Act, 74 times out of 77

`pdf_title` is read from the PDF's embedded metadata and reported under its own
name, which is honest provenance. For this corpus it is also almost always
*wrong*: the scraper's generator reuses whatever template was last open, so the
Negotiable Instruments Act 1881 carries `THE FERRIES ACT, 1878`, the Multi-Unit
Co-operative Societies Act carries `THE SUGAR-CANE ACT, 1934`, and one file
simply says `UNDER REVIEW`. Measured over 336 parsed statutes that have a title
of their own: **agrees 3, disagrees 74, absent 259.**

Naming a different *real* statute is worse than naming none, so this is called
out rather than left to be discovered. The field is kept -- it is what the file
says, and deleting a faithful reading to make a number look better is the
opposite of the rule everywhere else here -- but nothing reads it: the section
corpus takes `title`, which is read off the page.

### A schedule numbers its own entries from 1, and three things hid that

`in_schedule` exists because a schedule restarts numbering, so its entries must
not be read as sections of the Act. At corpus scale it was failing three
different ways, and each one put a schedule's entries in collision with a real
section.

**The heading was not matched.** The Stamp Act heads its duty table
`1[SCHEDULE 1` -- the amendment marker glued to the front, and the number
printed *after* the word rather than before it. `SCHEDULE_RE` allowed neither,
so 123 of the Act's 144 "sections" were schedule entries. Allowing both takes
it to 82 sections with a complete run and nothing ambiguous. Fifteen other
statutes were failing the same way.

**A chapter inside the schedule ended it.** The Carriage by Air Act reproduces
the Montreal Convention as its Fourth Schedule -- chapters, articles and all --
and `CHAPTER I` reset `in_schedule`, so the Convention's articles came back as
sections. The Act reports **116 sections before the fix and 10 after**, which is
what it actually has.

A test asserted the opposite: that a chapter after a schedule returns to
sections. It carried no reference to a real document, unlike its neighbours, so
it was checked rather than trusted. Over the 841 statutes parsed at that point,
Carriage by Air is the **only** document where a chapter follows a schedule at
all, and there the numbering restarts. No statute in the corpus returns to its
own sections that way, so the test was recording an assumption and was replaced
with the measured behaviour.

**The heading also names the section that calls it up.** "THE SCHEDULE (See
section 41)", "SCHEDULE [see SECTION 5]", and the rule printed above it returned
inside the same block -- "_____________ THE SCHEDULE (See section 41)". Both
forms are standard and neither was allowed. Over 465 parsed statutes, allowing
them matches 18 further headings in 17 statutes, **every one a real schedule**.
The Quaid-e-Azam University Act's First Statutes, which number from 1, sat under
exactly that heading: the Act reports 65 sections before and **54** after, with
nothing ambiguous and a complete run.

**An emphasised run mid-sentence was read as a heading.** The Succession Act
prints `Explanation 1.- A married woman may dispose by will...` with the number
set bold, and the inline-heading path -- which exists so a heading that follows
the previous section's closing words is not lost -- read it as section 1, in the
middle of Chapter II. A section heading opens a sentence, so what precedes it on
the line has to have finished; a trailing amendment marker is allowed for,
because `9.` then `4` then `[10. Power to make rules` is the run the path was
built for. The Succession Act goes from 397 sections with 8 ambiguous to 393
with none, and the Penal Code drops a spurious section "0" whose text was
mid-sentence prose.

### Dropping a document is worse than an uncitable section

The compendium rule first written here *excluded* a document from the section
corpus when its numbering restarted more than once. On 77 statutes that removed
exactly Estacode, which was the intent. On 700 it removed **fifteen real Acts** --
the Stamp Act, the Succession Act, the Carriage by Air Act and twelve more --
because an undetected schedule also restarts the numbering.

The rule was wrong in kind, not in threshold. Ambiguity is a property of a
*section*, not of a document: a citation has to resolve to one text, so where a
number names more than one, that section carries `citation: null` and
`ambiguous: true`, and every other section of the same Act keeps its citation.
Nothing is dropped -- the text is legitimate law either way -- and the act
record reports `numbering_restarts` and `ambiguous_sections` so a retriever can
see exactly what it is getting. Estacode now ships 381 sections of which 377 are
marked ambiguous, instead of vanishing.

What is left after the three schedule fixes is five university-type Acts whose
"First Statutes" restart numbering under a heading that is not the word
`SCHEDULE`. Those sections are marked ambiguous and keep their text; extending
the structural vocabulary to `STATUTES`/`REGULATIONS` is not done, because it
has not been measured.

### The front-matter cut ate section 1 of seventy-seven statutes

Cutting everything ahead of the enacting formula was right in principle and
wrong in two details, and the corpus said so: section 1 was the most commonly
missing number in the run, absent from **77 of the 178 statutes** whose runs had
a hole, with 2, 3, 4 and 5 close behind. That shape -- the *first* sections
going missing -- is a cut landing too late, not a detection failure.

**A statute recites its own name inside section 1.** "This Ordinance may be
called the Islamabad Rent Restriction Ordinance, 2001." matches the title
pattern, and the anchor is taken as the *last* match, so the body was anchored
one block *after* section 1's heading and the section was cut away with the
front matter. The title was therefore dropped from the anchor set: an enacting
formula is a formula, while a title is a heading that also occurs in prose. The
act number, commencement date, long title and preamble do not appear inside a
sentence that way.

**A contents list is a duplicate, so the cut is per marker.** The first repair
refused the whole cut when it would lose any number outright. That fixed
section 1 but broke the Sales Tax Act: a handful of its contents entries are
listed and *not* found in the body, so one missing number abandoned the cut
entirely and all 91 index entries came back -- 242 sections, 178 of them
ambiguous. What is true of a contents list is true entry by entry: a marker
ahead of the body is dropped only where the body repeats it, matched on kind and
number together so a contents *chapter* is dropped the same way. A marker that
appears nowhere else is kept, whatever sits above it.

Measured on seventeen statutes re-parsed to check, with the Penal Code, Forest
Act, Cantonments Act, Stamp Act, Succession Act, Carriage by Air Act and
Quaid-e-Azam University Act as unchanged controls:

| | before | after |
| --- | --- | --- |
| Merchant Shipping Ordinance | 507, incomplete | **611, complete** |
| Banking Companies Ordinance | 140, incomplete | **155, complete** |
| Mines Act | 72, incomplete | **75, complete** |
| State Bank Banking Services | 27, incomplete | **31, complete** |
| Army and Air Force Reserves | 6, incomplete | **9, complete** |
| Islamabad Rent Restriction | 32, incomplete | **33, complete** |
| Control of Employment | 16, incomplete | **17, complete** |
| Sales Tax Act | 149 | 152, 0 ambiguous |

Sales Tax keeps four contents entries whose sections are not found in the body.
Those are dot-leader chunks and they are the honest outcome: the statute lists a
section the parse did not locate, and saying so beats deleting the evidence.

### What the section corpus looks like once those are fixed

Measured over the 77 statutes that ship (Estacode excluded as a compendium),
2,641 sections:

| | before | after |
| --- | --- | --- |
| duplicate section numbers | 1,226 | 1 |
| dot-leader (contents) sections | 206 | 2 |
| sections with no heading | 14 | 0 |
| amendment notes inside the text | 17 | 0 |
| page footers inside the text | 0 | 0 |
| section number left inside the text | 0 | 0 |

Both remaining leader hits are dotted fill-lines in schedule forms
(`Dated..........................`), which is what the page prints. The single
remaining duplicate is not an error either: the Electoral Rolls Act genuinely
prints two section 30s -- the one repealed in 1981 and the `6[30. Breach of
official duty]` inserted in its place -- and keeping both is the faithful
reading.

---

### Chunking runs on text, and provenance comes from an offset map

The obvious way to chunk a parsed corpus is on block boundaries, and it fails
here. Justified text makes PyMuPDF break a line wherever the word gaps widen, so
a judgment line comes back as four blocks, one word each -- "and", "recovery",
"of", "certain". Measured over the corpus as it was then parsed, **16.8% of
blocks shared a line with a neighbour and carried 4.9% of all characters**; 92%
of Lahore judgments were affected, median block 58 characters. The parser now
joins such fragments, which takes those figures to **6.8% and 2.4%** -- the
remainder is deliberate, being printed columns and cover label/value pairs that
must not be joined.

The first attempt repaired the geometry: join same-line blocks, then group lines
into paragraphs by indent and gap. It needed constants tuned to the corpus, it
misread two-column tables as prose, and a bug in it -- taking the *minimum* left
edge as the body margin, so a margin number at x=108 made every line look
indented -- turned 21,855 blocks into 23,151 "paragraphs". More than that, it
was solving the wrong problem.

Blocks joined with single spaces already read as correct continuous prose. That
is exactly why the page-text benchmark never moved while the fragmentation was
there. So the document is flattened to one string and every character remembers
the block, page and box it came from; chunking is then any text algorithm, and a
chunk's character span maps back to page regions.

Two things follow that the geometric version could not offer. Chunk text is
**identical** whether a document was parsed before or after the line-joining
parser fix -- verified on documents parsed both ways, where only the number of
boxes changed (115 to 50 on one) -- so a corpus parsed half each way is not a
problem. And provenance is checkable: re-extracting text from the PDF at the
recorded coordinates covered **100% of the chunk's words on 24 of 24 sampled
chunks**, which is now a test against a real fixture rather than an assertion.

### A statute states its own chunk boundaries; a judgment mostly does not

Section-as-chunk is not a preference, it is what the measurements say: over the
30,995 parsed sections the median is 589 characters (~147 tokens), p95 is 2,854,
and exactly **one** section exceeds 8,192 tokens. A section is also the unit a
judgment cites. Only the 9.6% over budget are split further, on their own
sub-section markers.

Judgments number their paragraphs in 55% of Lahore cases, and a printed number
is both the natural boundary and the citable unit; elsewhere text is packed to
budget and broken at the latest sentence end that fits.

Chunk size was then ablated rather than chosen -- nine configurations over the
same 308 queries. **512 is the right statute budget**: lookup recall@5 of 0.974
against 0.903 at 256 and 0.870 at 1024, at two thirds the chunk count of 256.
Query length mattered more than corpus chunk size: in-context recall@5 fell from
0.273 to 0.175 as the query grew from 256 to 1024 tokens, because a longer
passage of argument dilutes the words that identify the law.

### Late chunking was considered and rejected on cost, not fashion

Late chunking embeds the whole document and pools per chunk, so each chunk
carries document context. Its published benefit grows with document length, and
it "tends to sacrifice relevance and completeness" against contextual retrieval.
Neither condition favours this corpus: a statute section is self-contained by
construction -- that is what a section *is* -- and 3% of judgments exceed the
8,192-token window anyway. On a machine with no GPU it is also the expensive
option, because attention is quadratic in length, so one 8,192-token pass costs
far more than sixteen 512-token ones.

What is used instead is a **context prefix** -- act and section heading, or
court, case and date -- prepended for indexing only, which the legal-RAG
literature calls summary-augmented chunking and finds sufficient. It costs
nothing. The chunk's own text stays what the page printed, so what is quoted to
a reader is the law rather than our annotation of it.

### The corpus labels its own evaluation set

Every chunking and retrieval choice is an opinion without one, and the labels
are already in the corpus: **a judgment names the section it applies**, and we
hold 30,995 sections. 60 judgments yielded **308 labelled queries**, which is
about 48,000 across the full Lahore corpus, free.

Two query kinds, kept separate because one number would hide the difference.
*Lookup* ("section 302 PPC") tests exact retrieval. *In context* -- the
judgment's own paragraph with the citation string removed -- tests whether the
section is found from a description of the law rather than from its number.
Removing the citation is the point: left in, BM25 answers both from the number
and the evaluation says nothing about the dense leg.

Getting the set to materialise took two corrections. Statute citations read
"section 302 of THE PAKISTAN PENAL CODE" because the chunker looked for an
`act_short` field the parser never writes, so **zero** queries resolved. And
abbreviations turned out not to be derivable at all.

### A statute's abbreviation is conventional, not derivable

Reading it from the filename gave PPC, CRPC and QSO correctly, and with equal
confidence gave "XIX", "XIV", "ACTX" and "TAX" -- **515 sections were keyed to
the roman numeral of their act number**. On the judgment side the same guesswork
read "of", "Act" and "Coloni[sation]" as abbreviations of statutes.

So `rag/acts.py` names the codes that carry the caselaw, in both the forms a
judgment prints and the titles a statute prints, and the citation pattern is
built *from that table* so the two cannot drift apart. Three of the most cited
codes -- CrPC, CPC and the Qanun-e-Shahadat -- print no title the parser can
read, and their abbreviation is recovered from the filename as a whole token,
never a substring. That took the evaluation set from 62 queries to 308.

Anything not named there is still chunked, still retrievable, and still carries
its full title; what it does not get is a short citation, and it takes no part
in the evaluation. A smaller set that is right beats a larger one that is not.

### Hybrid retrieval is not free, and a weak leg is worse than no leg

Three legs -- BGE-M3 dense, BGE-M3 sparse, BM25 -- fused by reciprocal rank
rather than weighted score, because the three are on incomparable scales and any
weighting is a constant that would need tuning against an evaluation set that
did not exist when the fusion was written.

Rank fusion weights every leg equally, and that has a sharp consequence. Fusing
BM25 with the deliberately dumb stand-in embedder took lookup recall@5 from
**0.974 to 0.104** -- an order of magnitude worse than not fusing at all,
because two weak legs outvote one strong one. So the evaluation reports every
leg on its own and then the blends. If BGE-M3's legs are weak on Pakistani legal
text, naive fusion will be worse than the lexical search already in hand, and
only the per-leg table says so.

BM25 is kept beside BGE-M3's learned sparse weights for an operational reason
rather than a quality one: it rebuilds in seconds with no forward pass, where a
dense index over this corpus is hours on Apple Silicon and days on a CPU.

Three defects in the retrieval layer were found by running it rather than by
reading it. BM25 was fitted on the chunk text while the dense leg embedded the
prefix with it, so the act's name was invisible to lexical search and "section
302 PPC" scored recall@1 of **zero** against the section it names. Scoring
walked every chunk for every query token, which is unusable at 200,000 chunks.
And the stand-in embedder bucketed on Python's `hash()`, which is randomised per
process, so an index saved by one run and queried by another returned nothing
sensible -- silently, since nothing errors.

### What lexical search cannot do at all

The metrics say the dense leg has to improve an in-context recall@5 of 0.234.
Four probes say what that means concretely.

| query | top hit |
| --- | --- |
| section 302 PPC | **302 PPC** |
| bail before arrest non-bailable offence | **497 CrPC**, the bail section |
| punishment for qatl-i-amd | 316 PPC, then 302 -- "Punishment *for*" outscored "Punishment *of*" |
| what is the punishment for murder | 108/109 PPC, abetment. **302 is not in the top four** |

**The Penal Code says "qatl-i-amd" and never says "murder."** A user asking
about murder, a bounced cheque or dowry gets nothing from lexical search,
because the words they use appear nowhere in the statute that governs them. That
is not a tuning problem, and it is the only thing that justifies paying for a
568M-parameter model on borrowed hardware.

### The Lahore corpus holds the same judgment twice

9,512 PDFs, **8,243 unique documents**: 1,269 are byte-identical second copies,
1,255 of them named like a duplicate download. Indexed as they stand, a search
returns the same judgment twice and pushes a real second result off the page --
and every count taken over that corpus, including the "9,512" used earlier in
this document, is inflated by an eighth.

Chunking skips a document whose text it has already seen and reports how many.
Matching is on the text rather than the filename, so a copy saved under an
unrelated name is caught too. Statutes need none of it: 0% exact duplicates and
0.5% near, all of them omitted-section stubs, which is what the print says.

An initial measurement put duplication at 26.9%. That was a sampling artifact --
duplicate files sort next to their originals, so the first 1,200 documents
parsed were duplicate-dense. 13.3% is the rate.

---

## Open items

Tracked here rather than closed by assertion.

| item | why it is open |
| --- | --- |
| Urdu at corpus scale | `specter urdu` is wired and tested on crops, never run over a corpus. No local engine reads Nastaleeq. |
| IHC counsel | An order-sheet judgment has no cover table; counsel appear as prose in the first proceedings entry. There is no label to measure an extractor against, so none was built. |
| Citation to document resolution | Measured at **0 of 1,118 edges** over 231 LHC judgments. Documents are keyed by neutral citation; judgments cite by reported citation. IHC publishes the reported citation for **230 of its 30,226 cases**, which is the only concordance the corpus contains and covers 0.7% of it. |
| Graph size | `graph.json` runs about **187 MB** for the full 71,870-document corpus, extrapolated from 0.60 MB at 229 documents. Fine as a build artifact, too large to load per query -- it is input to an index, not the index. |
| LHC decision dates | 95.7% agree with the year in their own neutral citation; 14 of 329 are wrong by more than a year. The label exists and is free -- wiring it into `specter benchmark` as an LHC date report is not done. |
| Text gold for IHC and statutes | **The only annotated gold is LHC** -- 192 pages. SC has a partial review (43 corrected pages, behind `--sc-gold`) that has not been run as a reported baseline; IHC and the statute corpus have none at all. Their metadata is measured against the corpus's own labels and their page text is not measured against anything. |
| `SCC` is ambiguous | Indian "Supreme Court Cases" and a Pakistani tax-reporting usage share the abbreviation, so `1993 SCC 1011` -- a Pakistani case -- is tagged `IN`. Measured at **1 bare SCC against 20 with a volume** in 400 documents, so ~0.08% of all citations. The volume is recorded, so a consumer can apply the discriminator; the pipeline does not guess. |
| Linking a named act to the statute we hold | `acts` now names the statute as the page does, and the statute corpus is parsed and keyed, but the two are not joined. The 121 names found across 90 judgments are the input to that join; matching them against the 1,039 statute titles would close the "statute and judgment linking" item below with no new extraction. |
| The rest of the IHC record | `meta.json` also carries `author`, `approved_for_reporting`, `important_category`, `discussed_laws` (33% of documents name statute sections) and a full `case_history`. All of it is written through to `source_metadata` verbatim; none of it is read into a field or measured. `discussed_laws` is a free label for statute linking. |
| Rhetorical section tagging | Measured by running the Indian pipeline's own 16-type detector over 90 Pakistani judgments: **97.5% of blocks fall into its catch-alls** (`body`, `paragraph`, `section`) and the real labels it does emit are mostly wrong -- `^RATIO` matches "rationale", `^ISSUE` matches "issued". Pakistani judgments are unheaded numbered paragraphs, so a heading-driven tagger does not transfer. What the corpus does offer, measured over the same 90: an outcome verb in the last 30% (88.9%), "learned counsel" (82.2%), an author line opening the opinion (57.8%), an explicit facts marker (26.7%). A 4-label tagger over those is buildable and measurable; a 16-label one is not, without annotation. |
| Provincial statute corpora | The corpus is the federal code. 35 of 166 unmatched statute names are provincial or foreign. Scraping the Punjab Code (and Sindh, KP, Balochistan) would close most of it; the linker already reports the gap by jurisdiction so the size of the job is measurable rather than guessed. |
| Statute version history | `version_risk` fires on 59 of the 104 judgment links: the corpus holds consolidated current text and the judgment construed an earlier version. Nothing here can say what the earlier words were. Closing it needs a versioned source, not better parsing. |
| A path-order sample is not a stable sample | Two `--ihc-metadata --limit 250` runs against the same root shared only **7 of 250** documents, because the corpus on disk changed between them. Path order makes runs reproducible only while the corpus is frozen, so a benchmark figure cannot be compared across corpus edits. Pinning the sample by document identity is not done. |
| Precedent retrieval is unmeasured | The free labels are judgment-to-section citations, so the evaluation set says nothing about finding a *case*. Judgment-to-judgment labels would need the citation graph to resolve, and that is measured at **0 of 1,118 edges** because judgments cite reported citations while the corpus is keyed by neutral ones. IHC's 230-case concordance is the only bridge. |
| The evaluation covers six acts | Queries resolve only for the codes named in `rag/acts.py` -- PPC, CrPC, CPC, QSO, CNSA, ATA. That is a sample of criminal and civil practice, not of the 982 statutes. Naming more is cheap; deriving them is what does not work. |
| No dense numbers | BGE-M3 has not been run: 568M parameters, ~86M tokens over ~200,000 chunks, no GPU here. Every retrieval figure recorded is the lexical baseline it has to beat, not a result for the system. |
| Six statutes set without any heading emphasis | The Seed Act 1976, the Finance Ordinance 2001 and four others carry no bold or underline on their section headings at all, so `_is_heading_block` rejects every one and the document yields **zero** structure -- 50,000 characters of the Seed Act go unaddressed. A fallback that fires only where nothing at all was found was prototyped and measured against the contents listing in the corpus's own `.txt` sidecars: **44 of the 45 sections it finds are listed there**, but recall is partial (32 found in the Seed Act with 8 holes) and it needs a window constant chosen from the data. 45 sections against 30,995 did not justify the code or a fifth corpus re-parse, so the six are named in `sections/index.json` under `no_sections_detected` instead of vanishing from it. |
| University "First Statutes" restart the numbering | Five university-type Acts schedule their statutes under a heading that is not the word `SCHEDULE`, so those entries collide with the Act's own sections. They are marked `ambiguous` and keep their text and their citation is withheld. Extending the structural vocabulary to `STATUTES` / `REGULATIONS` has not been measured, so it has not been done. |
| 61 statutes titled by their filename | `TITLE_RE` recovers a printed title for 921 of 982. The rest fall back to the slug -- "west pakistan control of goondas ordinance 1959" -- which is legible but is not what the page says. The `.txt` sidecar beside every PDF carries the title on its opening lines and has not been used. |
| `statute_labels` is computed and dropped | `apply_statute` calls it only as a truthiness gate for "is this a statute", then discards the `statute_id`, `category` and slug `year` it returns -- so the parse output carries none of them, and `statute_sections` had a `category` field that was always `null`. The section corpus now reads the category back off `source_file`, which records the folder losslessly, so nothing is lost; wiring the labels in at parse time (fill-a-gap, as for judgments) is the proper fix and needs a re-parse. |
| `dates_found` on a statute | The judgment date extractor runs over statutes too and returns things like `0.1.00` and `21 of 1960`, which are enactment references, not dates. Harmless where it sits -- nothing reads it for a statute -- but it is noise in the record. |
| One statute section hole | `323_works_of_defence_act_1903` is missing section 1, which appears to be absent from the print rather than missed. Unproven. |
