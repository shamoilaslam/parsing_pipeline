"""The GPU batch seam: render pages to images before the GPU session, read raw outputs after it."""

import fitz

from docintel.engines.nemotron_batch import append_raw, ingest, load_raw, render_jobs


def make_pdf(path, pages=2):
    doc = fitz.open()
    for number in range(pages):
        doc.new_page().insert_text((72, 100), f"Page {number + 1}", fontsize=11)
    doc.save(path)
    return path


def test_each_requested_page_is_rendered_to_an_image_with_its_sizes_recorded(tmp_path):
    pdf = make_pdf(tmp_path / "case.pdf", pages=3)
    jobs = render_jobs(pdf, [1, 3], tmp_path / "images")
    assert [job["page_number"] for job in jobs] == [1, 3]
    job = jobs[0]
    assert job["page_size"] == (595.0, 842.0)  # PyMuPDF's A4 default, in points
    assert job["image_size"] == (1190, 1684)  # rendered at 2x
    assert (tmp_path / "images" / job["image"]).stat().st_size > 0


RAW = "<x_0.1><y_0.1>Hello<x_0.5><y_0.2><class_Text>"


def jobs_for(tmp_path, pages):
    return render_jobs(make_pdf(tmp_path / "case.pdf", pages=3), pages, tmp_path / "images")


def test_raw_outputs_become_pages_sized_from_their_jobs(tmp_path):
    jobs = jobs_for(tmp_path, [1, 3])
    pages, pending = ingest(jobs, {1: RAW, 3: RAW})
    assert [page["page_number"] for page in pages] == [1, 3]
    assert (pages[0]["width"], pages[0]["height"]) == (595.0, 842.0)
    assert pending == []


def test_jobs_the_session_did_not_reach_are_returned_for_the_next_one(tmp_path):
    jobs = jobs_for(tmp_path, [1, 3])
    pages, pending = ingest(jobs, {1: RAW})
    assert [page["page_number"] for page in pages] == [1]
    assert pending == [jobs[1]]


def test_raw_outputs_round_trip_through_the_log_exactly(tmp_path):
    log = tmp_path / "raw.jsonl"
    append_raw(log, 1, "line one\nجو جائیداد <x_0.1>")
    append_raw(log, 3, RAW)
    assert load_raw(log) == {1: "line one\nجو جائیداد <x_0.1>", 3: RAW}


def test_a_half_written_last_line_from_a_killed_session_is_ignored(tmp_path):
    log = tmp_path / "raw.jsonl"
    append_raw(log, 1, RAW)
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"page_number": 2, "raw": "<x_0.')
    assert load_raw(log) == {1: RAW}


def test_a_missing_log_means_nothing_is_done_yet(tmp_path):
    assert load_raw(tmp_path / "raw.jsonl") == {}
