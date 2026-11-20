"""Understanding a judgment with a language model that labels and quotes but never writes the record."""

from docintel.understand.judgment import (
    TAGS,
    analyse_judgment,
    chunk_tree,
    extract_metadata,
    paragraphs,
    summarise,
    tag_paragraphs,
)

TEXTS = [
    ("heading", "IN THE LAHORE HIGH COURT, LAHORE"),
    ("text", "W.P. No. 12345 of 2023. Ali Khan versus Province of Punjab. Date of hearing: 12-03-2024."),
    ("text", "Mr. Justice Ahmad Raza: The petitioner, Ali Khan, a retired clerk, seeks release of his pension of Rs. 45,000 per month."),
    ("text", "The respondent argues that the petitioner's service record was incomplete and the pension was rightly withheld."),
    ("text", "Section 12 of the Civil Servants Act, 1973 entitles every retiring servant to a pension without condition."),
    ("text", "For these reasons the petition is allowed and the respondent shall pay the pension within 30 days."),
]


def document():
    blocks = [{"id": f"p1_b{i}", "type": kind, "text": text, "bbox": [0, 10 * i, 100, 10 * i + 8], "reading_order": i, "decorative": False}
              for i, (kind, text) in enumerate(TEXTS)]
    blocks += [{"id": "p1_h", "type": "header", "text": "page 1", "bbox": [0, 0, 1, 1], "reading_order": 99, "decorative": True}]
    return {"document": {"metadata": {"case_number": "W.P. No. 12345 of 2023"}},
            "pages": [{"page_number": 1, "blocks": blocks}]}


