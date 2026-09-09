"""Linking a statute a judgment names to the statute the corpus holds.

Every name here is one a judgment in the corpus actually printed.
"""

import unittest
from pathlib import Path

from specter.statute_links import (
    FEDERAL,
    Statute,
    _words,
    jurisdiction,
    link,
)


def held(title, year=None, category="general_laws", slug=None):
    """One statute in the index, named the way the corpus names it."""
    return Statute(
        statute_id=slug or title.lower().replace(" ", "_"),
        title=title,
        year=year,
        jurisdiction=jurisdiction(title),
        category=category,
        path=Path("nowhere.pdf"),
        haystacks=(_words(title), _words(slug or "")),
    )


INDEX = [
    held("THE LIMITATION ACT, 1908", 1908, "civil_laws"),
    held("THE COMPANIES ORDINANCE, 1984", 1984),
    held("THE COMPANIES ACT, 2017", 2017),
    held("THE CONSTITUTION OF THE ISLAMIC REPUBLIC OF PAKISTAN", None),
    # The scraper glued an abbreviation and a status note onto the filename.
    held("THE PAKISTAN PENAL CODE", 1860, "criminal_laws",
         slug="pakistan penal code ppc1860 under review"),
    held("THE COMPANIES PROFITS WORKERS PARTICIPATION ACT, 1968", 1968),
]


class JurisdictionTests(unittest.TestCase):
    """The corpus is the federal code; a judgment applies provincial law too."""

    def test_a_statute_states_its_own_legislature(self):
        for name, expected in (
                ("Punjab Pre-emption Act, 1991", "Punjab"),
                ("Sindh Tenancy Act", "Sindh"),
                ("Balochistan Local Government Act", "Balochistan"),
                ("West Pakistan Family Court Act, 1964", "West Pakistan"),
                ("Constitution of India", "India"),
                ("Limitation Act, 1908", FEDERAL)):
            self.assertEqual(jurisdiction(name), expected, name)

    def test_west_pakistan_is_read_before_its_successors(self):
        # These acts survive as provincial law but are not Punjab's own.
        self.assertEqual(jurisdiction("West Pakistan Urban Rent Restriction Ordinance"),
                         "West Pakistan")

    def test_the_government_of_india_act_is_not_filed_under_india(self):
        # It was Pakistan's own interim constitution; "Indian" is the adjective
        # that marks a foreign statute, not the word "India" anywhere.
        self.assertEqual(jurisdiction("Government of India Act, 1935"), FEDERAL)
        self.assertEqual(jurisdiction("Indian Penal Code"), "India")


class MatchTests(unittest.TestCase):
    def test_a_name_is_found_through_the_scrapers_noise(self):
        found = link(INDEX, "Pakistan Penal Code, 1860")
        self.assertTrue(found["matched"])
        self.assertEqual(found["statute_title"], "THE PAKISTAN PENAL CODE")

    def test_the_words_must_be_a_run_not_a_subsequence(self):
        # "companies act" appears in order inside "Companies Profits Workers
        # Participation Act" and is not that statute.
        found = link(INDEX, "Companies Act, 2017")
        self.assertEqual(found["statute_title"], "THE COMPANIES ACT, 2017")

    def test_the_missing_the_does_not_break_the_match(self):
        found = link(INDEX, "Constitution of Islamic Republic of Pakistan, 1973")
        self.assertTrue(found["matched"])


class AmendmentTests(unittest.TestCase):
    """A year in the title is part of the statute's identity."""

    def test_a_repealed_act_is_not_the_act_that_replaced_it(self):
        # The Companies Act, 2017 repealed the 1984 Ordinance.  Without the
        # year rule the index answered "Companies Act, 1913" with the 2017 Act.
        self.assertFalse(link(INDEX, "Companies Act, 1913")["matched"])

    def test_each_companies_enactment_resolves_to_itself(self):
        self.assertEqual(link(INDEX, "Companies Ordinance, 1984")["statute_id"],
                         "the_companies_ordinance,_1984")
        self.assertEqual(link(INDEX, "Companies Act, 2017")["statute_id"],
                         "the_companies_act,_2017")

    def test_a_name_without_a_year_still_matches(self):
        self.assertTrue(link(INDEX, "Limitation Act")["matched"])


class NotHeldTests(unittest.TestCase):
    """Why a name did not match is as useful as the match itself."""

    def test_a_provincial_statute_is_a_gap_not_a_miss(self):
        found = link(INDEX, "Punjab Pre-emption Act, 1991")
        self.assertFalse(found["matched"])
        self.assertEqual(found["reason"], "not_held_for_this_jurisdiction")
        self.assertEqual(found["jurisdiction"], "Punjab")

    def test_subordinate_legislation_is_not_in_the_code(self):
        # `pakistancode.gov.pk` publishes primary legislation; rules and policy
        # orders are made under an Act and are not in it.
        for name in ("Police Rules, 1934", "Import Policy Order, 2016",
                     "Pakistan Prison Rules"):
            self.assertEqual(link(INDEX, name)["reason"],
                             "subordinate_legislation_not_in_the_code", name)

    def test_a_federal_act_we_should_hold_is_reported_as_such(self):
        self.assertEqual(link(INDEX, "Customs Act, 1969")["reason"],
                         "not_found_in_federal_code")

    def test_no_statute_is_invented_for_an_unmatched_name(self):
        self.assertNotIn("statute_id", link(INDEX, "Punjab Forensic Science Agency Act, 2007"))


if __name__ == "__main__":
    unittest.main()
