# Retrieval

Chunking, embedding and hybrid retrieval over a parsed Specter corpus, with the
provenance to cite a bounding box for every answer.

```bash
python -m rag pipeline "/path/to/pdfs" --out work
```

That is the whole thing: **parse, chunk, index**, in order, under one working
directory. It needs a folder of PDFs and nothing else, picks its own backend
(Metal, CUDA or CPU), and produces `work/index` ready to search.

```bash
python -m rag search "bail before arrest in a non-bailable offence" --index work/index
python -m rag evaluate --statutes work/chunks.jsonl --judgments judgments.jsonl --index work/index
```

The stages are also separate commands (`rag chunk`, `rag index`) when a corpus
needs one of them on its own, and `--stages chunk,index` runs a subset.

### It is built to be interrupted

At this size every run is. Parsing 9,512 judgments took hours; embedding 200,000
chunks is hours on Apple Silicon and days on a CPU. So rerunning the same
command continues rather than restarting:

| stage | what a rerun does |
| --- | --- |
| parse | skips a PDF whose JSON already exists |
| chunk | reuses the chunk file, unless the parsed document count has changed -- chunks built from fewer documents are stale, and a stale chunk file would be embedded without complaint |
| index | resumes from the last whole shard; a shard missing its sparse half is redone |

`work/pipeline.json` records what each stage did, with counts and timings.
`--force` redoes a stage instead of resuming.

The parse stage shells out to `specter parse` rather than importing its loop.
That is deliberate: the command already handles worker pooling, filename
collisions, resume and the Windows spawn guard, and an earlier attempt to
reimplement that produced a `BrokenProcessPool`.

## The offset map, and why chunking never touches geometry

A parsed document is flattened to **one string in which every character
remembers the block, page and box it came from**. Chunking then runs on plain
text — any algorithm — and a chunk's character span maps back to exact page
regions.

This was not the first design. The obvious thing is to chunk on block
boundaries, and the obvious thing fails here: justified text makes PyMuPDF
return a line as several blocks, so a judgment line arrives as

```
'and' | 'recovery' | 'of' | 'certain'
```

Measured over the corpus as it was then parsed, **16.8% of blocks shared a line
with a neighbour and carried 4.9% of all characters**; 92% of Lahore High Court
judgments were affected, median block 58 characters. Chunking on blocks would
cut sentences into single words. The parser now joins such fragments — 6.8% and
2.4% after the fix, the remainder being printed columns that must not be joined
— but the offset map means chunking never depended on that.

An attempt to repair that with geometry — same-line joins, indent and gap
thresholds — needed constants tuned to the corpus and still misread two-column
tables. It was also unnecessary: concatenating block text with single spaces
already yields correct continuous prose, which is exactly why the page-text
benchmark never moved. The offset map keeps provenance without asking geometry
where a paragraph begins, and a corpus parsed half before and half after the
line-joining parser fix produces **identical chunk text** either way — only the
provenance granularity differs.

## Two chunking strategies, chosen by what the document offers

**A statute states its own boundaries.** A section is self-contained, citable,
and is the unit a judgment cites — "302 PPC", not "page 106". Over the 30,995
parsed sections: median 589 characters (~147 tokens), p95 2,854, and exactly one
section above 8,192 tokens. So the section *is* the chunk; only the 9.6% over
budget split further, on their own sub-section markers.

**A judgment states them less often.** 55% of Lahore judgments number their
paragraphs, and a printed number is both the natural boundary and the citable
unit. Where none is printed, text is packed to budget and broken at the latest
sentence end that fits.

Both carry a `prefix` — act and section heading, or court, case and date —
prepended **for indexing only**. The legal-RAG literature calls this
summary-augmented chunking; it costs nothing, where late chunking would mean an
8,192-token forward pass per document with attention that is quadratic in
length. `text` stays what the page printed, so what is quoted to a reader is the
law and not our annotation of it.

## Hybrid retrieval

