# Pipeline plan: preprocess, OCR, parse, enrich

**Goal.** Turn any legal file into *silver* data: a faithful parse (text, page boxes, structure) plus
evidence-backed metadata. Fact extraction, legal reasoning and search live downstream.
Two tracks use the same code: **public** (law and caselaw, R2 + Dagster, goes to the vector DB) and
**client** (casefiles, nothing leaves our infrastructure, goes to fact extraction).

Status key: **built** = in the repo and tested. **untested** = code exists, never run for real.

## 1. Stages, formats, status

| # | Stage | Input | Output | Built | To do |
|---|---|---|---|---|---|
| 0 | **Ingest** | file: pdf, png/jpg/tif, docx/doc/odt/rtf, eml, zip | one PDF per document + `{id = sha256, source_name}` | pdf; images (EXIF, perspective, multi-page); Word (**untested**: needs LibreOffice) | eml (body + attachments as child docs), zip |
| 1 | **Triage + route** | a PDF page | `PageProfile {text_chars, dominant_image, pua_ratio, sensitive}` → `RoutePlan {route, reasons, max_policy}` | all of it, incl. CNIC/phone/medical/victim cues (English + Urdu) | page language; save the plan in the output (one planned snapshot re-record) |
| 2 | **OCR / read** | page image + route | blocks: `{text, bbox (pt), confidence, source, readings[]}` | native text; RapidOCR (CPU); **Nemotron 3.5 parser, engine and runner (CPU-tested)**; Urdu vision stage with policy gate | GPU run for speed; Urdu route; minimal verify (`needs_review`) |
| 3 | **Parse / structure** | blocks | `<stem>.json` (specter.v2) + `<stem>.md` | judgments and statutes | caselaw paragraph structure; light structure for client docs |
| 4 | **Enrich** | specter.v2 | `metadata/<stem>.json`, `document_types.json`, **`akn/<stem>.json`** | rule metadata, citations, classifier, **Akoma Ntoso JSON with ECLI-style ids** | evidence + confidence on every field; entities; outcome |
| 5 | **Package / serve** | bronze object or upload | silver folder + `manifest.json` + `report.json` | CLI `docintel run <folder>` | public: R2 adapter + Dagster asset + checks. Client: API + queue + per-tenant policy and storage |

**Field shape (target for every metadata field):** `{value, quote, page, bbox, confidence, source: rule|model}`.
A model-produced value is accepted only if its `quote` appears verbatim in the page text.

## 1b. Two output layers

- **Legal research layer** (unchanged): `<stem>.json`, `<stem>.md`, `metadata/`, `akn/`, `document_types.json`.
- **Fact-intelligence layer** (`facts/<stem>/<stem>.json` + `pages/NNNN.png`, written by `docintel/facts.py`):
  per page `source` (`text_layer`|`ocr`, with `engine`), text in reading order without headers/footers,
  blocks with boxes as page fractions `[x,y,w,h]`, `language` shares, `quality {score, ocr_confidence, flags}`,
  `printed_page_number`, header/footer text, per-page `status`. Rules: never invent text (Urdu no engine can
  read is an `illegible` block with its box), no correction (model markdown removed, nothing else), parsed
  files are never modified. Urdu goes to Gemini then LlamaParse on a copy (`--urdu-policy`, default
  `third_party`; pages flagged sensitive stay local).
- Not built yet for this layer: `source: mixed` (stamps/pasted scans on digital pages), `handwriting`/`stamp`/
  `signature` blocks, most quality flags (only `blank`, `skewed`, `urdu_unread`), `lines[]`, email/docx metadata,
  per-page model failure (a model failure still falls back per document), `seconds`.

## 2. What was measured

**Nemotron 3.5 Parse, on this CPU (no GPU).**
- English page vs human gold (2021LHC9943 p1, same image): **CER 0.016 / WER 0.016**; local RapidOCR on the same
  image: **CER 0.074 / WER 0.125**. One page only.
