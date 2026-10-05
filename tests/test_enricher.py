"""The enricher and legal-hints seams."""

from pathlib import Path

from docintel.engines.hints import LegalHints
from docintel.engines.native import _extract_metadata
from docintel.enrich import Enricher
from docintel.legal.enricher import LegalEnricher
from docintel.legal.hints import LEGAL_HINTS
from docintel import pipeline

TEXT = "IN THE LAHORE HIGH COURT\nGhulam Muhammad Vs. The State etc.\n"


def test_legal_enricher_satisfies_the_protocol():
    assert isinstance(LegalEnricher(), Enricher)


def test_pipeline_runs_the_legal_enricher():
    assert any(isinstance(e, LegalEnricher) for e in pipeline.ENRICHERS)


def test_enricher_marks_a_judgment_and_attaches_citations():
    result = {"document": {}, "pages": []}
    LegalEnricher().enrich(result, Path("D:/corpus/other/judgment.pdf"))
    assert result["document"]["document_kind"] == "judgment"
    assert result["document"]["citations"] == []


def test_default_hints_are_the_legal_registry():
    assert _extract_metadata(TEXT, {})["court_id"] == _extract_metadata(TEXT, {}, LEGAL_HINTS)["court_id"]


def test_injected_hints_replace_the_court_rules():
    generic = LegalHints(canonical_court=str.upper, court_id=lambda _: "x", sits_in_benches=lambda _: False,
                         find_citations=lambda _: [])
    meta = _extract_metadata(TEXT, {}, generic)
    assert meta["court_id"] == "x"
    assert meta["court"] == meta["court"].upper()
    assert meta["bench"] is None