Three legs — BGE-M3 dense, BGE-M3 sparse, BM25 — fused by **reciprocal rank**.
Rank fusion needs no weighting constant between three incomparable score scales,
which matters when the evaluation set is new: the first measurement should not
already depend on a number chosen by hand.

BM25 is kept beside BGE-M3's learned sparse weights, not because the weights are
worse, but because BM25 rebuilds in seconds with no forward pass while a dense
index over this corpus is a multi-day job on CPU. Every leg indexes the same
text; fitted on `text` alone, BM25 could not match "PPC" at all — the act's name
lives in the prefix — and scored **recall@1 of zero** against the very section
it names.

## Measured baseline, at corpus scale

`ACT_FORMS` in `acts.py` names the codes that carry the caselaw. Abbreviations
are conventional, not derivable: deriving them produced "XIX", "ACTX" and "TAX"
from statute filenames — 515 sections keyed to the roman numeral of their act
number — and read "of", "Act" and "Coloni[sation]" as abbreviations on the
judgment side.

The corpus labels the evaluation set for us, and at full size it is large:

| | |
| --- | --- |
| statute chunks (all 1,039 statutes) | **38,292** |
| judgment chunks (8,235 unique Lahore judgments) | **92,250** |
| **labelled queries** | **42,836** |
| judgments contributing one | 3,705 of 8,235 (45%) |
| by act | CrPC 22,745 · PPC 10,158 · CPC 6,971 · CNSA 1,236 · ATA 1,044 · NAO 224 |

3,000 randomly sampled queries against all 38,292 chunks:

| legs | look@1 | look@5 | look MRR | ctx@1 | ctx@5 | ctx MRR |
| --- | --- | --- | --- | --- | --- | --- |
| bm25 | 0.208 | 0.516 | 0.344 | 0.115 | **0.203** | 0.153 |
| **exact** | **1.000** | **1.000** | **1.000** | 0.000 | 0.000 | 0.000 |
| exact + bm25 | **1.000** | **1.000** | **1.000** | 0.087 | 0.197 | 0.135 |

### Lexical search does not survive the corpus growing; the exact leg does not care

The same measurement on a 2,279-chunk subset gave lookup recall@5 of **0.974**.
Over the full 38,292 it is **0.516**. Nothing changed but the number of
distractors: every section that says "punishment" and every cross-reference that
mentions 302 now competes. That is the single most important number here,
because it says a lexical baseline measured on a sample **overstates what it
will do on the real corpus by nearly a factor of two**.

The exact leg is unmoved at 1.000, because a lookup by identifier does not care
how many other sections exist. In-context recall barely moved either
(0.208 → 0.203): a passage describing the law competes on many words, so a few
more distractors change little.

**A citation is an identifier, not a bag of words.** It is a *leg* rather than a
short-circuit, because a query may name a section and also describe a problem,
and the description still has to be searched.

The `exact` leg scoring **0.000** in context is not a weakness, it is the
control: a leg that needs a citation must score nothing on queries that have
none. Getting it to 0.000 exposed a flaw in the evaluation set itself — only the
*matched* occurrence of a citation was removed, and a judgment paragraph
commonly names the same section twice, so the leftover mention let the query
answer itself (the exact leg scored 0.247 on passages meant to contain no
citation).

So the dense leg's job is now precisely stated: **lookup is solved** — exactly,
not statistically, and at any corpus size — and everything BGE-M3 has to earn is
in that 0.203, plus the queries lexical search cannot answer at all.

### BM25 had to be made 663× faster before any of this was runnable

The first full-scale run took **9.5 hours for 3,000 queries — 11.5 seconds
each**. An in-context query is a whole judgment paragraph, and common words
appear in nearly every one of 38,292 chunks, so a single query touched roughly
19 million postings in a Python loop.

Two changes, both forced by the corpus rather than chosen: take each query term
**once** with its count as a factor (a paragraph repeats "the" a dozen times,
and walking those postings a dozen times cannot change a ranking), and
accumulate with a vectorised scatter-add instead of a loop. **0.017 s/query** —
the same evaluation now takes 54 seconds.

