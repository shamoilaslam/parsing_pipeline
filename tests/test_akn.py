"""Akoma Ntoso-style JSON for judgments and acts: identity, header, body, each with its source blocks.

The shape follows Akoma Ntoso's own: meta/identification with FRBR work, expression and
manifestation, then a document-type body.  It is JSON, not XML, and an internal schema.
"""

from docintel.akn.build import to_akn

SHA = "ab" * 32


def judgment(blocks=(), **metadata):
    fields = {
        "court_id": "lahore_high_court",
        "neutral_citation": "2024 LHC 1690",
        "decision_date_iso": "2024-05-13",
        "case_number": "Civil Revision No.21823 of 2024",
    }
    fields.update(metadata)
    return {
        "schema_version": "specter.v1",
        "document": {
            "id": SHA,
            "source_name": "2024LHC1690.pdf",
            "document_kind": "judgment",
            "metadata": fields,
        },
        "pages": [{"page_number": 1, "blocks": list(blocks)}],
    }


def test_a_judgments_identity_carries_its_ecli_style_id_date_and_court():
    akn = to_akn(judgment())
    work = akn["meta"]["identification"]["FRBRWork"]
    assert akn["type"] == "judgment"
    assert work["this"] == "/akn/pk/judgment/lhc/2024-05-13/1690"
    assert work["ecli"] == "ECLI:PK:LHC:2024:1690"
    assert work["date"] == {"value": "2024-05-13", "name": "decision"}
    assert work["author"] == "#lahore_high_court"
    assert work["country"] == "pk"
    assert work["number"] == "Civil Revision No.21823 of 2024"
    expression = akn["meta"]["identification"]["FRBRExpression"]
    assert expression["this"] == "/akn/pk/judgment/lhc/2024-05-13/1690/eng@2024-05-13"
    assert expression["language"] == "eng"
    manifestation = akn["meta"]["identification"]["FRBRManifestation"]
    assert manifestation["this"] == "/akn/pk/judgment/lhc/2024-05-13/1690/eng@2024-05-13/main.json"
    assert (manifestation["sourceSha256"], manifestation["sourceName"]) == (SHA, "2024LHC1690.pdf")


def test_without_a_court_or_date_the_uri_falls_back_to_the_content_hash_and_no_ecli_is_invented():
    akn = to_akn(judgment(court_id=None, neutral_citation=None, decision_date_iso=None))
    identification = akn["meta"]["identification"]
    assert identification["FRBRWork"]["this"] == f"/akn/pk/judgment/{SHA[:16]}"
    assert identification["FRBRWork"]["ecli"] is None
    assert identification["FRBRWork"]["date"] is None
    assert identification["FRBRWork"]["author"] is None
    assert identification["FRBRExpression"]["this"] == f"/akn/pk/judgment/{SHA[:16]}/eng"


def test_the_header_names_the_court_parties_and_judges_through_references():
    akn = to_akn(
        judgment(
            petitioner="Muhammad Azam",
            respondent="Province of the Punjab",
            judges=["Shams Mehmood Mirza"],
        )
    )
    references = akn["meta"]["references"]
    assert references["TLCOrganization"] == [{"eId": "lahore_high_court", "showAs": "Lahore High Court"}]
    assert references["TLCPerson"] == [
        {"eId": "party_1", "showAs": "Muhammad Azam"},
        {"eId": "party_2", "showAs": "Province of the Punjab"},
        {"eId": "judge_1", "showAs": "Shams Mehmood Mirza"},
    ]
    header = akn["judgment"]["header"]
    assert header["court"] == {"refersTo": "#lahore_high_court", "showAs": "Lahore High Court"}
    assert header["docNumber"] == "Civil Revision No.21823 of 2024"
    assert header["docDate"] == "2024-05-13"
    assert header["parties"] == [
        {"role": "petitioner", "refersTo": "#party_1", "showAs": "Muhammad Azam"},
        {"role": "respondent", "refersTo": "#party_2", "showAs": "Province of the Punjab"},
    ]
    assert header["judges"] == [{"refersTo": "#judge_1", "showAs": "Shams Mehmood Mirza"}]


def block(block_id, kind, text, *, page_box=(50, 100, 500, 120), decorative=False):
    return {"id": block_id, "type": kind, "text": text, "bbox": list(page_box), "decorative": decorative}