- Boxes: within 4 pt of the PDF's own text boxes (coordinates are plain fractions of the page image).
- Speed: about 15 minutes for a full page on 4 CPU cores (most of it reading the image), about a minute for a
  paragraph crop. CPU is for tests; GPU speed is not measured.
- **Urdu: not usable.** On a printed Nastaliq paragraph it returned letter-reversed words with wrong letters,
  and read another paragraph as a picture. Same result as the NVIDIA-hosted test. Layout boxes were plausible.

**GLiNER2 base (`fastino/gliner2-base-v1`, CPU, 10 to 20 s per document), zero-shot on 4 real judgments.**
Good: dates (all correct), statute and section mentions (on one order sheet: PPC, FIA Act 1974, ss. 392, 302,
155-C, 497), advocate names. Bad: **roles**. 3 of 10 distinct "judge" answers were judges (the rest were
advocates, prosecutors or a tribunal); a judge was labelled petitioner; a plaintiff bank was labelled respondent.
It also returns junk ("learned counsel", "HCJD/C-121" as a statute). **Verdict: not a replacement for the rules.
Use it only as an optional second pass for statute/section mentions, dates and unlabelled names, with a verbatim
check; do not use it for roles.**

**Rule-based enrichment, 16 digital LHC/SC judgments, filled from the page:** case number 16/16, court 15/16
(the miss is a law-journal reprint), parties 15/16, decision date (ISO) 12/16 before the fix, judges 11/16.
Defects found by hand-checking, and where they stand:
1. Order sheets had a date but no ISO date: **fixed** (3 documents changed in the snapshot, all intended; baseline re-recorded).
2. The classifier called order sheets judgments: **fixed** (4 real order sheets now `order`, 12 judgments unchanged).
3. No parties when a "JUDGMENT" title line sits above the caption (`2024LHC2628`): **open.** The cause is the
   caption scan stopping at that line. Not changed yet: there is no larger sample here to check the fix against.

**Akoma Ntoso JSON + ECLI-style ids** on the same 16: 15 get an id; the one without is the law-journal reprint
(no court), correctly left empty.

## 3. Order of work

1. **Core on real files** (needs your external drive): run `docintel run` on judgments, statutes and casefile
   documents; look at the report; fix what it shows. Add eml + zip. Commit.
2. **Scan engine:** Nemotron 3.5 for English scans on a GPU (4.6x lower CER than RapidOCR on the one page tested),
   RapidOCR as the CPU fallback. Confirm on more pages once a GPU is available.
3. **Urdu:** vision-model crops for public data; `needs_review` for client data. Revisit when we have our own model.
4. **Enrichment:** evidence + confidence on every field; outcome as an extracted quote; GLiNER2 only as the
   optional second pass above.
5. **Public track:** R2 adapter, Dagster asset with asset checks (schema valid, body not empty, confidence floor).
6. **Client track:** API + queue, per-tenant policy flag, router plan saved in the output (one snapshot re-record),
   per-tenant storage prefix and keys, no cross-tenant batches or caches.
7. **Later:** own Urdu VLM, handwriting, fine-tuning.

## 4. Cut (not now)

- Handwriting transcription: detect and flag only.
- Our own VLM: Urdu goes to a third-party vision model for public data and to `needs_review` for client data.
- Kanon 2 Enricher as a dependency: API-only beta, and unusable for client files. We use the open ILDGS ideas, not the model.
- Akoma Ntoso XML: JSON only, no XML export.
- A new layout model for Urdu, an ML document-type classifier, vLLM serving, fine-tuning, a VM per tenant, a review UI.
- Per-field model "agents": one ladder only: rules, then (optionally) a small extractor, then (public only) an API fallback.

## 5. Needs from you

- The external drive (real judgments, statutes, FIRs, pleadings) to test on.
- A GPU session to measure Nemotron speed (`docs/gpu-runbook.md`).
- LibreOffice on the server image (Word files).
- Whether client files may sit in R2, or must stay in storage we run.
