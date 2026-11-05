# ADR 0002: A legal-intelligence layer over the parsed corpus

- **Status:** proposed
- **Date:** 2026-10-06
- **Scope:** everything between "a judgment has been parsed" and "a retrieval
  pipeline can use it". Embedding, reranking and the RAG pipeline itself are
  **out of scope** here; they are being evaluated separately and will be
  rebuilt on top of the enriched data this ADR produces.
- **Plan:** [`docs/legal-intelligence-plan.md`](../legal-intelligence-plan.md)

## Context

### What exists on `main`

The parser (`specter/`) turns Pakistani judgments and statutes into canonical
JSON in which every block carries its page and bounding box. On top of that it
already extracts, deterministically:

| what | where | measured |
| --- | --- | --- |
| Court, bench, judges, case number, decision/hearing dates, parties (petitioner/respondent), counsel | `specter_parser._extract_metadata`, cover-table readers | against corpus labels (`RESULTS.md` §B): SC judge 95.0%, case number 82.5%; IHC against `meta.json` |
| Case citations, both grammars (`2008 SCMR 598`, `PLD 2015 SC 123`), against a closed whitelist of reporters, each with page and box | `specter/citations.py` | hand-sampled precision (§D) |
| Statutes named in a judgment (`acts`) and section numbers (`sections`) | `specter_parser`, `rag/acts.py` | — |
| A federal statute corpus: 1,039 statutes, 30,995 sections | `specter/statutes.py`, `statute_sections.py` | free checks (section numbering holes) |
| Judgment-to-statute links with jurisdiction and `version_risk` | `specter/statute_links.py` | 104 links over 90 judgments; `version_risk` on 59 |
| A citation graph: one node per authority, one edge per reliance | `citations.build_graph` | **0 of 1,118 edges resolve to a judgment we hold** |

### What the platform needs that does not exist

The product is moving from "retrieve passages that look relevant" to legal
intelligence: for any judgment, *what is it, what law does it apply, what
questions does it decide, what does it rely on, who relies on it, and can it
still be relied on*. Concretely:

1. **A citation graph that connects judgments to judgments.** It does not
   today, and the reason is structural, not a matching bug: judgments cite
   *private reporter citations* (PLD, SCMR, PLJ, CLC, YLR, PCrLJ, MLD …), which
   a publisher assigns on publication, while the documents we hold are keyed by
   *court identity* — a neutral citation (LHC, in every filename), a case
   number and decision date (SC filenames, IHC `meta.json`). The two key
   spaces share no field. IHC fills in a reported citation for only 230 of
   30,226 cases.
2. **Rich judgment metadata** beyond identity: disposition, relief, the
   impugned order, case type, area of law, the questions of law decided, the
   holding, and which paragraphs are facts, argument, analysis and order. A
   heading-driven role tagger was measured and rejected: 97.5% of blocks fell
   into catch-all labels, because Pakistani judgments are unheaded numbered
   paragraphs.
3. **Provision-level law.** "Applies the Cr.P.C." is not useful; "construes
   s. 497(2) Cr.P.C., as it stood in 2016" is. Provincial law is missing (35 of
   166 unmatched statute names are provincial or foreign) and there is no
   statute version history.
4. **A citator signal** — whether a judgment has been overruled, set aside on
   appeal, doubted, distinguished, or had its statutory basis changed.

### Constraints carried over

- Authoritative legal text is never rewritten. `text` is what the page printed
  (ADR 0001; `docs/architecture.md`, "text / markdown contract").
- **No number without its sample**, and every regression gate must stay green
  (unit tests, byte snapshot, LHC gold gate).
- A wrong edge is worse than a missing edge: a citation graph that asserts an
  authority the judge never relied on is a liability in legal work. This is
  the same trade the codebase already makes for bounding boxes.
- Cost must stay low: CPU and deterministic code first, models only where
  deterministic code measurably cannot do the job.
- Corpus data on this machine: `D:\Shamoil Data\specter_data\{LHC, SC,
  pakistancode}` and their `parsed_*` runs. IHC is not on this machine; IHC
  numbers in this repository come from earlier runs.

## Decision

### 1. One knowledge graph, with an explicit identity model

The intelligence layer is a typed property graph. Its central design choice
is to separate *what a decision is* from *the names it is known by*:

