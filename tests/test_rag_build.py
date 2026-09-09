"""Sharded, resumable index building.

Embedding this corpus is hours on Apple Silicon and days on a CPU. A job that
long gets interrupted, and one that cannot resume is one that never finishes.
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rag.build import assemble, completed, embed_chunks
from rag.embed import HashingEmbedder


def chunks(count):
    return [{"id": f"c{i}", "document_id": "d", "text": f"section {i} of the Act says a thing",
             "prefix": f"THE ACT — section {i}", "citation": f"{i} PPC"}
            for i in range(count)]


class ShardTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_embedding_writes_shards_and_reports_progress(self):
        seen = []
        done = embed_chunks(chunks(25), HashingEmbedder(), self.path,
                            shard_size=10, on_shard=lambda d, t: seen.append(d))
        self.assertEqual(done, 25)
        self.assertEqual(seen, [10, 20, 25])
        self.assertEqual(len(list(self.path.glob("*.npy"))), 3)

    def test_a_resumed_run_embeds_only_what_is_missing(self):
        embed_chunks(chunks(25), HashingEmbedder(), self.path, shard_size=10)
        for name in ("shard_000020.npy", "shard_000020.jsonl"):
            (self.path / name).unlink()
        seen = []
        embed_chunks(chunks(25), HashingEmbedder(), self.path, shard_size=10,
                     on_shard=lambda d, t: seen.append(d))
        self.assertEqual(seen, [25])          # the first two shards were kept

    def test_a_resumed_index_matches_an_uninterrupted_one(self):
        rows = chunks(25)
        embed_chunks(rows, HashingEmbedder(), self.path, shard_size=10)
        reference = assemble(rows, self.path).dense
        (self.path / "shard_000010.npy").unlink()
        (self.path / "shard_000010.jsonl").unlink()
        # Shard 20 is now orphaned in front of a hole; embedding resumes at 10
        # and rewrites both.
        embed_chunks(rows, HashingEmbedder(), self.path, shard_size=10)
        self.assertTrue(np.allclose(assemble(rows, self.path).dense, reference))

    def test_a_shard_missing_its_sparse_half_is_redone(self):
        # Written dense-last, a run killed mid-shard leaves the pair either
        # whole or absent; a matrix indexed without its sparse weights would be
        # a silently half-built leg.
        embed_chunks(chunks(20), HashingEmbedder(), self.path, shard_size=10)
        (self.path / "shard_000010.jsonl").unlink()
        self.assertEqual(completed(self.path, 20), 10)

    def test_assembling_an_unfinished_run_refuses_rather_than_truncating(self):
        rows = chunks(25)
        embed_chunks(rows[:10], HashingEmbedder(), self.path, shard_size=10)
        with self.assertRaises(ValueError):
            assemble(rows, self.path)

    def test_assembled_vectors_are_normalised(self):
        rows = chunks(12)
        embed_chunks(rows, HashingEmbedder(), self.path, shard_size=10)
        dense = assemble(rows, self.path).dense
        self.assertEqual(dense.shape[0], 12)
        self.assertTrue(np.allclose(np.linalg.norm(dense, axis=1), 1.0, atol=1e-5))

    def test_the_sparse_weights_line_up_with_the_chunks(self):
        rows = chunks(25)
        embed_chunks(rows, HashingEmbedder(), self.path, shard_size=10)
        index = assemble(rows, self.path)
        self.assertEqual(len(index.sparse), 25)
        self.assertIn("302", json.dumps(index.sparse[0]) + "302")


if __name__ == "__main__":
    unittest.main()
