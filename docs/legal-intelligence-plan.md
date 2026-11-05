# Plan: the legal-intelligence layer

Decisions and their reasons are in
[ADR 0002](adr/0002-legal-intelligence-layer.md). This file is the order of
work, what each phase delivers, and the check that says it is done.

**Out of scope:** embedding models, reranking and the RAG pipeline. They are
being evaluated separately and will be rebuilt on the enriched data produced
here.

**Every phase**:
- keeps the existing gates green (unit tests, byte snapshot, LHC gold gate);
- never changes parser output;
- writes every fact with the evidence record from ADR 0002, Decision 2;
- ends with a measured number in `RESULTS.md`, in the tier it belongs to, with
  its sample.

**Data on this machine:** `D:\Shamoil Data\specter_data\{LHC, SC,
pakistancode}` and their `parsed_*` runs. Nothing else under `specter_data` is
read or written. New sources (provincial codes, amending Acts) go into a new
folder agreed first.

---

## LI-0: Foundations

Everything later phases write into, and the means to measure them.

| task | deliverable |
| --- | --- |
| Settle the branch base: rebase onto `phase-1` (the `docintel` package and v2 ids) once it merges to `main`, or compute `document.id` and block addresses locally until then | **decided: computed locally.** `intel.identity` hashes the source PDF (SHA-256, the same definition as v2 `document.id`), so ids agree when the branches meet |
| Graph schema in Postgres: `judgment`, `alias`, `document`, `court`, `bench`, `judge`, `statute`, `provision`, `provision_version`, `issue`, `topic`, plus edge tables, each with JSONB `evidence` and `review` | `intel/schema.sql` + migrations |
| The evidence record as a pydantic model, and the verbatim-quote check that maps a quote to page and box through the offset map | `intel/evidence.py`, unit-tested on quotes that are present, absent, and present twice |
| Extractor registry: name, version, input window, output schema; content-hash + version caching so a re-run only recomputes what changed | `intel/extractors/` base |
| Versioned JSONL artifacts per extractor beside the parsed corpus, and a loader that rebuilds the graph from them | rebuild command; graph snapshot gate (like `eval.snapshot`) |
| Label Studio project templates for each gold set in ADR 0002, Decision 9 | templates + export → gold JSON converters |
| Evaluation harness: one command per extractor, producing precision, recall or coverage against its gold set | `python -m intel.evaluate <extractor>` |

**Exit:**
- Loading the existing `parsed_LHC` and `parsed_SC` metadata into the schema
  produces a graph.
- Rebuilding from artifacts is byte-identical.
- The harness runs against an empty gold set without error.

---

## LI-1: Judgment identity

One `Judgment` per decision, with every alias we can attach from what we
already hold.

| task | deliverable |
| --- | --- |
| Seed `Judgment` from corpus self-identity: LHC neutral citation (filename), SC filename labels (case type, number, year, decision date) | judgments + `neutral` and `case_number` aliases |
| Case-number normaliser: "Crl. Misc. No. 6486-B of 2015", "W.P. No.23303/13", consolidated "W.P. Nos. 1, 2 & 3 of 2020" → canonical keys per court | `intel/case_numbers.py` + tests on corpus spellings |
| Duplicate detection across documents: the same decision held twice (two corpora, a court print and a reporter print) → one `Judgment`, several `Document`s | merge report with every merge's evidence |
| **Measure reporter headers**: how many held documents open with a reporter citation (e.g. "PLJ 2016 Cr.C. (Lahore) 519"), by corpus and reporter | a number in `RESULTS.md` (tier C) |
| Reporter-header extractor: citation + case number + date from the head of the page → concordance aliases at deterministic precision | `reported` aliases from held documents |

**Exit:**
- Every parsed LHC and SC document maps to exactly one `Judgment`.
- Duplicate merges are hand-checked on a sample of 100 (tier D).
- The reporter-header count is recorded.

---

## LI-2: Citation resolution

Turn `CITES` edges into judgment-to-judgment edges. The baseline is **0 of
1,118** resolved.