| node | identity | notes |
| --- | --- | --- |
| `Judgment` | court + case number(s) + decision date | one decision. The thing a citation ultimately points at. |
| `Alias` | the string as printed, normalised | a reported citation (`2008 SCMR 598`), a neutral citation (`2023 LHC 5924`), a case number, a document id. Many aliases → one `Judgment`. |
| `Document` | content hash (`document.id`, ADR 0001 / v2) | one PDF we hold. A judgment may be held more than once (several corpora, a reporter print and a court print). |
| `Court`, `Bench`, `Judge` | registry ids (`specter/courts.py`) | bench size matters for precedent weight. |
| `Statute`, `Provision`, `ProvisionVersion` | statute id + section key (+ in-force interval) | the unit a judgment construes. |
| `Issue` | canonical question of law (clustered) | "matters discussed". |
| `Topic` | node in a curated area-of-law taxonomy | e.g. Criminal › Bail › Pre-arrest bail. |

| edge | from → to | carries |
| --- | --- | --- |
| `CITES` | Judgment → Alias (→ Judgment once resolved) | every mention with page, box and context; `treatment` |
| `APPEAL_FROM` | Judgment → Judgment | result on appeal (upheld, set aside, modified, remanded, leave refused) |
| `CONCERNS` | Judgment → Provision | `role`: charged-under, applied, interpreted, struck-down, mentioned |
| `DECIDES` | Judgment → Issue | the holding on that issue, as an evidence span |
| `ABOUT` | Judgment → Topic | |
| `DECIDED_BY` | Judgment → Judge | author vs member |
| `AMENDS` / `OMITS` / `STRIKES_DOWN` | Statute or Judgment → ProvisionVersion | dates |

A citation is resolved when its `Alias` is attached to a `Judgment`. An
unresolved alias stays in the graph as a node of its own. "Which of our
judgments rely on 2008 SCMR 598" is answerable today from the citing side
alone, and that stays true.

### 2. Every fact carries evidence, or it is not stored

Every node attribute and edge written by the layer carries:

```
{ value, method, extractor, extractor_version, confidence,
  evidence: [{ document_id, page, bbox, char_start, char_end, quote }],
  review: { state: unreviewed | accepted | corrected | rejected, by, at } }
```

- `method` is one of `deterministic`, `label` (the corpus stated it),
  `linkage` (probabilistic record linkage), `model`, `human`.
- A field produced by a language model is **accepted only if its `quote` is
  found verbatim in the source text**. It is then mapped back to page and box
  through the offset map (`rag/normalise.py` already builds one). A quote that
  is not found is a rejected extraction, not a field. This makes
  hallucinations mechanically detectable and keeps the "never rewrite the
  page" rule.
- `extractor_version` makes re-runs incremental: only documents whose content
  hash or extractor version changed are recomputed.

### 3. Case identity is resolved from several independent sources, not by one matcher

No single source closes the gap, so citations are resolved through a fixed
ladder. Each rung records its method, and a higher rung outranks a lower one:

1. **Self-identity from the corpus.** Neutral citation (LHC filenames), SC
   filename labels, IHC `meta.json`. This seeds `Judgment` and its first
   aliases.
2. **Reporter headers on held documents.** Some corpus PDFs are reporter
   prints. The LHC gold page `2015LHC9171.pdf` opens with "PLJ 2016 Cr.C.
   (Lahore) 519 … Crl. Misc. No. 6486-B of 2015, decided on 18.11.2015": a
   reported citation, a case number and a date on one page, for a document we
   hold. Every such header is a concordance row at deterministic precision.
   *How many corpus documents carry one is unmeasured*; measuring it is the
   first task.
3. **Parallel citations.** Judges print one case under several reporters —
   "PLD 2010 SC 1 = 2010 SCMR 5", or two citations inside one bracket after one
   case name. Co-printed aliases are merged with union-find. This shrinks the
   alias space without touching the corpus.
4. **Case-name linkage.** A citation is usually printed beside its case name:
   "*Muhammad Akram v. The State* (2008 SCMR 598)". The parties, the year, the
   court the reporter implies, and the citing context are extracted. Candidates
   are blocked by court and year, and scored against `Judgment` records on party
   names, year and court with probabilistic record linkage (Fellegi–Sunter,
   implemented with **Splink**). A link is written only above a threshold set
   on a labelled sample, so precision is chosen, not hoped for.
5. **Appeal chains.** An appellate judgment recites what it is against —
   "against the judgment dated 12.03.2019 passed by the Lahore High Court,
   Lahore in W.P. No. 1234 of 2018". That is a court, a case number and a date:
   keys we already hold, with no reporter involved. Each matched recital
   creates an `APPEAL_FROM` edge, which is also the strongest citator signal
   (Decision 6).
6. **Human review** on a queue ordered by in-degree. Citations follow a power
   law, so reviewing the most-cited aliases first covers most of the citation
   mass for the least review.
7. **Licensed concordance (optional, a business decision).** Reporter
   publishers hold the complete concordance. If coverage from rungs 1–6
   plateaus too low, licensing it is cheaper than the long tail. Citation
   strings are facts and are used freely; **publisher headnotes and editorial
   summaries are their copyright and are never ingested or reproduced**
   without a licence.

