# Measured results

Every number here was produced by a command in this repository and can be
reproduced by running it. Nothing is estimated.

The numbers are grouped by **what they are measured against**, because that is
what decides how much they are worth:

| tier | what it means | how much to trust it |
| --- | --- | --- |
| **A. Gold** | Human-annotated pages. Someone read the page and typed what it says. | The real accuracy figure. |
| **B. Corpus labels** | The corpus states the answer itself, in a filename or a `meta.json` the court published. | Good, but the label can be wrong too, so a disagreement is reported, not resolved. |
| **C. Free checks** | Neither, but the data contradicts itself when the parser is wrong -- a statute numbers its sections 1..N, so a hole is a miss. | Finds real defects, cannot give an accuracy percentage. |
| **D. Hand-sampled** | Matches read against the page by eye. | Precision only, and only on the sample stated. |

> **The gold set covers Lahore High Court only.** 192 annotated pages, all LHC.
> There is **no IHC gold** and no statute gold: nobody has read those pages and
> typed what they say. Every IHC and statute number below is tier B or C -- a
> comparison against a label the corpus published about itself, or a check the
> data makes possible on its own. They find real defects and they are worth
> having, but they are not accuracy figures and must not be quoted as such.

---

## A. Text fidelity, against gold — 191 annotated pages

The only human-annotated set in the project: 191 Lahore High Court pages,
stratified across the hard cases rather than sampled evenly.

```bash
python -m specter benchmark --baseline artifacts/benchmark/phase5b.json
```

| slice | pages | CER mean | CER median | WER mean | exact pages |
| --- | --- | --- | --- | --- | --- |
| digital text | 106 | **0.0011** | 0.0000 | 0.0018 | 86 of 106 |
| tables | 38 | **0.0093** | 0.0027 | 0.0193 | 5 |
| scanned (OCR) | 32 | **0.0901** | 0.0611 | 0.1784 | 0 |
| Urdu | 8 | 0.1162 | 0.1014 | 0.2551 | 0 |
| hand-edited text layer | 7 | 0.1652 | 0.0212 | 0.2175 | 0 |

Read that as: **digital pages are transcribed at 99.89% character accuracy**,
and 86 of 106 are character-for-character exact. Scanned pages run at 91%,
which is what CPU-only OCR gives on this corpus.

Table structure on the same pages:

| table kind | pages | detected | column count right | cell recall |
| --- | --- | --- | --- | --- |
| cover table | 8 | 8 | 8 | 0.797 |
| order sheet | 34 | 31 | 1 | 0.013 |

False positives: 4 of 149 non-table pages were given a table.

The order-sheet cell recall is **diagnostic only and is not a defect**: the
gold wraps the proceedings body into form-header rows, and that body is prose.
It is reported rather than quietly dropped.

**Every one of the five slices is bit-identical to the stored baseline after
all the changes in this project.** That is the regression gate: metadata and
citation work is only allowed to land if the gold does not move.

---

## B. Metadata, against the labels the corpus states

No annotation needed: Supreme Court filenames state the case number, year and
decision date; IHC publishes a `meta.json` beside every judgment.

```bash
python -m specter benchmark --sc-metadata --limit 40
python -m specter benchmark --ihc-metadata --limit 250
```

### Supreme Court — 40 documents, 0 parse errors

| field | agreement |
| --- | --- |
| judge | **95.0%** |
| case number | **82.5%** |
| decision date | 62.5% |
| decision date, among documents that state one | 69.4% |
| documents stating a decision date at all | 90.0% |
| court found | 100% |
| counsel found | 42.5% |

### Islamabad High Court — 250 documents, 0 parse errors

Measured against the `meta.json` the court publishes beside each judgment.
**This is not gold**: the record is produced by the court's own site, and where
it disagrees with the page the page is often right.

