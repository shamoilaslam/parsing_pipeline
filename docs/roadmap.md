# Roadmap: Specter → `docintel`

Decisions and their reasons are in [ADR 0001](adr/0001-generalise-specter-into-docintel.md).

Each phase ends with a check that can be run.

## Every-phase gate

Every phase must pass all three:

1. The unit tests.
2. The byte snapshot:

   ```
   python -m eval.snapshot check data/pdfs
   ```

3. The LHC 5-slice gold benchmark, bit-identical per page, under the `offline` policy. This takes about 80 minutes, almost all of it OCR on the scanned gold documents:

   ```
   python -m specter benchmark --output artifacts/benchmark/latest.json
   python -m eval.gold_gate artifacts/benchmark/latest.json
   ```

## Phase 0: make the gates real (no behaviour change)

| task | status |
| --- | --- |
| Byte snapshot gate over `data/pdfs`, with schema validation | done |
| `specter.v1` written down as a pydantic model, plus exported JSON Schema | done |
| ruff / mypy / pre-commit / GitHub Actions, scoped to new code | done |
| Gemini key moved from the URL to a header; corpus roots overridable by env | done |
| Dependencies: PyMuPDF pinned exactly, RapidOCR pinned to the measured version, unused `python-bidi` dropped | done |
| LHC gold baseline regenerated and committed (`eval/baselines/lhc_gold.json`); it reproduces RESULTS.md §A exactly | done |
| Per-page gold gate (`python -m eval.gold_gate <report>`) | done |
| The 76 gold source PDFs (~24 MB) live only at `D:\Shamoil Data\specter_data\LHC`, so the gold gate runs on that machine only | open: needs the PDFs in the repo (Git LFS) |
| Linux vs Windows byte equality of the snapshot | open |

## Phase 1: `docintel` package, seams, v2 output

| task | status |
| --- | --- |
| `git mv` the modules into `docintel/`, leaving `sys.modules`-style aliases under `specter` | done |
| `Engine` protocol (`docintel/engines/base.py`) wrapping the native and OCR engines | done |
| `Enricher` protocol; `LegalEnricher` takes the post-layout legal steps; `LegalHints` injected into the metadata pass | done |
| v2: document `id`, block `uid` | done |
| v2: block `script` / `direction` | done |
| v2: block `readings[]` | done |
| v2: `row` / `column` on scanned table cells | done (unit-tested only: no scanned ruled table in `data/pdfs`) |
| v2: block-level `source` and `provenance` | open: undefined; needs a decision on what they add to the existing `source` string and `metadata_provenance` |
| Bump `schema_version` to `specter.v2` and make the v2 fields required | open: waits on the item above |
| Gold gate on slice 4 | open: `benchmark` scores the engines directly and does not pass through the v2 step, so it cannot move; run it once at the end of the phase |
| Validators (`validate`) and the inline metadata block moved behind `LegalEnricher` | open: deliberately left (ADR 0001 slice depth) |

Gates passed per slice: unit tests, byte snapshot, and (slices 1-3 together) the LHC gold gate, 191 pages, 0 changed.

## Phase 2: triage, cache, Gemini, Urdu at corpus scale for public data

- Page signals, a pure router, and the tenant policy gate.
- Region escalation: crop → cache → batch → verify → accept.
  - The char-bag check against native Nastaleeq text lets readings be accepted automatically, with no annotation.
- `urdu/normalize` and `urdu/verify`.
- Build a printed-Urdu gold set of 120 pages.
- The first corpus-scale Urdu run.

## Phase 3: self-hosted GPU engines

- Modal functions.
- Bake-offs: English layout, and confidential Urdu.
- A threshold sweep on the scanned gold.
- An egress allowlist for confidential jobs.

## Phase 4: service MVP

- FastAPI, Procrastinate/Postgres, R2.
- Idempotency and signed webhooks.
- Tenants, budgets and a cost ledger.
- Observability.

## Phase 5: format adapters

Images and photos, DOCX, XLSX, EML/MSG, HTML, WhatsApp, ZIP. Attachments and embedded media become child documents.

## Phase 6: review queue and data flywheel

- Label Studio review.
- Human corrections become readings.
- Fine-tune the self-hosted Urdu engine on verified pairs.

## Phase 7: handwritten Urdu

A handwritten gold set, a handwriting ladder with mandatory review, and a classifier.
