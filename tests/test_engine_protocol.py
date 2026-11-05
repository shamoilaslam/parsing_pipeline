"""The engine seam: the pipeline reaches parsers only through ``Engine``."""

from pathlib import Path

import pytest

from docintel import pipeline
from docintel.engines.base import Engine
from docintel.engines.local import NativeEngine, OcrEngine


def test_native_engine_satisfies_the_protocol():
    engine = NativeEngine()
    assert isinstance(engine, Engine)
    assert (engine.name, engine.policy) == ("digital", "local")


def test_ocr_engine_satisfies_the_protocol():
    pytest.importorskip("rapidocr_onnxruntime")
    engine = OcrEngine()
    assert isinstance(engine, Engine)
    assert (engine.name, engine.policy) == ("scanned", "local")


def test_native_engine_ignores_pages_and_forwards_stem(monkeypatch):
    calls = []
    engine = NativeEngine()
    monkeypatch.setattr(engine._parser, "parse", lambda *a, **k: calls.append((a, k)) or {"document": {}})
    engine.parse(Path("a.pdf"), None, stem="s", pages=[1, 2])
    assert calls == [((Path("a.pdf"), None), {"stem": "s"})]


def test_pipeline_routes_a_digital_pdf_through_the_native_engine(monkeypatch):
    seen = {}

    class Fake:
        name, policy = "digital", "local"

        def __init__(self, rtl_mode="image"):
            seen["rtl_mode"] = rtl_mode

        def parse(self, pdf, output_dir, *, stem=None, pages=None):
            seen["stem"] = stem
            return {"document": {}}

    monkeypatch.setattr(pipeline, "NativeEngine", Fake)
    monkeypatch.setattr(pipeline, "classify_pdf", lambda pdf: {"mode": "digital", "scan_pages": [], "native_pages": [1]})
    result = pipeline.parse_pdf(Path("a.pdf"), Path("out"), rtl_mode="raw", stem="x")
    assert seen == {"rtl_mode": "raw", "stem": "x"}
    assert result["document"]["router"]["selected"] == "digital"
