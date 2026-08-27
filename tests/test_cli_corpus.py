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

from specter.ingest_pdfs import output_stems, _parse_one, expand_paths, main, write_metadata
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

    def test_same_stem_in_two_folders_is_renamed_not_dropped(self):
        # These were skipped, and reported as skipped, which is honest but
        # useless at IHC: 21,712 of its 60,529 PDFs are called judgment.pdf, so
        # skipping every repeat would parse an eighth of the corpus.
        shutil.copy(SAMPLE, self.src / "sub" / "doc.pdf")
        run(str(self.src), "--out", str(self.out), "--recursive", "--quiet")
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdfs_found"], 2)
        self.assertEqual(manifest["parsed"], 2)
        stems = sorted(row["stem"] for row in manifest["documents"])
        self.assertEqual(len(set(stems)), 2, stems)
        for stem in stems:
            self.assertTrue((self.out / f"{stem}.json").exists(), stem)
        self.assertEqual(len(manifest["renamed_for_uniqueness"]), 2)

    def test_a_name_nothing_else_shares_is_left_exactly_as_it_is(self):
        # LHC and SC output paths must not move; only a corpus that reuses a
        # filename pays for it.
        run(str(self.src), "--out", str(self.out), "--recursive", "--quiet")
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["documents"][0]["stem"], "doc")
        self.assertEqual(manifest["renamed_for_uniqueness"], [])

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
                                   "auto", "raw", 2.0, "grayscale", 0.45, "does_not_exist", False))
        self.assertIsNone(row)
        self.assertIn("error", failure)
        self.assertIn("does_not_exist.pdf", failure["pdf"])

    def test_the_job_tuple_is_picklable(self):
        # ProcessPoolExecutor pickles every argument; a Path or float is fine,
        # but this pins it so a future non-picklable addition fails loudly here
        # rather than at corpus scale on Windows spawn.
        import pickle

        job = (Path("a.pdf"), Path("out"), "auto", "image", 2.0, "grayscale", 0.45, "a", False)
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

    def test_an_assigned_name_is_what_the_file_is_called(self):
        # Every IHC judgment is `judgment.pdf`; without this they would all be
        # metadata/judgment.json.
        with tempfile.TemporaryDirectory() as tmp:
            result = {"document": {"source_name": "judgment.pdf", "output_stem": "Crl_Appeal-31-2020_ab12cd34"}}
            path = write_metadata(result, Path(tmp))
            self.assertEqual(path, Path(tmp) / "metadata" / "Crl_Appeal-31-2020_ab12cd34.json")

    def test_the_record_the_corpus_published_is_written_out_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = {"document": {"source_name": "judgment.pdf",
                                   "source_metadata": {"description": "Appeal against conviction"}}}
            path = write_metadata(result, Path(tmp))
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["source_metadata"]["description"], "Appeal against conviction")


class OutputNamingTests(unittest.TestCase):
    """Names stay recognisable where they can, and stay distinct always."""

    def test_distinct_filenames_are_kept_verbatim(self):
        pdfs = [Path("D:/c/2023LHC5924.pdf"), Path("D:/c/2013LHC3273.pdf")]
        self.assertEqual(list(output_stems(pdfs).values()), ["2023LHC5924", "2013LHC3273"])

    def test_a_repeated_filename_takes_its_case_folder(self):
        pdfs = [Path("D:/c/2020/J/Criminal Appeal-31-2020 _ Citation Awaited/judgment.pdf"),
                Path("D:/c/2020/J/Writ Petition-4-2019 _ Citation Awaited/judgment.pdf")]
        stems = list(output_stems(pdfs).values())
        self.assertTrue(stems[0].startswith("Criminal_Appeal-31-2020_"), stems[0])
        self.assertTrue(stems[1].startswith("Writ_Petition-4-2019_"), stems[1])

    def test_the_same_case_under_two_judges_still_gets_two_names(self):
        # The case folder alone is not distinct either: IHC repeats a case
        # number across judges and years, 7,544 times.
        pdfs = [Path("D:/c/2026/A/Criminal Appeal-16-2026/judgment.pdf"),
                Path("D:/c/2026/B/Criminal Appeal-16-2026/judgment.pdf")]
        stems = list(output_stems(pdfs).values())
        self.assertEqual(len(set(stems)), 2, stems)

    def test_a_name_stays_short_enough_to_write(self):
        long = "x" * 300
        pdfs = [Path("D:/c/" + long + "/judgment.pdf"), Path("D:/c/" + long + "2/judgment.pdf")]
        for stem in output_stems(pdfs).values():
            self.assertLessEqual(len(stem), 130, stem)


if __name__ == "__main__":
    unittest.main()