class Brain:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def json(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_paragraphs_are_the_body_blocks_in_order_without_headers_and_footers():
    paras = paragraphs(document())
    assert [p.id for p in paras] == [f"p1_b{i}" for i in range(6)]


def test_a_field_is_confirmed_only_when_its_quote_is_on_the_page_and_holds_the_value():
    answer = {
        "court": {"value": "LAHORE HIGH COURT", "quote": "IN THE LAHORE HIGH COURT, LAHORE"},
        "case_number": {"value": "W.P. No. 12345 of 2023", "quote": "W.P. No. 12345 of 2023"},
        "decision_date": {"value": "12 March 2024", "quote": "Date of hearing: 12-03-2024"},  # value is not in the quote
        "judges": [{"value": "Ahmad Raza", "quote": "Mr. Justice Ahmad Raza"}],
        "petitioner": {"value": "Ali Khan", "quote": "Ali Khan, a retired judge"},  # quote is not on the page
        "respondent": None,
        "outcome": {"value": "petition is allowed", "quote": "the petition is allowed"},
    }
    meta = extract_metadata(Brain(answer), paragraphs(document()), rule_case_number="W.P. No. 12345 of 2023")
    assert meta["court"]["status"] == "confirmed" and meta["court"]["page"] == 1 and meta["court"]["bbox"]
    assert meta["case_number"]["status"] == "confirmed" and meta["case_number"]["agrees_with_rules"] is True
    assert meta["decision_date"]["status"] == "rejected" and "value_not_in_quote" in meta["decision_date"]["why"]
    assert meta["petitioner"]["status"] == "rejected" and "quote_not_found" in meta["petitioner"]["why"]
    assert meta["respondent"]["status"] == "not_found"
    assert [j["status"] for j in meta["judges"]] == ["confirmed"] and meta["outcome"]["status"] == "confirmed"


def test_an_unusable_metadata_answer_confirms_nothing():
    meta = extract_metadata(Brain(None), paragraphs(document()))
    assert all(meta[field]["status"] == "not_found" for field in ("court", "case_number", "petitioner"))


def test_each_paragraph_gets_one_tag_from_the_closed_list_and_others_stay_other():
    paras = paragraphs(document())
    answer = {"tags": [{"id": "p1_b0", "tag": "heading"}, {"id": "p1_b3", "tag": "respondent_argument"},
                       {"id": "p1_b4", "tag": "law_cited"}, {"id": "p1_b5", "tag": "poem"}, {"id": "nope", "tag": "facts"}]}
    tags = tag_paragraphs(Brain(answer), paras)
    assert tags["p1_b0"] == "heading" and tags["p1_b3"] == "respondent_argument" and tags["p1_b4"] == "law_cited"
    assert tags["p1_b5"] == "other" and tags["p1_b1"] == "other"  # an invalid tag and an unlabelled paragraph
    assert set(tags.values()) <= set(TAGS)


def test_long_documents_are_tagged_in_windows():
    many = [{"id": f"b{i}", "type": "text", "text": f"Paragraph {i}.", "bbox": [0, i, 1, i + 1], "reading_order": i, "decorative": False}
            for i in range(70)]
    paras = paragraphs({"pages": [{"page_number": 1, "blocks": many}]})
    brain = Brain({"tags": []})
    tag_paragraphs(brain, paras)
    assert len(brain.prompts) == 3


def test_a_summary_sentence_stays_only_if_it_cites_real_paragraphs_that_support_its_facts():
    paras = paragraphs(document())
    tags = {"p1_b2": "facts", "p1_b5": "holding"}
    answer = {"sentences": [
        {"text": "Ali Khan seeks release of his pension of Rs. 45,000 per month.", "cites": ["p1_b2"]},
        {"text": "The court awarded Rs. 90,000 in damages.", "cites": ["p1_b5"]},  # an amount that is not in the cited text
        {"text": "The petition is allowed.", "cites": ["p9_b9"]},  # a paragraph that does not exist
        {"text": "The petition is allowed.", "cites": []},  # no citation
    ]}
    summary = summarise(Brain(answer), paras, tags)
    assert [s["text"] for s in summary["sentences"]] == ["Ali Khan seeks release of his pension of Rs. 45,000 per month."]
    assert {r["why"] for r in summary["rejected"]} == {"unsupported_figure_or_name", "unknown_paragraph", "no_citation"}


def test_the_chunk_tree_groups_runs_of_one_tag_under_a_section_with_its_paragraphs_as_children():
    paras = paragraphs(document())
    tags = {"p1_b0": "heading", "p1_b1": "heading", "p1_b2": "facts", "p1_b3": "respondent_argument",
            "p1_b4": "law_cited", "p1_b5": "holding"}
    tree = chunk_tree(paras, tags)
    assert [(s["tag"], s["paragraph_ids"]) for s in tree["sections"]] == [
        ("heading", ["p1_b0", "p1_b1"]), ("facts", ["p1_b2"]), ("respondent_argument", ["p1_b3"]),
        ("law_cited", ["p1_b4"]), ("holding", ["p1_b5"]),
    ]
    assert tree["sections"][1]["text"].startswith("Mr. Justice Ahmad Raza")
    assert {p["id"]: p["section"] for p in tree["paragraphs"]}["p1_b3"] == "s3"


def test_the_whole_analysis_keeps_every_paragraphs_text_unchanged_and_reports_what_it_rejected():
    brain = Brain(
        {"court": {"value": "LAHORE HIGH COURT", "quote": "LAHORE HIGH COURT"}, "petitioner": {"value": "Zed", "quote": "Zed"}},
        {"tags": [{"id": "p1_b5", "tag": "holding"}]},
        {"sentences": [{"text": "The petition is allowed.", "cites": ["p1_b5"]}]},
    )
    result = analyse_judgment(document(), brain)
    assert result["metadata"]["court"]["status"] == "confirmed" and result["metadata"]["petitioner"]["status"] == "rejected"
    assert result["summary"]["sentences"][0]["cites"] == ["p1_b5"]
    assert [p["text"] for p in result["paragraphs"]] == [text for _, text in TEXTS]
    assert result["needs_review"] is True
