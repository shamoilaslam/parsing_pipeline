# Specter Case-Law PDF Parser

Specter is a deterministic, layout-aware parser for Pakistani case-law PDFs. It uses native PDF text and geometry first, keeps OCR isolated to scanned documents, and never uses an LLM to rewrite authoritative legal text.

## Start here

```powershell
# Parsing only -- no torch, installs in seconds on any machine
python -m pip install -e .

# Add OCR (needed for scanned documents: 29% of IHC, 28% of SC, 3.5% of LHC)
python -m pip install -e ".[ocr]"

# Add the benchmark scorer, and the local Urdu experiments, only if you need them
python -m pip install -e ".[ocr,bench]" 

# Parse all fully digital PDFs in data/pdfs/
python -m specter digital

# Parse a whole folder with automatic digital/scanned routing
python -m specter parse "D:\corpus\LHC" --out artifacts/run --recursive

# Or a single file / glob
python -m specter parse data/pdfs/2024LHC6559.pdf --out artifacts/run

# Run tests
python -m unittest discover -s tests -v
```

## Parsing a corpus

`python -m specter parse <folder> --out <dir>` is the production entry point. It
walks the folder, routes each PDF to the engine that can read it, and writes:

```text
<out>/<stem>.json            canonical document: blocks, bboxes, tables, provenance
<out>/<stem>.md              Markdown rendering
<out>/metadata/<stem>.json   case identity, provenance, route, quality -- small enough to index
<out>/manifest.json          one row per document, plus failures and renamed outputs
<out>/assets/...             rendered Urdu crops (input to the VLM route)
```

JSON and Markdown stay at the output root on purpose: the Markdown references
`assets/` with relative links, which moving it into a sub-folder would break.

Useful flags for long runs:

| flag | why |
| --- | --- |
| `--recursive` | descend into sub-folders (needed for SC, which nests by judge, and IHC, which nests by year, judge and case) |
| `--workers N` | parse N documents at once; they are independent, so this scales close to linearly |
| `--skip-existing` | resume a part-finished run without re-parsing |
| `--quiet` | suppress the per-document progress line |
| `--engine digital\|scanned` | override routing (default `auto`) |
| `--page-images` | also keep the rendered image of every scanned page (off by default) |

Measured on a representative 60-document sample (10% scanned, 4 CPUs,
`--workers 3`): **5.7 s per document**, **~330 KB of output per document**. That
is roughly 115 hours and 24 GB for all 71,870 PDFs -- or **~9 GB** without
`--page-images`.

The rendered page image of a scanned page is OCR *input*. Saved, it was 63% of
a run's bytes and nothing read it back: `specter inspect` re-renders from the
source PDF, which is the same picture. It is therefore off by default, and the
canonical JSON reports `rendered_image: null` rather than a path to a file that
is not there. Urdu crops are different and are always written -- the text layer
under them is unrecoverable, so the crop is the data.
Output is identical whatever the worker count -- results are consumed in
submission order, and collision and resume decisions are made before any work
is handed out.

A PDF that cannot be read is recorded in `manifest.json` under `failures` and the
run continues -- one damaged file does not end a 30,000-document job. Two PDFs
sharing a filename in different sub-folders would overwrite each other, so
output names are assigned before anything is written: a filename that is
already distinct across the run is kept, and any other is replaced by one built
from its folder and a digest of its path, listed under
`renamed_for_uniqueness`. Nothing is skipped for sharing a name.

## Watching a run

```powershell
python -m specter report artifacts/run
```

Reads the run's `metadata/` folder -- not its manifest, so a run still in
flight or interrupted can be watched -- and answers the three questions worth
asking of tens of thousands of documents:

* **Did it work?** routes taken, failures, and how many documents their own
  diagnostics marked `REVIEW` rather than `PASS`.
* **Is it right?** every place the page and the corpus's own labels differ,
  grouped by field. At this scale that is the only correctness signal that
  needs no annotation.
