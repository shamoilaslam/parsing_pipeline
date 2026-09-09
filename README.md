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

# Parse a whole folder with automatic digital/scanned routing
python -m specter parse "D:\corpus\LHC" --out artifacts/run --recursive

# Or a single file / glob
python -m specter parse data/pdfs/2024LHC6559.pdf --out artifacts/run

# Run tests
python -m pytest tests -q
```

## Running it on your own PDFs

Nothing in the pipeline is specific to one corpus: point it at a folder of PDFs
and it routes each one itself. The whole loop is five commands.

```bash
git clone <this repo> && cd parsing_pipeline
python -m pip install -e ".[ocr,bench]"      # ocr is needed for scanned pages
python -m pytest tests -q                    # 484 tests, ~2 minutes

# 1. Parse.  --recursive walks sub-folders; --workers scales near-linearly.
python -m specter parse "/path/to/your/pdfs" --out artifacts/run --recursive --workers 4

# 2. See what the run produced: routes, failures, label disagreements, worst documents.
python -m specter report artifacts/run

# 3. Look at the output beside the source pages, with boxes drawn.
python -m specter inspect artifacts/run --out artifacts/demo.html --max-pages 3

# 4. Build the citation graph over everything parsed.
python -m specter graph artifacts/run

# 5. Link the statutes those judgments name to a statute corpus, if you have one.
python -m specter link artifacts/run --statutes "/path/to/statute/pdfs"