| field | agreement | judgments only (66) | orders only (184) |
| --- | --- | --- | --- |
| decision date, among documents that state one | **84.7%** | | |
| case number | 74.8% | **93.9%** | 67.9% |
| decision date | 66.4% | 62.1% | 67.9% |
| judge | 50.0% | 74.2% | 41.3% |
| court found | 98.4% | | |
| counsel found | 15.2% | | |

The judgment/order split is the number that matters: two thirds of IHC's PDFs
are interim order sheets, which carry far less of a cover than a judgment does.
On judgments alone the case number is right **93.9%** of the time.

**These figures are not comparable to the earlier stored IHC run**, and saying
so matters more than the numbers. Both drew 250 documents in path order from
the same root, but only **7 documents overlap** -- the corpus on disk changed
between the runs, so path order selected a different sample. A path-order draw
is not a stable sample, and treating the difference as a regression would be
wrong. Fixing that means pinning the sample by identity, which is not done.

Two caveats stated rather than buried. IHC counsel is low because an order
sheet has no cover table -- counsel appear as prose -- and no extractor was
built for something there is no label to measure. And party names are measured
against a *display* title (`FOP etc`, `MD, OGDCL etc`) where the page prints
the name in full, so the page is usually the better of the two.

### As delivered — what a consumer actually receives

The benchmark scores **extraction alone**, deliberately: the corpus label is
withheld so the parser cannot be scored against itself. What ships is
extraction *plus* the label filling gaps it left. Over a 120-document run
spanning all four corpora:

| | before this session's fixes | after |
| --- | --- | --- |
| label disagreements | 21 | **18** |
| of which case number | 7 | **4** |

Worth understanding, because it is easy to misread: the case-number fix did
**not** move the SC benchmark at all. It turned a confidently wrong value into
an empty one, and the benchmark scores wrong and empty the same. But an empty
field is what lets the corpus label fill it correctly, so the shipped output
improved even though the extraction score did not.

---

## C. Free correctness checks — no annotation, real defects

### Statute section runs — the whole pakistancode corpus, 1,039 documents

A statute numbers its sections 1..N, so a hole is a miss and needs no gold.
This is now run over every statute rather than a sample.

| | |
| --- | --- |
| documents parsed | **1,039**, 0 failed |
| section-addressable statutes | 982 |
| sections | **30,995** (median 589 characters) |
| complete runs | **854 of 982 (87.0%)** |
| runs with a hole | 123 |
| numbering unresolved (a compendium, or a schedule not found) | 5 |
| no sections detected | 57 — 47 repealed stubs, all named in `index.json` |

### The section corpus itself — 30,995 sections

What must not be in a section's text, because a retrieval system would quote it
as law:

| | |
| --- | --- |
| page footers in the text | **0** |
| the section number left inside its own text | **0** |
| amendment notes in the text | **11** (0.04%) |
| dot-leader lines from a contents page | 39 (0.13%) — verified as schedule form fill-lines, `Dated..........` |
| sections with no heading | 13 — 12 of them in the two copies of Estacode |
| sections whose citation cannot resolve | 818 (2.6%) — 754 in Estacode, each marked `ambiguous` and given no citation |
| statutes carrying a category | **982 of 982** |

The check earns its keep: it caught a regression in this pipeline, not just in
the documents. After the front-matter cut was added, **section 1 was the most
commonly missing number — absent from 77 of 178 incomplete runs**. That shape
is a cut landing past the start of the body, and it was: a statute recites its
own name inside section 1, and that sentence reads as a title. Fixed, section 1
is 15 occurrences and no longer the most common (4, 3 and 5 lead at 18 each),
and complete runs went from 799 to 854.

Three earlier systematic causes were found the same way and fixed: the
footnote-marker stripper was eating the section *number*; a number set alone on
its line reads as a page number to the layout engine; and a schedule's own
entries, which restart at 1, were read as sections of the Act wherever its
heading was not matched.

### Amendment footnotes leaving the statute text — 60 statutes

A statute's amendment notes (`Subs. by the Law Reforms Ordinance, 1972`) are
set in small type at the foot of the page. Header/footer detection works by
repetition across pages and a footnote differs on every page, so they were
typed as body text *and merged into it*.

