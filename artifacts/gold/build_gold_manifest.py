#!/usr/bin/env python3
"""Build a small, stratified gold-set manifest from a large case-law corpus.

Single-file, dependency-light (only PyMuPDF required), cross-platform
(Mac/Windows/Linux). Walks every PDF under a folder, including subfolders,
profiles each one cheaply, then selects a deterministic, stratified sample of
documents and a few high-value pages per document for human annotation.

This does not annotate PDFs itself - it decides *which* pages are worth a
human's time, so annotation effort goes where it teaches you the most.

Usage:
    python build_gold_manifest.py /path/to/pdf/folder --output gold_manifest.json
    python build_gold_manifest.py "D:\\courts\\LHC" --documents 250 --workers 8

Fixes vs. the previous version:
  - No dependency on an external "specter" package - fully standalone.
  - Table detection now falls back to a text/whitespace strategy when the
    default vector-line strategy finds nothing, which is common for
    borderless Word-generated tables (this was silently returning tables=0
    on real tables).
  - Court detection now trusts the filename's court code first (portal
    filenames are reliable), and if it must fall back to text, only reads
    the first ~800 characters of page 1 - the letterhead zone - instead of
    the first three pages. Citations to other courts anywhere in the body
    can no longer be mistaken for the issuing court. Returns "unknown"
    rather than a confident wrong guess when neither signal is present.
  - Added detection for Private-Use-Area characters (legacy InPage/Nastaliq
    font encoding) as its own signal, separate from proper Unicode RTL -
    the previous RTL check silently missed this failure mode entirely.
  - Added a page-length bucket (short/medium/long) to stratification, so
    short procedural orders don't crowd out longer substantive judgments
    the way they did before (hardness score is an average per page, which
    structurally favors short documents).
  - Year is tracked and reported, but deliberately kept out of the hard
    stratification key to avoid fragmenting the sample into mostly-unique
    singletons - check corpus_summary's year histogram and add a dedicated
    era-focused sampling pass later if it looks skewed.
  - PDF profiling runs in parallel across CPU cores (falls back to serial
    for small corpora), with per-file error handling so one corrupt or
    password-protected PDF can't crash the whole run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

# --------------------------------------------------------------------------
# Inlined helpers (previously imported from an external package) - kept here
# so this file has exactly one third-party dependency: PyMuPDF.
# --------------------------------------------------------------------------

_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_PUA_RANGE = (0xE000, 0xF8FF)


def _is_rtl(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _ARABIC_RANGES)


def _is_pua(ch: str) -> bool:
    return _PUA_RANGE[0] <= ord(ch) <= _PUA_RANGE[1]


def _normal_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# --------------------------------------------------------------------------
# Courts. Two-tier matching on purpose:
#   - "code" patterns are trusted against the FILENAME ONLY. Court portals
#     put the court code directly in the filename almost universally
#     (e.g. "2025LHC131.pdf"), so a short code is safe there.
#   - "phrase" patterns are the only thing checked against document TEXT,
#     and only within the page-1 letterhead window - short codes are not
#     safe against body text (citations, cross-references), full phrases
#     mostly are.
# Extend this list for other court portals; unmatched documents get
# "unknown" rather than a wrong guess.
# --------------------------------------------------------------------------

COURTS: list[tuple[str, "re.Pattern[str]", "re.Pattern[str]"]] = [
    ("supreme_court", re.compile(r"SUPREME\s+COURT\s+OF\s+PAKISTAN", re.I), re.compile(r"\bSC\b")),
    ("federal_shariat_court", re.compile(r"FEDERAL\s+SHARIAT\s+COURT", re.I), re.compile(r"\bFSC\b")),
    ("lahore_high_court", re.compile(r"LAHORE\s+HIGH\s+COURT", re.I), re.compile(r"\bLHC\b")),
    ("sindh_high_court", re.compile(r"SINDH\s+HIGH\s+COURT", re.I), re.compile(r"\bSHC\b")),
    ("islamabad_high_court", re.compile(r"ISLAMABAD\s+HIGH\s+COURT", re.I), re.compile(r"\bIHC\b")),
    ("peshawar_high_court", re.compile(r"PESHAWAR\s+HIGH\s+COURT", re.I), re.compile(r"\bPHC\b")),
    ("balochistan_high_court", re.compile(r"BALOCHISTAN\s+HIGH\s+COURT", re.I), re.compile(r"\bBHC\b")),
]

LETTERHEAD_CHARS = 800          # how much of page 1 counts as "the letterhead"
MAX_TABLE_SCAN_PAGES = 8        # cap expensive table parsing per document
HEADER_TEXT_PAGES = 1           # court detection only looks at page 1


def _doc_id(pdf: Path, root: Path) -> str:
    try:
        relative = str(pdf.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        relative = pdf.name
    return hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]


def _court(pdf: Path, page1_text: str) -> str:
    stem = pdf.stem
    for name, _phrase, code in COURTS:
        if code.search(stem):
            return name
    header = page1_text[:LETTERHEAD_CHARS]
    for name, phrase, _code in COURTS:
        if phrase.search(header):
            return name
    return "unknown"


def _year(pdf: Path) -> str:
    match = re.search(r"(?:19|20)\d{2}", pdf.stem)
    return match.group(0) if match else "unknown"


def _length_bucket(page_count: int) -> str:
    if page_count <= 5:
        return "short"
    if page_count <= 15:
        return "medium"
    return "long"


def _count_tables(page: "fitz.Page") -> int:
    """Vector-line strategy first (exact, free); fall back to a text/
    whitespace strategy for borderless tables, which the default strategy
    silently reports as zero tables even when one is clearly visible."""
    try:
        found = page.find_tables()
        if found.tables:
            return len(found.tables)
    except Exception:
        pass
    try:
        found = page.find_tables(strategy="text")
        return len(found.tables)
    except Exception:
        return 0


def _dominant_image(page: "fitz.Page") -> bool:
    page_area = float(page.rect.width * page.rect.height)
    if not page_area:
        return False
    try:
        infos = page.get_image_info()
    except Exception:
        return False
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        if (x1 - x0) * (y1 - y0) >= page_area * 0.72:
            return True
    return False


def _page_features(page: "fitz.Page", text: str, dominant_image: bool, detect_tables_on_page: bool) -> dict[str, Any]:
    nonspace = [c for c in text if not c.isspace()]
    n = max(1, len(nonspace))
    rtl = sum(_is_rtl(c) for c in text)
    pua = sum(_is_pua(c) for c in text)
    tables = _count_tables(page) if detect_tables_on_page else 0
    width, height = float(page.rect.width), float(page.rect.height)
    return {
        "page_number": page.number + 1,
        "width": round(width, 2),
        "height": round(height, 2),
        "orientation": "landscape" if width > height else "portrait",
        "native_characters": len(nonspace),
        "native_rtl_characters": rtl,
        "native_rtl_ratio": round(rtl / n, 4),
        "native_pua_characters": pua,
        "native_pua_ratio": round(pua / n, 4),
        "image_count": len(page.get_images(full=True)),
        "dominant_image": dominant_image,
        "tables": tables,
        "text_density": round(len(nonspace) / max(1.0, width * height), 6),
    }


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


def _page_priority(page: dict[str, Any], page_count: int) -> float:
    priority = 0.0
    if page["page_number"] in {1, page_count}:
        priority += 0.20
    if page["tables"]:
        priority += 0.45
    if page["native_rtl_characters"]:
        priority += 0.30
    if page["native_pua_characters"]:
        priority += 0.35
    if page["dominant_image"]:
        priority += 0.15
    if page["native_characters"] < 100:
        priority += 0.15
    if page["orientation"] == "landscape":
        priority += 0.10
    return priority


def _page_reasons(page: dict[str, Any], page_count: int) -> list[str]:
    reasons = []
    if page["page_number"] in {1, page_count}:
        reasons.append("boundary_page")
    if page["tables"]:
        reasons.append("table_candidate")
    if page["native_rtl_characters"]:
        reasons.append("rtl_candidate")
    if page["native_pua_characters"]:
        reasons.append("pua_candidate")
    if page["dominant_image"]:
        reasons.append("scan_candidate")
    if page["native_characters"] < 100:
        reasons.append("low_text_density")
    if not reasons:
        reasons.append("template_content_page")
    return reasons


def profile_pdf(pdf: Path, root: Path, table_detection: bool, predictions_root: Path | None) -> dict[str, Any]:
    prediction = _load_prediction(predictions_root, pdf.stem)
    doc = fitz.open(pdf)
    try:
        pages: list[dict[str, Any]] = []
        scan_pages: list[int] = []
        native_pages: list[int] = []
        page1_text = ""
        table_checks = 0
        for page in doc:
            text = page.get_text("text") or ""
            if page.number == 0:
                page1_text = text
            dominant = _dominant_image(page)
            page_number = page.number + 1
            if dominant:
                scan_pages.append(page_number)
            elif text.strip():
                native_pages.append(page_number)
            should_check_tables = False
            if table_detection:
                if page_number == 1 and (not dominant) and text.strip():
                    should_check_tables = True
                elif (not dominant) and text.strip() and table_checks < MAX_TABLE_SCAN_PAGES:
                    should_check_tables = True
                if should_check_tables:
                    table_checks += 1
            pages.append(_page_features(page, text, dominant, should_check_tables))
    finally:
        doc.close()

    if scan_pages:
        route_mode = "scanned" if not native_pages or len(scan_pages) >= len(pages) * 0.5 else "mixed_scanned_first"
    else:
        route_mode = "digital"
    route = {"mode": route_mode, "page_count": len(pages), "scan_pages": scan_pages, "native_pages": native_pages}

    rtl = any(p["native_rtl_characters"] > 0 for p in pages)
    pua = any(p["native_pua_characters"] > 0 for p in pages)
    script_tag = "+".join(filter(None, [("rtl" if rtl else None), ("pua" if pua else None)])) or "latin"
    has_tables = any(p["tables"] > 0 for p in pages)
    length_bucket = _length_bucket(len(pages))
    template = f"{pages[0]['orientation']}:{round(pages[0]['width']/10)*10}x{round(pages[0]['height']/10)*10}:{route_mode}" if pages else "unknown"

    confidence_by_page = {}
    if prediction:
        confidence_by_page = {int(p.get("page_number")): float(p.get("confidence", 0.0)) for p in prediction.get("pages", [])}
    for page in pages:
        page["prediction_confidence"] = confidence_by_page.get(page["page_number"])
        page["selection_priority"] = _page_priority(page, len(pages))
        if page["prediction_confidence"] is not None:
            page["selection_priority"] += max(0.0, 0.9 - page["prediction_confidence"]) * 2.0

    court = _court(pdf, page1_text)
    stratum = "|".join([court, route_mode, script_tag, "tables" if has_tables else "no_tables", length_bucket, template])
    hard_score = sum(p["selection_priority"] for p in pages) / max(1, len(pages))

    return {
        "doc_id": _doc_id(pdf, root),
        "path": str(pdf.resolve()),
        "source_name": pdf.name,
        "year": _year(pdf),
        "court": court,
        "route": route,
        "page_count": len(pages),
        "mode": "scanned" if scan_pages else "digital",
        "has_native_rtl": rtl,
        "has_native_pua": pua,
        "has_tables": has_tables,
        "length_bucket": length_bucket,
        "template_key": template,
        "stratum": stratum,
        "hardness_score": round(hard_score, 4),
        "pages": pages,
    }


def _profile_one(args: tuple[str, str, bool, str | None]) -> dict[str, Any]:
    pdf_str, root_str, table_detection, predictions_root_str = args
    try:
        return profile_pdf(
            Path(pdf_str), Path(root_str), table_detection,
            Path(predictions_root_str) if predictions_root_str else None,
        )
    except Exception as exc:  # a single bad PDF must never kill the whole run
        return {"doc_id": None, "path": pdf_str, "error": f"{type(exc).__name__}: {exc}"}


def _combo_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """The three things you said matter for coverage and proportion: is it
    scanned or digital, does it contain Urdu at all (proper Unicode or
    PUA-corrupted - either way, "this document has Urdu in it"), and does
    it have a table. Finer distinctions (exact template, court, year) still
    ride along in `stratum` and get used as a tiebreaker within each combo,
    they just don't drive the target count the way these three do."""
    urdu = "urdu" if (record["has_native_rtl"] or record["has_native_pua"]) else "no_urdu"
    table = "table" if record["has_tables"] else "no_table"
    return (record["mode"], urdu, table)


