# ADR 0001: Generalise Specter into `docintel`, a bilingual document-intelligence pipeline

- **Status:** accepted
- **Date:** 2026-09-29

## Context

Specter parses Pakistani judgments and statutes from PDF. Measured against gold:

| slice | CER |
| --- | --- |
| digital | 0.0011 |
| scanned (RapidOCR, CPU) | 0.090 |

Urdu is the weak point. On Urdu words alone, the native text layer gets 40 of 576 right and the local Arabic recogniser gets 1 of 576. The vision-model route for Urdu has never been run at corpus scale.

The platform built on top of it, fact intelligence and case graphs, needs the parser to go further:

- More kinds of document: client case files (FIRs, petitions, affidavits, contracts), office formats (DOCX, XLSX, EML/MSG, HTML), images and phone photos, and WhatsApp exports.
- English and Urdu both, with every fact traceable to a page region.
- The lowest cost we can manage.

## Decision

1. **One package, `docintel`, grows out of `specter/`.** Code is moved, not rewritten. `specter/` becomes alias shims so existing commands and tests keep working.
2. **Layout and legal knowledge are separated.** Engines produce layout. Legal knowledge (courts, citations, statutes, case metadata) runs afterwards as an *enricher*.
   - Where a legal rule currently steers a layout decision, it is injected as a hint rather than duplicated.
3. **Pages are triaged cheaply on CPU, and only what needs it is escalated.**
   - Born-digital text stays native, at $0.
   - Only low-confidence or Urdu *regions* are cropped and sent to a model.
   - Boxes always come from the layout or OCR engine. A language model supplies text only.
4. **Every candidate transcription is kept.**
   - `text` holds the accepted reading.
   - `readings[]` holds all of them, including the native text, with the checks that accepted or rejected each one.
   - Heuristics never rewrite text.
5. **Data policy is set per tenant.**
   - Every engine carries a policy class: `local`, `self_hosted` or `third_party`.
   - Public corpora may use third-party APIs. Confidential client documents may only use self-hosted engines.
   - The CLI and CI default to `offline`, which is local engines only.
6. **Engines are chosen by bake-off on our own gold, not by leaderboard.** Candidates, from research in Sept 2026:
   - **English layout:** PaddleOCR-VL-1.6 (Apache-2.0) vs Nemotron Parse v1.2 (NVIDIA Open Model License). Both return boxes and allow commercial use. Nemotron has no Urdu.
   - **Urdu, public data:** Gemini Flash-Lite, then Gemini Flash. Gemini holds the best published Urdu result: 2.5 Pro, WER 0.133, from the LREC 2026 Urdu newspaper benchmark.
   - **Urdu, confidential data:** DeepSeek-OCR vs PaddleOCR-VL vs Qwen3-VL-8B, self-hosted. DeepSeek-OCR is the strongest open model on the MORE benchmark, at 70.5.
   - **Excluded:**
     - Marker, Surya and Chandra: their licences are capped by company revenue.
     - AWS Textract: no Urdu.
     - MinerU: scores 0 on Urdu.
     - OpenRouter's free tier: unknown sub-processors.
7. **Service shape:**
   - FastAPI plus Procrastinate, a queue backed by the Postgres we need anyway.
   - Storage on R2 or another S3-compatible store.
   - Serverless GPU (Modal) that scales to zero.
   - A content-hash cache.
   - HMAC-signed webhooks.
8. **Handwritten Urdu comes last.** Until then, regions suspected to be handwriting are flagged for review, not transcribed.

## Consequences

- The LHC gold gate and the byte snapshot (`eval/snapshot.py`) must stay identical under the `offline` policy at every step. An intended change lands with a re-recorded baseline and a decision-log entry in `docs/architecture.md`.
- The canonical schema grows by addition only. `specter.v1` is written down first (`docintel/schema/specter_v1.py`), and v2 is a strict superset of it.
- Two legal questions need counsel before the work that depends on them:
  - Whether Gemini's terms allow training an open model on its outputs.
  - Whether Modal counts as "self-hosted" under client contracts.

The phase plan is in `docs/roadmap.md`.
