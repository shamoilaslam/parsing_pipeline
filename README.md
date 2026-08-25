# Specter Case-Law PDF Parser

Specter is a deterministic, layout-aware parser for Pakistani case-law PDFs. It uses native PDF text and geometry first, keeps OCR isolated to scanned documents, and never uses an LLM to rewrite authoritative legal text.

## Start here

```powershell
# Install the package and core dependencies
python -m pip install -e .

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
<out>/manifest.json          one row per document, plus failures and duplicate stems
<out>/assets/...             rendered Urdu crops and scan images
```

JSON and Markdown stay at the output root on purpose: the Markdown references
`assets/` with relative links, which moving it into a sub-folder would break.

Useful flags for long runs:

| flag | why |
| --- | --- |
| `--recursive` | descend into sub-folders (needed for the SC corpus, which nests by judge) |
| `--workers N` | parse N documents at once; they are independent, so this scales close to linearly |
| `--skip-existing` | resume a part-finished run without re-parsing |
| `--quiet` | suppress the per-document progress line |
| `--engine digital\|scanned` | override routing (default `auto`) |

Scanned pages cost roughly 10s each on CPU, so `--workers` is what makes a full
corpus practical: SC is about 7 CPU-hours single-threaded, LHC about 14 more.
Output is identical whatever the worker count -- results are consumed in
submission order, and collision and resume decisions are made before any work
is handed out.

A PDF that cannot be read is recorded in `manifest.json` under `failures` and the
run continues -- one damaged file does not end a 30,000-document job. Two PDFs
sharing a stem in different sub-folders would overwrite each other, so the second
is skipped and reported under `duplicate_stems` rather than silently lost.

The older `python -m specter digital` batch remains for digital-only runs; it
writes to `artifacts/digital/` with validation reports under
`artifacts/digital/validation/` and a manifest at `artifacts/digital/corpus_summary.json`.

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
python -m specter benchmark --sc-metadata      # extraction vs the path labels
python -m specter benchmark --sc-gold          # the reviewed SC gold pages
python -m specter benchmark                    # the LHC gold pages
```

`--sc-metadata` deliberately scores the parser *before* labels are applied;
folding them in earlier would score the filename against itself.

## Court registry

Everything that differs between courts lives in `specter/courts.py`: the
canonical id, the spellings seen on the page, and the filename pattern where a
corpus states the case in its path. Adding a court is an entry in `COURTS`, not
an edit spread across the parser, the router and the benchmark.

```python
Court(court_id="islamabad_high_court", name="ISLAMABAD HIGH COURT")
```

The registry is why every metadata field a query filters on has a canonical
twin beside the verbatim one -- `court` / `court_id`, `decision_date` /
`decision_date_iso`, `parties` / `petitioner` + `respondent`. A corpus that
stores only what the page prints ends up with one court under several
spellings and dates in several formats, and every filter then misses rows
without raising anything.

## Repository map

```text
specter/
  courts.py              Court registry: ids, name variants, filename patterns
  specter_parser.py      Digital parser: blocks, boxes, tables, metadata, provenance
  scanned_parser.py      OCR parser for scanned pages
  ingest_pdfs.py         Router + corpus entry point (`specter parse`)
  benchmark.py           Scoring against the gold pages and the path labels
  inspect_html.py        Renders output beside the source pages (`specter inspect`)
  validate_document.py   Contract and quality checks on a parsed document
  evaluate_*.py          Text, layout and Urdu evaluation against references
  urdu_ocr.py            Local Urdu recognizer adapter
  urdu_vision.py         VLM fallback for Urdu crops; not wired to the CLI
tests/                   226 tests: contract, regression and unit
scripts/                 Small operator helpers
docs/                    Architecture, evaluation, gold-data, and project reports
data/pdfs/               Input PDFs; never overwritten by the pipeline
data/references/         LlamaParse/reference JSONs; not ground truth
models/                  Optional local OCR models and model notes
artifacts/gold/          Gold sets, manifests and annotation artifacts
artifacts/benchmark/     Benchmark baselines; each phase's numbers
notebooks/               Exploratory notebooks only
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
