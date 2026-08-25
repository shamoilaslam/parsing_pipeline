import unittest

from specter.specter_parser import _bench_judges, _extract_metadata, _party_near, _party_side


class PartySideTests(unittest.TestCase):
    """A role marker is not a party name."""

    def test_a_bare_role_marker_is_not_a_party(self):
        for marker in ("…Appellants.", "...Petitioner", "…Respondent(s)", "…Petitioner (s)", "Appellants."):
            self.assertEqual(_party_side(marker), "", marker)

    def test_a_trailing_role_suffix_is_stripped_from_a_real_name(self):
        self.assertEqual(_party_side("M/s Lagarge Cement Company. …Appellant(s)"), "M/s Lagarge Cement Company.")
        self.assertEqual(_party_side("Muhammad Sadiq ...Petitioners"), "Muhammad Sadiq")

    def test_a_consolidated_case_reference_is_not_a_party(self):
        self.assertEqual(_party_side("(in Civil Appeal No.101-P/2011)"), "")
        self.assertEqual(_party_side("Petitioner (In Cr.P.25-K/21)"), "")

    def test_a_trailing_case_reference_is_stripped(self):
        self.assertEqual(_party_side("Abdul Qadir (in C.A.55/2020)"), "Abdul Qadir")

    def test_an_ordinary_name_survives_untouched(self):
        self.assertEqual(_party_side("Syed Munawar Ali and others."), "Syed Munawar Ali and others.")


class PartyWalkTests(unittest.TestCase):
    """"VS" sits on its own line; the neighbour is rarely the party."""

    def lines(self):
        return [
            "Commissioner of Income Tax Company Zone,",
            "Income Tax Officer, Peshawar",
            "(in Civil Appeal No.102-P/2011).",
            "…Appellants.",
            "VS",
            "Syed Munawar Ali and others.",
        ]

    def test_the_walk_passes_role_markers_and_case_references(self):
        # Taking the adjacent line produced "...Appellants. VS Syed Munawar
        # Ali" -- a caption naming only one side.
        self.assertEqual(_party_near(self.lines(), 4, -1),
                         "Commissioner of Income Tax Company Zone, Income Tax Officer, Peshawar")

    def test_a_name_wrapped_over_two_lines_is_rejoined(self):
        # The previous line ends in a comma, which is how a printed list marks
        # a continuation.
        self.assertIn("Commissioner of Income Tax Company Zone,", _party_near(self.lines(), 4, -1))

    def test_the_walk_stops_at_the_counsel_block(self):
        # Otherwise the respondent comes back as "For the Petitioner(s)".
        lines = ["Javed Ali", "Versus", "For the Petitioner(s):", "Mr. Ali Raza, ASC"]
        self.assertEqual(_party_near(lines, 1, +1), "")

    def test_nothing_within_reach_yields_nothing(self):
        self.assertEqual(_party_near(["…Appellants.", "VS"], 1, +1), "")


class BenchTests(unittest.TestCase):
    """A five-judge bench must not be reported as one judge."""

    def head(self):
        return [
            "IN THE SUPREME COURT OF PAKISTAN",
            "(Appellate Jurisdiction)",
            "Present:",
            "Mr. Justice Anwar Zaheer Jamali, CJ",
            "Mr. Justice Mian Saqib Nisar",
            "Mr. Justice Amir Hani Muslim",
            "Civil Appeals No.101 & 102-P of 2011.",
        ]

    def test_every_judge_on_the_bench_is_read(self):
        self.assertEqual(_bench_judges(self.head()),
                         ["Anwar Zaheer Jamali", "Mian Saqib Nisar", "Amir Hani Muslim"])

    def test_the_list_ends_where_the_bench_ends(self):
        # The case number follows the bench; it is not a judge.
        self.assertNotIn("Civil Appeals No.101 & 102-P of 2011.", _bench_judges(self.head()))

    def test_other_bench_headings_are_understood(self):
        for heading in ("Coram:", "Before:", "PRESENT"):
            lines = [heading, "Mr. Justice Faisal Arab"]
            self.assertEqual(_bench_judges(lines), ["Faisal Arab"], heading)

    def test_a_document_with_no_bench_block_yields_nothing(self):
        self.assertEqual(_bench_judges(["IN THE SUPREME COURT OF PAKISTAN", "Civil Appeal No.1"]), [])


class MetadataIntegrationTests(unittest.TestCase):
    def test_bench_and_author_are_merged_without_duplicates(self):
        text = (
            "IN THE SUPREME COURT OF PAKISTAN\n"
            "Present:\n"
            "Mr. Justice Amir Hani Muslim\n"
            "Mr. Justice Mushir Alam\n"
            "Civil Appeal No.101 of 2011\n"
            "Regional Commissioner Income Tax\n"
            "…Appellants.\n"
            "VS\n"
            "Syed Munawar Ali and others.\n"
            "AMIR HANI MUSLIM, J.- These appeals\n"
        )
        metadata = _extract_metadata(text, {})
        self.assertEqual(metadata["judges"][:2], ["Amir Hani Muslim", "Mushir Alam"])
        # The author already appears in the bench, so it is not repeated.
        self.assertEqual(len(metadata["judges"]), 2)
        self.assertEqual(metadata["parties"], "Regional Commissioner Income Tax VS Syed Munawar Ali and others.")

    def test_each_side_is_reported_on_its_own(self):
        # "X Versus Y" as one string cannot answer "which cases named the State
        # as respondent", which is an ordinary question to ask of a case index.
        metadata = _extract_metadata(
            "IN THE SUPREME COURT OF PAKISTAN\n"
            "Muhammad Sadiq and others\nVersus\nThe State and another\n", {})
        self.assertEqual(metadata["petitioner"], "Muhammad Sadiq and others")
        self.assertEqual(metadata["respondent"], "The State and another")

    def test_no_caption_means_no_sides_rather_than_empty_strings(self):
        metadata = _extract_metadata("IN THE SUPREME COURT OF PAKISTAN\nNo caption here.\n", {})
        self.assertIsNone(metadata["petitioner"])
        self.assertIsNone(metadata["respondent"])


if __name__ == "__main__":
    unittest.main()