The headline metric is **the share of `CITES` edges to Pakistani reporters
that resolve to a `Judgment`**, against today's 0 of 1,118, reported together
with hand-sampled precision. A rise in coverage at falling precision is a
regression.

### 4. Deterministic extraction first; a language model only for what it measurably adds

Stays deterministic (already built, already measured): citations, case
numbers, dates, court and bench, judges, statute and section mentions,
corpus-label corroboration, reporter headers, parallel-citation grouping,
appeal recitals (regex-first; a model only on the residue).

Goes to a language model, as **structured output against a fixed schema**,
under the evidence rule of Decision 2:

- disposition and relief (allowed, dismissed, partly allowed, remanded,
  converted, bail granted or refused, sentence altered);
- case type and the impugned order;
- questions of law decided, and the holding on each;
- paragraph roles in a small, measurable label set (facts, submissions,
  analysis, holding, order; at most six labels);
- the role of each provision mention (Decision 5);
- the treatment of each citation (Decision 6);
- topic assignment against the curated taxonomy (Decision 7).

**Inputs are windows, not whole judgments**, which is most of the cost
control:

| task | window |
| --- | --- |
| disposition, relief | the last 30% of the judgment (an outcome verb occurs there in 88.9% of 90 sampled judgments) |
| case type, impugned order, appeal recital | the head of the document |
| citation treatment | the paragraph containing each citation, plus its neighbours |
| provision role | the sentence containing each mention |
| issues, holdings, paragraph roles | the full text, but only once per document and only by the bulk model |

**Models are chosen by bake-off on our own labelled data**, as in ADR 0001:

- Bulk tier: a low-cost model (candidates: Claude Haiku 4.5, Gemini 2.x
  Flash-Lite), run through the provider's **batch API** with prompt caching
  on the fixed instructions.
- Escalation tier: a stronger model (candidates: Claude Sonnet 5.5, Gemini
  Flash/Pro) only for extractions that fail validation, fail the evidence
  check, or fall below a confidence threshold.
- Later, a self-hosted open model (Qwen3 class) fine-tuned on
  **human-verified** labels, to cut unit cost and to serve confidential client
  documents under the `self_hosted` policy (ADR 0001). Whether a provider's
  terms allow training on its outputs is the open legal question already
  recorded in ADR 0001, and it gates that step.
- Prices are not written into this ADR. They are read from the provider's
  current price sheet when the bake-off is costed.

Public judgments are public data, so third-party APIs are permitted for them
under the ADR 0001 policy. Client documents are not processed by this layer
until the `self_hosted` path exists.

### 5. Law is linked at provision level, and in time

- Every statute mention is normalised to `statute_id + section key`:
  `s. 497(2) Cr.P.C.`, `Article 199 of the Constitution`, `Order XXXIX Rules 1
  & 2 CPC`, `302(b) PPC`. This extends `statute_links` and `rag/acts.py`.
- Each mention gets a **role**. The difference between "the FIR was registered
  under s. 302 PPC" and "the question is the scope of s. 302(b)" is the
  difference between noise and signal.
- **Provincial codes** (Punjab, Sindh, Khyber Pakhtunkhwa, Balochistan) are
  added as statute corpora. The linker already reports misses by
  jurisdiction, so the size of the gap is known before the work starts.
- **Point-in-time text.** A `ProvisionVersion` timeline is built from the
  amendment footnotes the statutes carry ("Subs. by Act No. XLI of 2025, s.2")
  plus the amending Acts. Where the timeline is complete, a judgment links to
  the version in force on its decision date. Where it is not, the link keeps
  `version_risk`, which is the honest state.

### 6. The citator is computed from evidence, never asserted as a label

A judgment's status is derived from four signals, each with its evidence
chain:

