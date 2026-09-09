"""Hybrid retrieval: dense, sparse and BM25 fused by rank.

Fusion is by **reciprocal rank** rather than by weighted score. Dense cosine,
BGE-M3 lexical weights and BM25 are on three incomparable scales, and any
weighting between them is a constant that would need tuning against an
evaluation set. Rank fusion needs no such constant, which matters here because
the evaluation set does not exist yet -- so the first honest measurement should
not already depend on a number chosen by hand.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from rag.embed import BM25, Embedder, Embeddings
from rag.evaluate import cited, statute_key

RRF_K = 60          # the constant from the original reciprocal-rank-fusion work
ALL_LEGS = ("exact", "bm25", "dense", "sparse")


def indexed_text(chunk: dict[str, Any]) -> str:
    """What every retrieval leg sees: the passage under its document's context.

    The prefix -- act and section heading, or case, court and date -- is part of
    what is *indexed* and no part of what is quoted back to a reader, which
    stays the words the page printed.

    Every leg must see the same text. Fitted on ``text`` alone, BM25 could not
    match "PPC" at all -- the act's name lives in the prefix, not in the
    section's own words -- so "section 302 PPC" scored a recall@1 of zero
    against the very section it names.
    """
    prefix = (chunk.get("prefix") or "").strip()
    return f"{prefix}\n\n{chunk['text']}".strip() if prefix else chunk["text"]


@dataclass
class Hit:
    chunk_id: str
    score: float
    rank: int
    chunk: dict[str, Any]
    legs: dict[str, int]        # which retrieval leg ranked it where


def _unit(vector: "np.ndarray") -> "np.ndarray":
    """Normalised, so a dot product is a cosine."""
    norm = float(np.linalg.norm(vector)) or 1.0
    return vector / norm


def _matrix(vectors: Sequence[Sequence[float]]) -> "np.ndarray":
    matrix = np.asarray(vectors, dtype="float32")
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0.0, 1.0, norms)


def sparse_dot(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(token, 0.0) for token, weight in a.items())


def _ranks(scores: "dict[int, float] | Sequence[float] | np.ndarray", limit: int) -> list[int]:
    """The best `limit` chunk indices, by score, discarding zeroes."""
    if isinstance(scores, np.ndarray):
        # argpartition finds the top `limit` without sorting 200,000 scores.
        take = min(limit, scores.size)
        top = np.argpartition(-scores, take - 1)[:take] if take else []
        # Chunk index breaks ties, because argpartition orders equal scores
        # arbitrarily: without it, 12 queries in 3,000 ranked differently from
        # one run to the next, and a retrieval figure that moves on its own is
        # not a figure anyone can check.
        return [int(i) for i in sorted(top, key=lambda i: (-scores[i], i)) if scores[i] > 0]
    pairs = scores.items() if isinstance(scores, dict) else enumerate(scores)
    ranked = sorted((pair for pair in pairs if pair[1] > 0), key=lambda pair: -pair[1])
    return [index for index, _ in ranked[:limit]]


class HybridIndex:
    """An index over chunks with an exact-citation, dense, sparse and BM25 leg.

    The exact leg exists because a citation is an **identifier**, not a bag of
    words. Asked for "section 302 PPC", lexical search put the right section
    first only 35% of the time -- it competes against every other section that
    says "punishment" and every cross-reference that mentions 302 -- while the
    query named the answer outright. Ranking a named section by textual
    similarity to its own name is the wrong question.

    It is a leg rather than a short-circuit: a query may name a section *and*
    describe a problem, and the description still has to be searched.
    """

    def __init__(self, chunks: list[dict[str, Any]], embeddings: Embeddings | None = None,
                 dense: "np.ndarray | None" = None,
                 sparse: list[dict[str, float]] | None = None) -> None:
        self.chunks = chunks
        # Held as one normalised float32 matrix rather than a list of lists.
        # Scoring is then a single matrix-vector product; the Python loop it
        # replaces was ~200M multiply-adds per query at corpus scale, which is
        # seconds per query rather than milliseconds.
        if embeddings is not None:
            dense = _matrix(embeddings.dense)
            sparse = embeddings.sparse
        self.dense = dense
        self.sparse = sparse
        self.bm25 = BM25().fit(indexed_text(chunk) for chunk in chunks)
        # citation key -> the chunks that state that section.
        self.by_citation: dict[str, list[int]] = {}
        for position, chunk in enumerate(chunks):
            key = statute_key(chunk.get("citation"))
            if key:
                self.by_citation.setdefault(key, []).append(position)

    @classmethod
    def build(cls, chunks: list[dict[str, Any]], embedder: Embedder,
              batch_size: int = 8) -> "HybridIndex":
        return cls(chunks, embedder.encode([indexed_text(chunk) for chunk in chunks],
                                           batch_size=batch_size))

    def search(self, query: str, embedder: Embedder | None = None, limit: int = 10,
               depth: int = 100, use: Sequence[str] = ALL_LEGS) -> list[Hit]:
        """Fused results, optionally from a subset of the legs.

        `use` exists because rank fusion weights every leg equally, so two weak
        legs outvote one strong one: fusing BM25 with a deliberately dumb
        stand-in embedder scored 0.104 recall@5 where BM25 alone scored 0.974.
        A leg has to be measured on its own before it is trusted in a blend.
        """
        legs: dict[str, list[int]] = {}
        if "exact" in use:
            named = self.by_citation.get(cited(query), ())
            if named:
                legs["exact"] = list(named)
        if "bm25" in use:
            legs["bm25"] = _ranks(self.bm25.score(query), depth)
        if self.dense is not None and embedder is not None and {"dense", "sparse"} & set(use):
            asked = embedder.encode([query])
            if "dense" in use:
                legs["dense"] = _ranks(
                    self.dense @ _unit(np.asarray(asked.dense[0], dtype="float32")), depth)
            if "sparse" in use:
                legs["sparse"] = _ranks([sparse_dot(asked.sparse[0], weights)
                                         for weights in (self.sparse or [])], depth)
        fused: dict[int, float] = {}
        placed: dict[int, dict[str, int]] = {}
        for leg, order in legs.items():
            for rank, index in enumerate(order):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank + 1)
                placed.setdefault(index, {})[leg] = rank + 1
        best = sorted(fused, key=lambda i: -fused[i])[:limit]
        return [Hit(chunk_id=self.chunks[i]["id"], score=fused[i], rank=position + 1,
                    chunk=self.chunks[i], legs=placed[i])
                for position, i in enumerate(best)]

    def save(self, directory: Path) -> None:
        """Chunks and sparse weights as JSONL; dense vectors as a binary matrix.

        Dense vectors written as JSON text cost **1.78 GB** for 200,000 chunks
        against 0.76 GB as float32, and have to be re-parsed a line at a time to
        load. The matrix is memory-mapped back in, so opening an index does not
        read it all.
        """
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "chunks.jsonl").write_text(
            "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in self.chunks),
            encoding="utf-8")
        if self.dense is not None:
            np.save(directory / "dense.npy", self.dense)
            (directory / "sparse.jsonl").write_text(
                "\n".join(json.dumps(weights) for weights in (self.sparse or [])),
                encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "HybridIndex":
        chunks = [json.loads(line) for line in
                  (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
        dense_path, sparse_path = directory / "dense.npy", directory / "sparse.jsonl"
        if not dense_path.exists():
            return cls(chunks)
        sparse = [json.loads(line) for line in
                  sparse_path.read_text(encoding="utf-8").splitlines() if line] \
            if sparse_path.exists() else []
        return cls(chunks, dense=np.load(dense_path, mmap_mode="r"), sparse=sparse)
