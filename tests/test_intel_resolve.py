"""Resolution against held aliases: exact, never guessed."""

from intel import resolve


def _citation(alias, reporter="SCMR", jurisdiction="PK"):
    return {"id": alias, "reporter": reporter, "jurisdiction": jurisdiction, "year": 2008,
            "mentions": [{"page": 2, "bbox": [0, 0, 1, 1], "block_id": "p2_b1", "text": alias}]}


DOCS = [{"corpus": "LHC", "stems": ["a"], "document_id": "da", "judgment_id": "ja"},
        {"corpus": "LHC", "stems": ["b"], "document_id": "db", "judgment_id": "jb"}]


def test_a_held_reporter_alias_resolves_and_an_unheld_one_does_not():
    aliases = [{"alias_type": "reported", "alias": "2008 SCMR 598", "judgment_id": "jb"}]
    reads = [{"corpus": "LHC", "stem": "a", "names": {}, "parallel": [], "citations": [_citation("2008 SCMR 598"), _citation("2010 SCMR 1")]}]
    result = resolve.build(reads, DOCS, aliases)
    by_alias = {e["alias"]: e for e in result["edges"]}
    assert by_alias["2008 SCMR 598"]["to_judgment"] == "jb"
    assert by_alias["2010 SCMR 1"]["to_judgment"] is None
    assert result["report"]["pakistani_reporter_edges"] == {"total": 2, "resolved": 1}


def test_an_alias_on_two_judgments_is_ambiguous_not_resolved():
    aliases = [{"alias_type": "reported", "alias": "2008 SCMR 598", "judgment_id": j} for j in ("jb", "jc")]
    result = resolve.build([{"corpus": "LHC", "stem": "a", "names": {}, "parallel": [], "citations": [_citation("2008 SCMR 598")]}], DOCS, aliases)
    [edge] = result["edges"]
    assert edge["to_judgment"] is None and edge["ambiguous"] == ["jb", "jc"]


def test_a_judgment_citing_itself_is_not_an_edge_to_itself():
    aliases = [{"alias_type": "neutral", "alias": "2013 LHC 1", "judgment_id": "ja"}]
    result = resolve.build([{"corpus": "LHC", "stem": "a", "names": {}, "parallel": [], "citations": [_citation("2013 LHC 1", "LHC")]}], DOCS, aliases)
    assert result["edges"][0]["to_judgment"] is None


JUDGMENTS = [{"judgment_id": "jb", "key": "supreme_court_pakistan/C.A./1/2007/2007-05-01", "court_id": "supreme_court_pakistan"},
             {"judgment_id": "jc", "key": "supreme_court_pakistan/C.A./2/2007/2007-06-01", "court_id": "supreme_court_pakistan"}]


def _reads(names, parallel=(), cites=("2008 SCMR 598",)):
    citing = {"corpus": "LHC", "stem": "a", "names": names, "parallel": list(parallel),
              "citations": [_citation(c) for c in cites], "petitioner": "X", "respondent": "Y"}
    held = [{"corpus": "LHC", "stem": "b", "names": {}, "parallel": [], "citations": [],
             "petitioner": "Khuda Bakhsh", "respondent": "Khushi Muhammad and 3 others"},
            {"corpus": "LHC", "stem": "c", "names": {}, "parallel": [], "citations": [],
             "petitioner": "Muhammad Akram", "respondent": "The State"}]
    return [citing, *held]


DOCS3 = DOCS + [{"corpus": "LHC", "stems": ["c"], "document_id": "dc", "judgment_id": "jc"}]


def test_a_printed_case_name_links_to_the_one_matching_judgment():
    names = {"2008 SCMR 598": {"petitioner": "Khuda Bakhsh", "respondent": "Khushi Muhammad and others", "quote": "q"}}
    [edge] = resolve.build(_reads(names), DOCS3, [], JUDGMENTS)["edges"]
    assert edge["to_judgment"] == "jb" and edge["rung"] == "case_name" and edge["method"] == "linkage"


def test_a_name_carried_only_by_the_state_does_not_link():
    names = {"2008 SCMR 598": {"petitioner": "Someone Else", "respondent": "The State", "quote": "q"}}
    [edge] = resolve.build(_reads(names), DOCS3, [], JUDGMENTS)["edges"]
    assert edge["to_judgment"] is None


def test_a_parallel_citation_resolves_through_its_partner():
    aliases = [{"alias_type": "reported", "alias": "1993 PTD 1108", "judgment_id": "jb"}]
    reads = _reads({}, parallel=[["1993 PTD 1108", "1993 SCMR 1108"]], cites=("1993 SCMR 1108",))
    [edge] = resolve.build(reads, DOCS3, aliases, JUDGMENTS)["edges"]
    assert edge["to_judgment"] == "jb" and edge["rung"] == "parallel"