| task | deliverable |
| --- | --- |
| Citation context extractor: for each mention, the case name printed beside it (parties, "v."/"versus"), the bracket group, and the sentence | context on every `CITES` mention |
| Parallel-citation grouping: co-printed citations ("PLD 2010 SC 1 = 2010 SCMR 5", two citations in one bracket after one case name) → union-find alias groups | alias groups + merge-precision gold |
| Case-name linkage with Splink: block on court and year; compare parties, year and court; train on rung-1/rung-2 matches as positives; set the threshold on a labelled sample for a chosen precision | `linkage` aliases with match weights |
| Appeal-recital extractor: "against the judgment dated … passed by … in W.P. No. …" → court + case number + date, matched to `Judgment` | `APPEAL_FROM` edges (result filled in LI-5) |
| Review queue ordered by alias in-degree: accept, correct or reject, written back as `human` | Label Studio queue + write-back |
| Gold sets: citation → judgment pairs stratified by reporter and decade; appeal recital → judgment pairs | `artifacts/gold/citation_resolution.json`, `appeal_links.json` |

**Exit:**
- Resolved share of Pakistani-reporter `CITES` edges reported against the
  0 / 1,118 baseline, by resolution method.
- Hand-sampled precision of resolved edges on at least 200 edges, proposed
  target **≥ 0.98** (a wrong edge is worse than a missing one).
- Coverage is reported only beside precision.
- A decision recorded on whether licensing a concordance is needed (ADR 0002,
  Decision 3, rung 7), based on where coverage plateaus.

---

## LI-3: Provision-level law

| task | deliverable |
| --- | --- |
| Provision normaliser: `s. 497(2) Cr.P.C.`, `Article 199 of the Constitution`, `Order XXXIX Rules 1 & 2 CPC`, `302(b) PPC` → `statute_id + section key`, extending `statute_links` and `rag/acts.py` | `CONCERNS` edges at provision level |
| Provision-role classifier (charged-under / applied / interpreted / struck-down / mentioned): rules first, then the model on the residue, on the sentence window | `role` on every `CONCERNS` edge |
| Provincial statute corpora: Punjab first (the LHC corpus applies it constantly), then Sindh, KP, Balochistan; parsed with the existing statute pipeline into a new agreed folder | statutes + sections per province; linker miss rate by jurisdiction before and after |
| Version timelines: amendment footnotes ("Subs. by Act No. XLI of 2025, s.2") + amending Acts → `provision_version` with in-force intervals; link a judgment to the version in force on its decision date where the timeline is complete | `provision_version`; `version_risk` kept where it is not |
| Gold: lawyer-labelled provision mentions with roles | `artifacts/gold/provision_roles.json` |

**Exit:**
- Share of statute mentions linked at provision level.
- Role macro-F1 on gold.
- The provincial miss rate re-measured (from 35 of 166 unmatched).
- The count of judgment links that move from `version_risk` to an exact
  version.

---

## LI-4: Judgment metadata by model, with evidence

Run the model bake-off before bulk extraction.

| task | deliverable |
| --- | --- |
| Output schemas (pydantic) for disposition, relief, case type, impugned order, questions of law, holdings, paragraph roles (≤ 6 labels) | `intel/schemas/` |
| Window builders (ADR 0002, Decision 4): tail 30% for disposition; head for case type and impugned order; full text once for issues, holdings and roles | `intel/windows.py` |
| Gold sets, lawyer-labelled: disposition and relief (stratified by court and case type), paragraph roles, questions of law | `artifacts/gold/disposition.json`, `roles.json`, `issues.json` |
| Bake-off: bulk candidates (Claude Haiku 4.5, Gemini Flash-Lite) and escalation candidates (Claude Sonnet 5.5, Gemini Flash/Pro) on the gold, scored on accuracy, evidence-check pass rate, and cost per 1,000 judgments from current price sheets | a decision-log entry choosing the bulk and escalation models |
| Bulk run through batch APIs with prompt caching; failures, evidence-check rejects and low-confidence outputs escalate | enriched metadata for the LHC and SC corpora |
| Topic taxonomy v1 authored with lawyers (a few hundred nodes), versioned in the repo; assignment by the bulk model | `intel/taxonomy/topics.yaml`; `ABOUT` edges |
| Issue clustering: canonical question-of-law statements → `Issue` nodes; lawyer review of the largest clusters | `Issue` nodes; `DECIDES` edges with holding spans |

