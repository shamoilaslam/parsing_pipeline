"""How a statute is named when it is cited.

An abbreviation is conventional, not derivable. Every case here is one the
derived version got wrong on the real corpus.
"""

import unittest

from rag.acts import short_form, short_form_from_slug
from rag.evaluate import CITATION, statute_key


class ShortFormTests(unittest.TestCase):
    def test_a_printed_title_resolves(self):
        self.assertEqual(short_form("THE PAKISTAN PENAL CODE"), "PPC")
        self.assertEqual(short_form("THE CODE OF CIVIL PROCEDURE, 1908"), "CPC")

    def test_an_act_with_no_conventional_abbreviation_gets_none(self):
        # Deriving one produced "TAX", "ACTX" and "XIX" -- the roman numeral of
        # the act number -- for 515 sections.
        self.assertIsNone(short_form("THE STAMP ACT, 1899"))
        self.assertIsNone(short_form("THE CANTONMENTS ACT, 1924"))

    def test_the_filename_supplies_it_where_the_page_prints_no_title(self):
        # 61 statutes print no title the parser can read, and three of them are
        # the most cited codes in practice.
        self.assertEqual(short_form_from_slug("4_code_of_criminal_procedure_crpc_1898_under_review"), "CRPC")
        self.assertEqual(short_form_from_slug("6_code_of_civil_procedure_cpc_1908_under_review"), "CPC")
        self.assertEqual(short_form_from_slug("945_qanun_e_shahadat_order_1984_qso"), "QSO")

    def test_the_slug_is_matched_by_token_and_not_by_substring(self):
        self.assertIsNone(short_form_from_slug("151_stamp_act_1899"))
        self.assertIsNone(short_form_from_slug("290_cantonments_act_1924"))


class CitationFormTests(unittest.TestCase):
    def resolve(self, text):
        match = CITATION.search(text)
        return (match.group("number"), short_form(match.group("act"))) if match else None

    def test_the_forms_a_judgment_actually_prints(self):
        self.assertEqual(self.resolve("convicted under section 302 PPC"), ("302", "PPC"))
        self.assertEqual(self.resolve("bail u/s 497 Cr.P.C."), ("497", "CRPC"))
        self.assertEqual(self.resolve("section 12 of the Code of Civil Procedure"), ("12", "CPC"))
        # 489-F is heavily litigated and the hyphen is how it is printed.
        self.assertEqual(self.resolve("u/s 489-F P.P.C."), ("489-F", "PPC"))

    def test_criminal_procedure_is_not_read_as_civil_procedure(self):
        # "C.P.C." sits inside "Cr.P.C.", so the alternation must prefer the
        # longer form or every criminal citation becomes a civil one.
        self.assertEqual(self.resolve("u/s 497 Cr.P.C.")[1], "CRPC")

    def test_an_act_we_cannot_name_makes_no_match(self):
        self.assertIsNone(self.resolve("section 30 of the Colonisation Act")[1])


class CitationRemovalTests(unittest.TestCase):
    def test_every_mention_of_the_cited_section_is_struck_out(self):
        """A judgment paragraph commonly names the same section twice.

        Removing only the matched occurrence left the other behind, so an
        in-context query still answered itself: the exact-citation leg, which
        cannot work without a citation, scored 0.247 recall@1 on queries that
        were supposed to have none.
        """
        from rag.evaluate import _without, cited

        text = ("The appellant was convicted under section 302 PPC and the trial court, "
                "applying section 302 PPC, awarded the sentence of death.")
        stripped = _without(text, "302", "PPC")
        self.assertNotIn("302", stripped)
        self.assertIsNone(cited(stripped))
        self.assertIn("awarded the sentence of death", stripped)

    def test_a_different_section_in_the_same_passage_is_left_alone(self):
        from rag.evaluate import _without

        text = "convicted under section 302 PPC and granted bail under section 497 Cr.P.C."
        stripped = _without(text, "302", "PPC")
        self.assertIn("497", stripped)


class StatuteKeyTests(unittest.TestCase):
    def test_a_section_citation_becomes_a_key(self):
        self.assertEqual(statute_key("302 PPC"), "302|PPC")
        self.assertEqual(statute_key("489-F PPC"), "489-F|PPC")

    def test_a_roman_numeral_is_not_an_abbreviation(self):
        self.assertIsNone(statute_key("5 XIX"))
        self.assertIsNone(statute_key("section 3 of THE STAMP ACT, 1899"))


if __name__ == "__main__":
    unittest.main()