* **What should I look at?** the lowest-scoring documents, named by their case
  so `specter inspect` has somewhere to start.

## Looking at the output

```powershell
python -m specter inspect artifacts/run --out artifacts/inspect/index.html
```

Writes one self-contained HTML file: each page rendered with every block's
bounding box drawn on it, the blocks listed beside it in reading order, and the
document's metadata with where each field was found. Hover a box or a block to
link the two. This is the fastest way to check a bounding box, because a box is
a spatial claim and reading JSON cannot falsify it.

`--max-pages` caps pages per document (default 6) so the file stays openable;
`--scale` and `--quality` trade file size against legibility.

## Supreme Court corpus

SC PDFs are stored as `<judge>/SCP_<type>.<number>_<year>_<date>.pdf`, so the
path itself names the case, its decision date, and the judge. The CLI reads
those labels and uses them to *fill* metadata fields extraction could not find,
never to overwrite one it did -- a filled field is marked `source="filename"`
with an empty bbox, and every disagreement is recorded under
`document.label_check` and counted in `manifest.json`.

Those same labels make SC metadata measurable without annotation:

```powershell
python -m specter benchmark --sc-metadata               # extraction vs the path labels
python -m specter benchmark --ihc-metadata --shuffle    # extraction vs each meta.json record
python -m specter benchmark --sc-gold          # the reviewed SC gold pages
python -m specter benchmark                    # the LHC gold pages
```

`--limit` reads the corpus in path order so a report stays comparable with
earlier ones; `--shuffle` draws from across it with a fixed seed, which IHC
needs because it is filed by year and judge.

Both label reports deliberately score the parser *before* labels are applied;
folding them in earlier would score the corpus against itself. They separate
`decision_date` (right, of every document the labels date) from
`decision_date_when_stated` (right, of those where extraction produced
anything), because a wrong value ships as fact and blocks the label from
filling the gap, while an empty one does neither.

## Court registry

Everything that differs between courts lives in `specter/courts.py`: the
canonical id, the spellings seen on the page, and the filename pattern where a
corpus states the case in its path. Adding a court is an entry in `COURTS`, not
an edit spread across the parser, the router and the benchmark.

```python
Court(
    court_id="islamabad_high_court",
    name="ISLAMABAD HIGH COURT",
    path_pattern=IHC_PATH,          # the case is named by the folder, not the file
    path_from="parent",
    judge_from_folder=True,
    sidecar="meta.json",            # and a record sits beside every judgment
    case_number_format="{case_type}-{case_number}-{year}",
)
```

The registry is why every metadata field a query filters on has a canonical
twin beside the verbatim one -- `court` / `court_id`, `decision_date` /
`decision_date_iso`, `parties` / `petitioner` + `respondent`. A corpus that
stores only what the page prints ends up with one court under several
spellings and dates in several formats, and every filter then misses rows
without raising anything.

## Islamabad High Court

```powershell
python -m specter parse "D:\Shamoil Data\specter_data\IHC" --out artifacts/ihc --recursive --workers 4
```

IHC gives every case a folder holding `judgment.pdf`, any interim
`order_<ddmmyyyy>_<n>.pdf`, and a `meta.json` naming the parties, the bench,
the author, the filing category and the date of the order. Three things follow
from that shape:

* **The record is a label, not an answer.** It fills a field extraction left
  empty, marked `source: "sidecar"` with no bbox, and never overwrites what the
  page states. Where the two differ, `label_check` says so.
* **The record is kept whole** under `document.source_metadata`. It carries
  more than the pipeline models -- every hearing, its short order and its
  disposal date -- and discarding what is not modelled yet would mean
  re-reading 22,000 files to get it back.
* **An order is not a judgment.** `document_kind` separates them: an order
  decides a step in the case and states no final disposition, so a corpus that
  filed both as judgments would answer "what did the court hold" with an
  adjournment.

