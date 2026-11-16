"""The language model labels and selects; grounding decides what survives."""

from docintel.understand.agent import understand

PAGES = [
    {"page_number": 1, "blocks": [
        {"id": "b0", "text": "BHP MINERALS Reference: BM/41", "bbox": [0, 0, 1, 1]},
        {"id": "b1", "text": "16 September 1993", "bbox": [0, 2, 1, 3]},
        {"id": "b2", "text": "Dear Sir, we refer to the proposed joint venture.", "bbox": [0, 4, 1, 5]}]},
    {"page_number": 2, "blocks": [{"id": "b3", "text": "Yours faithfully, Director", "bbox": [0, 0, 1, 1]}]},
    {"page_number": 3, "blocks": [{"id": "b4", "text": "MINUTES OF THE BOARD MEETING", "bbox": [0, 0, 1, 1]}]},
]


class Brain:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def json(self, prompt):
        self.prompts.append(prompt)
        return self.answer


GOOD = {"documents": [
    {"first_page": 1, "kind": "letter", "kind_quote": "Dear Sir", "date_quote": "16 September 1993",
     "parties": [{"quote": "BHP MINERALS", "role": "sender"}]},
    {"first_page": 3, "kind": "minutes", "kind_quote": "MINUTES OF THE BOARD MEETING", "date_quote": None, "parties": []},
]}


def test_documents_come_back_with_pages_kind_and_the_evidence_for_each_field():
    result = understand(PAGES, Brain(GOOD))
    letter, minutes = result["documents"]
    assert (letter["first_page"], letter["last_page"], letter["kind"]) == (1, 2, "letter")
    assert (minutes["first_page"], minutes["last_page"], minutes["kind"]) == (3, 3, "minutes")
    assert letter["date"] == {"text": "16 September 1993", "page": 1, "block_id": "b1", "bbox": [0, 2, 1, 3]}
    assert letter["parties"][0]["role"] == "sender"
    assert letter["needs_review"] is False


def test_the_model_sees_the_opening_of_every_page_and_the_closed_list_of_kinds():
    brain = Brain(GOOD)
    understand(PAGES, brain)
    assert "[PAGE 3]" in brain.prompts[0] and "MINUTES OF THE BOARD" in brain.prompts[0]
    assert "letter" in brain.prompts[0] and "ledger" in brain.prompts[0]


def test_a_quote_the_page_does_not_contain_is_dropped_and_recorded_not_kept():
    answer = {"documents": [{"first_page": 1, "kind": "letter", "kind_quote": "Dear Sir",
                             "date_quote": "4 July 2001", "parties": [{"quote": "Rio Tinto", "role": "sender"}]}]}
    [letter] = understand(PAGES, Brain(answer))["documents"]
    assert letter["date"] is None and letter["parties"] == []
    assert {item["field"] for item in letter["rejected"]} == {"date", "party"}


def test_a_kind_with_no_grounded_quote_is_unknown_and_needs_review():
    answer = {"documents": [{"first_page": 1, "kind": "affidavit", "kind_quote": "I solemnly affirm"}]}
    [document] = understand(PAGES, Brain(answer))["documents"]
    assert (document["kind"], document["needs_review"]) == ("unknown", True)


def test_a_kind_outside_the_closed_list_is_unknown():
    answer = {"documents": [{"first_page": 1, "kind": "love poem", "kind_quote": "Dear Sir"}]}
    assert understand(PAGES, Brain(answer))["documents"][0]["kind"] == "unknown"


def test_an_unusable_answer_falls_back_to_one_unknown_document_for_the_whole_file():
    for bad in ({}, {"documents": []}, {"documents": [{"first_page": 2, "kind": "letter"}]}, None):
        [document] = understand(PAGES, Brain(bad))["documents"]
        assert (document["first_page"], document["last_page"], document["kind"]) == (1, 3, "unknown")
        assert document["needs_review"] is True
