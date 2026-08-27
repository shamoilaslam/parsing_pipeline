import unittest

from specter.courts import canonical_name, court_id, matches_case_number, matches_judge, path_labels, same_date


class PathLabelTests(unittest.TestCase):
    """The SC corpus states case identity in its own paths."""

    def labels(self, name, judge="Mr. Justice Syed Mansoor Ali Shah"):
        return path_labels(rf"D:\corpus\SC\{judge}\{name}")

    def test_a_standard_filename_is_read(self):
        got = self.labels("SCP_C.R.P.458_2024_16-05-2025.pdf")
        self.assertEqual(got["case_type"], "C.R.P")
        self.assertEqual(got["case_number"], "458")
        self.assertEqual(got["year"], "2024")
        self.assertEqual(got["decision_date"], "16-05-2025")
        self.assertEqual(got["judge"], "Syed Mansoor Ali Shah")

    def test_a_bench_suffix_stays_with_the_number(self):
        # "35-K" is the Karachi registry's numbering, not a separate field.
        self.assertEqual(self.labels("SCP_Crl.A.35-K_2016_04-05-2018.pdf")["case_number"], "35-K")

    def test_a_missing_trailing_date_still_parses(self):
        # 13 of the 790 files omit it; the case is still identified.
        got = self.labels("SCP_C.A.224_2003_.pdf")
        self.assertEqual(got["case_number"], "224")
        self.assertIsNone(got["decision_date"])

    def test_the_judge_prefix_is_stripped_in_any_casing(self):
        for folder in ("Mr. Justice Faisal Arab", "MR. JUSTICE FAISAL ARAB", "Justice Faisal Arab"):
            got = self.labels("SCP_C.A.1_2020_01-01-2021.pdf", judge=folder)
            self.assertNotIn("justice", got["judge"].casefold(), folder)

    def test_a_filename_no_court_claims_is_not_guessed_at(self):
        self.assertIsNone(path_labels(r"D:\corpus\SC\judge\judgment.pdf"))

    def test_a_second_court_gets_its_own_pattern(self):
        # The registry is the point: another court is an entry, not an edit.
        got = path_labels(r"D:\corpus\LHC\2023LHC5924.pdf")
        self.assertEqual(got["court_id"], "lahore_high_court")
        self.assertEqual(got["year"], "2023")
        # LHC filenames name no judge, so none is claimed.
        self.assertIsNone(got["judge"])


class RegistryTests(unittest.TestCase):
    """Every spelling of a court resolves to one identifier."""

    def test_spellings_resolve_to_one_id(self):
        for spelling in ("IN THE SUPREME COURT OF PAKISTAN", "SUPREME COURT OF PAKISTAN",
                         "IN THE. SUPREME COURT OF PAKISTAN"):
            self.assertEqual(court_id(spelling), "supreme_court_pakistan", spelling)
        self.assertEqual(court_id("HIGH COURT OF SINDH"), "sindh_high_court")

    def test_unknown_courts_are_not_guessed(self):
        self.assertIsNone(court_id("Something Else"))
        self.assertIsNone(court_id(None))

    def test_only_a_joined_name_is_rewritten(self):
        self.assertEqual(canonical_name("INTHESUPREMECOURTOFPAKISTAN"), "SUPREME COURT OF PAKISTAN")
        for spaced in ("IN THE SUPREME COURT OF PAKISTAN", "IN THE LAHORE HIGH COURT, LAHORE"):
            self.assertEqual(canonical_name(spaced), spaced)


class DateComparisonTests(unittest.TestCase):
    """The corpus writes the same date several ways."""

    def test_separators_and_padding_do_not_matter(self):
        self.assertTrue(same_date("16-05-2025", "16.05.2025"))
        self.assertTrue(same_date("2-6-2005", "02.06.2005"))

    def test_a_different_day_does_not_match(self):
        self.assertFalse(same_date("16-05-2025", "17.05.2025"))

    def test_a_two_digit_year_is_refused_rather_than_guessed(self):
        self.assertFalse(same_date("16-05-2025", "16.05.25"))

    def test_missing_values_do_not_match(self):
        self.assertFalse(same_date(None, "16.05.2025"))
        self.assertFalse(same_date("16.05.2025", ""))


class CaseNumberMatchTests(unittest.TestCase):
    def setUp(self):
        self.labels = {"case_number": "634", "year": "2018"}

    def test_number_and_year_together_match(self):
        self.assertTrue(matches_case_number(self.labels, "Civil Appeal No. 634 of 2018"))
        self.assertTrue(matches_case_number(self.labels, "C.A.634/2018"))

    def test_a_number_without_its_year_is_not_a_match(self):
        # This is the observed SC defect: the year is dropped, which the
        # measurement must report rather than quietly accept.
        self.assertFalse(matches_case_number(self.labels, "Civil Appeals No. 634"))

    def test_a_different_case_does_not_match(self):
        self.assertFalse(matches_case_number(self.labels, "Civil Appeal No. 999 of 2018"))
        self.assertFalse(matches_case_number(self.labels, None))


class JudgeMatchTests(unittest.TestCase):
    def test_the_folder_judge_is_found_among_extracted_names(self):
        labels = {"judge": "Maqbool Baqar"}
        self.assertTrue(matches_judge(labels, ["MAQBOOL BAQAR"]))
        self.assertTrue(matches_judge(labels, ["Mian Saqib Nisar", "Maqbool Baqar"]))

    def test_an_absent_judge_is_reported_as_absent(self):
        self.assertFalse(matches_judge({"judge": "Maqbool Baqar"}, []))
        self.assertFalse(matches_judge({"judge": "Maqbool Baqar"}, ["Asif Saeed Khan Khosa"]))
        self.assertFalse(matches_judge({"judge": None}, ["Maqbool Baqar"]))


class CaseNumberComparisonTests(unittest.TestCase):
    def test_a_leading_zero_is_not_a_different_case(self):
        # The court writes "No.08-B of 2026"; the corpus filed it as 8.
        labels = {"case_number": "8", "year": "2026"}
        self.assertTrue(matches_case_number(labels, "Criminal Miscellaneous No.08-B of 2026"))

    def test_the_year_must_still_be_there(self):
        labels = {"case_number": "8", "year": "2026"}
        self.assertFalse(matches_case_number(labels, "Criminal Miscellaneous No.08-B"))


if __name__ == "__main__":
    unittest.main()
