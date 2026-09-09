"""PDFs to a searchable index in one command, resumable at every stage.

At this corpus size every run gets interrupted -- parsing 9,512 judgments took
hours, embedding 200,000 chunks is hours on Apple Silicon and days on a CPU --
so what is tested here is mostly what happens on the *second* run.
"""

import json
import tempfile
import unittest
from pathlib import Path

from rag.pipeline import STAGES, detect_device, run


def parsed_document(name, text, kind="statute"):
    return {
        "schema_version": "specter.v1",
        "pages": [{"page_number": 1, "blocks": [
            {"id": "p1_b0", "type": "text", "text": text,
             "bbox": [72.0, 100.0, 540.0, 140.0]}]}],
        "document": {"source_file": f"{name}.pdf", "document_kind": kind,
                     "metadata": {"title": "THE PAKISTAN PENAL CODE"},
                     "structure": [{"kind": "section", "number": "302",
                                    "title": "Punishment of qatl-i-amd",
                                    "block_id": "p1_b0"}]},
    }


class StageTests(unittest.TestCase):
    """Stages are exercised without parsing, which needs real PDFs."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.work = Path(self._dir.name) / "work"
        self.addCleanup(self._dir.cleanup)
        parsed = self.work / "parsed"
        parsed.mkdir(parents=True)
        (parsed / "ppc.json").write_text(json.dumps(parsed_document(
            "ppc", "302. Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished.")),
            encoding="utf-8")
        self.events = []

    def go(self, **kwargs):
        self.events.clear()
        return run([Path("unused")], self.work, source="pakistancode",
                   embedder="hashing", stages=("chunk", "index"),
                   on_event=self.events.append, **kwargs)

    def statuses(self, stage):
        return [event.get("status") for event in self.events
                if event.get("stage") == stage and event.get("status")]

    def test_chunk_then_index_produces_a_searchable_index(self):
        from rag.retrieve import HybridIndex

        state = self.go()
        self.assertEqual(state["stages"]["chunk"]["status"], "done")
        self.assertEqual(state["stages"]["index"]["status"], "done")
        index = HybridIndex.load(self.work / "index")
        self.assertTrue(index.chunks)
        self.assertEqual(index.search("qatl-i-amd", limit=1)[0].chunk["citation"], "302 PPC")

    def test_the_second_run_reuses_the_chunks_and_re_embeds_nothing(self):
        self.go()
        state = self.go()
        self.assertIn("reused", self.statuses("chunk"))
        # Every chunk was already embedded, so the index stage starts at the end.
        resumed = [event for event in self.events if event.get("resuming_from") is not None]
        self.assertEqual(resumed[0]["resuming_from"], state["stages"]["index"]["chunks"])

    def test_chunks_are_rebuilt_when_a_document_is_added(self):
        self.go()
        (self.work / "parsed" / "crpc.json").write_text(json.dumps(parsed_document(
            "crpc", "497. When bail may be taken in case of a non-bailable offence.")),
            encoding="utf-8")
        # Chunks built from fewer documents are stale, and a stale chunk file
        # would be embedded without complaint.
        state = self.go()
        self.assertIn("done", self.statuses("chunk"))
        self.assertEqual(state["stages"]["chunk"]["documents"], 2)

    def test_force_redoes_a_finished_stage(self):
        self.go()
        state = self.go(force=True)
        self.assertNotIn("reused", self.statuses("chunk"))
        self.assertEqual(state["stages"]["chunk"]["status"], "done")

    def test_the_state_file_records_what_ran(self):
        self.go()
        state = json.loads((self.work / "pipeline.json").read_text(encoding="utf-8"))
        self.assertEqual(set(state["stages"]), {"chunk", "index"})
        self.assertIn("seconds", state["stages"]["index"])

    def test_a_corpus_with_no_chunks_stops_rather_than_indexing_nothing(self):
        for path in (self.work / "parsed").glob("*.json"):
            path.unlink()
        state = self.go()
        self.assertEqual(state["stages"]["index"]["status"], "skipped")


class DeviceTests(unittest.TestCase):
    def test_a_backend_is_always_named(self):
        # The difference between Metal and CPU here is hours, so it is chosen
        # explicitly rather than left to a library default.
        self.assertIn(detect_device(), {"cpu", "mps", "cuda"})


class StageNameTests(unittest.TestCase):
    def test_an_unknown_stage_is_refused_rather_than_silently_skipped(self):
        from rag.__main__ import main

        self.assertEqual(main(["pipeline", ".", "--out", ".", "--stages", "embed"]), 2)

    def test_the_known_stages_are_the_documented_ones(self):
        self.assertEqual(STAGES, ("parse", "chunk", "index"))


if __name__ == "__main__":
    unittest.main()
