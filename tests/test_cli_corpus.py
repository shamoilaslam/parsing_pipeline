"""The folder-level CLI: what a corpus run must guarantee.

A run over tens of thousands of documents is unattended, so the properties
worth pinning are the ones that decide whether it finishes at all and whether
its output can be trusted afterwards: one bad file must not end the run, no
document may be silently overwritten, and a resumed run must still describe
the whole corpus.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from specter.ingest_pdfs import _parse_one, expand_paths, main, write_metadata
from specter.specter_parser import _md_link

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "pdfs" / "2021LHC9943.pdf"      # 2 pages, digital: fast


def run(*argv):
    saved = sys.argv
    sys.argv = ["specter parse", *argv]
    try:
        main()
    finally:
        sys.argv = saved


class ExpandPathsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "sub").mkdir()
        shutil.copy(SAMPLE, self.tmp / "a.pdf")
        shutil.copy(SAMPLE, self.tmp / "sub" / "b.pdf")
        self.addCleanup(self._tmp.cleanup)

    def test_a_folder_yields_its_pdfs(self):
        self.assertEqual([p.name for p in expand_paths([self.tmp])], ["a.pdf"])

    def test_recursive_descends_into_sub_folders(self):
        found = sorted(p.name for p in expand_paths([self.tmp], recursive=True))
        self.assertEqual(found, ["a.pdf", "b.pdf"])


class CorpusRunTests(unittest.TestCase):
    """One end-to-end run, inspected from several angles."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.src, cls.out = tmp / "in", tmp / "out"
        cls.src.mkdir()
        shutil.copy(SAMPLE, cls.src / "good.pdf")
        # A file that is not a PDF at all -- the corpus contains real damage,
        # and the run has to survive it.
        (cls.src / "broken.pdf").write_bytes(b"this is not a pdf")
        run(str(cls.src), "--out", str(cls.out), "--quiet")
        cls.manifest = json.loads((cls.out / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_each_document_gets_json_markdown_and_metadata(self):
        self.assertTrue((self.out / "good.json").exists())
        self.assertTrue((self.out / "good.md").exists())
        self.assertTrue((self.out / "metadata" / "good.json").exists())

    def test_markdown_is_not_empty(self):
        self.assertTrue((self.out / "good.md").read_text(encoding="utf-8").strip())

    def test_metadata_carries_case_identity_and_trust(self):
        meta = json.loads((self.out / "metadata" / "good.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["source_name"], "good.pdf")
        self.assertIn("case_number", meta["metadata"])
        # Written after write_result, which is what attaches diagnostics.
        self.assertIn("status", meta["diagnostics"])
        self.assertIn("selected", meta["router"])

    def test_a_broken_pdf_is_recorded_and_does_not_end_the_run(self):
        self.assertEqual(self.manifest["parsed"], 1)
        self.assertEqual(self.manifest["failed"], 1)
        self.assertIn("broken.pdf", self.manifest["failures"][0]["pdf"])
        self.assertTrue(self.manifest["failures"][0]["error"])

    def test_the_manifest_indexes_what_was_parsed(self):
        self.assertEqual(self.manifest["pdfs_found"], 2)
        row = self.manifest["documents"][0]
        self.assertEqual(row["stem"], "good")
        self.assertEqual(row["pages"], 2)
        self.assertEqual(row["route"], "digital")

    def test_the_manifest_records_the_settings_used(self):
        # Output is only reproducible if the run says how it was produced.
        self.assertEqual(self.manifest["settings"]["preprocess_variant"], "grayscale")


class ResumeAndCollisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.src, self.out = tmp / "in", tmp / "out"
        (self.src / "sub").mkdir(parents=True)
        shutil.copy(SAMPLE, self.src / "doc.pdf")
        self.addCleanup(self._tmp.cleanup)

    def test_same_stem_in_two_folders_is_reported_not_overwritten(self):
        shutil.copy(SAMPLE, self.src / "sub" / "doc.pdf")
        run(str(self.src), "--out", str(self.out), "--recursive", "--quiet")
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdfs_found"], 2)
        self.assertEqual(manifest["parsed"], 1)
        self.assertEqual(len(manifest["duplicate_stems"]), 1)
        self.assertEqual(manifest["duplicate_stems"][0]["stem"], "doc")

    def test_resuming_skips_finished_work_but_still_lists_it(self):
        run(str(self.src), "--out", str(self.out), "--quiet")
        stamp = (self.out / "doc.json").stat().st_mtime_ns
        run(str(self.src), "--out", str(self.out), "--skip-existing", "--quiet")
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((self.out / "doc.json").stat().st_mtime_ns, stamp, "should not re-parse")
        # A resumed run must still describe the whole corpus, not just the
        # remainder, or the manifest silently loses earlier documents.
        self.assertEqual(len(manifest["documents"]), 1)
        self.assertEqual(manifest["documents"][0]["stem"], "doc")


class WorkerJobTests(unittest.TestCase):
    """A worker returns its outcome instead of raising, in either mode."""

    def test_an_unreadable_pdf_returns_a_failure_rather_than_raising(self):
        # One bad document must not end a 30,000-document run, and the
        # sequential and pooled paths must agree on that -- an exception
        # crossing a process boundary would kill the pool.
        row, failure = _parse_one((Path("does_not_exist.pdf"), Path("artifacts/unused"),
                                   "auto", "raw", 2.0, "grayscale", 0.45))
        self.assertIsNone(row)
        self.assertIn("error", failure)
        self.assertIn("does_not_exist.pdf", failure["pdf"])

    def test_the_job_tuple_is_picklable(self):
        # ProcessPoolExecutor pickles every argument; a Path or float is fine,
        # but this pins it so a future non-picklable addition fails loudly here
        # rather than at corpus scale on Windows spawn.
        import pickle

        job = (Path("a.pdf"), Path("out"), "auto", "image", 2.0, "grayscale", 0.45)
        self.assertEqual(pickle.loads(pickle.dumps(job)), job)


class MarkdownAssetLinkTests(unittest.TestCase):
    """Asset links must survive the filenames this corpus actually uses."""

    def test_spaces_and_parentheses_are_encoded(self):
        # "2018LHC1986 (1).pdf" is a real corpus filename; an unencoded ")"
        # ends the Markdown link early and the image never renders.
        link = _md_link("assets/2018LHC1986 (1)/page_078_block_000.png")
        self.assertNotIn(" ", link)
        self.assertNotIn("(", link)
        self.assertNotIn(")", link)
        self.assertEqual(link, "assets/2018LHC1986%20%281%29/page_078_block_000.png")

    def test_separators_are_left_alone(self):
        self.assertEqual(_md_link("assets/x/y.png"), "assets/x/y.png")


class WriteMetadataTests(unittest.TestCase):
    def test_it_writes_under_a_metadata_folder_keyed_by_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {"document": {"source_name": "x.pdf", "fingerprint": {"template_key": "A"}}}
            path = write_metadata(result, Path(tmp))
            self.assertEqual(path, Path(tmp) / "metadata" / "x.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["template_key"], "A")


if __name__ == "__main__":
    unittest.main()
