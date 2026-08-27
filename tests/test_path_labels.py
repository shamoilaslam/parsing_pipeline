import unittest
from pathlib import Path

from specter.ingest_pdfs import apply_path_labels


PDF = Path(r"D:\corpus\SC\Mr. Justice Maqbool Baqar\SCP_C.A.634_2018_11-09-2025.pdf")


def result(metadata, provenance=None):
    return {"document": {"metadata": dict(metadata), "metadata_provenance": dict(provenance or {})}}


class FillingGapsTests(unittest.TestCase):
    """A label fills what extraction could not find, and nothing else."""

    def test_an_empty_field_is_filled_from_the_path(self):
        # 20% of SC judgments never state their decision date in the text, so
        # no parser can find it; the filename is the only source there is.
        out = apply_path_labels(result({"decision_date": None}), PDF)
        self.assertEqual(out["document"]["metadata"]["decision_date"], "11-09-2025")

    def test_a_filled_field_is_marked_as_coming_from_the_filename(self):
        out = apply_path_labels(result({"decision_date": None}), PDF)
        entry = out["document"]["metadata_provenance"]["decision_date"]
        self.assertEqual(entry["source"], "filename")
        # No page region states it, so no box is claimed.  A box that does not
        # contain what it points at is worse than no box.
        self.assertEqual(entry["bbox"], [])
        self.assertIsNone(entry["page"])

    def test_missing_judges_are_filled_from_the_folder(self):
        out = apply_path_labels(result({"judges": []}), PDF)
        self.assertEqual(out["document"]["metadata"]["judges"], ["Maqbool Baqar"])

    def test_a_missing_case_number_is_composed_from_type_number_and_year(self):
        out = apply_path_labels(result({"case_number": None}), PDF)
        self.assertEqual(out["document"]["metadata"]["case_number"], "C.A.634/2018")


class NeverOverwriteTests(unittest.TestCase):
    """Extraction found it on the page; the scraper can be wrong too."""

    def test_an_extracted_value_survives_even_when_the_label_differs(self):
        out = apply_path_labels(result({"decision_date": "01.01.1999"}), PDF)
        self.assertEqual(out["document"]["metadata"]["decision_date"], "01.01.1999")

    def test_the_disagreement_is_recorded_rather_than_hidden(self):
        out = apply_path_labels(result({"decision_date": "01.01.1999"}), PDF)
        findings = out["document"]["label_check"]["findings"]
        self.assertEqual([f["state"] for f in findings if f["field"] == "decision_date"], ["disagrees"])

    def test_agreement_produces_no_finding(self):
        metadata = {"decision_date": "11.09.2025", "case_number": "C.A. 634 of 2018",
                    "judges": ["MAQBOOL BAQAR"], "court": "SUPREME COURT OF PAKISTAN",
                    "court_id": "supreme_court_pakistan"}
        out = apply_path_labels(result(metadata), PDF)
        self.assertEqual(out["document"]["label_check"]["findings"], [])

    def test_a_real_bbox_is_never_replaced(self):
        located = {"decision_date": {"value": "01.01.1999", "bbox": [1, 2, 3, 4], "source": "native_text"}}
        out = apply_path_labels(result({"decision_date": "01.01.1999"}, located), PDF)
        self.assertEqual(out["document"]["metadata_provenance"]["decision_date"]["bbox"], [1, 2, 3, 4])


class OtherCourtTests(unittest.TestCase):
    """Each court's filenames state a different amount."""

    def test_a_neutral_citation_never_becomes_a_case_number(self):
        # "2023LHC5924" is how the judgment is cited, not the number the court
        # gave the case.  Treating it as one both invented "None.5924/2023" and
        # made every LHC document disagree with its own label.
        out = apply_path_labels(result({"case_number": None}), Path(r"D:\corpus\LHC\2023LHC5924.pdf"))
        self.assertIsNone(out["document"]["metadata"]["case_number"])
        self.assertEqual(out["document"]["metadata"]["neutral_citation"], "2023 LHC 5924")

    def test_an_extracted_case_number_survives_beside_the_citation(self):
        out = apply_path_labels(result({"case_number": "Writ Petition No.23303/13"}),
                                Path(r"D:\corpus\LHC\2013LHC3273.pdf"))
        self.assertEqual(out["document"]["metadata"]["case_number"], "Writ Petition No.23303/13")
        states = [f["state"] for f in out["document"]["label_check"]["findings"]]
        self.assertNotIn("disagrees", states)

    def test_a_court_that_states_no_judge_claims_none(self):
        out = apply_path_labels(result({"judges": []}), Path(r"D:\corpus\LHC\2023LHC5924.pdf"))
        self.assertEqual(out["document"]["metadata"]["judges"], [])

    def test_a_filename_no_court_recognises_is_left_alone(self):
        out = apply_path_labels(result({"decision_date": None}), Path(r"D:\corpus\other\judgment.pdf"))
        self.assertIsNone(out["document"]["metadata"]["decision_date"])
        self.assertNotIn("label_check", out["document"])


if __name__ == "__main__":
    unittest.main()