**Exit:**
- Disposition accuracy per class.
- Paragraph-role agreement.
- Issue and topic precision on a stratified lawyer-reviewed sample.
- Evidence-check pass rate.
- Measured cost per 1,000 judgments, and the projected cost for the full
  corpus.
- Every stored field passes the verbatim-quote check.

---

## LI-5: Treatment and the citator

| task | deliverable |
| --- | --- |
| Treatment rules: high-precision cue patterns ("reliance is placed on", "distinguishable", "per incuriam", "overruled", "no longer good law") on the citing sentence | `treatment` on `CITES` edges with `method: deterministic` |
| Treatment model for the residue, on the citation's paragraph window, with the label set followed / relied upon / approved / referred / distinguished / doubted / per incuriam / overruled | `treatment` with `method: model` |
| Appellate results on `APPEAL_FROM` edges (upheld / set aside / modified / remanded / leave refused), read from the appellate judgment's disposition (LI-4) | results on `APPEAL_FROM` |
| Statutory-change signal: the construed `provision_version` was later amended, omitted or struck down | flag with the amending instrument as evidence |
| Precedent weight: court level (Arts. 189, 201), bench size, date; larger-bench overrules smaller | weight on each `Judgment` |
| Status computation: negative / caution / positive / no treatment seen, with the evidence chain and the number of citing judgments examined | `intel/citator.py` + one record per `Judgment` |
| Gold: lawyer-labelled citing sentences, **oversampling negative treatment** (it is rare and it matters most) | `artifacts/gold/treatment.json` |

**Exit:**
- Per-class treatment precision and recall, with negative-class precision
  reported on its own (proposed target ≥ 0.95).
- Appellate-result accuracy.
- Lawyers have audited a sample of `negative` statuses end to end.

---

## LI-6: Graph access for downstream consumers

What the later RAG pipeline and the product read from.

| task | deliverable |
| --- | --- |
| Read API over the graph: `judgment(id)`, `aliases`, `citing(id, treatment=…)`, `cited_by`, `appeal_history`, `provisions(id)`, `judgments_concerning(provision, at_date, role)`, `issues(id)`, `status(id)` | `intel/api.py` (library first; HTTP later with the service in ADR 0001) |
| Export: enriched per-judgment JSON (identity, aliases, disposition, provisions, issues, topics, citations with resolution and treatment, status), each field with its evidence | `intel export` command; the input contract for the RAG rebuild |
| Graph-level free checks: no `APPEAL_FROM` cycles; appellate court above lower court; decision dates ordered along `APPEAL_FROM` and `CITES` (a judgment cannot cite a later one) | checks in the evaluation harness (tier C) |

**Exit:**
- The export validates against its schema for every judgment.
- The free checks pass, or report every violation by name.
- The RAG work can start from the export alone.

---

## Order and dependencies

```
LI-0 ─▶ LI-1 ─▶ LI-2 ─────────────▶ LI-5 ─▶ LI-6
          │                        ▲
          └──▶ LI-3 ───────────────┤
                 LI-4 (bake-off can start after LI-0) ┘
```

- LI-2 is the critical path and the platform's core asset; start it as soon
  as LI-1 seeds identities.
- LI-3 and LI-4 can run in parallel with LI-2.
- LI-5 needs resolved citations (LI-2), provision versions (LI-3) and
  dispositions (LI-4).

## Decisions needed from people, not code

| decision | blocks | who |
| --- | --- | --- |
| Rebase onto `phase-1` now, or compute ids locally first | LI-0 | engineering |
| Use of reporter citation strings, and the boundary with publisher headnotes | LI-2 publishing | counsel |
| Whether to license a reporter concordance | after LI-2 measures the plateau | business |
| Topic taxonomy v1 and labelling capacity (who labels, how many hours) | LI-3, LI-4, LI-5 gold sets | lawyers |
| Folder and source for provincial codes and amending Acts | LI-3 | engineering + data owner |
| Fine-tuning on provider outputs (ADR 0001 open question) | the self-hosted model step | counsel |