def test_the_body_keeps_paragraphs_with_their_numbers_and_source_blocks_and_drops_page_furniture():
    blocks = [
        block("p1_b0", "header", "Form No: HCJD/C-121"),
        block("p1_b1", "heading", "JUDGMENT", page_box=(200, 150, 400, 170)),
        block("p1_b2", "text", "1. This suit is brought by the plaintiff bank.", page_box=(50, 200, 500, 240)),
        block("p1_b3", "text", "The defendants contest it.", page_box=(50, 250, 500, 270)),
        block("p1_b4", "footer", "Page 1 of 9"),
        block("p1_b5", "text", "stamp", decorative=True),
    ]
    body = to_akn(judgment(blocks))["judgment"]["judgmentBody"]
    assert body == [
        {
            "eId": "heading_1",
            "kind": "heading",
            "text": "JUDGMENT",
            "blocks": ["p1_b1"],
            "page": 1,
            "bbox": [200, 150, 400, 170],
        },
        {
            "eId": "para_1",
            "kind": "paragraph",
            "num": "1",
            "text": "1. This suit is brought by the plaintiff bank.",
            "blocks": ["p1_b2"],
            "page": 1,
            "bbox": [50, 200, 500, 240],
        },
        {
            "eId": "para_2",
            "kind": "paragraph",
            "num": None,
            "text": "The defendants contest it.",
            "blocks": ["p1_b3"],
            "page": 1,
            "bbox": [50, 250, 500, 270],
        },
    ]


def marker(kind, number, title, block_id):
    return {"kind": kind, "number": number, "title": title, "block_id": block_id, "page": 1}


def act(blocks, structure):
    return {
        "schema_version": "specter.v1",
        "document": {
            "id": SHA,
            "source_name": "5_pakistan_penal_code_ppc1860.pdf",
            "source_file": "5_pakistan_penal_code_ppc1860.pdf",
            "document_kind": "statute",
            "metadata": {
                "title": "THE PAKISTAN PENAL CODE, 1860",
                "act_number": "XLV",
                "act_number_value": 45,
                "act_year": "1860",
            },
            "structure": structure,
        },
        "pages": [{"page_number": 1, "blocks": blocks}],
    }


ACT_BLOCKS = [
    block(
        "b0", "text", "1. Title and extent of operation of the Code. This Act shall be called the Pakistan Penal Code."
    ),
    block("b1", "text", "2. Punishment of offences committed within Pakistan."),
]


def test_an_act_gets_an_eli_style_id_and_its_sections_with_their_source_blocks():
    akn = to_akn(
        act(ACT_BLOCKS, [marker("section", "1", "Title and extent", "b0"), marker("section", "2", "Punishment", "b1")])
    )
    work = akn["meta"]["identification"]["FRBRWork"]
    assert akn["type"] == "act"
    assert work["this"] == "/akn/pk/act/1860/45"
    assert work["eli"] == "eli/pk/act/1860/45"
    assert "ecli" not in work  # ECLI identifies case law only
    assert (work["name"], work["number"], work["country"]) == ("THE PAKISTAN PENAL CODE, 1860", "XLV", "pk")
    assert work["date"] == {"value": "1860", "name": "enactment"}
    sections = akn["act"]["body"]
    assert [(node["eId"], node["num"], node["kind"]) for node in sections] == [
        ("sec_1", "1", "section"),
        ("sec_2", "2", "section"),
    ]
    assert sections[0]["blocks"] == ["b0"]
    assert sections[0]["pages"] == [1, 1]
    assert sections[0]["heading"]
    assert sections[0]["text"].startswith("Title and extent")


def test_sections_nest_under_their_chapter_with_the_chapters_title():
    blocks = [
        block("b0", "heading", "CHAPTER I INTRODUCTION"),
        block(
            "b1",
            "text",
            "1. Title and extent of operation of the Code. This Act shall be called the Pakistan Penal Code.",
        ),
        block("b2", "heading", "CHAPTER II GENERAL EXPLANATIONS"),
        block("b3", "text", "2. Punishment of offences committed within Pakistan."),
    ]
    structure = [
        marker("chapter", "I", "INTRODUCTION", "b0"),
        marker("section", "1", "Title and extent", "b1"),
        marker("chapter", "II", "GENERAL EXPLANATIONS", "b2"),
        marker("section", "2", "Punishment", "b3"),
    ]
    body = to_akn(act(blocks, structure))["act"]["body"]
    assert [(node["eId"], node["kind"], node["num"], node["heading"]) for node in body] == [
        ("chp_I", "chapter", "I", "INTRODUCTION"),
        ("chp_II", "chapter", "II", "GENERAL EXPLANATIONS"),
    ]
    assert [child["eId"] for child in body[0]["children"]] == ["sec_1"]
    assert [child["eId"] for child in body[1]["children"]] == ["sec_2"]
