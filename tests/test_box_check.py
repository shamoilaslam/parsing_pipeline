"""The digital-page box check: Nemotron's boxes scored against the PDF's own text boxes."""

import fitz
import pytest

from docintel.engines.nemotron_batch import render_jobs
from docintel.engines.nemotron_batch import append_raw
from eval.box_check import check_pdf, main, summarise

PARAGRAPH = (
    "The petitioner prays that the order dated 12 March 2024 be set aside and the matter remanded for fresh decision."
)


def make_pdf(path):
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(72, 72, 500, 200), PARAGRAPH, fontsize=11)
    doc.save(path)
    return path


def tokens(bbox, text="x", layout_class="Text"):
    """Model output for a region at ``bbox`` (points) on an A4 page rendered at 2x.

    The 1190x1684 image sits centred on the model's 1664x2048 canvas, padded 237
    left and 182 above, so a canvas fraction is (2 * points + padding) / canvas.
    """
    x1, y1, x2, y2 = bbox
    fx = lambda value: (2 * value + 237) / 1664  # noqa: E731
    fy = lambda value: (2 * value + 182) / 2048  # noqa: E731
    return f"<x_{fx(x1)}><y_{fy(y1)}>{text}<x_{fx(x2)}><y_{fy(y2)}><class_{layout_class}>"


def test_a_box_where_the_text_is_scores_a_perfect_match(tmp_path):
    pdf = make_pdf(tmp_path / "digital.pdf")
    jobs = render_jobs(pdf, [1], tmp_path / "images")
    [row] = check_pdf(pdf, {1: tokens([72, 72, 481.06, 103.36], PARAGRAPH)}, jobs)
    assert row["page_number"] == 1
    assert (row["precision"], row["recall"], row["f1"]) == (1.0, 1.0, 1.0)
    assert row["mean_iou"] == pytest.approx(1.0, abs=1e-3)


def test_a_box_nowhere_near_the_text_scores_zero(tmp_path):
    pdf = make_pdf(tmp_path / "digital.pdf")
    jobs = render_jobs(pdf, [1], tmp_path / "images")
    [row] = check_pdf(pdf, {1: tokens([72, 600, 481, 640])}, jobs)
    assert (row["precision"], row["recall"], row["f1"]) == (0.0, 0.0, 0.0)
    assert row["mean_iou"] is None


def test_pages_without_model_output_are_left_out(tmp_path):
    pdf = make_pdf(tmp_path / "digital.pdf")
    jobs = render_jobs(pdf, [1], tmp_path / "images")
    assert check_pdf(pdf, {}, jobs) == []


def row(f1, mean_iou, tau):
    return {"page_number": 1, "precision": f1, "recall": f1, "f1": f1, "mean_iou": mean_iou, "tau": tau}


def test_summary_averages_defined_scores_and_applies_the_release_gate():
    rows = [row(1.0, 0.9, 1.0), row(0.8, 0.8, None), row(0.9, None, 0.96)]
    summary = summarise(rows)
    assert summary["f1"] == pytest.approx(0.9)
    assert summary["mean_iou"] == pytest.approx(0.85)  # the page with no matches is left out
    assert summary["tau"] == pytest.approx(0.98)  # pages with fewer than two blocks are left out
    assert summary["passes_gate"] is False  # mean_iou must be strictly above 0.85


def test_summary_passes_when_every_threshold_is_cleared():
    assert summarise([row(0.95, 0.9, 0.99)])["passes_gate"] is True


def test_the_command_scores_every_page_in_the_raw_log_and_reports_the_gate(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "digital.pdf")
    append_raw(tmp_path / "raw.jsonl", 1, tokens([72, 72, 481.06, 103.36], PARAGRAPH))
    exit_code = main([str(pdf), "--work", str(tmp_path)])
    output = capsys.readouterr().out
    assert "page 1" in output
    assert "f1=1.000" in output
    assert exit_code == 1  # one page has no tau, so the gate cannot be shown to pass
