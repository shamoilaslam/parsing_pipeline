"""NemotronEngine: the model's page outputs behind the Engine protocol."""

import fitz
import pytest

from docintel.engines.base import Engine
from docintel.engines.nemotron_batch import append_raw
from docintel.engines.nemotron_engine import NemotronEngine, PagesPending, log_transport
from docintel.schema.specter_v1 import SpecterV1

CAPTION = (
    "IN THE LAHORE HIGH COURT, LAHORE\nJudicial Department\nW.P. No. 12345 of 2023\n"
    "Ali versus Province of Punjab\nDate of hearing: 12-03-2024\nJUDGMENT"
)
# A region covering the upper half of the page; the box is not what these tests study.
RAW = f"<x_0.2><y_0.1>{CAPTION}<x_0.8><y_0.4><class_Text>"


def make_pdf(path, pages=2):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    return path


def serving(raw=RAW):
    """A transport that has model output for every page it is asked about."""
    return lambda pdf, jobs: {job["page_number"]: raw for job in jobs}


def test_the_engine_returns_a_valid_specter_result_and_declares_its_policy(tmp_path):
    engine = NemotronEngine(serving())
    assert isinstance(engine, Engine)
    assert (engine.name, engine.policy) == ("nemotron-parse", "self_hosted")
    result = engine.parse(make_pdf(tmp_path / "scan.pdf"), tmp_path / "out")
    SpecterV1.model_validate(result)
    assert result["document"]["page_count"] == 2
    assert result["document"]["engine"] == "nemotron-parse-v1.2"


def test_pages_the_transport_has_no_output_for_are_reported_not_emitted_empty(tmp_path):
    engine = NemotronEngine(lambda pdf, jobs: {1: RAW})
    with pytest.raises(PagesPending) as raised:
        engine.parse(make_pdf(tmp_path / "scan.pdf"), None)
    assert raised.value.pages == [2]


def test_case_metadata_is_read_from_the_models_text(tmp_path):
    result = NemotronEngine(serving()).parse(make_pdf(tmp_path / "scan.pdf"), None)
    metadata = result["document"]["metadata"]
    assert metadata["case_number"] == "W.P. No. 12345 of 2023"
    assert metadata["court_id"] == "lahore_high_court"
    assert metadata["decision_date_iso"] == "2024-03-12"


def test_each_metadata_field_points_back_at_the_region_it_was_read_from(tmp_path):
    result = NemotronEngine(serving()).parse(make_pdf(tmp_path / "scan.pdf"), None)
    provenance = result["document"]["metadata_provenance"]["case_number"]
    [block] = result["pages"][0]["blocks"]
    assert provenance["page"] == 1
    assert provenance["bbox"] == block["bbox"]


def test_a_log_copied_back_from_a_gpu_session_serves_the_engine(tmp_path):
    (tmp_path / "work").mkdir()
    for page in (1, 2):
        append_raw(tmp_path / "work" / "raw.jsonl", page, RAW)
    engine = NemotronEngine(log_transport(tmp_path / "work"))
    result = engine.parse(make_pdf(tmp_path / "scan.pdf"), None)
    assert result["document"]["metadata"]["case_number"] == "W.P. No. 12345 of 2023"


def test_only_the_requested_pages_are_read_and_the_rest_are_left_for_the_native_parse(tmp_path):
    asked = []

    def transport(pdf, jobs):
        asked.extend(job["page_number"] for job in jobs)
        return {job["page_number"]: RAW for job in jobs}

    result = NemotronEngine(transport).parse(make_pdf(tmp_path / "mixed.pdf"), None, pages=[2])
    SpecterV1.model_validate(result)
    assert asked == [2]
    first, second = result["pages"]
    assert (first["extraction"], first["blocks"]) == ("native_text_pending", [])
    assert second["extraction"] == "scanned" and second["blocks"]
    assert result["document"]["stats"]["blocks"] == len(second["blocks"])
