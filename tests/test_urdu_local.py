"""A local vision model for Urdu that is not trusted: a reading stands only if two looks at the crop agree."""

import json

import cv2
import fitz
import numpy as np

from docintel.engines.urdu_local import LocalVision, plausible_urdu
from docintel.engines.urdu_vision import run

URDU = "یہ ایک جملہ ہے جو عدالت کے فیصلے سے لیا گیا"


def crop(tmp_path, name="c1.png", size=(400, 80)):
    path = tmp_path / name
    cv2.imwrite(str(path), np.full((size[1], size[0], 3), 255, np.uint8))
    return {"id": name[:-4], "image": str(path), "status": "pending"}


def test_two_agreeing_readings_are_accepted_with_their_agreement_as_confidence(tmp_path):
    [result] = LocalVision(lambda image: URDU).complete([crop(tmp_path)])
    assert result["text"] == URDU and result["provider"] == "local" and result["confidence"] == 1.0


def test_readings_that_disagree_are_dropped_not_averaged_or_guessed(tmp_path):
    answers = iter([URDU, "اور بالکل مختلف عبارت جو تصویر میں نہیں تھی کوئی"])
    assert LocalVision(lambda image: next(answers)).complete([crop(tmp_path)]) == []


def test_each_crop_is_read_at_two_sizes(tmp_path):
    sizes = []
    LocalVision(lambda image: sizes.append(image.shape[1]) or URDU).complete([crop(tmp_path)])
    assert len(sizes) == 2 and sizes[0] != sizes[1]


def test_text_that_is_not_urdu_is_rejected():
    assert not plausible_urdu("The court is pleased to allow the petition", (400, 80))
    assert not plausible_urdu("", (400, 80))
    assert plausible_urdu(URDU, (400, 80))


def test_text_far_longer_than_the_crop_could_hold_is_rejected():
    assert not plausible_urdu(URDU * 30, (400, 80))


def test_a_repeating_loop_is_rejected():
    assert not plausible_urdu(" ".join(["عدالت"] * 12), (800, 200))


def test_the_model_saying_it_cannot_read_leaves_the_crop_unread(tmp_path):
    assert LocalVision(lambda image: "ILLEGIBLE").complete([crop(tmp_path)]) == []


def _case(tmp_path):
    doc = fitz.open()
    doc.new_page()
    pdf = tmp_path / "case.pdf"
    doc.save(pdf)
    prediction = tmp_path / "case.json"
    block = {"id": "p1_b0", "bbox": [50, 50, 300, 90], "contains_rtl": True, "type": "text", "text": "xx", "markdown": "xx",
             "confidence": {"score": 0.2, "reasons": []}}
    prediction.write_text(json.dumps({"pages": [{"blocks": [block]}]}, ensure_ascii=False), encoding="utf-8")
    return pdf, prediction


def test_under_a_local_ceiling_the_local_reader_reads_the_crop_and_no_third_party_is_called(tmp_path, monkeypatch):
    from docintel.engines import urdu_vision

    called = []
    monkeypatch.setattr(urdu_vision.GeminiVision, "complete", lambda self, tasks: called.append(tasks) or [])
    pdf, prediction = _case(tmp_path)
    summary = run(pdf, prediction, tmp_path / "out", ceiling="local", local_reader=lambda image: URDU)
    assert summary["accepted"] == 1 and called == []
    written = json.loads(prediction.read_text(encoding="utf-8"))["pages"][0]["blocks"][0]
    assert written["text"] == URDU and written["rtl"]["vision"]["provider"] == "local"


def test_without_a_local_reader_a_local_ceiling_still_sends_and_reads_nothing(tmp_path):
    pdf, prediction = _case(tmp_path)
    summary = run(pdf, prediction, tmp_path / "out", ceiling="local")
    assert summary["accepted"] == 0 and summary["withheld"] == [{"id": "p1_b0", "reason": "policy_ceiling"}]
