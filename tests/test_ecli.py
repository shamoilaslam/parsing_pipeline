"""ECLI-style identifiers for Pakistani caselaw: ECLI:PK:<court>:<year>:<ordinal>.

ECLI is a European scheme and Pakistan has no registered one, so these ids are
ECLI-style: same grammar, minted by us from what the document itself states.
"""

from docintel.akn.ecli import ecli


def test_a_neutral_citation_becomes_the_ordinal():
    metadata = {"court_id": "lahore_high_court", "neutral_citation": "2024 LHC 1690"}
    assert ecli(metadata) == "ECLI:PK:LHC:2024:1690"


def test_without_a_neutral_citation_the_case_number_and_decision_year_make_the_id():
    supreme = {
        "court_id": "supreme_court_pakistan",
        "case_number": "CIVIL PETITION NO.1017 OF 2022",
        "decision_date_iso": "2024-11-27",
    }
    assert ecli(supreme) == "ECLI:PK:SC:2024:CP1017.2022"
    lahore = {"court_id": "lahore_high_court", "case_number": "W.P No.10464 of 2021", "decision_date_iso": "2023-11-15"}
    assert ecli(lahore) == "ECLI:PK:LHC:2023:WP10464.2021"


def test_a_neutral_citation_wins_over_the_case_number():
    metadata = {
        "court_id": "lahore_high_court",
        "neutral_citation": "2024 LHC 1690",
        "case_number": "W.P No.1 of 2020",
        "decision_date_iso": "2024-05-13",
    }
    assert ecli(metadata) == "ECLI:PK:LHC:2024:1690"


def test_an_id_is_never_invented():
    assert ecli({"court_id": "unknown_court", "neutral_citation": "2024 LHC 1690"}) is None
    assert ecli({"court_id": "lahore_high_court", "case_number": "W.P No.1 of 2020"}) is None  # no decision year
    assert ecli({"court_id": "lahore_high_court", "decision_date_iso": "2024-05-13"}) is None  # no case identity
    assert ecli({}) is None
