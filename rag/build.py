"""Building a dense index in shards, so a long run can be resumed.

Embedding this corpus is a long job: BGE-M3 is 568M parameters and the corpus is
roughly 86M tokens across ~200,000 chunks. On a machine without a GPU that is on
the order of days, and even with Apple Silicon acceleration it is hours. A job
of that length will be interrupted -- a closed laptop, a full disk, a killed
process -- and restarting from zero each time is how an index never gets built.

So embedding writes a **shard every `shard_size` chunks** and records how far it
got. A resumed run reads the shards already on disk, skips exactly those chunks,
and carries on. The shards are concatenated into the final matrix at the end,
which is also the point at which the vectors are normalised, so an interrupted
run leaves nothing half-written that a reader could mistake for an index.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from rag.embed import Embedder
from rag.retrieve import HybridIndex, indexed_text

SHARD_SIZE = 512
SHARD = "shard_{:06d}"


def _shard_paths(directory: Path, start: int) -> tuple[Path, Path]:
    stem = SHARD.format(start)
    return directory / f"{stem}.npy", directory / f"{stem}.jsonl"


def completed(directory: Path, total: int, shard_size: int = SHARD_SIZE) -> int:
    """How many chunks are already embedded, counting only whole shards.

    A shard is written dense-first, so a run killed between the two files would
    leave a matrix with no sparse weights beside it. Requiring both means such a
    shard is redone rather than silently indexed without its sparse half.
    """
    done = 0
    while done < total:
        dense, sparse = _shard_paths(directory, done)
        if not (dense.exists() and sparse.exists()):
            break
        done += len(np.load(dense, mmap_mode="r"))
    return done


def embed_chunks(chunks: Sequence[dict[str, Any]], embedder: Embedder, directory: Path, *,
                 batch_size: int = 8, shard_size: int = SHARD_SIZE,
                 resume: bool = True,
                 on_shard: Callable[[int, int], None] | None = None) -> int:
    """Embed `chunks` into shards under `directory`; returns how many were done."""
    directory.mkdir(parents=True, exist_ok=True)
    start = completed(directory, len(chunks), shard_size) if resume else 0
    while start < len(chunks):
        batch = chunks[start:start + shard_size]
        result = embedder.encode([indexed_text(chunk) for chunk in batch],
                                 batch_size=batch_size)
        dense_path, sparse_path = _shard_paths(directory, start)
        # Sparse first: `completed` requires both files, and writing the cheap
        # one last leaves the pair either whole or absent.
        sparse_path.write_text(
            "\n".join(json.dumps(weights) for weights in result.sparse), encoding="utf-8")
        np.save(dense_path, np.asarray(result.dense, dtype="float32"))
        start += len(batch)
        if on_shard:
            on_shard(start, len(chunks))
    return start


def assemble(chunks: list[dict[str, Any]], directory: Path,
             shard_size: int = SHARD_SIZE) -> HybridIndex:
    """Concatenate the shards into one index."""
    dense_parts: list[np.ndarray] = []
    sparse: list[dict[str, float]] = []
    done = 0
    while done < len(chunks):
        dense_path, sparse_path = _shard_paths(directory, done)
        if not (dense_path.exists() and sparse_path.exists()):
            break
        part = np.load(dense_path)
        dense_parts.append(part)
        sparse.extend(json.loads(line) for line
                      in sparse_path.read_text(encoding="utf-8").splitlines() if line)
        done += len(part)
    if done != len(chunks):
        raise ValueError(f"{done} of {len(chunks)} chunks embedded; run the index again to finish")
    matrix = np.concatenate(dense_parts) if dense_parts else np.zeros((0, 0), dtype="float32")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) if matrix.size else None
    if norms is not None:
        matrix = matrix / np.where(norms == 0.0, 1.0, norms)
    return HybridIndex(chunks, dense=matrix, sparse=sparse)
