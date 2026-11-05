"""Case-name linkage: blocking by reporter, generic parties, and the margin rule."""

from intel import linkage
from intel.context import case_name, citation_spans, parallel_groups


def _cand(jid, pet, resp, court=linkage.SC, year=2007):
    return linkage.Candidate(jid, court, year, linkage.normalise_party(pet), linkage.normalise_party(resp))


def test_normalise_drops_the_noise_around_a_party():
    assert linkage.normalise_party("M/s Chappal Builders and 3 others") == "chappal builders"
    assert linkage.normalise_party("Federation of Pakistan through Secretary M/o Law") == "federation pakistan"
    assert linkage.is_generic(linkage.normalise_party("The Federation of Pakistan"))


def test_a_subset_name_does_not_match_a_longer_cited_name():
    held = _cand("j1", "Khan Muhammad", "Zarai Taraqiati Bank Limited")
    value, _ = linkage.score(linkage.normalise_party("Muhammad Ajmal Khan"),
                             linkage.normalise_party("Zarai Taraqiati Bank Limited"), held)
    assert value < linkage.THRESHOLD


def test_a_generic_side_must_still_agree():
    held = _cand("j1", "Muhammad Aslam", "Water and Power Development Authority")
    value, _ = linkage.score(linkage.normalise_party("Muhammad Aslam"), linkage.normalise_party("The State"), held)
    assert value < linkage.THRESHOLD


def test_scmr_reports_only_the_supreme_court_and_pld_follows_its_printed_court():
    assert linkage.candidate_courts("SCMR", None) == {linkage.SC}
    assert linkage.candidate_courts("PLD", "Lahore") == {linkage.LHC}
    assert linkage.candidate_courts("PLD", "Karachi") == set()


def test_two_equally_good_candidates_link_to_neither():
    index = {(linkage.SC, 2007): [_cand("j1", "Ali Khan", "Bashir Ahmad"), _cand("j2", "Ali Khan", "Bashir Ahmad")]}
    assert linkage.link({"petitioner": "Ali Khan", "respondent": "Bashir Ahmad"}, "SCMR", None, 2008, index) is None


def test_a_report_year_before_the_decision_finds_nothing():
    index = {(linkage.SC, 2007): [_cand("j1", "Ali Khan", "Bashir Ahmad")]}
    assert linkage.link({"petitioner": "Ali Khan", "respondent": "Bashir Ahmad"}, "SCMR", None, 2005, index) is None
    assert linkage.link({"petitioner": "Ali Khan", "respondent": "Bashir Ahmad"}, "SCMR", None, 2008, index)["judgment_id"] == "j1"


def test_case_name_and_parallel_reading():
    text = "Relied on Income- Tax Officer and another v. M/s Chappal Builders (1993 P T D 1108 = 1993 S C M R 1108) and"
    start = citation_spans(text)[0][0]
    assert case_name(text, start) == {"petitioner": "Income- Tax Officer and another", "respondent": "M/s Chappal Builders"}
    assert parallel_groups(text) == [["1993 PTD 1108", "1993 SCMR 1108"]]


def test_a_string_cite_is_not_parallel():
    assert parallel_groups("Reference can be made to PLD 1970 SC 39; 1972 SCMR 47 and 1981 SCMR 758.") == []


def test_a_generic_office_matches_whatever_branch_follows():
    held = _cand("j1", "Commissioner Inland Revenue, Zone-II, RTO Lahore", "Zahid Jee Fabrics (Pvt) Ltd")
    value, _ = linkage.score(linkage.normalise_party("Commissioner Inland Revenue"),
                             linkage.normalise_party("M/s. Zahid Jee Fabrics (Pvt.) Ltd."), held)
    assert value >= linkage.THRESHOLD


def test_the_same_office_against_a_different_litigant_does_not_match():
    held = _cand("j1", "Commissioner Inland Revenue, Zone-II", "Zahid Jee Fabrics")
    value, _ = linkage.score(linkage.normalise_party("Commissioner Inland Revenue"),
                             linkage.normalise_party("Naila Kareem"), held)
    assert value < linkage.THRESHOLD


def test_a_one_word_office_is_generic_only_as_the_whole_name():
    assert linkage.is_generic(linkage.normalise_party("The State"))
    assert not linkage.is_generic(linkage.normalise_party("State Life Insurance Corporation"))
