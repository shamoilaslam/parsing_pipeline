"""IHC states its metadata in a record beside each judgment, not in a filename.

The corpus gives every case a folder holding ``judgment.pdf``, any interim
``order_<ddmmyyyy>_<n>.pdf``, and a ``meta.json`` naming the parties, the
bench, the author and the date of the order.  That is a far better label than
a filename -- and it is still a label, so the same two rules hold: it fills a
field extraction left empty, and it never overwrites one found on the page.
"""
import json
import tempfile
import unittest
from pathlib import Path

from specter.courts import normalise_named_date, path_labels
from specter.ingest_pdfs import apply_document_kind, apply_path_labels


SIDECAR = {
    "case_no": "Criminal Appeal-31-2020 | Citation Awaited",
    "title": "Riaz Pervaiz VS The State etc",
    "year": "2020",
    "judge": "Honourable Mr. Justice Babar Sattar",
    "order_date": "28-APR-2022",
    "category": "N.A.B., UPTO 7 YEARS",
    "bench": ["Honourable Mr. Justice Babar Sattar", "Honourable Mr. Justice Mohsin Akhtar Kayani"],
    "author": "Honourable Mr. Justice Mohsin Akhtar Kayani",
    "description": "Appeal against conviction in NAB Matter",
}


class Corpus:
    """A throwaway copy of the corpus layout: year / judge / case / files."""

    def __init__(self, sidecar=SIDECAR, folder="Criminal Appeal-31-2020 _ Citation Awaited"):
        self.tmp = tempfile.TemporaryDirectory()
        self.case = (Path(self.tmp.name) / "2020_OfficialSite"
                     / "Honourable Mr. Justice Babar Sattar" / folder)
        self.case.mkdir(parents=True)
        if sidecar is not None:
            (self.case / "meta.json").write_text(json.dumps(sidecar), encoding="utf-8")

    def pdf(self, name="judgment.pdf"):
        path = self.case / name
        path.touch()
        return path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


def result(metadata, provenance=None):
    return {"document": {"metadata": dict(metadata), "metadata_provenance": dict(provenance or {})}}


class FolderLabelTests(unittest.TestCase):
    """The folder names the case even when the record is missing."""

    def test_the_case_folder_names_type_number_and_year(self):
        with Corpus(sidecar=None) as corpus:
            labels = path_labels(corpus.pdf())
        self.assertEqual(labels["court_id"], "islamabad_high_court")
        self.assertEqual(labels["case_type"], "Criminal Appeal")
        self.assertEqual(labels["case_number"], "31")
        self.assertEqual(labels["year"], "2020")

    def test_the_case_number_is_written_the_way_the_court_writes_it(self):
        with Corpus(sidecar=None) as corpus:
            labels = path_labels(corpus.pdf())
        self.assertEqual(labels["case_number_stated"], "Criminal Appeal-31-2020")

    def test_the_judge_comes_from_the_folder_above_the_case(self):
        with Corpus(sidecar=None) as corpus:
            labels = path_labels(corpus.pdf())
        self.assertEqual(labels["judge"], "Babar Sattar")

    def test_citation_awaited_is_not_recorded_as_a_citation(self):
        # 397 of 400 sampled cases carry it.  Stored, it would put the same
        # placeholder on the whole corpus and make the field useless to filter.
        with Corpus(sidecar=None) as corpus:
            self.assertIsNone(path_labels(corpus.pdf())["neutral_citation"])

    def test_a_real_citation_is_kept(self):
        with Corpus(sidecar=None, folder="Writ Petition-4-2019 _ 2021 PLD 55") as corpus:
            self.assertEqual(path_labels(corpus.pdf())["neutral_citation"], "2021 PLD 55")

    def test_an_unreadable_record_leaves_the_folder_labels_standing(self):
        with Corpus(sidecar=None) as corpus:
            (corpus.case / "meta.json").write_text("{not json", encoding="utf-8")
            labels = path_labels(corpus.pdf())
        self.assertEqual(labels["case_number_stated"], "Criminal Appeal-31-2020")
        self.assertEqual(labels["source"], "filename")