---

## Status

Measured on the held corpora (`parsed_LHC`, `parsed_SC`, read only). Outputs
are in `artifacts/intel/`. Commands: `python -m intel identity`, then
`python -m intel resolve`.

| what | number | tier |
| --- | --- | --- |
| Parsed documents read | 10,302 (1,269 byte-identical copies) → 9,033 distinct documents | — |
| Judgments | 9,029: 8,241 keyed by neutral citation, 777 by SC filename label, 15 by content hash only | — |
| Case numbers parsed to canonical keys | 8,603 of 8,731 printed (98.5%) | C |
| Held documents that are reporter prints (header on page 1) | **297 of 8,243 LHC**, 0 of 790 SC; YLR 50, PLC(CS) 48, CLD 44, PLJ 37, PTD 33, MLD 32, CLC 23 … | C |
| Reporter headers that are true headers mapped to the right alias | 25 of 25 sampled | D |
| `CITES` edges | 60,226: 57,073 to Pakistani reporters, 111 to neutral citations, 3,042 foreign | — |
| Resolved, rung 1 only (exact alias) | 263 (154 reporter, 109 neutral), up from 0 of 1,118 | C |
| Resolved, rungs 1-3 (`resolve` v3: exact, parallel, case name) | **2,432 edges**: 261 alias, 7 parallel, 2,164 case name; 1,219 distinct cited judgments | C |
| Case-name links whose parties match the cited name | 40 of 40 sampled (v3). v2 scored about 32 of 40 and was discarded | D |
| **Citation resolution, labelled sample** (`intel sample` / `intel evaluate`; 257 edges stratified, 249 decided, 8 unsure; labels provisional, by Claude, awaiting a lawyer) | **precision 1.00 (95% CI 0.972–1.00)**: 134 of 134 decided links correct. **Recall over edges that print a case name ≈ 0.88 (95% CI 0.56–0.98)**: 1 held case missed in 115 decided unresolved edges, weighted to ~330 missed of ~2,650 resolvable. 17,196 edges print no case name and are unmeasured | D |
| Edges with a case name printed beside the citation | 39,346 of 57,073 | C |
| Parallel groups ("A = B") | 68 | C |
| Ambiguous edges (two candidate judgments), left unresolved | 4 | — |
| Resolved edges where the citing judgment's own year predates the cited report | 5. Each traces to a citing document whose neutral-citation year is not its decision year (e.g. `2012 LHC 4165` cites 2016-2020 reports), not to a wrong link | C |
| Alias conflicts (one reported alias on two judgments) | 1 (`2018 PLC(CS) 726`), left ambiguous for review | — |
| Case numbers parsed (identity v3: per-number years, mixed types, FIR numbers excluded) | 8,646 of 8,731 printed | C |
| Judgments whose head carries an appeal recital (`appeals` v2) | 674: 537 SC, 137 LHC | C |
| `APPEAL_FROM` edges | 1,865. Lower court not held: 1,171 (Sindh 252, tribunals 225, subordinate courts 207, Islamabad 178, Peshawar 167, …); case not held: 557; no year in the case number: 96 | C |
| Resolved (case number and date both match a held judgment) | **28 edges, 21 distinct pairs**: 17 SC→LHC, 8 SC→SC (reviews and intra-court appeals), 3 LHC→LHC | C |
| Resolved pairs whose court, case and date agree with the recital | 21 of 21, all read by hand | D |
| Case held but on another date (a review candidate, not a link) | 13. Some held dates are days off the recital (17.04 vs 06.04.2015); some held judgments have no extracted date | — |

Case-name linkage uses a transparent scorer rather than Splink (ADR 0002 named
Splink). After blocking by reporter court and year, each citation has a
handful of candidates, so there is nothing for EM training to learn that a
threshold and a margin do not already express. Revisit if a labelled sample
shows the threshold is the limit.

