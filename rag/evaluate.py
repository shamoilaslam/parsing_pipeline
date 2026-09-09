"""An evaluation set the corpus labels for us, and the metrics over it.

Without this, every choice -- 512 tokens or 1,024, BGE-M3 sparse or BM25, late
chunking or a context prefix -- is an opinion. The corpus supplies the labels
almost free: **a judgment names the section it applies**, and we hold 30,995
parsed sections to match against. So a judgment paragraph citing "section 302
PPC" is a query whose correct answer is known.

Two query kinds, because they test different things and one number would hide
the difference:

* **Lookup** -- "section 302 PPC". Tests exact retrieval. BM25 should win this
  outright, and if a dense model does not roughly match it here, hybrid fusion
  is doing the work rather than the embeddings.
* **In context** -- the judgment's own paragraph, *with the citation string
  removed*. Tests whether the passage is found from the law it describes rather
  than from the number. This is the number that says whether the embeddings
  earn their cost.

Removing the citation matters. Leave it in and BM25 scores near-perfectly on
both, and the evaluation says nothing about the dense leg at all.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from rag.acts import LOOKUP, forms_pattern, normal, short_form as _short_form  # noqa: F401

# "section 302 PPC", "u/s 497 Cr.P.C.", "under section 12 of the Code of Civil
# Procedure" -- the forms a Pakistani judgment actually uses.  The act half is
# built from `rag.acts`, so the pattern and the table cannot drift apart.
CITATION = re.compile(
    r"(?i)(?<![A-Za-z])(?:u/s|under\s+section|sections?|s\.)\s*"
    # "489-F PPC" is a real and heavily litigated section, and the hyphen is
    # how it is printed; without it the act never attaches to the number.
    r"(?P<number>\d{1,3}(?:-?[A-Z]{1,2})?)(?:\s*\([^)]{1,4}\))?"
    r"(?:,?\s*(?:of\s+)?(?:the\s+)?(?P<act>" + forms_pattern() + r"))?")
MIN_CONTEXT = 200          # a query shorter than this is not a passage


@dataclass
class Query:
    """One evaluation query and the chunks that would answer it."""

    id: str
    kind: str                   # "lookup" | "in_context"
    text: str
    gold: set[str]
    source_document: str


def statute_key(citation: str | None) -> str | None:
    """"302 PPC" -> "302|PPC", so a judgment's mention can meet a section.

    Only the abbreviations named in ``ACT_FORMS`` resolve. A section keyed to
    "XIX" -- the roman numeral of its act number, which the filename heuristic
    produced for 515 sections -- is not a citation anybody writes, and matching
    on it would manufacture agreement rather than measure it.
    """
    if not citation:
        return None
    match = re.match(r"^\s*(\d{1,3}(?:-?[A-Z]{1,2})?)\s+([A-Za-z.]{2,10})\s*$", citation)
    if not match:
        return None
    short = LOOKUP.get(normal(match.group(2)))
    return f"{match.group(1)}|{short}" if short else None


def cited(query: str) -> str | None:
    """The section a query names, if it names one.

    "What does section 302 PPC say" -> "302|PPC". A query naming a section is
    asking for that section, and matching it by identifier rather than by
    wording is what takes lookup from a ranking problem to a lookup.
    """
    match = CITATION.search(query)
    if not match:
        return None
    short = _short_form(match.group("act"))
    return f"{match.group('number')}|{short}" if short else None


def _without(text: str, number: str, short: str) -> str:
    """The passage with every mention of one section struck out."""
    keep = []
    last = 0
    for found in CITATION.finditer(text):
        if found.group("number") != number or _short_form(found.group("act")) != short:
            continue
        keep.append(text[last:found.start()])
        last = found.end()
    keep.append(text[last:])
    return re.sub(r"\s{2,}", " ", " ".join(part.strip() for part in keep)).strip()


def build_queries(judgment_chunks: Sequence[dict[str, Any]],
                  statute_chunks: Sequence[dict[str, Any]],
                  limit: int | None = None) -> list[Query]:
    """Queries whose answers the corpus already states."""
    by_key: dict[str, set[str]] = defaultdict(set)
    for chunk in statute_chunks:
        key = statute_key(chunk.get("citation"))
        if key:
            by_key[key].add(chunk["id"])
    queries: list[Query] = []
    for chunk in judgment_chunks:
        text = chunk.get("text") or ""
        for match in CITATION.finditer(text):
            short = _short_form(match.group("act"))
            if not short:
                continue
            gold = by_key.get(f"{match.group('number')}|{short}")
            if not gold:
                continue
            queries.append(Query(id=f"{chunk['id']}:{match.group('number')}{short}:lookup",
                                 kind="lookup",
                                 text=f"section {match.group('number')} {short}",
                                 gold=set(gold), source_document=chunk["document_id"]))
            # The same passage with the citation removed: found from the law it
            # describes, or not found at all.
            #
            # *Every* mention of it, not just this one.  A judgment paragraph
            # commonly names the same section twice, so removing one occurrence
            # left the other behind and the query still answered itself: the
            # exact-citation leg, which cannot work without a citation, scored
            # 0.247 recall@1 on queries that were supposed to have none.
            stripped = _without(text, match.group("number"), short)
            if len(stripped) >= MIN_CONTEXT:
                queries.append(Query(id=f"{chunk['id']}:{match.group('number')}{short}:in_context",
                                     kind="in_context", text=stripped, gold=set(gold),
                                     source_document=chunk["document_id"]))
            if limit and len(queries) >= limit:
                return queries
    return queries


def score(queries: Iterable[Query], search: Callable[[str], Sequence[Any]],
          cutoffs: Sequence[int] = (1, 5, 10)) -> dict[str, Any]:
    """Recall@k and MRR, reported per query kind.

    Reported separately because a single average over both kinds would let a
    strong lookup score hide a weak one in context, which is the failure mode
    this set exists to expose.
    """
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "mrr": 0.0, **{f"recall@{k}": 0 for k in cutoffs}})
    for query in queries:
        hits = list(search(query.text))
        found = [index for index, hit in enumerate(hits)
                 if getattr(hit, "chunk_id", None) in query.gold]
        bucket = buckets[query.kind]
        bucket["n"] += 1
        for k in cutoffs:
            if any(index < k for index in found):
                bucket[f"recall@{k}"] += 1
        if found:
            bucket["mrr"] += 1.0 / (found[0] + 1)
    report: dict[str, Any] = {}
    for kind, bucket in buckets.items():
        n = max(1, bucket["n"])
        report[kind] = {"queries": bucket["n"], "mrr": round(bucket["mrr"] / n, 4),
                        **{f"recall@{k}": round(bucket[f"recall@{k}"] / n, 4) for k in cutoffs}}
    return report