Output names: 21,712 of IHC's 60,529 PDFs are called `judgment.pdf`. Where a
filename is not distinct across the run it is replaced by one built from the
case folder and a digest of the path (`renamed_for_uniqueness` in the
manifest). A corpus whose filenames already differ -- LHC, SC -- keeps them
exactly.

## Statutes

```powershell
python -m specter parse "D:\Shamoil Data\specter_data\pakistancode" --out artifacts/law --recursive --workers 4
```

Statutes are handled as a document *kind*, not another court. The output carries
`document_kind: "statute"`, statute metadata (title, enactment number,
commencement date, preamble), and a `structure` list naming every section,
chapter, part and schedule with its page and bbox.

Judgment-only fields (parties, bench, decision date) are dropped rather than
reported empty, so nothing downstream has to know which fields apply to which
kind of document.

Section detection is checkable without annotation: a statute numbers its
sections 1..N, so `sections_complete` being false means one was missed. That is
reported per document alongside `missing_sections`.

## Repository map

```text
specter/
  courts.py              Court registry: ids, name variants, path patterns, sidecars
  statutes.py            Statute metadata and section/chapter/schedule structure
  specter_parser.py      Digital parser: blocks, boxes, tables, metadata, provenance
  scanned_parser.py      OCR parser for scanned pages
  ingest_pdfs.py         Router + corpus entry point (`specter parse`)
  validate_document.py   Contract and quality checks on a parsed document
  report.py              What a corpus run produced (`specter report`)
  inspect_html.py        Output beside the source pages (`specter inspect`)
  benchmark.py           Scoring against the gold pages and the corpus's labels
  urdu_ocr.py            Local Urdu recogniser adapter
  urdu_vision.py         Urdu crops through a vision model (`specter urdu`)
tests/                   321 tests: contract, regression and unit
docs/                    Architecture, evaluation, and how the gold set was built
data/pdfs/               The sample PDFs the gold set and tests refer to
artifacts/gold/          The gold sets and the scripts that built them
models/                  The local Urdu recogniser's dictionary; weights are not committed
```

## Architecture

```text
PDF
  -> image-coverage router
  -> document fingerprint
  -> native PyMuPDF extraction or local OCR route
  -> paragraph/table/header-footer structure
  -> component validators
  -> component-level review/retry recommendation
  -> canonical Specter JSON + Markdown
  -> chunking gate
```

The digital route provides character/span/word geometry, paragraph bboxes, deterministic reading order, repeated header/footer detection, native table cells, metadata provenance, fingerprints, and a quality profile. The scanned route is separate and records OCR engine/model, preprocessing, line boxes, retries, and confidence.

Read [docs/architecture.md](docs/architecture.md) for the design contract, [docs/evaluation.md](docs/evaluation.md) for metrics and limitations, and [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) for the current corpus audit.

## Useful commands

```powershell
# Validate one canonical document
python -m specter validate artifacts/digital/2024LHC6559.json

# Compare text/layout with a reference (reference is not ground truth)
python -m specter evaluate `
  artifacts/digital/2024LHC6559.json `
  data/references/llamaparse/2024LHC6559.json `
  --output artifacts/digital/2024LHC6559_reference_metrics.json

# Build a cheap stratified annotation manifest
python artifacts/gold/build_gold_manifest.py "D:\corpus\SC" --output artifacts/gold/gold_manifest_SC.json

# Benchmark scanned preprocessing without changing production defaults
python -m specter benchmark --sc-gold
```

## Secrets

Copy `.env.example` to `.env` only if using optional Urdu vision providers. `.env` is ignored and must never be committed. Digital native parsing requires no API key.

## Status

The deterministic digital pipeline is the current production path. Tables and legal metadata remain the main areas requiring independent gold data before claiming corpus-wide accuracy. LlamaParse outputs in `data/references/` are regression references only.
