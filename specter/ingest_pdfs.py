"""Corpus entry point: route a folder of legal PDFs and write parsed output.

``python -m specter parse <folder> --out <dir>`` walks the folder, sends each
PDF to the engine that can read it, and writes, per document, ``<stem>.json``
(canonical), ``<stem>.md``, and ``metadata/<stem>.json``.  JSON and Markdown
stay at the output root because the Markdown references ``assets/`` relatively;
moving them into sub-folders would break those links.  A ``manifest.json``
indexes the run, including the documents that failed.

The router itself:

The canonical JSON contract stays unchanged.  Routing is decided per page, on
whether that page's own text layer is readable -- not on whether the document
as a whole looks scanned.  A page with no text, or with text that is almost
entirely private-use glyphs, is sent to OCR even when its neighbours are
perfectly good digital pages.  Documents that mix the two run both engines and
keep each page from the one that can read it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

from specter.courts import matches_case_number, matches_judge, path_labels, same_date
from specter.scanned_parser import _dominant_page_image, ScannedParser
from specter.specter_parser import PUA_CHAR_RE, SpecterParser, write_result


# A text layer this far into the private-use area is a legacy InPage/Nastaleeq
# encoding: it renders correctly on screen but extracts as unusable codepoints,
# so the page has no readable text even though it technically has text.
PUA_TEXT_RATIO = 0.5


def _page_needs_ocr(page: fitz.Page) -> bool:
    """True when a page's own text layer cannot be read.

    Asking this per page rather than per document is what stops a scanned page
    inside an otherwise digital PDF from being emitted empty.

    Two independent signals, either of which is enough:

    * A page-sized image is authoritative evidence of a scan even when the page
      also carries text -- that text is some other scanner's OCR, and trusting
      it over our own was measured to be worse.
    * A page whose own text is missing, or is almost entirely private-use
      glyphs, has nothing readable to extract regardless of what images it has.
      A partial image, or none at all, still leaves the page blank; the scanned
      parser drops genuinely empty pages itself.
    """
    if _dominant_page_image(page):
        return True
    text = page.get_text("text").strip()
    if not text:
        return True
    solid = [character for character in text if not character.isspace()]
    return bool(solid) and len(PUA_CHAR_RE.findall(text)) / len(solid) >= PUA_TEXT_RATIO


def classify_pdf(pdf: Path) -> dict[str, object]:
    """Decide, per page, which engine can actually read it.

    Every page lands in exactly one of ``scan_pages`` or ``native_pages``.  An
    earlier version keyed on a page-sized image, which left pages with neither
    a dominant image nor text in neither list -- those were silently emitted
    empty.  Measured on the SC corpus that was 190 pages across 23 documents.
    """
    doc = fitz.open(pdf)
    try:
        scan_pages = []
        native_pages = []
        for index in range(doc.page_count):
            try:
                needs_ocr = _page_needs_ocr(doc[index])
            except Exception:
                # A damaged page tree still describes a page; rendering it is
                # the only remaining way to read it, and failing one page must
                # not discard the rest of the judgment.
                needs_ocr = True
            (scan_pages if needs_ocr else native_pages).append(index + 1)
        if not scan_pages:
            mode = "digital"
        elif not native_pages or len(scan_pages) >= doc.page_count * 0.5:
            mode = "scanned"
        else:
            mode = "mixed_scanned_first"
        return {"mode": mode, "page_count": doc.page_count, "scan_pages": scan_pages, "native_pages": native_pages}
    finally:
        doc.close()


def _merge_native_pages(scanned: dict[str, object], digital: dict[str, object], native_pages: list[int]) -> dict[str, object]:
    """Take the pages that have a real text layer from the digital parse.

    A mixed PDF is routed to OCR so no scanned page silently loses text, but
    OCR-ing a page whose native text is already exact throws away the better
    answer.  Each page is taken from the engine that can actually read it.
    """
    wanted = set(native_pages)
    by_number = {page["page_number"]: page for page in digital.get("pages", [])}
    pages = [by_number.get(page["page_number"], page) if page["page_number"] in wanted else page
             for page in scanned.get("pages", [])]
    order = 0
    for page in pages:
        for block in page.get("blocks", []):
            block["document_reading_order"] = order
            order += 1
    scanned["pages"] = pages
    scanned["markdown"] = "\n\n---\n\n".join(page["markdown"] for page in pages if page.get("markdown"))
    scanned["document"]["stats"]["native_pages_kept"] = len(wanted)
    scanned["document"]["extraction_mode"] = "mixed_native_and_ocr"
    return scanned


def parse_pdf(pdf: Path, out: Path, engine: str = "auto", rtl_mode: str = "image", render_scale: float = 2.0,
              preprocess_variant: str = "grayscale", retry_threshold: float = 0.45) -> dict[str, object]:
    """Route one PDF to the engine that can read it, per page where they differ."""
    route = classify_pdf(pdf)
    selected = engine if engine != "auto" else ("scanned" if route["mode"] != "digital" else "digital")
    if selected == "digital":
        result = SpecterParser(rtl_mode=rtl_mode).parse(pdf, out)
    else:
        # OCR only the pages that need it.  A mixed document's readable pages
        # are taken from the digital parse below, so OCR-ing them costs ~30s
        # each and the result is thrown away.
        result = ScannedParser(render_scale=render_scale, preprocess_variant=preprocess_variant,
                               retry_threshold=retry_threshold).parse(pdf, out, ocr_pages=route["scan_pages"])
        if route["native_pages"]:
            result = _merge_native_pages(result, SpecterParser(rtl_mode=rtl_mode).parse(pdf, out), route["native_pages"])
    result["document"]["router"] = {"requested": engine, "selected": selected, **route}
    return result


def write_metadata(result: dict[str, object], output_dir: Path) -> Path:
    """Write the citable fields on their own.

    The canonical JSON carries every block and bbox and is large; indexing a
    corpus only needs the case identity, its provenance, and how far the
    document can be trusted.  Written after ``write_result`` because that is
    what attaches ``diagnostics``.
    """
    document = result["document"]
    meta_dir = output_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / f"{Path(document['source_name']).stem}.json"
    payload = {key: document.get(key) for key in (
        "source_name", "source_file", "page_count", "extraction_mode", "router",
        "metadata", "metadata_provenance", "confidence", "diagnostics", "stats", "warnings",
        "label_check",
    )}
    payload["template_key"] = (document.get("fingerprint") or {}).get("template_key")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def expand_paths(requested: list[Path], recursive: bool = False) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    paths = []
    for item in requested:
        if item.is_dir():
            paths.extend(sorted(item.glob(pattern)))
        elif any(char in str(item) for char in "*?["):
            paths.extend(sorted(item.parent.glob(item.name)))
        else:
            paths.append(item)
    return paths


def apply_path_labels(result: dict[str, object], pdf: Path) -> dict[str, object]:
    """Corroborate extracted metadata against labels stated by the file path.

    The SC corpus names the case and its decision date in the filename and the
    judge in the folder, which covers the 20% of documents that never state
    their decision date in the text at all.

    Two rules keep this honest.  A label only ever *fills* a field extraction
    left empty -- it never overwrites a value that was found on the page, since
    the scraper can be wrong too.  And a filled field is marked
    ``source="filename"`` with an empty bbox, because there is no page region
    to point at; the provenance contract stays sound.

    Deliberately applied by the CLI rather than inside ``parse_pdf``: the
    benchmark measures extraction, and folding labels in there would score the
    filename against itself.
    """
    labels = path_labels(pdf)
    if not labels:
        return result
    document = result["document"]
    metadata = document.setdefault("metadata", {})
    provenance = document.get("metadata_provenance") or {}
    # Not every court's filenames state every part.  LHC names a year and a
    # number but no case type, and composing one anyway wrote "None.5924/2023"
    # into the metadata -- a label worse than no label.
    number, year, case_type = labels.get("case_number"), labels.get("year"), labels.get("case_type")
    stated = {
        "case_number": f"{case_type + '.' if case_type else ''}{number}/{year}" if number and year else None,
        "decision_date": labels.get("decision_date"),
        "judges": [labels["judge"]] if labels.get("judge") else None,
        "neutral_citation": labels.get("neutral_citation"),
    }
    agrees = {
        "case_number": lambda value: matches_case_number(labels, value),
        "decision_date": lambda value: same_date(labels["decision_date"], value),
        "judges": lambda value: matches_judge(labels, value),
        # Nothing in the text states a neutral citation, so it is always a
        # fill and never a disagreement.
        "neutral_citation": lambda value: value == labels.get("neutral_citation"),
    }
    findings = []
    for field, value in stated.items():
        if not value:
            continue
        current = metadata.get(field)
        if not current:
            metadata[field] = value
            provenance[field] = {"value": value, "matched_value": None, "page": None,
                                 "bbox": [], "confidence": 0.5, "source": "filename"}
            findings.append({"field": field, "state": "filled_from_filename", "label": value})
        elif not agrees[field](current):
            findings.append({"field": field, "state": "disagrees", "label": value, "extracted": current})
    document["metadata_provenance"] = provenance
    document["label_check"] = {"source": "filename", "labels": labels, "findings": findings}
    return result


def _row(pdf: Path, result: dict[str, object]) -> dict[str, object]:
    document = result["document"]
    diagnostics = document.get("diagnostics") or {}
    return {
        "pdf": str(pdf),
        "stem": pdf.stem,
        "pages": document.get("page_count"),
        "route": (document.get("router") or {}).get("selected"),
        "extraction_mode": document.get("extraction_mode"),
        "case_number": (document.get("metadata") or {}).get("case_number"),
        "confidence": document.get("confidence"),
        "status": diagnostics.get("status"),
        "quality_score": diagnostics.get("quality_score"),
        "label_findings": [f["field"] + ":" + f["state"] for f in ((document.get("label_check") or {}).get("findings") or [])],
    }


def _parse_one(job: tuple) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Parse and write one document. Returns (manifest row, failure).

    Module level and returning rather than raising, so it can run either in
    this process or in a worker pool without the two paths diverging.  One
    unreadable PDF must never end a 30,000-document run.
    """
    pdf, out, engine, rtl_mode, render_scale, preprocess_variant, retry_threshold = job
    try:
        result = parse_pdf(pdf, out, engine=engine, rtl_mode=rtl_mode, render_scale=render_scale,
                           preprocess_variant=preprocess_variant, retry_threshold=retry_threshold)
        apply_path_labels(result, pdf)
        write_result(result, out)
        write_metadata(result, out)
        return _row(pdf, result), None
    except Exception as error:
        return None, {"pdf": str(pdf), "error": f"{type(error).__name__}: {error}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a folder of legal PDFs into Specter JSON, Markdown, and metadata.")
    parser.add_argument("pdf", nargs="+", type=Path, help="PDF files, glob patterns, or folders")
    parser.add_argument("--out", type=Path, default=Path("artifacts/run"))
    parser.add_argument("--engine", choices=["auto", "digital", "scanned"], default="auto")
    parser.add_argument("--rtl-mode", choices=["raw", "image", "both"], default="image")
    parser.add_argument("--render-scale", type=float, default=2.0)
    parser.add_argument("--preprocess-variant", choices=["clahe", "grayscale", "otsu", "adaptive", "sauvola", "deskew_clahe"], default="grayscale")
    parser.add_argument("--retry-threshold", type=float, default=0.45)
    parser.add_argument("--recursive", action="store_true", help="descend into sub-folders")
    parser.add_argument("--skip-existing", action="store_true", help="resume a part-finished run: skip PDFs whose JSON already exists")
    parser.add_argument("--quiet", action="store_true", help="suppress the per-document line")
    parser.add_argument("--workers", type=int, default=1, help="parse this many documents in parallel; documents are independent, so this scales close to linearly")
    args = parser.parse_args()
    pdfs = expand_paths(args.pdf, args.recursive)
    if not pdfs:
        parser.error("No PDF files found")

    args.out.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    collisions: list[dict[str, str]] = []
    seen: dict[str, Path] = {}
    jobs: list[tuple] = []

    for pdf in pdfs:
        # Outputs are keyed by stem, so two same-named PDFs in different
        # sub-folders would silently overwrite each other.  Report instead.
        if pdf.stem in seen:
            collisions.append({"stem": pdf.stem, "kept": str(seen[pdf.stem]), "skipped": str(pdf)})
            continue
        seen[pdf.stem] = pdf
        meta_path = args.out / "metadata" / f"{pdf.stem}.json"
        if args.skip_existing and (args.out / f"{pdf.stem}.json").exists():
            # Rebuild the manifest row from the metadata already on disk, so a
            # resumed run still produces a manifest covering the whole corpus.
            if meta_path.exists():
                documents.append(_row(pdf, {"document": json.loads(meta_path.read_text(encoding="utf-8"))}))
            else:
                documents.append({"pdf": str(pdf), "stem": pdf.stem, "status": "SKIPPED_EXISTING"})
            continue
        jobs.append((pdf, args.out, args.engine, args.rtl_mode, args.render_scale,
                     args.preprocess_variant, args.retry_threshold))

    # Documents are independent, so the only shared state is the output folder
    # and each job writes files named after its own stem.  Collision and
    # skip-existing decisions are made above, in one process, so workers never
    # race over them.  Results are consumed in submission order, keeping the
    # manifest identical whatever the worker count.
    if args.workers > 1 and jobs:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            outcomes = list(pool.map(_parse_one, jobs))
    else:
        outcomes = [_parse_one(job) for job in jobs]

    for index, (row, failure) in enumerate(outcomes, 1):
        if failure:
            failures.append(failure)
            if not args.quiet:
                print(json.dumps({"n": index, "pdf": failure["pdf"], "failed": failure["error"]}, ensure_ascii=False), flush=True)
            continue
        documents.append(row)
        if not args.quiet:
            print(json.dumps({"n": index, "of": len(jobs), **row}, ensure_ascii=False), flush=True)

    manifest = {
        "output_dir": str(args.out),
        "inputs": [str(item) for item in args.pdf],
        "pdfs_found": len(pdfs),
        "parsed": len(documents),
        "failed": len(failures),
        "settings": {"engine": args.engine, "rtl_mode": args.rtl_mode,
                     "preprocess_variant": args.preprocess_variant, "render_scale": args.render_scale,
                     "workers": args.workers},
        "documents": documents,
        "failures": failures,
        "duplicate_stems": collisions,
        "label_check": {
            "filled_from_filename": sum(1 for row in documents for finding in (row.get("label_findings") or []) if finding.endswith("filled_from_filename")),
            "disagreements": sum(1 for row in documents for finding in (row.get("label_findings") or []) if finding.endswith("disagrees")),
            "documents_with_findings": sum(1 for row in documents if row.get("label_findings")),
        },
    }
    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"parsed": len(documents), "failed": len(failures),
                      "duplicate_stems": len(collisions), "manifest": str(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