1. **Treatment by citing judgments.** Each citing sentence is classified as
   followed / relied upon / approved / referred / distinguished / doubted /
   per incuriam / overruled. Pakistani judgments use formulaic cues ("reliance
   is placed on", "distinguishable on facts", "per incuriam", "no longer good
   law"). Rules give a high-precision first pass; the model handles the rest.
2. **Appellate history.** `APPEAL_FROM` edges with their result: set aside,
   modified, upheld, leave refused.
3. **Statutory change.** A provision the judgment construed has since been
   amended, omitted, or struck down.
4. **Precedent weight.** Under Article 189 Supreme Court decisions bind all
   courts. Under Article 201 a High Court binds the courts subordinate to it.
   A larger bench outranks a smaller one; a later decision of a larger bench
   overrules an earlier one of a smaller bench.

The surfaced status is one of:

- **negative**: overruled, set aside, or declared per incuriam by a court with
  the power to do so;
- **caution**: distinguished, doubted, or statutory basis changed;
- **positive**: followed or approved;
- **no treatment seen**: none of the above.

It always comes with the citing passages and the count of judgments
examined. The system never says "good law". It can only say what it has
seen, and it says how much it has seen.

### 7. "Matters discussed" is two layers: a curated taxonomy and clustered issues

- A **topic taxonomy** of a few hundred nodes, authored with practising
  lawyers and versioned in the repository. It is assigned per judgment by the
  bulk model and audited on a sample.
- **Issues**: each judgment's questions of law, extracted as short canonical
  statements with their evidence spans. They are clustered into `Issue` nodes
  across the corpus, and the clusters are reviewed by lawyers. This yields the
  path `Judgment → DECIDES → Issue ← CONCERNS → Provision`, which answers "how
  have courts decided this question under this section since 2015".
- Headnote-style summaries are **written by us** from the judgment text, never
  copied from a reporter.

### 8. Storage: Postgres first

- The graph lives in **Postgres** as typed node and edge tables with JSONB
  evidence, queried with recursive CTEs. Postgres is already the system of
  record in ADR 0001 (service shape). The **Apache AGE** extension is added if
  Cypher-style traversal earns it.
- A dedicated graph database (e.g. Neo4j) is adopted only when a measured
  query cannot be served acceptably from Postgres.
- Extraction outputs are also written as versioned JSONL artifacts beside the
  parsed corpus, so the graph can be rebuilt from scratch, diffed between
  extractor versions, and snapshot-gated like the parser.

### 9. Evaluation and review are built before the extractors they measure

Each extractor ships with a gold set and a number, in the RESULTS.md tiers:

| extractor | gold | metric |
| --- | --- | --- |
| citation resolution | hand-labelled citation → judgment pairs, sampled across reporters and decades | precision, coverage |
| parallel-citation merge | hand-checked merge groups | merge precision |
| appeal-chain linking | hand-checked recital → judgment pairs | precision, recall |
| disposition | lawyer-labelled judgments | accuracy per class |
| provision role | lawyer-labelled mentions | macro-F1 |
| citation treatment | lawyer-labelled citing sentences, oversampling negatives | per-class precision/recall; **negative-class precision reported on its own** |
| issues and topics | lawyer review of a stratified sample | precision@k, agreement |

- Annotation runs in **Label Studio** (already on the ADR 0001 roadmap). Every
  correction becomes both eval data and future training data.
- A language model may grade outputs only after its grades have been
  calibrated against lawyer labels on the same task.
- The existing gates (unit tests, byte snapshot, LHC gold gate) keep running
  unchanged. This layer reads parser output and never changes it.

## Alternatives considered

- **"Just use a bigger model on whole judgments."** Rejected. It costs the
  most, cannot be audited, and does not solve the citation gap at all: no
  model can know that `2008 SCMR 598` is a given LHC judgment without a
  concordance.
- **Rely on licensing alone for the citation graph.** Kept as an option
  (Decision 3, rung 7), not the plan. Rungs 1–5 are cheap, are ours, and
  establish how much a licence would actually add before money is spent.
- **Graph database from day one.** Rejected for now (Decision 8). It is one
  more system to run before any query has shown that Postgres is not enough.
- **A 16-label rhetorical-role tagger.** Rejected on measurement: it is not
  supportable without annotation, and the heading-driven version failed at
  97.5% catch-all. A small label set is measurable and enough to scope
  extraction windows.
- **Asserting "good law" or "bad law".** Rejected (Decision 6). The corpus is
  incomplete, so absence of negative treatment is not evidence of good
  standing.

## Consequences

- The citation graph becomes the platform's core asset: resolved edges plus
  lawyer-verified treatment. Parsers and models are replaceable; this data is
  not.
- New data acquisition is needed (provincial codes, and amending Acts for
  version timelines). Each new source gets its own folder and its own
  approval. Nothing is written under `specter_data` beyond `LHC`, `SC` and
  `pakistancode` without that.
- Lawyer time becomes a planned input: taxonomy authoring, gold labelling,
  and the review queue.
- **Two legal questions** need counsel before the work that depends on them:
  - reuse of reporter citation strings, and the boundary with publisher
    headnotes;
  - whether a provider's terms permit fine-tuning on its outputs (already
    open in ADR 0001).
- **Relationship to Phase 1 (ADR 0001).** This branch starts from `main`,
  which predates the `docintel` package and the v2 identity fields
  (`document.id`, block `uid`, `readings[]`) built on `phase-1`. The layer
  needs content-hash document ids and stable block addresses, so it either
  rebases onto `phase-1` once that merges, or computes the same ids itself
  until then. The plan picks the order.
- Retrieval, embeddings and reranking will consume this graph. They are
  designed separately, once enriched data exists.