What remains unresolved is mostly Supreme Court reports cited many times:
`2009 SCMR 230` by 342 judgments, `1995 SCMR 1345` by 322, `PLD 2002 SC 1048`
by 166. For the five most cited, checked by hand, the decision is not among
the 790 SC documents held. The SC folder is recent-heavy: 0 decisions dated
1992-1995 and 10 dated 2006-2009, against 153 dated 2015-2018. No rung can
resolve these until the corpus holds the decisions (SC judgments for the older
years) or a concordance is licensed.
Appeal chains (rung 5) are limited by holdings, not reading. 522 recitals name
an LHC case that is not among the 8,241 LHC judgments held, and 1,171 name a
court whose judgments are not held at all.

The labelled sample says the resolver errs on the side of missing, as
intended. The one miss it found (`2021 PTD 1705`, Commissioner Inland Revenue v
Zahid Jee Fabrics) is held and scored 84 against the 88 threshold: the generic
side is printed with office details ("Zone-II, RTO Lahore") that pull the score
down. Recall is the wider interval, because one miss in 115 is a thin basis.
Two limits on what the sample can see:

* A held judgment whose own parties were extracted badly ranks low in the
  candidate list, so a labeller would mark that edge `not_held`. Recall is
  therefore an upper estimate.
* The labels are provisional. `artifacts/intel/gold/citation_resolution/sample.csv`
  is the sheet a lawyer labels; `intel evaluate --labels <sheet>` recomputes
  every number from it.

Generic sides are now compared by the office they name (`resolve` v4):
"Commissioner Inland Revenue, Zone-II, RTO Lahore" agrees with "Commissioner
Inland Revenue". Single words stay exact, so "State Life Insurance
Corporation" is still a litigant. Re-scored from the cached reads, this gained
6 edges (4 citations, all revenue cases, all correct by inspection), lost none
and changed none: 2,329 resolved, including the sampled miss. The gain is small
because the sampled miss was the only one of its kind in 115 unresolved edges,
which is also why the recall interval stays wide.

Next: a larger unresolved sample to narrow the recall interval, and a review
queue for the case-held-date-differs candidates.

### LI-3: provision-level law (`intel provisions` v5)

| what | number | tier |
| --- | --- | --- |
| Provision mentions read (section, article, order/rule) | 162,118 across 9,033 judgments | — |
| Resolved to a held statute | **92,056** (57%); to a section the index holds: **80,839** | C |
| How resolved | abbreviation 45,414 · title 29,222 · reference back 21,642 · "of the Constitution" 14,801 · bare Article 7,624 (Qanun-e-Shahadat by context: 531) · statute named before the number 799 | — |
| Not resolved, by reason | no statute stated 40,146 · refers back to an unheld statute 8,053 · title names no statute ("Ordinance 1984") 7,663 · not in the federal code 4,536 · provincial 2,509 (Punjab 1,973) · Article of another instrument 1,939 · subordinate legislation 974 | C |
| `CONCERNS` edges (judgment → provision) | **42,198**, across 7,812 judgments | — |
| Edge roles | mentioned 24,205 · charged_under 7,665 · invoked 5,070 · interpreted 4,789 · benchmark 360 · vires_challenged 109 | — |
| Resolution precision, 40 random resolved mentions | 37 of 40 (v4). Two of the three errors are fixed in v5 (an Indian Article 21; "Article 124 of 'The Order'"); the third ("337-Fi" keyed as 337) is open | D |
| Role precision, 8 per role (v5) | charged_under 8/8 · invoked ~7/8 · vires_challenged 8/8 · benchmark 8/8; interpreted 8/8 (v4) | D |

Getting here took five versions, each driven by a hand-checked sample rather
than by the totals. The totals *fell* from v3 to v4 (100,751 → 92,277) on
purpose: a kind and a year ("Ordinance 1984") or "of the Act" after an unheld
Act no longer resolve to whatever held statute shares the words.

Decisions made along the way:

* **Reference back is per kind of instrument.** "Of the Act" resolves to the
  latest Act named before it, beside a number or in prose; "of the Ordinance"
  to the latest Ordinance. If that statute is not held, the mention stays
  unresolved rather than falling back to an older, held statute.
