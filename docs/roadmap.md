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

3. The LHC 5-slice gold benchmark, bit-identical, under the `offline` policy.

## Phase 0: make the gates real (no behaviour change)

| task | status |
| --- | --- |
| Byte snapshot gate over `data/pdfs`, with schema validation | done |
| `specter.v1` written down as a pydantic model, plus exported JSON Schema | done |
| ruff / mypy / pre-commit / GitHub Actions, scoped to new code | done |
| Gemini key moved from the URL to a header; corpus roots overridable by env | done |
| Dependencies: PyMuPDF pinned exactly, RapidOCR pinned to the measured version, unused `python-bidi` dropped | done |
| Gold PDFs (76, ~24 MB) and `phase5b.json` baseline made fetchable, manifest paths relative | waiting on files |
| Linux vs Windows byte equality of the snapshot | open |

## Phase 1: `docintel` package, seams, v2 output

- `git mv` the modules into `docintel/`, leaving `sys.modules` shims.
- Add the `Enricher` protocol. `LegalEnricher` absorbs the legal steps in `ingest_pdfs`, the inline metadata block and the legal validators. `LegalHints` is injected into the native engine.
- Add the `Engine` protocol, wrapping the two existing engines.
- Schema v2 lands as deliberate snapshot changes, one PR each: `id`, `uid`, `source`, `provenance`, `script`/`direction`, `readings`, and row/col on scanned table cells.

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