# 6. Turn parsed statutes into one record per section, ready to chunk.
python -m specter sections artifacts/run
```

Per document you get `<stem>.json` (canonical, with page/block geometry),
`<stem>.md`, and `metadata/<stem>.json`; per run, `manifest.json`.

**Resuming.** `--skip-existing` picks up where a run stopped without re-parsing
what is done, and still rebuilds the manifest for the whole corpus.

**A different court.** Court-specific knowledge lives in one place,
`specter/courts.py`. Adding a court is an entry in `COURTS` -- its name, the
spellings seen on the page, and where its corpus states the case in the file
path -- not an edit to the parser, the router and the benchmark. A court the
registry does not know still parses; it just gets no path labels.

**What you need on the machine.** CPU only. No GPU, no API key, no network for
the parsing and OCR paths. A vision model is used *only* for Urdu paragraphs,
and only if you run `specter urdu` yourself.

**See it before running it.** [`RESULTS.md`](RESULTS.md) has every measured
number and what it is measured against. [`docs/demo/demo.html`](docs/demo/demo.html)
is a self-contained page showing eight parsed documents -- one per route and
corpus -- beside their source pages; open it in a browser, nothing to install.

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

## Citation graph

```powershell
python -m specter graph artifacts/run
```

Every authority a judgment relies on, read from the blocks so each one carries
the page and box it was printed in, and inverted across the corpus into a
graph: one node per authority, one edge per reliance.

Pakistan writes citations two ways -- `2008 SCMR 598` and `PLD 2015 SC 123` --
and both are matched against a whitelist of law reports rather than a general
shape, because `<year> <word> <number>` also matches "2018 AND 3" and "Crl.
Appeal No. 2014 ... 9". A wrong edge asserts an authority the judge never
relied on, which is worse than a missing one.

Case-to-case resolution is **0%**, and measurement says why rather than
guessing: of 231 parsed LHC judgments, all 231 state a citation for themselves
and not one cites another by it. Courts cite the *reported* citation
(`2008 SCMR 598`); a judgment downloaded from a court website only knows its
*neutral* one (`2013 LHC 1314`). Two namespaces for the same cases, and closing
the gap needs a concordance only a law publisher has.

What the graph gives without it is co-citation -- and that is the query a
lawyer actually runs. "Which of our judgments rely on 2008 SCMR 598" is
answerable from the citing side alone, with the page and box of every
reliance.

It also checks itself. A judgment cannot rely on a case decided after it, so
`cited_year > citing_year` is a free correctness signal. Over 400 LHC
judgments it fired 36 times -- and the citations were right every time. What
was wrong was the *decision date*: `2014 LHC 3328` had been extracted as
`17.9.2004`. Both possibilities are reported, neither is corrected.

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

Two things a printed statute does that a naive reading gets wrong:

* **Its contents page numbers sections 1..N exactly as the body does.** Every
  entry is set like a heading, so read straight it duplicates the whole Act --
  91 phantom sections in the Sales Tax Act, each one a row of dot leaders. The
  body is taken to begin at the enacting formula (act number, commencement
  date, long title, preamble), and markers before it are index entries.
* **Some documents are not one Act.** Estacode is 1,044 pages of separate
  rule-sets, each numbered from 1, so "section 1" names sixty different texts.
  A run that begins again is counted: `numbering_restarts > 1` means the
  document has no single section run, `sections_complete` is reported as `null`
  rather than guessed, and `specter sections` names it in `index.json` under
  `compendia` instead of writing unciteable chunks into the corpus.

## Retrieval

Chunking, embedding and hybrid retrieval live in `rag/`, with their own
[README](rag/README.md).

One command takes a folder of PDFs to a searchable index -- parse, chunk,
index -- and needs nothing else on the machine:

```bash
python -m rag pipeline "D:\corpus\pakistancode" --out work --workers 4
python -m rag search "bail before arrest in a non-bailable offence" --index work/index
```

It picks its own backend (Metal, CUDA or CPU) and **resumes** if interrupted,
which matters because parsing 9,512 judgments takes hours and embedding 200,000
chunks is hours on Apple Silicon and days on a CPU. `work/pipeline.json` records
what each stage did. The stages are separate commands too (`rag chunk`,
`rag index`), and `--stages chunk,index` runs a subset.

Chunks carry the page and bounding boxes they came from, so an answer can point
at the region of the page that supports it -- verified by re-reading the PDF at
those coordinates, not asserted. Index building is sharded and resumes after an
interruption, because embedding this corpus is hours on Apple Silicon and days
on a CPU.

`rag evaluate` scores against a query set the corpus labels itself: a judgment
names the section it applies, and the statute corpus holds that section. It
reports **each retrieval leg separately**, because rank fusion weights legs
equally and a weak leg makes the blend worse than the strong leg alone.

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
  citations.py           Case citations and the graph over them (`specter graph`)
  statute_links.py       Judgment-to-statute linking (`specter link`)
  statute_sections.py    Section-addressable statute corpus (`specter sections`)
rag/                     Retrieval over a parsed corpus -- see rag/README.md
  normalise.py           One record shape, and the offset map that carries provenance
  chunk.py               Section chunks for statutes, paragraph chunks for judgments
  acts.py                How a statute is named when it is cited
  embed.py               BGE-M3 dense and sparse, and BM25 beside them
  build.py               Sharded, resumable index building
  retrieve.py            Hybrid retrieval fused by reciprocal rank
  evaluate.py            The evaluation set the corpus labels itself
tests/                   484 tests: contract, regression and unit
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

Read [docs/architecture.md](docs/architecture.md) for the design contract, [docs/evaluation.md](docs/evaluation.md) for metrics and limitations.

## Useful commands

```powershell
# Validate one canonical document
python -m specter validate artifacts/digital/2024LHC6559.json

# Build a cheap stratified annotation manifest
python artifacts/gold/build_gold_manifest.py "D:\corpus\SC" --output artifacts/gold/gold_manifest_SC.json

# Benchmark scanned preprocessing without changing production defaults
python -m specter benchmark --sc-gold
```

## Secrets

Copy `.env.example` to `.env` only if using optional Urdu vision providers. `.env` is ignored and must never be committed. Digital native parsing requires no API key.

## Status

The deterministic digital pipeline is the production path.

What is measured, and against what, is in [`RESULTS.md`](RESULTS.md). The short
version: **the only human-annotated gold is 192 Lahore High Court pages.**
Supreme Court and Islamabad High Court metadata are scored against labels the
corpora publish about themselves, which is useful but is not gold; their page
text is not scored against anything, and neither is the statute corpus. Table
structure and legal metadata are the areas that most need independent gold
before anyone claims corpus-wide accuracy.

Retrieval is measured, but **lexically only**: BGE-M3 has not been run here --
568M parameters and no GPU -- so every figure in `rag/` is the baseline a dense
leg has to beat rather than a result for the system. Nothing yet measures
finding a *precedent*, only finding a section.

LlamaParse outputs in `data/references/` are regression references, not ground
truth.