| | |
| --- | --- |
| statutes carrying amendment notes | 41 of 60 |
| characters moved out of body text | **143,431 (3.9%)** |
| in the Penal Code alone | 29,942 (about 6% of its body) |

That is text a retrieval system would have quoted as law. The notes are kept
under a `footnote` block type, not dropped -- they name the amending Act.

### Citations — 698 digital IHC judgments

Every occurrence of `SCMR`, `PLD`, `CLC` and nine more reporters was counted
and checked for whether it fell inside an extracted citation.

| | |
| --- | --- |
| reporter tokens inside an extracted citation | **95.8%** (1,139 of 1,189) |
| before this session's four fixes | 91.4% |

Most of what remains is not a pattern gap but the source PDF's own mis-OCRed
text layer -- `20'T..6 SCMR 1976`, `PLD 7973 SC 7`. A citation that cannot be
read cannot be extracted, and inventing the digits would assert an authority
the judge never relied on.

### Impossible citations

A judgment cannot rely on a case decided after it, so `cited_year > citing_year`
is free. Across 400 LHC judgments it fired 36 times -- and **the citations were
right every time**. What was wrong was the decision date. The citation graph
turned out to be a check on the date field.

---

## D. Precision, hand-sampled

| what | sample | correct |
| --- | --- | --- |
| citations, on the forms added this session | 28 | **28** |
| citations, general sample with page context | 30 | **30** |
| statute names extracted from judgments | 40 | ~36 (the rest are truncations, not inventions) |

On the reference judgment used to build the extractor, Specter finds **17 of
17** authorities. A published Pakistani case-law site lists 8 for the same
judgment.

---

## E. Retrieval — chunking and the lexical baseline

Chunking, indexing and retrieval live in `rag/`. These numbers are **lexical
only**: BGE-M3 has not been run here (568M parameters, no GPU), so everything
below is the baseline a dense leg has to beat, not a result for the system.

### The evaluation set the corpus labels itself

A judgment names the section it applies and we hold 30,995 parsed sections, so
the labels are free. **60 judgments produced 308 labelled queries** — about
48,000 across the full Lahore corpus.

Two kinds, kept apart because one average would hide the difference. *Lookup*
(`section 302 PPC`) tests exact retrieval. *In context* — the judgment's own
paragraph with the citation string removed — tests whether the section is found
from a description of the law rather than from its number.

| legs, 2,279 statute chunks | look@1 | look@5 | ctx@1 | ctx@5 | ctx MRR |
| --- | --- | --- | --- | --- | --- |
| bm25 | 0.351 | 0.974 | 0.091 | **0.208** | 0.146 |
| **exact citation** | **1.000** | **1.000** | 0.000 | 0.000 | 0.000 |
| exact + bm25 | **1.000** | **1.000** | 0.084 | 0.208 | 0.136 |

A citation is an identifier, not a bag of words: matching it directly takes
lookup from 0.351 to **1.000** recall@1. The exact leg scoring 0.000 in context
is the control that proves those queries carry no citation — and getting it to
0.000 exposed a flaw in the set. Only the *matched* citation was being removed,
while a judgment paragraph often names the same section twice, so the query
answered itself. With every mention struck out the honest in-context baseline is
**0.208 recall@5**, not the 0.234 measured through the leak.

### Chunk size, ablated over nine configurations

| statute budget | judgment budget | chunks | look@5 | ctx@5 |
| --- | --- | --- | --- | --- |
| 256 | 256 | 3,226 | 0.903 | **0.273** |
| **512** | **256** | 2,279 | **0.974** | **0.273** |
| 512 | 512 | 2,279 | **0.974** | 0.234 |
| 512 | 1024 | 2,279 | **0.974** | 0.208 |
| 1024 | 1024 | 1,922 | 0.870 | 0.169 |

512 is the best statute budget at two thirds the chunk count of 256. Query
length matters more than corpus chunk size, and shorter is better.

