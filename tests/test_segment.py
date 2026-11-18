"""Splitting one PDF of many documents: a page that opens with a document's title starts a new document."""

from docintel.understand.segment import segment

LETTER_1 = "BHP MINERALS\nReference: BM/41\nDear Sir,\nWe refer to the proposed joint venture."
LETTER_2 = "The parties will continue in good faith. We look forward to your reply.\nYours faithfully, Director"
AGREEMENT_1 = "JOINT VENTURE AGREEMENT\nThis Agreement is made between the Parties hereinafter referred to as the Operator."
AGREEMENT_2 = "2. The Operator shall carry out the exploration programme set out in Schedule A of this Agreement."
MINUTES_1 = "MINUTES OF THE BOARD MEETING\nPresent: Mr A. RESOLVED that the novation be approved."
LEDGER_1 = "EXPLORATION EXPENDITURE LEDGER\nDate Particulars Debit Credit Balance"


def kinds(pages):
    return [(s.first_page, s.last_page, s.kind) for s in segment(pages)]


def test_pages_with_no_new_title_continue_the_document_before_them():
    assert kinds([LETTER_1, LETTER_2]) == [(1, 2, "letter")]


def test_a_page_that_opens_with_another_documents_title_starts_a_new_document():
    pages = [LETTER_1, LETTER_2, AGREEMENT_1, AGREEMENT_2, MINUTES_1, LEDGER_1]
    assert kinds(pages) == [(1, 2, "letter"), (3, 4, "contract"), (5, 5, "minutes"), (6, 6, "ledger")]


def test_a_title_of_the_kind_already_running_does_not_split_it():
    # A long judgment that quotes "judgment" at the top of a later page is still one judgment.
    assert kinds([AGREEMENT_1, AGREEMENT_2, AGREEMENT_1]) == [(1, 3, "contract")]


def test_a_document_with_an_unrecognised_start_is_unknown_until_a_page_names_it():
    assert kinds(["scribbled note", "more scribbling", AGREEMENT_1]) == [(1, 2, "unknown"), (3, 3, "contract")]


def test_a_parsed_folder_lists_the_documents_inside_each_file(tmp_path):
    import json

    from docintel.understand.segment import classify_folder

    pages = [{"blocks": [{"text": text}]} for text in (LETTER_1, LETTER_2, MINUTES_1)]
    (tmp_path / "bundle.json").write_text(json.dumps({"document": {}, "pages": pages}), encoding="utf-8")
    entry = classify_folder(tmp_path)["bundle"]
    assert entry["kind"] == "letter"  # the file is named by its first document
    assert [(d["first_page"], d["last_page"], d["kind"]) for d in entry["documents"]] == [(1, 2, "letter"), (3, 3, "minutes")]
