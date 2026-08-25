"""Build a small, stratified gold-set manifest from a large case-law corpus.

This does not annotate 30,000 PDFs. It profiles every file cheaply, selects a
deterministic document sample, and then selects a few high-value pages per
document for human annotation. Sampling is by court/template/mode/language/
table signals and is boosted by available parser confidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import fitz

try:
    from specter.specter_parser import _is_rtl, _normal_key
except ModuleNotFoundError:
    # Allow direct execution from within the specter folder.
    from specter_parser import _is_rtl, _normal_key


COURTS = [
    ("lahore_high_court", re.compile(r"LHC|LAHORE\s+HIGH\s+COURT", re.I)),
    ("supreme_court", re.compile(r"SUPREME\s+COURT", re.I)),
    ("lahore_high_court", re.compile(r"LAHORE\s+HIGH\s+COURT", re.I)),
    ("sindh_high_court", re.compile(r"SINDH\s+HIGH\s+COURT", re.I)),
    ("islamabad_high_court", re.compile(r"ISLAMABAD\s+HIGH\s+COURT", re.I)),
    ("peshawar_high_court", re.compile(r"PESHAWAR\s+HIGH\s+COURT", re.I)),
    ("balochistan_high_court", re.compile(r"BALOCHISTAN\s+HIGH\s+COURT", re.I)),
    ("other", re.compile(r".")),
]

# Cap expensive table parsing work per document. This is enough to capture
# common first-page table layouts while keeping full-corpus profiling fast.
MAX_TABLE_SCAN_PAGES = 8


def _doc_id(pdf: Path, root: Path) -> str:
    relative = str(pdf.resolve().relative_to(root.resolve())).replace("\\", "/")
    return hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]


def _court_hint(pdf: Path) -> str | None:
    source = f"{pdf.stem} {pdf.as_posix()}"
    for name, pattern in COURTS:
        if pattern.search(source):
            return name
    return None


def _court(text: str, pdf: Path) -> str:
    hint = _court_hint(pdf)
    if hint:
        return hint
    for name, pattern in COURTS:
        if pattern.search(text):
            return name
    return "other"


def _year(pdf: Path) -> str:
    match = re.search(r"(?:19|20)\d{2}", pdf.stem)
    return match.group(0) if match else "unknown"


def _page_features(page: fitz.Page, text: str, dominant_image: bool, detect_tables_on_page: bool = False) -> dict[str, Any]:
    nonspace = [char for char in text if not char.isspace()]
    rtl = sum(_is_rtl(char) for char in text)
    tables = 0
    if detect_tables_on_page:
        try:
            tables = len(page.find_tables().tables)
        except Exception:
            tables = 0
    width, height = float(page.rect.width), float(page.rect.height)
    return {
        "page_number": page.number + 1,
        "width": round(width, 2),
        "height": round(height, 2),
        "orientation": "landscape" if width > height else "portrait",
        "native_characters": len(nonspace),
        "native_rtl_characters": rtl,
        "native_rtl_ratio": round(rtl / len(nonspace), 4) if nonspace else 0.0,
        "image_count": len(page.get_images(full=True)),
        "dominant_image": dominant_image,
        "tables": tables,
        "text_density": round(len(nonspace) / max(1.0, width * height), 6),
    }


def _dominant_image(page: fitz.Page) -> bool:
    page_area = float(page.rect.width * page.rect.height)
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    return bool(page_area and any(float(info.get("bbox", (0, 0, 0, 0))[2] - info.get("bbox", (0, 0, 0, 0))[0]) * float(info.get("bbox", (0, 0, 0, 0))[3] - info.get("bbox", (0, 0, 0, 0))[1]) >= page_area * 0.72 for info in infos))


def _load_prediction(predictions_root: Path | None, stem: str) -> dict[str, Any] | None:
    if not predictions_root:
        return None
    path = predictions_root / f"{stem}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def profile_pdf(pdf: Path, root: Path, table_detection: bool = False, predictions_root: Path | None = None) -> dict[str, Any]:
    prediction = _load_prediction(predictions_root, pdf.stem)
    doc = fitz.open(pdf)
    try:
        pages = []
        scan_pages: list[int] = []
        native_pages: list[int] = []
        first_text_parts: list[str] = []
        table_checks = 0
        for page in doc:
            text = page.get_text("text") or ""
            if page.number < 3:
                first_text_parts.append(text)
            dominant = _dominant_image(page)
            page_number = page.number + 1
            if dominant:
                scan_pages.append(page_number)
            elif text.strip():
                native_pages.append(page_number)
            # Always check page 1; then bound expensive checks to likely text pages.
            should_check_tables = False
            if table_detection:
                if page_number == 1 and (not dominant) and text.strip():
                    should_check_tables = True
                elif (not dominant) and text.strip() and table_checks < MAX_TABLE_SCAN_PAGES:
                    should_check_tables = True
                if should_check_tables:
                    table_checks += 1
            pages.append(_page_features(page, text, dominant, should_check_tables))
        first_text = " ".join(first_text_parts)
    finally:
        doc.close()
    if scan_pages:
        route_mode = "scanned" if not native_pages or len(scan_pages) >= len(pages) * 0.5 else "mixed_scanned_first"
    else:
        route_mode = "digital"
    route = {"mode": route_mode, "page_count": len(pages), "scan_pages": scan_pages, "native_pages": native_pages}
    mode = "scanned" if scan_pages else "digital"
    rtl = any(page["native_rtl_characters"] > 0 for page in pages)
    has_tables = any(page["tables"] > 0 for page in pages)
    template = f"{pages[0]['orientation']}:{round(pages[0]['width']/10)*10}x{round(pages[0]['height']/10)*10}:{mode}" if pages else "unknown"
    confidence_by_page = {}
    if prediction:
        confidence_by_page = {int(page.get("page_number")): float(page.get("confidence", 0.0)) for page in prediction.get("pages", [])}
    for page in pages:
        page["prediction_confidence"] = confidence_by_page.get(page["page_number"])
        page["selection_priority"] = _page_priority(page, len(pages))
        if page["prediction_confidence"] is not None:
            page["selection_priority"] += max(0.0, 0.9 - page["prediction_confidence"]) * 2.0
    court = _court(first_text, pdf)
    stratum = "|".join([court, mode, "rtl" if rtl else "latin", "tables" if has_tables else "no_tables", template])
    hard_score = sum(page["selection_priority"] for page in pages) / max(1, len(pages))
    return {
        "doc_id": _doc_id(pdf, root),
        "path": str(pdf.resolve()),
        "source_name": pdf.name,
        "year": _year(pdf),
        "court": court,
        "route": route,
        "page_count": len(pages),
        "mode": mode,
        "has_native_rtl": rtl,
        "has_tables": has_tables,
        "template_key": template,
        "stratum": stratum,
        "hardness_score": round(hard_score, 4),
        "pages": pages,
    }


def _page_priority(page: dict[str, Any], page_count: int) -> float:
    priority = 0.0
    if page["page_number"] in {1, page_count}:
        priority += 0.20
    if page["tables"]:
        priority += 0.45
    if page["native_rtl_characters"]:
        priority += 0.35
    if page["dominant_image"]:
        priority += 0.15
    if page["native_characters"] < 100:
        priority += 0.15
    if page["orientation"] == "landscape":
        priority += 0.10
    return priority


def _select_documents(records: list[dict[str, Any]], target: int, seed: int) -> list[dict[str, Any]]:
    if target >= len(records):
        return records
    rng = random.Random(seed)
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        strata[record["stratum"]].append(record)
    selected: list[dict[str, Any]] = []
    # Guarantee rare strata representation first, then fill proportionally.
    for key in sorted(strata):
        candidates = sorted(strata[key], key=lambda item: (-item["hardness_score"], rng.random()))
        selected.append(candidates[0])
    selected_ids = {item["doc_id"] for item in selected}
    if len(selected) > target:
        selected = sorted(selected, key=lambda item: (-item["hardness_score"], item["doc_id"]))[:target]
        selected_ids = {item["doc_id"] for item in selected}
    remaining = [item for item in records if item["doc_id"] not in selected_ids]
    remaining.sort(key=lambda item: (-item["hardness_score"], rng.random()))
    selected.extend(remaining[:max(0, target - len(selected))])
    return selected


def _select_pages(record: dict[str, Any], pages_per_doc: int, full_document: bool) -> list[dict[str, Any]]:
    pages = record["pages"]
    if full_document or len(pages) <= pages_per_doc:
        return [{"page_number": page["page_number"], "reasons": ["full_document" if full_document else "document_short"], "features": page} for page in pages]
    ranked = sorted(pages, key=lambda page: (-page["selection_priority"], page["page_number"]))
    chosen = ranked[:pages_per_doc]
    return [{"page_number": page["page_number"], "reasons": _page_reasons(page, record["page_count"]), "features": page} for page in sorted(chosen, key=lambda item: item["page_number"])]


def _page_reasons(page: dict[str, Any], page_count: int) -> list[str]:
    reasons = []
    if page["page_number"] in {1, page_count}:
        reasons.append("boundary_page")
    if page["tables"]:
        reasons.append("table_candidate")
    if page["native_rtl_characters"]:
        reasons.append("rtl_candidate")
    if page["dominant_image"]:
        reasons.append("scan_candidate")
    if page["native_characters"] < 100:
        reasons.append("low_text_density")
    if not reasons:
        reasons.append("template_content_page")
    return reasons


def build_manifest(root: Path, output: Path, target_docs: int, pages_per_doc: int, seed: int, table_detection: bool, predictions_root: Path | None, render_selected: bool, full_documents: int) -> dict[str, Any]:
    pdfs = sorted(root.rglob("*.pdf"))
    records = [profile_pdf(pdf, root, table_detection, predictions_root) for pdf in pdfs]
    selected = _select_documents(records, min(target_docs, len(records)), seed)
    selected.sort(key=lambda item: item["doc_id"])
    full_ids = {item["doc_id"] for item in sorted(selected, key=lambda item: (-item["hardness_score"], item["doc_id"]))[:full_documents]}
    assets_dir = output.parent / f"{output.stem}_assets"
    documents = []
    for record in selected:
        pages = _select_pages(record, pages_per_doc, record["doc_id"] in full_ids)
        if render_selected:
            doc = fitz.open(record["path"])
            try:
                for page in pages:
                    page_dir = assets_dir / record["doc_id"]
                    page_dir.mkdir(parents=True, exist_ok=True)
                    image_path = page_dir / f"page_{page['page_number']:04d}.png"
                    pix = doc[page["page_number"] - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    pix.save(str(image_path))
                    page["image"] = str(image_path)
            finally:
                doc.close()
        documents.append({
            "doc_id": record["doc_id"],
            "path": record["path"],
            "source_name": record["source_name"],
            "court": record["court"],
            "year": record["year"],
            "stratum": record["stratum"],
            "mode": record["mode"],
            "template_key": record["template_key"],
            "page_count": record["page_count"],
            "hardness_score": record["hardness_score"],
            "selected_pages": pages,
            "annotation": {"status": "pending", "annotators": [], "adjudicated": False, "gold_json": None},
        })
    report = {
        "manifest_version": 1,
        "annotation_schema": {
            "page": {"page_number": "int", "width": "float", "height": "float", "blocks": "ordered list"},
            "block": {"type": "header|footer|heading|text|list|table", "text": "verified transcription", "bbox": "[x0,y0,x1,y1]", "reading_order": "int", "cells": "optional ordered table cells"},
            "cell": {"text": "verified transcription", "bbox": "[x0,y0,x1,y1]", "row": "int", "column": "int", "row_span": "int", "column_span": "int"},
        },
        "sampling": {"root": str(root.resolve()), "seed": seed, "target_documents": target_docs, "selected_documents": len(documents), "pages_per_document": pages_per_doc, "full_documents": full_documents, "policy": "stratified court/mode/language/table/template sample plus high-hardness pages; no model training labels are inferred as gold"},
        "corpus_summary": {"pdfs": len(records), "courts": dict(Counter(record["court"] for record in records)), "modes": dict(Counter(record["mode"] for record in records)), "strata": len(set(record["stratum"] for record in records))},
        "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Select a stratified, active-learning gold-set manifest.")
    parser.add_argument("root", type=Path, help="Corpus root containing PDFs")
    parser.add_argument("--output", type=Path, default=Path("artifacts/gold/gold_manifest.json"))
    parser.add_argument("--documents", type=int, default=250)
    parser.add_argument("--pages-per-document", type=int, default=5)
    parser.add_argument("--full-documents", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--detect-tables", dest="detect_tables", action="store_true")
    parser.add_argument("--no-detect-tables", dest="detect_tables", action="store_false")
    parser.set_defaults(detect_tables=True)
    parser.add_argument("--predictions-root", type=Path)
    parser.add_argument("--render-selected", action="store_true")
    args = parser.parse_args()
    report = build_manifest(args.root, args.output, args.documents, args.pages_per_document, args.seed, args.detect_tables, args.predictions_root, args.render_selected, args.full_documents)
    print(json.dumps({"corpus": report["corpus_summary"], "sampling": report["sampling"], "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
