"""What the Urdu vision stage is allowed to send out."""

import json

import fitz

from docintel.engines.urdu_vision import prepare_queue, run


def case(tmp_path, blocks):
    """A one-page PDF and a parse result whose RTL blocks are ``blocks``."""
    doc = fitz.open()
    doc.new_page()
    pdf = tmp_path / "case.pdf"
    doc.save(pdf)
    prediction = tmp_path / "case.json"
    page = {
        "blocks": [
            {
                "id": f"p1_b{index}",
                "bbox": [50, 50 + 60 * index, 300, 90 + 60 * index],
                "contains_rtl": True,
                "type": "text",
                "text": text,
                "markdown": text,
            }
            for index, text in enumerate(blocks)
        ]
    }
    prediction.write_text(json.dumps({"pages": [page]}, ensure_ascii=False), encoding="utf-8")
    return pdf, prediction


def queued(tmp_path, blocks, ceiling="third_party"):
    pdf, prediction = case(tmp_path, blocks)
    return json.loads(prepare_queue(pdf, prediction, tmp_path / "out", ceiling=ceiling).read_text(encoding="utf-8"))


def test_a_block_whose_own_text_is_sensitive_is_withheld_not_queued(tmp_path):
    manifest = queued(tmp_path, ["جو جائیداد", "شناختی کارڈ 35202-1234567-1"])
    assert [task["id"] for task in manifest["tasks"]] == ["p1_b0"]
    assert manifest["withheld"] == [{"id": "p1_b1", "reason": "sensitive_text"}]


def test_a_local_ceiling_queues_nothing_and_says_why(tmp_path):
    manifest = queued(tmp_path, ["جو جائیداد", "وصولی اپنے ہاتھ سے"], ceiling="local")
    assert manifest["tasks"] == []
    assert manifest["withheld"] == [
        {"id": "p1_b0", "reason": "policy_ceiling"},
        {"id": "p1_b1", "reason": "policy_ceiling"},
    ]


def test_a_third_party_ceiling_queues_clean_blocks(tmp_path):
    manifest = queued(tmp_path, ["جو جائیداد"], ceiling="third_party")
    assert [task["id"] for task in manifest["tasks"]] == ["p1_b0"]


def test_run_under_a_local_ceiling_sends_nothing_and_reports_the_withheld_blocks(tmp_path):
    pdf, prediction = case(tmp_path, ["جو جائیداد"])
    summary = run(pdf, prediction, tmp_path / "out", ceiling="local")
    assert summary["accepted"] == 0
    assert summary["withheld"] == [{"id": "p1_b0", "reason": "policy_ceiling"}]
