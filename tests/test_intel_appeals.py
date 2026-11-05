"""Appeal recitals: read the court, case and date; link only when case and date both match."""

from intel import appeals

SC = "supreme_court_pakistan"


def test_a_recital_names_court_case_and_date():
    [r] = appeals.recitals("(Against the impugned judgment dated 24.03.2022 passed by the Lahore High Court, "
                           "Lahore in W.P. No. 75382/2021) Ishfaq Ahmed … Petitioner Versus", SC)
    assert (r["court_id"], r["dates"], r["cases"]) == ("lahore_high_court", ["2022-03-24"], ["WP/75382/2021"])


def test_this_court_is_the_appellate_court_itself():
    [r] = appeals.recitals("Against the order dated 11.06.2024 passed by this Court in CPLA No. 304/2022) X", SC)
    assert r["court_id"] == SC and r["cases"] == ["C.P./304/2022"]


def test_a_recital_without_a_case_number_is_not_an_edge():
    assert appeals.recitals("against the judgments delivered in writ petitions, intra court appeals were not "
                            "maintainable dated 12.01.2020", SC) == []


def _build(decided):
    reads = [{"corpus": "SC", "stem": "s", "court_id": SC, "recitals": [{
        "court_id": "lahore_high_court", "dates": ["2022-03-24"], "cases": ["WP/75382/2021"],
        "quote": "q", "page": 1, "bboxes": [], "block_ids": []}]}]
    docs = [{"corpus": "SC", "stems": ["s"], "document_id": "ds", "judgment_id": "js"}]
    aliases = [{"alias_type": "case_number", "alias": "lahore_high_court:WP/75382/2021", "judgment_id": "jl"}]
    judgments = [{"judgment_id": "jl", "court_id": "lahore_high_court", "decision_date": decided},
                 {"judgment_id": "js", "court_id": SC, "decision_date": "2023-01-01"}]
    return appeals.build(reads, docs, aliases, judgments)["edges"][0]


def test_case_and_date_both_matching_resolves():
    edge = _build("2022-03-24")
    assert edge["to_judgment"] == "jl" and edge["state"] == "resolved"


def test_the_same_case_on_another_date_is_a_candidate_not_a_link():
    edge = _build("2021-11-02")
    assert edge["to_judgment"] is None and edge["state"] == "case_held_date_differs"
    assert edge["candidates"] == [{"judgment_id": "jl", "decision_date": "2021-11-02"}]
