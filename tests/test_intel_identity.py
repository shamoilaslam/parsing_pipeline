"""Identity: evidence quotes, the read-only guard, reporter headers and judgment keys."""

from pathlib import Path

import pytest

from intel import identity
from intel.corpus import OutputInsideInput, check_output
from intel.evidence import locate
from rag.normalise import flatten


def _payload(*texts):
    return {"document": {}, "pages": [{"page_number": 1, "blocks": [
        {"id": f"p1_b{i}", "type": "text", "text": t, "bbox": [0, i * 10, 100, i * 10 + 9]} for i, t in enumerate(texts)]}]}


def test_locate_finds_a_quote_across_blocks_and_maps_it_to_boxes():
    text = flatten(_payload("the appeal is", "dismissed with costs"), "d1", "LHC")
    [found] = locate(text, "is dismissed")
    assert found.page == 1 and found.block_ids == ["p1_b0", "p1_b1"] and len(found.bboxes) == 2


def test_locate_rejects_words_not_on_the_page():
    text = flatten(_payload("the appeal is dismissed"), "d1", "LHC")
    assert locate(text, "the appeal is allowed") == []


def test_output_inside_an_input_is_refused(tmp_path):
    corpus = tmp_path / "parsed_LHC"
    corpus.mkdir()
    with pytest.raises(OutputInsideInput):
        check_output(corpus / "intel", [corpus])
    assert check_output(tmp_path / "out", [corpus])


def test_reporter_header_is_found_on_a_reporter_print():
    found = identity.reporter_header(_payload("PLJ 2016 Cr.C. (Lahore) 519", "[Multan Bench Multan]"), "d1")
    assert found and found[0]["id"] == "PLJ 2016 Cr.C. 519" and found[1].block_ids == ["p1_b0"]


def test_a_citation_inside_an_opening_sentence_is_not_a_header():
    payload = _payload("This appeal relies on 2008 SCMR 598 and the law laid down in it at length.")
    assert identity.reporter_header(payload, "d1") is None


def test_a_neutral_citation_is_not_a_reporter_header():
    assert identity.reporter_header(_payload("2013 LHC 1026"), "d1") is None


def _doc(stem, document_id, labels, metadata, header=None):
    return {"corpus": "SC", "stem": stem, "document_id": document_id, "id_basis": "pdf",
            "source_file": f"D:/x/{stem}.pdf", "parsed_json": "", "document_kind": "judgment", "page_count": 1,
            "metadata": metadata, "provenance": {}, "labels": labels, "header": header}


def test_one_decision_filed_under_two_judges_is_one_judgment():
    labels = {"court_id": "supreme_court_pakistan", "case_type": "Crl.A", "case_number": "599",
              "year": "2020", "decision_date": "28-01-2021"}
    result = identity.build([_doc("a", "h1", labels, {}), _doc("b", "h2", labels, {})])
    [judgment] = result["judgments"]
    assert judgment["key"] == "supreme_court_pakistan/CRL.A/599/2020/2021-01-28"
    assert sorted(judgment["document_ids"]) == ["h1", "h2"]


def test_identical_bytes_are_one_document():
    labels = {"court_id": "lahore_high_court", "neutral_citation": "2013 LHC 1104"}
    result = identity.build([_doc("2013LHC1104", "h", labels, {}), _doc("2013LHC1104 (1)", "h", labels, {})])
    [document] = result["documents"]
    assert document["stems"] == ["2013LHC1104", "2013LHC1104 (1)"]
    assert result["report"]["duplicate_files"] == 1


def test_case_number_aliases_are_scoped_to_the_court():
    labels = {"court_id": "lahore_high_court", "neutral_citation": "2013 LHC 1"}
    result = identity.build([_doc("x", "h", labels, {"case_number": "W.P. No. 5 of 2013"})])
    assert "lahore_high_court:WP/5/2013" in {a["alias"] for a in result["aliases"]}


def test_a_document_without_a_court_identifier_keys_on_its_hash():
    result = identity.build([_doc("x", "abc", {}, {})])
    assert result["judgments"][0]["key"] == "document/abc"
