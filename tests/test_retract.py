"""A letter, contract or ledger is not a court document: it must not carry a court, a case number or a judgment label."""

import json

from docintel.akn.build import write_akn
from docintel.understand.retract import retract_non_court

COURT_FIELDS = {"court": "approved by the Islamabad High Court on 11 April", "court_id": "islamabad_high_court",
                "case_number": "W.P. 1 of 2020", "judges": ["A"], "decision_date_iso": "2008-04-11",
                "dates_found": ["11 April 2008"], "acts": ["Mineral Rules, 2002"]}


def parsed_folder(tmp_path, stem, first_page_text, kind="judgment"):
    document = {"id": "ab" * 32, "source_name": f"{stem}.pdf", "document_kind": kind, "metadata": dict(COURT_FIELDS),
                "metadata_provenance": {"court_id": {}, "dates_found": {}}}
    pages = [{"page_number": 1, "blocks": [{"id": "b0", "text": first_page_text, "bbox": [0, 0, 1, 1]}]}]
    (tmp_path / f"{stem}.json").write_text(json.dumps({"document": document, "pages": pages}), encoding="utf-8")
    (tmp_path / "metadata").mkdir(exist_ok=True)
    (tmp_path / "metadata" / f"{stem}.json").write_text(json.dumps(document), encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_non_court_document_loses_its_court_identity_and_takes_the_classifiers_kind(tmp_path):
    parsed_folder(tmp_path, "X09", "GRANT OF EXPLORATION LICENCE. Approved by the Islamabad High Court.")
    retract_non_court(tmp_path, {"X09": {"kind": "order"}, "X01": {"kind": "contract"}})
    parsed_folder(tmp_path, "X01", "JOINT VENTURE AGREEMENT")
    retract_non_court(tmp_path, {"X01": {"kind": "contract"}})
    for target in (tmp_path / "X01.json", tmp_path / "metadata" / "X01.json"):
        document = read(target).get("document", read(target))
        assert document["document_kind"] == "contract"
        assert all(document["metadata"][key] is None for key in ("court", "court_id", "case_number", "decision_date_iso"))
        assert document["metadata"]["judges"] == []
        assert document["metadata"]["dates_found"] == ["11 April 2008"]  # what the page says is kept
        assert "court_id" not in document["metadata_provenance"] and "dates_found" in document["metadata_provenance"]


def test_a_real_court_document_is_left_alone(tmp_path):
    parsed_folder(tmp_path, "J1", "IN THE LAHORE HIGH COURT, LAHORE JUDGMENT")
    before = (tmp_path / "J1.json").read_bytes()
    retract_non_court(tmp_path, {"J1": {"kind": "judgment"}})
    assert (tmp_path / "J1.json").read_bytes() == before


def test_an_unclassified_document_is_left_alone(tmp_path):
    parsed_folder(tmp_path, "U1", "???")
    before = (tmp_path / "U1.json").read_bytes()
    retract_non_court(tmp_path, {"U1": {"kind": "unknown"}})
    assert (tmp_path / "U1.json").read_bytes() == before


def test_a_licence_order_that_only_mentions_a_court_gets_no_akn_but_a_court_order_does(tmp_path):
    parsed_folder(tmp_path, "X09", "GRANT OF EXPLORATION LICENCE. The record was approved by the Islamabad High Court.")
    parsed_folder(tmp_path, "O1", "IN THE COURT OF ADDITIONAL SESSIONS JUDGE, LAHORE. ORDER SHEET")
    written = write_akn(tmp_path, {"X09": {"kind": "order"}, "O1": {"kind": "order"}})
    assert written == ["O1"]


def test_an_order_that_does_not_open_with_a_courts_name_is_retracted_but_keeps_its_kind(tmp_path):
    parsed_folder(tmp_path, "X09", "GRANT OF EXPLORATION LICENCE. The record was approved by the Islamabad High Court.")
    retract_non_court(tmp_path, {"X09": {"kind": "order"}})
    document = read(tmp_path / "X09.json")["document"]
    assert document["document_kind"] == "order" and document["metadata"]["court_id"] is None