class SidecarTests(unittest.TestCase):
    """What the record states, read into the fields the pipeline has."""

    def test_the_parties_are_taken_from_the_case_title(self):
        with Corpus() as corpus:
            labels = path_labels(corpus.pdf())
        self.assertEqual(labels["parties"], "Riaz Pervaiz VS The State etc")
        self.assertEqual(labels["petitioner"], "Riaz Pervaiz")
        self.assertEqual(labels["respondent"], "The State etc")

    def test_the_whole_bench_is_named_without_honorifics(self):
        with Corpus() as corpus:
            self.assertEqual(path_labels(corpus.pdf())["judges"],
                             ["Babar Sattar", "Mohsin Akhtar Kayani"])

    def test_a_named_month_becomes_a_number(self):
        # ``same_date`` compares the numbers in a date, so "28-APR-2022" left
        # as written would report a disagreement on every IHC document.
        with Corpus() as corpus:
            self.assertEqual(path_labels(corpus.pdf())["decision_date"], "28-04-2022")

    def test_dates_that_are_not_written_that_way_are_left_alone(self):
        self.assertEqual(normalise_named_date("11-09-2025"), "11-09-2025")
        self.assertIsNone(normalise_named_date(None))

    def test_an_order_carries_its_own_date_not_the_judgment_s(self):
        with Corpus() as corpus:
            labels = path_labels(corpus.pdf("order_16022022_1.pdf"))
        self.assertEqual(labels["decision_date"], "16-02-2022")

    def test_the_record_is_kept_whole(self):
        # It states more than the pipeline models -- the filing category, the
        # laws discussed, every hearing and its short order.
        with Corpus() as corpus:
            out = apply_path_labels(result({}), corpus.pdf())
        self.assertEqual(out["document"]["source_metadata"]["description"],
                         "Appeal against conviction in NAB Matter")

    def test_the_record_is_not_repeated_inside_the_label_check(self):
        with Corpus() as corpus:
            out = apply_path_labels(result({}), corpus.pdf())
        self.assertNotIn("sidecar", out["document"]["label_check"]["labels"])


class ApplyTests(unittest.TestCase):
    """The same two rules as every other court."""

    def test_empty_fields_are_filled_from_the_record(self):
        with Corpus() as corpus:
            metadata = apply_path_labels(result({}), corpus.pdf())["document"]["metadata"]
        self.assertEqual(metadata["petitioner"], "Riaz Pervaiz")
        self.assertEqual(metadata["decision_date"], "28-04-2022")
        self.assertEqual(metadata["court_id"], "islamabad_high_court")
        self.assertEqual(metadata["category"], "N.A.B., UPTO 7 YEARS")

    def test_a_filled_field_claims_no_page_region(self):
        with Corpus() as corpus:
            out = apply_path_labels(result({}), corpus.pdf())
        entry = out["document"]["metadata_provenance"]["petitioner"]
        self.assertEqual(entry["bbox"], [])
        self.assertEqual(entry["source"], "sidecar")

    def test_what_the_page_states_survives_the_record(self):
        with Corpus() as corpus:
            out = apply_path_labels(result({"petitioner": "RIAZ PERVAIZ SON OF"}), corpus.pdf())
        self.assertEqual(out["document"]["metadata"]["petitioner"], "RIAZ PERVAIZ SON OF")

    def test_a_party_name_is_filled_from_the_record_but_never_checked_against_it(self):
        # The record writes a display title -- "FOP etc", "MD, OGDCL etc",
        # "Toyata Islamabad Moters" -- where the cause title prints the name in
        # full, and the page is usually the better of the two.  Comparing them
        # reported a disagreement on 176 of 281 documents and not one was
        # actionable; a check that fires on half a corpus is not a check.
        # Party extraction is measured in the benchmark, where the caveat can
        # be stated.
        with Corpus() as corpus:
            out = apply_path_labels(result({"petitioner": "Muhammad Aslam"}), corpus.pdf())
        self.assertEqual([f["state"] for f in out["document"]["label_check"]["findings"]
                          if f["field"] == "petitioner"], [])

    def test_one_judge_of_the_bench_is_agreement(self):
        # The record names both; the page names whoever wrote.
        with Corpus() as corpus:
            out = apply_path_labels(result({"judges": ["Mohsin Akhtar Kayani"]}), corpus.pdf())
        self.assertEqual([f["state"] for f in out["document"]["label_check"]["findings"]
                          if f["field"] == "judges"], [])


class DocumentKindTests(unittest.TestCase):
    def test_an_interim_order_is_not_called_a_judgment(self):
        # It decides a step in the case and states no final disposition.  Filed
        # as a judgment, it would answer "what did the court hold" with an
        # adjournment.
        with Corpus() as corpus:
            out = apply_document_kind(result({}), corpus.pdf("order_16022022_1.pdf"))
        self.assertEqual(out["document"]["document_kind"], "order")

    def test_the_judgment_is_a_judgment(self):
        with Corpus() as corpus:
            out = apply_document_kind(result({}), corpus.pdf())
        self.assertEqual(out["document"]["document_kind"], "judgment")


class OtherCourtsUnaffectedTests(unittest.TestCase):
    """The IHC folder pattern is loose; it must not claim another court's file."""

    def test_a_supreme_court_filename_still_reads_as_supreme_court(self):
        labels = path_labels(Path(r"D:\c\SC\Mr. Justice Maqbool Baqar\SCP_C.A.634_2018_11-09-2025.pdf"))
        self.assertEqual(labels["court_id"], "supreme_court_pakistan")
        self.assertEqual(labels["case_number_stated"], "C.A.634/2018")

    def test_a_lahore_filename_still_reads_as_lahore(self):
        labels = path_labels(Path(r"D:\c\LHC\2023LHC5924.pdf"))
        self.assertEqual(labels["court_id"], "lahore_high_court")
        self.assertIsNone(labels["case_number_stated"])


if __name__ == "__main__":
    unittest.main()