Verified not to change the answers: rerunning the identical 3,000-query sample
reproduces the 9.5-hour figures to rounding (max delta 0.0003). Getting there
also needed an explicit tie-break, since `argpartition` orders equal scores
arbitrarily and 12 queries in 3,000 ranked differently between runs — a
retrieval figure that moves on its own is not one anybody can check.

## Chunk size, measured rather than chosen

Nine configurations over the same 308 queries, BM25 only. `stat` is the statute
chunk budget (the corpus); `judg` is the judgment budget, which here controls
the **length of the in-context query**.

| stat | judg | chunks | look@5 | ctx@5 | ctx@10 | ctx MRR |
| --- | --- | --- | --- | --- | --- | --- |
| 256 | 256 | 3,226 | 0.903 | **0.273** | 0.351 | **0.204** |
| 512 | 256 | 2,279 | **0.974** | **0.273** | 0.331 | 0.199 |
| 512 | 512 | 2,279 | **0.974** | 0.234 | 0.318 | 0.177 |
| 512 | 1024 | 2,279 | **0.974** | 0.208 | 0.247 | 0.126 |
| 1024 | 1024 | 1,922 | 0.870 | 0.169 | 0.214 | 0.103 |

Two things fall out.

**512 is the right statute budget.** It gives the best lookup recall@5 (0.974
against 0.903 at 256 and 0.870 at 1024) at two thirds the chunk count of 256.
Larger loses the exact section among its neighbours; smaller splits sections
that did not need splitting.

**Query length dominates, and shorter is better.** ctx@5 falls 0.273 -> 0.175
from 256 to 1024 tokens, a 36% relative drop, because a longer passage of
argument dilutes the words that identify the law. Note what that measures: a
real user's question is short, so live retrieval sits nearer the `judg=256` row
than the `judg=1024` one.

Both findings are **lexical**. Dense models weigh length differently, and the
query-length effect in particular may not transfer — which is the point of
keeping the harness: rerun it with BGE-M3 and find out rather than assume.

## Measure each leg before you trust the blend

`rag evaluate` reports every leg on its own and then the blends, because rank
fusion weights legs equally and a weak leg therefore *drags a strong one down*.
With the deliberately dumb stand-in embedder standing in for BGE-M3:

| legs | look@1 | look@5 | look MRR | ctx@5 |
| --- | --- | --- | --- | --- |
| bm25 | 0.351 | **0.974** | **0.577** | **0.234** |
| dense (stand-in) | 0.000 | 0.000 | 0.000 | 0.006 |
| sparse (stand-in) | 0.000 | 0.000 | 0.000 | 0.000 |
| bm25 + dense | 0.013 | 0.104 | 0.062 | 0.033 |
| all three | 0.091 | 0.104 | 0.097 | 0.026 |

The stand-in is a bag-of-words hash and is *supposed* to score nothing; what
matters is the fourth row. **Fusing a useless leg with BM25 took recall@5 from
0.974 to 0.104** — an order of magnitude worse than not fusing at all.

So "hybrid" is not free. If BGE-M3's legs turn out weak on this corpus, naive
equal-weight fusion will be worse than lexical search alone, and the per-leg
table is what will say so. Run it before trusting the blend.

## Storage and query cost

Dense vectors are stored as one normalised float32 matrix (`dense.npy`,
memory-mapped on load), not as JSON text: **1.78 GB against 0.76 GB** at 200,000
chunks, without the line-by-line parse. Scoring is a matrix-vector product
rather than a Python loop over every vector, which at corpus scale was ~200
million multiply-adds per query.

## Asking it something, and the vocabulary gap

```bash
python -m rag search "bail before arrest in a non-bailable offence" --index index -k 5
```

Each hit prints its citation, page range, how many boxes back it, and which leg
found it at what rank — so a weak leg shows up on a real query, not only in the
metrics.

Four probes against the lexical leg alone say more about what the dense leg is
for than any recall figure:

