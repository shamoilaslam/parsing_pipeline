"""A check over the whole facts output: is anything missing, failed, unreadable or out of spec?"""

import json

from docintel.facts_report import problems, summarise


def block(**extra):
    return {"id": "b0", "order": 0, "type": "paragraph", "text": "x", "bbox": [0.1, 0.1, 0.5, 0.1], "confidence": 0.9, "language": "en", **extra}


def page(number=1, **extra):
    base = {"page": number, "status": "ok", "source": "text_layer", "width": 600, "height": 800, "unit": "pt", "rotation": 0,
            "image": f"pages/{number:04d}.png", "language": {"en": 1.0}, "quality": {"score": 0.9, "ocr_confidence": None, "flags": []},
            "text": "x", "blocks": [block()]}
    return {**base, **extra}


def write(tmp_path, name, pages, **file_extra):
    folder = tmp_path / name
    (folder / "pages").mkdir(parents=True)
    for item in pages:
        if item.get("image"):
            (folder / item["image"]).write_bytes(b"png")
    document = {"file": {"name": f"{name}.pdf", "sha256": "ab", "mime": "application/pdf", "pages": len(pages), "parser": "docintel",
                         "parser_version": "0.1.0", "seconds": 1.0, **file_extra},
                "metadata": {"email": None, "docx": None}, "pages": pages}
    (folder / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")


def test_a_clean_output_has_no_problems_and_is_counted(tmp_path):
    write(tmp_path, "a", [page(1), page(2)])
    summary = summarise(tmp_path)
    assert (summary["files"], summary["pages"], summary["sources"]) == (1, 2, {"text_layer": 2})
    assert problems(summary) == []


def test_failed_pages_unread_ink_low_quality_and_missing_images_are_each_reported(tmp_path):
    bad = [
        page(1, status="failed", reason="CUDA out of memory", blocks=[], text=""),
        page(2, blocks=[block(type="illegible", text="")], quality={"score": 0.9, "ocr_confidence": None, "flags": ["unaccounted_ink"]}),
        page(3, quality={"score": 0.2, "ocr_confidence": 0.2, "flags": ["urdu_unread"]}),
        page(4, image="pages/0004.png"),
    ]
    write(tmp_path, "a", bad)
    (tmp_path / "a" / "pages" / "0004.png").unlink()
    found = problems(summarise(tmp_path))
    text = " | ".join(found)
    assert "a p1" in text and "CUDA out of memory" in text
    assert "illegible" in text and "a p2" in text
    assert "quality" in text and "a p3" in text
    assert "image" in text and "a p4" in text


def test_a_page_missing_a_required_field_is_a_spec_problem(tmp_path):
    broken = page(1)
    del broken["quality"]
    write(tmp_path, "a", [broken])
    assert any("quality" in item and "spec" in item for item in problems(summarise(tmp_path)))


def test_an_email_file_without_its_headers_is_a_problem(tmp_path):
    write(tmp_path, "m", [page(1)], mime="message/rfc822")
    assert any("email" in item for item in problems(summarise(tmp_path)))