* **A letter-suffixed section is kept as printed.** "382-B Cr.P.C." is a real
  section the index lacks; "9-C CNSA" is usually clause 9(c). The printed key is
  stored and the clause reading offered (`clause_reading`), not substituted.
* **"Struck down" was replaced** by `vires_challenged` (the impugned
  provision, whether declared, argued or held *not* ultra vires) and
  `benchmark` (the Article it is measured against).
* **Penal provisions only** can be `charged_under`; procedure, evidence and the
  Constitution cannot.

Gaps the run exposed in the data rather than the code:

* The section index lacks Constitution Articles 185 and 187 (the Supreme
  Court's appellate jurisdiction) and CPC Orders, which are linked at statute
  level with the Order and Rule as a pinpoint.
* No provincial statutes: 2,509 mentions name one, 1,973 of them Punjab.


### LI-3: version timelines (`intel versions` v2)

Read from the amendment footnotes of the 279 statutes judgments cite. The
corpus holds only consolidated current text, so the **old words cannot be
recovered**; what the footnotes give is when each section changed, how, and by
what.

| what | number | tier |
| --- | --- | --- |
| Sections with at least one amendment event | 3,517 (83 repealed) | C |
| Events | 6,816: substituted 4,426 · inserted 1,728 · omitted 1,154 · added 887 · repealed 169 · renumbered 129. 6,639 carry a year, 371 an effective date ("w.e.f."), 789 change a whole section | C |
| Version status of the 42,198 `CONCERNS` edges | current text applies 33,228 · section not indexed 5,332 · **amended since the decision 2,407** · **whole section replaced since 764** · decision year unknown 262 · undated change 164 · repealed 39 · not yet in force 2 | C |
| Statuses checked by hand | replaced_since: CPC ss. 115/96 (Act XIX of 2023), CNSA s. 9 (2022), Constitution Arts. 186/189/191 (Twenty-seventh Amendment, 2025) all genuine. amended_since: Art. 199 (Twenty-sixth Amendment, 2024) genuine. repealed: PPC ss. 497, 498, 366 (Hudood laws, 1979) genuine. The 2 not-yet-in-force edges (NAO s. 33F cited in 2012, inserted 2022; Societies Registration Act s. 21 cited in 2018, added 2024) need review: either the date or the section reading is wrong | D |

How a status is decided:

* A footnote marker at the very start of a section is a change to the whole
  section; one inside it, to some of its words. Markers are matched to the note
  of the same number on the same page, since footnote numbers restart per page.
* **Not yet in force** needs a *pure* insertion after the decision. A first
  version called Articles 186, 189 and 191 "not yet in force" because the
  Twenty-seventh Amendment's note reads "Subs., Ins. and Omitted"; those
  articles have stood since 1973, and the note is a replacement. Mixed kinds
  are now `replaced_since`.
* The year compared is the amending instrument's, which is its enactment year
  and can precede commencement. A change in the decision's own year counts as
  *not* after it.

Open:

* Commencement dates beyond the 371 "w.e.f." notes would sharpen same-year
  cases. They are in the Gazette, not in the code.
* 5,332 edges point at sections the index does not hold (CPC Orders, the
  missing Constitution Articles), so they get no status.
* Earlier wording needs a versioned source; nothing here invents it.


### LI-3: the Punjab Code (`intel punjab`, `intel provisions` v7, `intel versions` v3)

The Punjab Code from `D:\open-pak-law\data` was read only: the catalogue
parquet plus the content-addressed PDFs. Each statute went through the same
parser and statute logic as the federal code. Outputs are in
`C:\Users\shamo\intel_outputs\` (D: was full): `punjab\`, `provisions_v7\` and
`versions_v3\`.

| what | number | tier |
| --- | --- | --- |
| Punjab statutes parsed | **1,349 of 1,349** distinct PDFs (the catalogue lists 1,361; 12 files are listed twice). Digital 1,327, mixed 20, scanned 2; 5 produced no sections; 220 are repealed per the catalogue | A |
| Mentions not held because they were Punjab law | 1,973 → **443** (now `not_found_in_punjab_code`) | C |
| Mentions resolved to a held statute | 92,056 (v5) → **95,499**; to a held section 80,839 → **84,064** | C |
| `CONCERNS` edges | 42,198 → **44,075**, across 8,025 judgments (was 7,812) | — |
| Linked to a Punjab statute | 3,746 mentions. Most cited: Rented Premises Act 2009 (461), Pre-emption Act 1991 (348), Local Government Act 2013 (246), Civil Servants Act 1974 (236), Consumer Protection Act 2005 (192) | — |
| Punjab links whose printed name states no province (`jurisdiction_inferred`) | 111 mentions | — |
| Hand check, 23 random Punjab links (v6) | 21 right, 1 wrong ("s. 186 of Act XXXIII of 2022" read as the Local Government Act 2013; the 2022 Act was not matched by number), 1 unclear | D |
| Version timelines with Punjab | 445 statutes read (was 279); 7,637 events (was 6,816) | C |

Rules for jurisdiction:

* **The catalogue fixes jurisdiction, not the title.** Every statute in it is
  Punjab's, including those whose catalogue title omits "Punjab".
* **A name that says "Punjab" is looked up only in the Punjab Code.**
* **A name that states no province** is looked up in the federal code first.
  Only if nothing matches there is the Punjab Code tried, and the link is then
  marked `jurisdiction_inferred`.
* **A bare "Finance Act" never falls back to Punjab.** It is almost always
  the federal Act, which the code does not hold. The fallback had linked about
  13 such mentions, and v7 removed them. 302 mentions dropped in total,
  because "section X of the Act" references back to those titles went with
  them.

Open:

* **The Juvenile Justice System Ordinance 2000** (35 mentions) is federal,
  but its copy sits in the Punjab Code, so it links there with
  `jurisdiction_inferred`. A federal copy would replace that link.
* **Acts cited by number** ("Act XXXIII of 2022") are not matched to the
  catalogue's Act numbers yet.


### LI-4: judgment metadata by model (in progress)

Built, and **not yet run against any provider**: no API key is configured on
this machine, and nothing is sent without one and an explicit `--run`.

* `intel extract`: the first extractor, **disposition** (outcome, relief,
  proceeding). It reads the last 30% of the judgment, bounded to 3,000-14,000
  characters. The request asks for JSON against a fixed schema (`output_config.format`)
  and caches the system prompt. Opus and Sonnet 5.5 run at effort `low` with
  `fallbacks: "default"`. Every outcome and relief must come with a sentence
  copied from the judgment; `intel.evidence.locate` looks it up, and an
  unfound quote rejects the extraction. Results are cached per document, model
  and extractor version.
* Dry run by default: it builds every request and estimates tokens and cost.
  Estimate at 4 characters per token, 350 output tokens, effort low, no
  caching credit:

| model | 200-judgment pilot | all 9,029 judgments (standard / batch) |
| --- | --- | --- |
| claude-opus-5-5 | $2.65 | $120 / $60 |
| claude-sonnet-5-5 | $1.33 | $60 / $30 |
| claude-haiku-4-5 | $0.66 | $30 / $15 |

* `intel disposition-sheet`: 120 judgments stratified by court and era (LHC,
  SC; pre-2015, 2015-2019, 2020+), with the end of each judgment, written to
  `artifacts/intel/gold/disposition/disposition_sheet.csv` for a lawyer to label.
* `intel evaluate-disposition`: per-class accuracy of a run against the
  labelled sheet, with the share of answers whose quote verified.

Next, in order: the labelled sheet; a bake-off of the three models on those
120 judgments (about $3 in total, estimated); the model choice recorded with
its accuracy and cost; then the whole corpus through the Batch API.



### LI-5: treatment and the citator, rules only (`intel citator` v3, `intel.treatment` v4)

Cue phrases only; no model was used and nothing was sent anywhere. Outputs are
in `C:\Users\shamo\intel_outputs\citator_v4\`:

* `cites_treatment.jsonl`: treatment on every `CITES` edge, with the sentence
  as evidence.
* `appeal_results.jsonl`.
* `status.jsonl`: one record per judgment.

The rule throughout is that **a weak or unclear cue reads as `referred`**. A
`referred` edge says nothing about the cited case, but a wrong `overruled`
would.

| what | number | tier |
| --- | --- | --- |
| Treatment, all 60,226 `CITES` edges | referred 41,710 · followed 9,385 · cited by counsel 4,938 · not read 3,884 · distinguished 293 · overruled 11 · per incuriam 3 · not good law 1 · reversed 1 | C |
| Treatment, the 2,438 resolved edges | referred 1,688 · followed 331 · cited by counsel 235 · not read 170 · distinguished 13 · reversed 1 | C |
| Negative readings, hand-checked (all 16) | **15 of 16 right**. The miss is counsel's attack reported by the court ("he has attacked on the ground that the said judgment is per incuriam") | D |
| Followed, 12 random (v4) | 12 of 12 | D |
| Distinguished, 12 random | about 10 of 12 (one cue in the next sentence, since fixed; one unclear) | D |
| Cited by counsel, 10 random | 10 of 10 | D |
| Appeal results, the 28 resolved `APPEAL_FROM` edges | leave refused 7 · upheld 3 · set aside 3 · unknown 15. Of the 13 read, 12 are right; the 13th is one judgment that decided two petitions in opposite directions and reads as leave refused. 7 unknowns come from one judgment | D |
| Dispositions read from the end of each judgment (the rules baseline for LI-4) | dismissed 2,903 · allowed 2,665 · none found 2,342 · remanded 534 · disposed of 269 · partly allowed 170 · leave refused 146; **not yet measured against labels** | C |
| Status per judgment (9,029) | no treatment seen 8,736 · positive 201 · caution 88 · **negative 4** | C |

How it decides:

* **Negative cues bind to one case, by sentence structure.** The cited case
  is the treated one only in two forms:
  * it is the subject of a passive cue ("X (cite) was overruled", "(cite),
    which has since been set aside by the Supreme Court", "(cite)
    (overruled)");
  * it is the object of an active cue ("their Lordships overruled X (cite)").

  The first version also took the *agent* as the treated case: "declared not
  good law in X (cite)", "X (cite) overruled both judgments", "X (cite),
  wherein it was held …". Only 16 of its 45 negatives were right.
* **Not a precedent:** an overruled objection or plea, a modal ("can be
  overruled"), the doctrine explained ("the term per incuriam"), or a passage
  quoted from the cited case (after "):-").
* **Counsel's voice is not the court's.** A sentence that reports an argument
  ("contended", "argued", "asserts", "contentions") reads as
  `cited_by_counsel`, even if it says "overruled". Past-tense reliance
  ("reliance was placed", "he placed reliance") is counsel's; "reliance is
  placed" is the court's.
* **`not_read`**: 3,884 citations were found per block but not in the joined
  text, so they have no sentence. Most are reporter headers and citations
  split across blocks.

What the statuses rest on:

* **Negative (4).** Three appellate judgments we hold set the lower judgment
  aside, and in one, the Supreme Court set aside *General (R) Pervez Musharraf
  v. Federation* (PLD 2020 Lahore 285).
* **Caution (88).** All come from statutory change: a provision the judgment
  interpreted has since been replaced or repealed. Examples are CPC s. 115
  (Act XIX of 2023) and Constitution Arts. 186 and 189 (Twenty-seventh
  Amendment, 2025). No held judgment doubted another.
* **Positive (201).** A held judgment relied on it.
* **No treatment seen (8,736).** This is **not** "good law". Only 973 of the
  held judgments are cited by another held judgment at all. Most of the
  overruled cases above are not held (to_judgment is empty), so their
  negative treatment is recorded on the edge but cannot become anyone's
  status.

Open:

* A labelled treatment gold set, oversampling negatives. The numbers above
  are one person's hand-check on small samples.
* The model pass for the `referred` residue (LI-5 task 2) and the
  disposition model (LI-4) both need API access.
* Precedent weight is recorded as court level, judges named and decision
  date. "Larger bench overrules smaller" is not computed: the parsed metadata
  names only the authoring judge in many SC judgments.