| query | top hit | |
| --- | --- | --- |
| `section 302 PPC` | **302 PPC** | correct |
| `bail before arrest non-bailable offence` | **497 CrPC** | correct — that is the bail section |
| `punishment for qatl-i-amd` | 316 PPC, then 302 PPC | near: "Punishment *for*" outscored "Punishment *of*" |
| `what is the punishment for murder` | 108/109 PPC (abetment) | **wrong — 302 is not in the top four** |

The last row is the case for embeddings, stated concretely. **The Penal Code
says "qatl-i-amd" and never says "murder."** A user asking about murder, cheque
bouncing or dowry gets nothing from lexical search, because the words they use
appear nowhere in the statute that governs them. No amount of BM25 tuning closes
that gap; a multilingual semantic model is the only leg that can.

Which sharpens what BGE-M3 has to prove. Not "is it better on average" — it is
already clear that lexical search wins citation lookup at 97.4% recall@5 and
will keep doing so. The question is whether the dense leg answers the queries
BM25 cannot answer *at all*, and the per-leg table plus these probes are how to
tell.

## The corpus contains the same judgment twice

Measured over the Lahore source PDFs:

| | |
| --- | --- |
| source PDFs | 9,512 |
| named like a duplicate download (`... (1).pdf`) | 1,255 (13.2%) |
| **byte-identical second copies** | **1,269 (13.3%)** |
| **unique documents** | **8,243** |

Indexed as they stand, a search returns the same judgment twice and pushes a
real second result off the page — and every count taken over the corpus is
inflated by an eighth.

`rag chunk` therefore skips a document whose text it has already seen, and says
how many it skipped. Matching is on the **document's own text**, not the
filename, so a copy saved under an unrelated name is caught too. `--keep-duplicates`
turns it off.

Statutes need none of this: 0% exact duplicates and 0.5% near-duplicates, and
those are all `[Omitted]` and `* * *` stubs, which are what the print says. That
is the section-as-chunk strategy paying off — sections do not repeat the way
judgment boilerplate does.

## Running the dense index on another machine

Only the chunk file has to travel — **70 MB** for the whole statute corpus
(30,995 sections at about 2.4 KB each), not the parsed documents. The index it
produces is 121 MB of float32.

```bash
python -m rag chunk "<parse output>" --out statutes.jsonl        # here
# copy statutes.jsonl across
pip install FlagEmbedding
python -m rag index statutes.jsonl --out index --embedder bge-m3        --device mps --fp16 --batch-size 4                        # on the Mac
```

On an 8 GB Apple Silicon machine use `--device mps --fp16` and keep the batch
small: BGE-M3 is 568M parameters, and half precision on the GPU is the
difference between running and swapping. `--device cpu` works and is slower.

**The run is resumable and does not need watching.** Embedding writes a shard
every 512 chunks and prints its progress; if the process dies, rerunning the
same command picks up from the last whole shard. Verified by deleting a shard
mid-corpus and re-running: only the missing chunks were re-embedded, and the
resulting matrix was bit-identical to an uninterrupted build. A shard whose
sparse half is missing — a process killed between the two writes — is redone
rather than indexed as a dense leg with no sparse weights.

Then bring `index/` back and run `rag evaluate`, which reports each leg
separately. That table, not this README, decides whether BGE-M3 stays.

## What this does not yet say

* **No dense numbers.** BGE-M3 has not been run: 568M parameters, ~86M tokens
  over ~200,000 chunks, no GPU. The table above is the lexical baseline it must
  beat, not a result for the system.
* **Statute retrieval only.** The free labels are judgment→section citations, so
  nothing here measures finding a *precedent*, which needs different labels.
* **One corpus.** IHC is unparsed, and its `discussed_laws` field — a section
  list on 33% of documents — is a larger and independent evaluation set.
* **Six acts.** Queries resolve only for the codes named in `ACT_FORMS`. A
  smaller set that is right beats a larger one that is not, but it is a sample
  of criminal and civil practice, not of the whole code.
