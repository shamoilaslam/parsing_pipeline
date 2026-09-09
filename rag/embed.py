"""Embeddings: BGE-M3 dense and sparse from one forward pass.

BGE-M3 is the right model for this corpus for two reasons that are specific to
it, not general praise: it emits **learned sparse weights alongside the dense
vector in the same pass**, so hybrid retrieval costs one inference rather than
two; and it is multilingual, which matters for the Urdu that the parser routes
separately.

Two things to be honest about, both measured rather than assumed:

* **Urdu is rare in what we have parsed.** RTL blocks are 0.01% of Lahore High
  Court blocks and 0% of Supreme Court. So multilingual capability is not what
  earns BGE-M3 its place here; the sparse-with-dense pass is. IHC may differ and
  is not yet parsed.
* **There is no GPU.** BGE-M3 is 568M parameters. The corpus is roughly 86M
  tokens across ~200,000 chunks, which is on the order of days of continuous
  CPU for one full index -- so the model is loaded here behind an interface, and
  an index build is expected to run on a machine with acceleration.

`BM25` is kept beside it rather than relying on BGE-M3's sparse alone. Not
because the learned weights are worse -- the paper reports they beat BM25 --
but because BM25 rebuilds in minutes with no forward pass, and exact lexical
match is what actually answers "302 PPC". When the dense index is a multi-day
job, an independently rebuildable retrieval leg is operational insurance.
"""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

import numpy as np

# "302 PPC", "section 302", "PLD 2015 SC 123" -- the tokens that must match
# exactly are numbers and abbreviations, so tokenisation keeps them whole.
TOKEN = re.compile(r"[A-Za-z]+|\d+[A-Za-z]*")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN.findall(text)]


@dataclass
class Embeddings:
    """What one encode call returns."""

    dense: list[list[float]]
    sparse: list[dict[str, float]]


class Embedder(Protocol):
    """Anything that turns passages into dense and sparse vectors."""

    dimension: int

    def encode(self, texts: Sequence[str], batch_size: int = 8) -> Embeddings: ...


class BGEM3Embedder:
    """BGE-M3 via FlagEmbedding, loaded lazily.

    Import is deferred so that chunking, indexing and the tests all run without
    the model present -- on this machine it is neither installed nor runnable at
    corpus scale.
    """

    dimension = 1024

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str | None = None,
                 use_fp16: bool = False) -> None:
        self.model_name = model_name
        self.device = device
        self.use_fp16 = use_fp16
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel  # noqa: PLC0415 -- deferred on purpose

            self._model = BGEM3FlagModel(self.model_name, use_fp16=self.use_fp16,
                                         device=self.device)
        return self._model

    def encode(self, texts: Sequence[str], batch_size: int = 8) -> Embeddings:
        out = self._load().encode(list(texts), batch_size=batch_size,
                                  return_dense=True, return_sparse=True,
                                  return_colbert_vecs=False)
        sparse = [{str(key): float(value) for key, value in weights.items()}
                  for weights in out["lexical_weights"]]
        return Embeddings(dense=[list(map(float, vector)) for vector in out["dense_vecs"]],
                          sparse=sparse)


def _stable_bucket(token: str, dimension: int) -> int:
    """A bucket that is the same in every process, unlike ``hash()``."""
    return zlib.crc32(token.encode("utf-8")) % dimension


class HashingEmbedder:
    """A deterministic stand-in so everything downstream is testable offline.

    It is not a semantic model and makes no pretence of being one; it exists so
    that indexing, fusion and the evaluation harness can be exercised and
    reviewed without a 568M-parameter download.
    """

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def encode(self, texts: Sequence[str], batch_size: int = 8) -> Embeddings:
        dense, sparse = [], []
        for text in texts:
            vector = [0.0] * self.dimension
            counts = Counter(tokenize(text))
            for token, count in counts.items():
                # Python randomises str.hash per process, so the same token
                # landed in a different bucket on every run: an index saved by
                # one process and queried by another returned nothing that made
                # sense, silently.  A stable digest is the whole point of a
                # deterministic stand-in.
                vector[_stable_bucket(token, self.dimension)] += float(count)
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            dense.append([value / norm for value in vector])
            sparse.append({token: float(count) for token, count in counts.items()})
        return Embeddings(dense=dense, sparse=sparse)


class BM25:
    """Okapi BM25 over the chunk texts.

    Rebuilt from text alone, in minutes, with no model -- which is exactly why
    it is here beside a dense index that takes days.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        # token -> (chunk indices, term counts, precomputed idf), as arrays.
        # Scoring is then vectorised over a term's postings instead of looping
        # them in Python, which is what makes a long query affordable.
        self.postings: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
        self.lengths: np.ndarray = np.zeros(0, dtype="float32")
        self.average_length = 0.0

    def fit(self, texts: Iterable[str]) -> "BM25":
        raw: dict[str, list[tuple[int, int]]] = {}
        lengths: list[int] = []
        for index, text in enumerate(texts):
            tokens = tokenize(text)
            lengths.append(len(tokens))
            for token, count in Counter(tokens).items():
                raw.setdefault(token, []).append((index, count))
        self.lengths = np.asarray(lengths, dtype="float32")
        total = len(lengths)
        self.average_length = float(self.lengths.mean()) if total else 0.0
        self.postings = {}
        for token, entries in raw.items():
            appearances = len(entries)
            idf = math.log(1 + (total - appearances + 0.5) / (appearances + 0.5))
            self.postings[token] = (
                np.fromiter((index for index, _ in entries), dtype="int32", count=appearances),
                np.fromiter((count for _, count in entries), dtype="float32", count=appearances),
                idf)
        return self

    def score(self, query: str) -> "np.ndarray":
        """A score per chunk, zero where no query term appears.

        Three things make this affordable, and the corpus forced each one.

        Walking every chunk for every term is O(vocabulary x corpus); the
        postings list makes it proportional to the matches.

        Terms are then taken **once** each. A judgment paragraph is a legitimate
        query and repeats "the" a dozen times; walking that term's postings a
        dozen times multiplied the cost for no change in ranking, since a
        repeated term's contribution is a constant factor.

        And the walk itself is vectorised. Over 38,292 chunks a paragraph-length
        query touched roughly 19 million postings -- common words appear in
        nearly every chunk -- which took **11.5 seconds per query** in a Python
        loop and made a 3,000-query evaluation a 9.5-hour job.
        """
        # float64 for the accumulator: at float32 the running sums tie
        # differently and 12 queries in 3,000 changed rank, which is a
        # measurable difference bought for nothing -- one query's scores
        # are a few hundred kilobytes either way.
        totals = np.zeros(len(self.lengths), dtype="float64")
        for token, occurrences in Counter(tokenize(query)).items():
            found = self.postings.get(token)
            if not found:
                continue
            indices, counts, idf = found
            denominator = counts + self.k1 * (
                1 - self.b + self.b * self.lengths[indices] / (self.average_length or 1.0))
            # A term's postings are distinct chunks, so this is a plain
            # scatter-add rather than needing np.add.at.
            totals[indices] += (idf * occurrences * (self.k1 + 1)) * counts / denominator
        return totals
