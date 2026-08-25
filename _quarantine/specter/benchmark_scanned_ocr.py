"""Benchmark deterministic scanned OCR components without changing the parser.

This benchmark is deliberately honest about engine availability. It does not
download models or silently substitute a cloud/VLM backend. Available engines
are measured on the same rendered page and preprocessing variants; missing
engines are recorded as unavailable so a future installation is comparable.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import fitz
import psutil

from specter.scanned_parser import _render_page, preprocess_variants


def _engine_inventory() -> list[dict[str, Any]]:
    inventory = [{"name": "rapidocr-onnx-general-cpu", "available": True, "reason": "installed"}]
    for name, module in [("paddleocr", "paddleocr"), ("tesseract-lstm", "pytesseract"), ("easyocr", "easyocr")]:
        try:
            __import__(module)
            inventory.append({"name": name, "available": True, "reason": "installed"})
        except ImportError:
            inventory.append({"name": name, "available": False, "reason": "not installed; no model or package was added"})
    return inventory


def _page_numbers(value: str | None, count: int) -> list[int]:
    if not value:
        return list(range(1, count + 1))
    pages = sorted({int(item) for item in value.split(",") if item.strip()})
    invalid = [page for page in pages if page < 1 or page > count]
    if invalid:
        raise ValueError(f"Invalid page numbers: {invalid}")
    return pages


def _run_rapid(ocr: Any, image: Any) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    result, _ = ocr(image)
    elapsed = time.perf_counter() - started
    lines = []
    for item in result or []:
        if len(item) < 3:
            continue
        text = str(item[1] or "").strip()
        score = float(item[2] or 0.0)
        if text:
            lines.append({"text": text, "score": score, "bbox": item[0]})
    return lines, elapsed


def benchmark(pdf_path: Path, pages: list[int], output: Path, render_scale: float = 1.5) -> dict[str, Any]:
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    process = psutil.Process()
    results = []
    doc = fitz.open(pdf_path)
    try:
        for page_no in pages:
            page = doc[page_no - 1]
            image = _render_page(page, render_scale)
            page_results = []
            for variant_name, (clean, _, metadata) in preprocess_variants(image).items():
                before = process.memory_info().rss
                lines, elapsed = _run_rapid(ocr, clean)
                after = process.memory_info().rss
                confidence = statistics.mean([line["score"] for line in lines]) if lines else 0.0
                text_length = sum(len(line["text"]) for line in lines)
                page_results.append({
                    "variant": variant_name,
                    "mean_ocr_confidence": round(confidence, 6),
                    "detected_lines": len(lines),
                    "text_characters": text_length,
                    "processing_seconds": round(elapsed, 4),
                    "rss_delta_mb": round((after - before) / (1024 * 1024), 4),
                    "preprocessing": metadata,
                })
            selected = max(page_results, key=lambda item: (item["mean_ocr_confidence"], item["text_characters"], item["detected_lines"]))
            results.append({"page": page_no, "selected_variant": selected["variant"], "variants": page_results})
    finally:
        doc.close()
    report = {
        "pdf": str(pdf_path),
        "render_scale": render_scale,
        "engine_inventory": _engine_inventory(),
        "selection_policy": "highest mean OCR confidence, then text characters, then detected lines",
        "pages": results,
        "summary": {
            "selected_variants": {variant: sum(item["selected_variant"] == variant for item in results) for variant in sorted({item["selected_variant"] for item in results})},
            "mean_selected_confidence": statistics.mean(next(v["mean_ocr_confidence"] for v in item["variants"] if v["variant"] == item["selected_variant"]) for item in results) if results else 0.0,
            "mean_selected_seconds": statistics.mean(next(v["processing_seconds"] for v in item["variants"] if v["variant"] == item["selected_variant"]) for item in results) if results else 0.0,
        },
        "limitations": [
            "CER/WER and bbox IoU require verified text/box ground truth and are not inferred from OCR confidence.",
            "Only RapidOCR is installed in this environment; no alternate OCR engine was added just to manufacture an ensemble.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark scanned OCR preprocessing and available CPU engines.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", help="Comma-separated 1-based pages; default is every page")
    parser.add_argument("--render-scale", type=float, default=1.5)
    parser.add_argument("--output", type=Path, default=Path("artifacts/scanned/benchmark/report.json"))
    args = parser.parse_args()
    with fitz.open(args.pdf) as doc:
        pages = _page_numbers(args.pages, doc.page_count)
    report = benchmark(args.pdf, pages, args.output, args.render_scale)
    print(json.dumps({"pdf": report["pdf"], "pages": len(report["pages"]), "summary": report["summary"], "engines": report["engine_inventory"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
