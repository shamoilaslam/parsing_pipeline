import unittest

from specter.specter_parser import _decision_date


class DecisionDateTests(unittest.TestCase):
    """A judgment states several dates; only one is the court's own.

    Measured before this rule existed: the SC decision date agreed with the
    corpus label on 1.0% of documents, and the LHC date fell in the judgment's
    own year on 33.7%.  Both were wrong far more often than they were right,
    which nothing caught because the page benchmark scores text, not metadata.
    """

    def test_an_explicit_judgment_date_wins(self):
        text = "Date of judgment: 03.02.2021\nDate of Hearing: 01.01.2021"
        self.assertEqual(_decision_date(text), "03.02.2021")

    def test_pronouncement_wording_is_read(self):
        for phrasing in ("decided on 15.06.2019", "announced on 15.06.2019", "pronounced on 15.06.2019"):
            self.assertEqual(_decision_date(phrasing), "15.06.2019", phrasing)

    def test_the_impugned_date_on_an_sc_cover_is_not_taken(self):
        # Every SC cover opens with the order under appeal.  Reading its date
        # as this judgment's was the single largest metadata defect on SC.
        text = ("IN THE SUPREME COURT OF PAKISTAN\n"
                "(Against the judgment dated 24.08.2017 passed by the High Court)\n"
                "Date of Hearing: 11.09.2025")
        self.assertEqual(_decision_date(text), "11.09.2025")

    def test_the_hearing_date_is_used_when_nothing_states_a_judgment_date(self):
        # In 30 of the 34 sampled SC documents whose true decision date appears
        # in the text at all, it appears as the Date of Hearing.
        self.assertEqual(_decision_date("Date of Hearing: 11.09.2025"), "11.09.2025")

    def test_an_explicit_date_still_beats_the_hearing_date(self):
        text = "Date of Hearing: 01.01.2021\nJudgment announced on 05.05.2021"
        self.assertEqual(_decision_date(text), "05.05.2021")

    def test_a_bare_dated_is_accepted_when_nothing_marks_it_impugned(self):
        self.assertEqual(_decision_date("This order dated 07.03.2020 disposes of the matter"), "07.03.2020")

    def test_a_cover_with_only_an_impugned_date_still_reports_it(self):
        # Known limit, kept deliberately.  A guard that skipped dates following
        # "against"/"passed by" was measured and made LHC worse (64.9% vs
        # 68.4%) while changing SC not at all, so the simpler rule stands: when
        # a document states no date of its own, the one date present is
        # reported rather than nothing.  The filename label is what corrects
        # these, and it records the disagreement instead of hiding it.
        self.assertEqual(_decision_date("Against the order dated 01.01.2001"), "01.01.2001")

    def test_no_date_yields_nothing_rather_than_a_guess(self):
        self.assertIsNone(_decision_date("There is no date in this judgment."))
        self.assertIsNone(_decision_date(""))

    def test_separator_styles_in_the_corpus_are_all_read(self):
        for written in ("11.09.2025", "11/09/2025", "11-09-2025", "1.9.25"):
            self.assertEqual(_decision_date(f"Date of Hearing: {written}"), written, written)


if __name__ == "__main__":
    unittest.main()