### Fusing a weak leg is worse than not fusing

| legs | look@5 | look MRR |
| --- | --- | --- |
| bm25 | **0.974** | **0.577** |
| bm25 + a deliberately dumb stand-in embedder | 0.104 | 0.062 |

Reciprocal-rank fusion weights legs equally, so two weak legs outvote one
strong one. `rag evaluate` therefore reports every leg on its own before the
blends.

### Provenance, verified against the source PDFs

Re-extracting text from the PDF at each chunk's recorded coordinates covered
**100% of the chunk's words on 24 of 24 sampled chunks**. That is now a test
against a real fixture, so it cannot regress silently.

### What lexical search cannot do at all

| query | top hit |
| --- | --- |
| `section 302 PPC` | **302 PPC** |
| `bail before arrest non-bailable offence` | **497 CrPC**, the bail section |
| `what is the punishment for murder` | 108/109 PPC — abetment. **302 is absent from the top four** |

The Penal Code says *qatl-i-amd* and never says "murder". That gap is what the
dense leg exists to close, and it is not a tuning problem.

### Corpus duplication

The Lahore corpus holds 9,512 PDFs and **8,243 unique documents** — 1,269
byte-identical second copies (13.3%). Indexed as they stand, a search returns
the same judgment twice. `rag chunk` skips them, matching on document text
rather than filename. Statutes need none of it: 0% exact duplicates.

## What the pipeline produces

Over a 120-document run (30 each from LHC, SC, IHC and the statute corpus),
**0 failures**:

```bash
python -m specter report artifacts/run
```

| | |
| --- | --- |
| routes | 101 digital, 19 scanned/mixed |
| kinds | 90 judgments, 30 statutes |
| quality score | median 0.98, p10 0.86, min 0.82 |
| judgments naming at least one statute | 68% (was 21% before the whitelist was replaced) |
| statute mentions linked to a statute we hold | 104 of 197 (**53%**) |
| linked, but our statute text is newer than the judgment | 59 of 104 |
| scanned documents with a cover-extraction defect | **0** (was 8 of 17) |

Citation graph over 400 LHC judgments: 2,620 nodes, 2,694 edges, 2,220 distinct
authorities.

---

## What these numbers do **not** say

* **Retrieval is measured lexically only.** Section E has a query-to-section
  set the corpus labels itself, but **BGE-M3 has not been run** -- no GPU here --
  so every retrieval figure is the baseline a dense leg has to beat, not a
  result for the system. Nothing measures finding a *precedent*: the free labels
  are judgment-to-section, and judgment-to-judgment needs a citation graph that
  resolves, which it does not (0 of 1,118 edges).
* **Urdu has never been run at corpus scale.** The route is wired and tested on
  crops; no local engine reads Nastaleeq, so those paragraphs go to a vision
  model that has not been run over the whole corpus.
* **Text is only gold-measured on LHC.** SC and IHC metadata are measured
  against corpus labels; their *page text* is not measured against anything.
* **Statute coverage is federal.** `pakistancode.gov.pk` holds 1,039 federal
  statutes, of which exactly one names Punjab. 35 of 166 unmatched statute
  names are provincial or foreign -- a corpus gap, not a parsing failure.
* **Statutes are consolidated, not versioned.** `version_risk` fires on 59 of
  104 links: the judgment construed the section as it stood, and what we hold
  is today's text.

---

## Reproducing all of it

```bash
pip install -e ".[ocr,eval]"
python -m pytest tests/ -q                                  # 484 tests
python -m specter benchmark --baseline artifacts/benchmark/phase5b.json
python -m specter benchmark --sc-metadata  --limit 40
python -m specter benchmark --ihc-metadata --limit 250

# Section E: chunk, index and measure retrieval
python -m rag chunk "<parse output>" --out statutes.jsonl
python -m rag index statutes.jsonl --out index --embedder bge-m3 --device mps --fp16
python -m rag evaluate --statutes statutes.jsonl --judgments judgments.jsonl --index index
```