def _select_documents(records: list[dict[str, Any]], target: int, seed: int, coverage_min: int = 3) -> list[dict[str, Any]]:
    """Two-phase selection:

    1. Coverage - every (mode, urdu, table) combination that actually
       exists in the corpus gets at least `coverage_min` documents, so a
       rare-but-real combination like scanned+Urdu is never invisible just
       because it's a tiny fraction of the corpus.
    2. Proportional fill - the remaining slots are filled so the mix of
       digital/scanned and Urdu/no-Urdu in your sample matches the true
       corpus percentages, not an arbitrary hardness ranking. This is what
       stops a 3.5%-scanned corpus from turning into a 34%-scanned sample.

    If a rounding shortfall remains after both phases, it's filled by
    hardness score from whatever's left, same as before.
    """
    if target >= len(records):
        return records
    rng = random.Random(seed)
    total = len(records)

    combos: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        combos[_combo_key(r)].append(r)

    # Phase 1: coverage floor per combination. These are PROTECTED - nothing
    # later in this function is allowed to remove them, even if the total
    # goes over target. An earlier version of this sorted everything by
    # hardness at the end to trim down to target, which silently discarded
    # exactly the rare-combination coverage this phase exists to guarantee.
    selected: list[dict[str, Any]] = []
    protected_ids: set[str] = set()
    for key in sorted(combos):
        pool = sorted(combos[key], key=lambda r: (-r["hardness_score"], rng.random()))
        for r in pool[:coverage_min]:
            selected.append(r)
            protected_ids.add(r["doc_id"])

    if len(selected) >= target:
        return selected  # coverage alone fills the budget - keep it all, don't trim.

    # Phase 2: proportional fill on (mode, urdu) for the remaining budget only.
    def mu(r: dict[str, Any]) -> tuple[str, str]:
        return (r["mode"], "urdu" if (r["has_native_rtl"] or r["has_native_pua"]) else "no_urdu")

    mode_urdu: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        mode_urdu[mu(r)].append(r)

    remaining_budget = target - len(selected)
    fill: list[dict[str, Any]] = []
    for key, group in mode_urdu.items():
        cell_target = round((len(group) / total) * target)
        already = sum(1 for r in selected if mu(r) == key)
        need = max(0, cell_target - already)
        pool = sorted((r for r in group if r["doc_id"] not in protected_ids), key=lambda r: (-r["hardness_score"], rng.random()))
        fill.extend(pool[:need])
    if len(fill) > remaining_budget:
        fill = fill[:remaining_budget]  # rounding overflow trimmed here, never from protected coverage
    selected.extend(fill)
    selected_ids = protected_ids | {r["doc_id"] for r in fill}

    # Phase 3: any leftover shortfall (rounding down) filled by hardness.
    if len(selected) < target:
        remaining = sorted((r for r in records if r["doc_id"] not in selected_ids), key=lambda r: (-r["hardness_score"], rng.random()))
        selected.extend(remaining[: target - len(selected)])

    return selected


def _combo_coverage(records: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For each (mode, urdu, table) combination: how many exist in the whole
    corpus, and how many made it into the sample. Check this after running -
    "in_sample": 0 for a combo that has documents "in_corpus" means it was
    missed and coverage_min needs raising, not a silent gap."""
    in_corpus: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in records:
        in_corpus[_combo_key(r)] += 1
    in_sample: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in selected:
        in_sample[_combo_key(r)] += 1
    return {
        "|".join(key): {"in_corpus": count, "in_sample": in_sample.get(key, 0)}
        for key, count in sorted(in_corpus.items())
    }


def _select_pages(record: dict[str, Any], pages_per_doc: int, full_document: bool) -> list[dict[str, Any]]:
    pages = record["pages"]
    if full_document or len(pages) <= pages_per_doc:
        reason = "full_document" if full_document else "document_short"
        return [{"page_number": p["page_number"], "reasons": [reason], "features": p} for p in pages]
    ranked = sorted(pages, key=lambda p: (-p["selection_priority"], p["page_number"]))
    chosen = ranked[:pages_per_doc]
    return [
        {"page_number": p["page_number"], "reasons": _page_reasons(p, record["page_count"]), "features": p}
        for p in sorted(chosen, key=lambda item: item["page_number"])
    ]


def _iter_pdfs(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".pdf":
            yield p


def build_manifest(
    root: Path,
    output: Path,
    target_docs: int,
    pages_per_doc: int,
    seed: int,
    table_detection: bool,
    predictions_root: Path | None,
    render_selected: bool,
    full_documents: int,
    workers: int,
    coverage_min: int = 3,
    from_cache: bool = False,
) -> dict[str, Any]:
    pdfs = sorted(_iter_pdfs(root))
    if not pdfs:
        raise SystemExit(f"No PDFs found under {root} (searched recursively, case-insensitive .pdf)")

    cache_path = output.parent / f"{output.stem}_profile_cache.json"
    records: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    if from_cache and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        records = cached["records"]
        errors = cached.get("errors", [])
        print(f"  loaded {len(records)} profiled documents from cache: {cache_path}", file=sys.stderr)
    else:
        tasks = [(str(p), str(root), table_detection, str(predictions_root) if predictions_root else None) for p in pdfs]
        workers = workers if workers > 0 else max(1, (os.cpu_count() or 2) - 1)

        started = time.time()
        results: list[dict[str, Any]] = []
        if workers <= 1 or len(tasks) < 16:
            for i, t in enumerate(tasks, 1):
                results.append(_profile_one(t))
                if i % 500 == 0:
                    print(f"  profiled {i}/{len(tasks)}...", file=sys.stderr)
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for i, res in enumerate(pool.map(_profile_one, tasks, chunksize=16), 1):
                    results.append(res)
                    if i % 500 == 0:
                        print(f"  profiled {i}/{len(tasks)}...", file=sys.stderr)
        print(f"  profiled {len(tasks)}/{len(tasks)} in {time.time()-started:.1f}s", file=sys.stderr)

        records = [r for r in results if not r.get("error")]
        errors = [r for r in results if r.get("error")]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"records": records, "errors": errors}, ensure_ascii=False), encoding="utf-8")
        print(f"  saved profile cache -> {cache_path} (reuse with --from-cache for a different --documents/--coverage-min without re-scanning)", file=sys.stderr)

    selected = _select_documents(records, min(target_docs, len(records)), seed, coverage_min)
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
            "has_native_rtl": record["has_native_rtl"],
            "has_native_pua": record["has_native_pua"],
            "length_bucket": record["length_bucket"],
            "template_key": record["template_key"],
            "page_count": record["page_count"],
            "hardness_score": record["hardness_score"],
            "selected_pages": pages,
            "annotation": {"status": "pending", "annotators": [], "adjudicated": False, "gold_json": None},
        })

    report = {
        "manifest_version": 2,
        "annotation_schema": {
            "page": {"page_number": "int", "width": "float", "height": "float", "blocks": "ordered list"},
            "block": {"type": "header|footer|heading|text|list|table", "text": "verified transcription", "bbox": "[x0,y0,x1,y1]", "reading_order": "int", "cells": "optional ordered table cells"},
            "cell": {"text": "verified transcription", "bbox": "[x0,y0,x1,y1]", "row": "int", "column": "int", "row_span": "int", "column_span": "int"},
        },
        "sampling": {
            "root": str(root.resolve()), "seed": seed, "target_documents": target_docs,
            "selected_documents": len(documents), "pages_per_document": pages_per_doc,
            "full_documents": full_documents, "workers": workers,
            "policy": "stratified court/mode/script(rtl,pua)/table/length/template sample plus high-hardness pages; year tracked but not stratified, see corpus_summary; no model training labels are inferred as gold",
        },
        "corpus_summary": {
            "pdfs": len(pdfs), "profiled_ok": len(records), "profile_errors": len(errors),
            "courts": dict(Counter(r["court"] for r in records)),
            "modes": dict(Counter(r["mode"] for r in records)),
            "route_modes": dict(Counter(r["route"]["mode"] for r in records)),
            "length_buckets": dict(Counter(r["length_bucket"] for r in records)),
            "years": dict(sorted(Counter(r["year"] for r in records).items())),
            "has_native_pua": sum(r["has_native_pua"] for r in records),
            "strata": len(set(r["stratum"] for r in records)),
            "combo_coverage": _combo_coverage(records, selected),
        },
        "profile_errors": errors[:50],
        "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Select a stratified, active-learning gold-set manifest from any folder of court PDFs.")
    parser.add_argument("root", type=Path, help="Corpus root containing PDFs (scanned recursively, any subfolder depth)")
    parser.add_argument("--output", type=Path, default=Path("artifacts/gold/gold_manifest.json"))
    parser.add_argument("--documents", type=int, default=50, help="run this once per court folder")
    parser.add_argument("--coverage-min", type=int, default=3, help="min docs guaranteed per mode/urdu/table combination")
    parser.add_argument("--pages-per-document", type=int, default=5)
    parser.add_argument("--full-documents", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--detect-tables", dest="detect_tables", action="store_true")
    parser.add_argument("--no-detect-tables", dest="detect_tables", action="store_false")
    parser.set_defaults(detect_tables=True)
    parser.add_argument("--predictions-root", type=Path)
    parser.add_argument("--render-selected", action="store_true")
    parser.add_argument("--workers", type=int, default=0, help="0 = auto (cpu_count - 1); 1 = serial, useful for debugging")
    parser.add_argument("--from-cache", action="store_true", help="reuse the previous run's full profile instead of re-scanning every PDF - use this to try a different --documents or --coverage-min cheaply")
    args = parser.parse_args()
    report = build_manifest(
        args.root, args.output, args.documents, args.pages_per_document, args.seed,
        args.detect_tables, args.predictions_root, args.render_selected, args.full_documents, args.workers,
        args.coverage_min, args.from_cache,
    )
    print(json.dumps({"corpus": report["corpus_summary"], "sampling": report["sampling"], "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
