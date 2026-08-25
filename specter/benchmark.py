"""Score the parser against the manually reviewed LHC gold pages.

Gold is recorded per page, so every comparison here is segmentation invariant:
the parser's blocks for a page are concatenated before scoring.  This measures
text fidelity, not block boundaries, and therefore cannot be gamed by merging
or splitting paragraphs.

Five slices are reported separately because they fail for different reasons and
need different fixes.  A single blended number hides that a scanned page has no
text layer at all while an ordinary page is already exact.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any

import fitz
import jiwer

from specter.ingest_pdfs import classify_pdf
from specter.specter_parser import SpecterParser


GOLD_PATH = Path("artifacts/gold/final_review_corrections_v2.json")
ENRICHED_PATH = Path("artifacts/gold/gold_lhc_benchmark_enriched.json")

# ``prepare_review.py`` prefixed each draft page with a synthetic metadata
# block.  ``enrich_gold_benchmark.py`` stripped most of them, but three records
# kept a ``Case No:`` line that is annotation scaffolding, not page content.
SYNTHETIC_RE = re.compile(r"^[ \t]*(?:Court|Case No|Parties):.*$", re.MULTILINE)
TABLE_MARKER_RE = re.compile(r"\[/?TABLE\]")
# Symbol-font bullets survive in three gold records as raw private-use
# codepoints.  Dropping them from both sides keeps the comparison fair whether
# or not the parser remaps them.
PUA_RE = re.compile("[\ue000-\uf8ff]")
WHITESPACE_RE = re.compile(r"\s+")
ORDER_SHEET_RE = re.compile(r"order\s*/?\s*proceeding|signature of (?:the )?judge", re.IGNORECASE)
RTL_RE = re.compile("[\u0600-\u06ff]")


def normalise(text: str) -> str:
    """Collapse whitespace and drop private-use glyphs.

    Whitespace normalisation is mandatory, not cosmetic: the gold was captured
    under an older PyMuPDF that emitted space-only lines which 1.28 no longer
    produces, so raw comparison reports differences that do not exist.
    """
    return WHITESPACE_RE.sub(" ", PUA_RE.sub("", text)).strip()


def gold_text(record: dict[str, Any]) -> str:
    body = SYNTHETIC_RE.sub("", record["text"])
    return normalise(TABLE_MARKER_RE.sub("", body))


def gold_tables(record: dict[str, Any]) -> list[list[list[str]]]:
    """Pull ``[TABLE] … [/TABLE]`` blocks out of a gold page."""
    tables = []
    for block in re.findall(r"\[TABLE\](.*?)\[/TABLE\]", record["text"], re.DOTALL):
        rows = [[cell.strip() for cell in line.split("|")] for line in block.strip().splitlines() if line.strip()]
        if rows:
            tables.append(rows)
    return tables


def cells_of(tables: list[list[list[str]]]) -> set[str]:
    return {normalise(cell) for rows in tables for row in rows for cell in row if normalise(cell)}


def table_kind(tables: list[list[list[str]]]) -> str:
    """Separate the two gold table shapes, which need different targets.

    A ``cover`` table is a real key-value grid (``Date of hearing`` / counsel)
    and the parser should reproduce its cells.  An ``order_sheet`` is the
    3-column form header stamped on most LHC judgments; the annotator wrapped
    the whole proceedings body into its rows, but that body is ordinary prose,
    so cell recall against it is not a target the parser should chase.  Scoring
    them together would make correct behaviour look like a regression.
    """
    return "order_sheet" if ORDER_SHEET_RE.search(" ".join(cells_of(tables))) else "cover"


def max_columns(tables: list[list[list[str]]]) -> int:
    return max((len(row) for rows in tables for row in rows), default=0)


def error_rates(reference: str, hypothesis: str) -> tuple[float, float]:
    if not reference:
        return (0.0, 0.0) if not hypothesis else (1.0, 1.0)
    if not hypothesis:
        return 1.0, 1.0
    return jiwer.cer(reference, hypothesis), jiwer.wer(reference, hypothesis)


def classify(record: dict[str, Any], gold: str, native: str) -> str:
    """Assign one primary slice per record, most-fundamental failure first."""
    if gold and len(RTL_RE.findall(gold)) >= len(gold.replace(" ", "")) * 0.4:
        # A predominantly Urdu page is reported as Urdu even when it also has
        # no usable text layer.  Both are true, but OCR-ing it does not help --
        # no local engine reads Nastaleeq -- so Urdu is the binding constraint
        # and calling it "scanned" would blame the wrong subsystem.  The
        # threshold keeps an English page carrying a few Urdu words in
        # whichever structural slice actually explains its errors.
        return "urdu"
    if len(native) < len(gold) * 0.1:
        # No usable text layer: the page is a scan regardless of anything else.
        return "scanned"
    if len(RTL_RE.findall(gold)) > 5:
        return "urdu"
    if "[TABLE]" in record["text"]:
        return "table"
    if native == gold:
        return "regression"
    return "text_edit"


def page_text(result: dict[str, Any], page_number: int) -> str:
    for page in result.get("pages", []):
        if page["page_number"] == page_number:
            return normalise(" ".join(block["text"] for block in page["blocks"]))
    return ""


def page_tables(result: dict[str, Any], page_number: int) -> list[list[list[str]]]:
    for page in result.get("pages", []):
        if page["page_number"] == page_number:
            return [block["rows"] for block in page["blocks"] if block["type"] == "table" and block.get("rows")]
    return []


def page_form_headers(result: dict[str, Any], page_number: int) -> int:
    """Count ORDER SHEET boilerplate boxes stripped from this page.

    These are deliberately not type ``table`` (they are excluded from
    content, not reproduced as cells -- see ``_is_order_sheet_header`` in
    ``specter_parser.py``), so ``page_tables`` never counts them.  Tracked
    separately so a correct classification does not read as a missed table.
    """
    for page in result.get("pages", []):
        if page["page_number"] == page_number:
            return sum(block["type"] == "form_header" for block in page["blocks"])
    return 0


def load_records() -> list[dict[str, Any]]:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    paths = {
        (item["doc_id"], item["page_number"]): item["path"]
        for item in json.loads(ENRICHED_PATH.read_text(encoding="utf-8"))
    }
    records = []
    for record in gold:
        if record.get("discard"):
            continue
        path = paths.get((record["doc_id"], record["page_number"]))
        if path and Path(path).exists():
            records.append({**record, "path": path})
    return records


SC_GOLD_PATH = Path("artifacts/gold/gold_corrections_sc_review.json")
SC_AUTO_PATH = Path("artifacts/gold/auto_accepted_sc_review.json")
SC_MANIFEST_PATH = Path("artifacts/gold/gold_manifest_SC.json")


def load_sc_records() -> list[dict[str, Any]]:
    """Load the reviewed SC gold pages.

    Two files together are the batch: the pages a human corrected, and the
    pages the review tool auto-accepted because they carried no table, RTL,
    private-use or scan signal.  Dropping the auto-accepted ones would quietly
    remove the easiest pages and flatter the score.

    Paths come from the SC manifest rather than an enriched benchmark file --
    ``build_gold_manifest.py`` already records where each document lives.
    """
    paths = {
        document["doc_id"]: document["path"]
        for document in json.loads(SC_MANIFEST_PATH.read_text(encoding="utf-8"))["documents"]
    }
    gold = json.loads(SC_GOLD_PATH.read_text(encoding="utf-8"))
    if SC_AUTO_PATH.exists():
        gold = gold + json.loads(SC_AUTO_PATH.read_text(encoding="utf-8"))
    records = []
    for record in gold:
        if record.get("flagged") or record.get("discard"):
            continue
        path = paths.get(record["doc_id"])
        if path and Path(path).exists():
            records.append({**record, "page_number": int(record["page_number"]), "path": path})
    return records


def evaluate(limit: int | None = None, include_scanned: bool = True,
             records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Score gold pages. Pass ``records`` to score a corpus other than LHC.

    The slices, the segmentation-invariant comparison and the table analysis
    are identical for every corpus, so SC reuses this rather than growing a
    second scorer that could drift from it.
    """
    records = load_records() if records is None else records
    if limit:
        records = records[:limit]
    engine = SpecterParser(rtl_mode="raw")
    parsed: dict[str, dict[str, Any]] = {}
    natives: dict[str, dict[int, str]] = {}
    routes: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    scratch = tempfile.TemporaryDirectory()

    for record in records:
        path = record["path"]
        if path not in parsed:
            # Each document goes through the engine the router would choose,
            # so the scanned slice reports what the OCR route actually
            # produces instead of what the digital route fails to.
            routes[path] = classify_pdf(path)["mode"]
            if routes[path] != "digital" and include_scanned:
                from specter.ingest_pdfs import parse_pdf

                # Routed through the same entry point production uses, so a
                # mixed document keeps the native text of its digital pages
                # instead of having them re-read by OCR.
                parsed[path] = parse_pdf(Path(path), Path(scratch.name), rtl_mode="raw")
            else:
                # output_dir=None keeps the run read-only: no Urdu crops are written.
                parsed[path] = engine.parse(path, None)
            with fitz.open(path) as document:
                natives[path] = {n + 1: normalise(page.get_text("text")) for n, page in enumerate(document)}
        page_number = record["page_number"]
        gold = gold_text(record)
        native = natives[path].get(page_number, "")
        predicted = page_text(parsed[path], page_number)
        cer, wer = error_rates(gold, predicted)
        gold_tbl = gold_tables(record)
        pred_tbl = page_tables(parsed[path], page_number)
        gold_cells = cells_of(gold_tbl)
        rows.append({
            "file": record["source_name"],
            "page": page_number,
            "slice": classify(record, gold, native),
            "cer": cer,
            "wer": wer,
            "gold_chars": len(gold),
            "predicted_chars": len(predicted),
            "gold_tables": len(gold_tbl),
            "predicted_tables": len(pred_tbl),
            "gold_max_columns": max_columns(gold_tbl),
            "predicted_max_columns": max_columns(pred_tbl),
            "table_kind": table_kind(gold_tbl) if gold_tbl else None,
            "cell_recall": (len(gold_cells & cells_of(pred_tbl)) / len(gold_cells)) if gold_cells else None,
            "form_headers": page_form_headers(parsed[path], page_number),
            "route": routes[path],
        })

    scratch.cleanup()
    return {"records": len(rows), "slices": summarise(rows), "tables": table_summary(rows), "rows": rows}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for name in ("regression", "table", "scanned", "urdu", "text_edit"):
        group = [row for row in rows if row["slice"] == name]
        if group:
            summary[name] = {
                "pages": len(group),
                "cer_mean": round(statistics.mean(row["cer"] for row in group), 4),
                "cer_median": round(statistics.median(row["cer"] for row in group), 4),
                "wer_mean": round(statistics.mean(row["wer"] for row in group), 4),
                "exact": sum(row["cer"] == 0 for row in group),
            }
    return summary


def table_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    without = [row for row in rows if not row["gold_tables"]]
    summary: dict[str, Any] = {
        "false_positive_pages": sum(bool(row["predicted_tables"]) for row in without),
        "no_table_pages": len(without),
    }
    for kind in ("cover", "order_sheet"):
        group = [row for row in rows if row["table_kind"] == kind]
        recalls = [row["cell_recall"] for row in group if row["cell_recall"] is not None]
        # An order_sheet page is correctly handled by finding its header and
        # stripping it (form_headers), not by reproducing it as a "table" --
        # so "detected" must count form_header there, not the table type.
        detected = sum(bool(row["predicted_tables"]) or bool(row["form_headers"]) for row in group)
        summary[kind] = {
            "pages": len(group),
            "detected": detected,
            "column_count_match": sum(row["gold_max_columns"] == row["predicted_max_columns"] for row in group),
            "cell_recall_mean": round(statistics.mean(recalls), 4) if recalls else None,
        }
    return summary


def render(report: dict[str, Any]) -> str:
    lines = [f"gold pages scored: {report['records']}", "", f"{'slice':<12}{'pages':>6}{'CER mean':>10}{'CER med':>9}{'WER mean':>10}{'exact':>7}"]
    for name, values in report["slices"].items():
        lines.append(f"{name:<12}{values['pages']:>6}{values['cer_mean']:>10.4f}{values['cer_median']:>9.4f}{values['wer_mean']:>10.4f}{values['exact']:>7}")
    tables = report["tables"]
    lines += ["", f"{'gold table':<12}{'pages':>6}{'found':>7}{'cols ok':>9}{'cell recall':>13}"]
    for kind in ("cover", "order_sheet"):
        values = tables[kind]
        recall = "n/a" if values["cell_recall_mean"] is None else f"{values['cell_recall_mean']:.4f}"
        lines.append(f"{kind:<12}{values['pages']:>6}{values['detected']:>7}{values['column_count_match']:>9}{recall:>13}")
    lines += [
        f"false positives: {tables['false_positive_pages']}/{tables['no_table_pages']} non-table pages got a table",
        "",
        "note: order_sheet cell recall is diagnostic only.  The gold wraps the",
        "proceedings body into form-header rows; that body is prose, so a low",
        "number here is expected and is not a defect to fix.",
    ]
    return "\n".join(lines)


SC_ROOT = Path(r"D:\Shamoil Data\specter_data\SC")


def evaluate_sc_metadata(root: Path, limit: int | None = None, include_scanned: bool = False) -> dict[str, Any]:
    """Score SC metadata against the labels the corpus states in its own paths.

    There is no annotated SC gold yet, but the filename names the case and its
    decision date, and the folder names the judge.  That is a free label for
    three fields across all 790 documents, which is enough to measure the
    metadata defects without waiting on annotation.

    Only the fields the path actually states are scored.  ``court``, ``parties``
    and ``counsel`` are reported as coverage (how often anything was found), not
    as accuracy, because nothing here can say whether they are right.
    """
    from specter.courts import matches_case_number, matches_judge, path_labels, same_date

    engine = SpecterParser(rtl_mode="raw")
    scratch = tempfile.TemporaryDirectory()
    rows: list[dict[str, Any]] = []
    for pdf in sorted(Path(root).rglob("*.pdf")):
        labels = path_labels(pdf)
        if not labels:
            continue
        mode = classify_pdf(pdf)["mode"]
        if mode != "digital" and not include_scanned:
            continue
        try:
            if mode == "digital":
                result = engine.parse(pdf, None)
            else:
                from specter.ingest_pdfs import parse_pdf

                result = parse_pdf(pdf, Path(scratch.name), rtl_mode="raw")
        except Exception as error:  # one bad PDF must not end a 790-document run
            rows.append({"pdf": pdf.name, "mode": mode, "error": repr(error)})
            continue
        metadata = result["document"].get("metadata") or {}
        rows.append({
            "pdf": pdf.name,
            "judge_folder": pdf.parent.name,
            "mode": mode,
            "case_number_ok": matches_case_number(labels, metadata.get("case_number")),
            "decision_date_ok": same_date(labels["decision_date"], metadata.get("decision_date")),
            "judge_ok": matches_judge(labels, metadata.get("judges")),
            "has_decision_date_label": bool(labels["decision_date"]),
            "court_found": bool(metadata.get("court")),
            "counsel_found": bool(metadata.get("counsel")),
            "cover_fields": len(result["document"].get("cover_fields") or []),
            "label": labels,
            "got_case_number": metadata.get("case_number"),
            "got_decision_date": metadata.get("decision_date"),
            "got_judges": metadata.get("judges"),
        })
        if limit and len(rows) >= limit:
            break
    scratch.cleanup()
    return {"documents": len(rows), "summary": summarise_sc(rows), "rows": rows}


def summarise_sc(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if "error" not in row]
    dated = [row for row in scored if row["has_decision_date_label"]]
    def rate(subset, key):
        return round(sum(row[key] for row in subset) / len(subset), 4) if subset else None
    return {
        "scored": len(scored),
        "errors": len(rows) - len(scored),
        "case_number": rate(scored, "case_number_ok"),
        "decision_date": rate(dated, "decision_date_ok"),
        "judge": rate(scored, "judge_ok"),
        "court_coverage": rate(scored, "court_found"),
        "counsel_coverage": rate(scored, "counsel_found"),
        "zero_cover_fields": sum(1 for row in scored if not row["cover_fields"]),
    }


def render_sc(report: dict[str, Any]) -> str:
    values = report["summary"]
    lines = [f"SC documents scored: {values['scored']} (errors: {values['errors']})", ""]
    lines.append(f"{'field':<16}{'agreement':>11}   (against the label in the file path)")
    for key in ("case_number", "decision_date", "judge"):
        got = values[key]
        lines.append(f"{key:<16}{'n/a' if got is None else format(got, '.1%'):>11}")
    lines += ["", f"{'field':<16}{'coverage':>11}   (found something; correctness unknown)"]
    for key in ("court_coverage", "counsel_coverage"):
        got = values[key]
        lines.append(f"{key:<16}{'n/a' if got is None else format(got, '.1%'):>11}")
    lines.append(f"{'zero cover_fields':<16}{values['zero_cover_fields']:>11} of {values['scored']} documents")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the parser against the LHC gold pages.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark/latest.json"))
    parser.add_argument("--limit", type=int, default=None, help="score only the first N gold records")
    parser.add_argument("--no-scanned", action="store_true", help="skip the OCR route (faster; scanned slice becomes meaningless)")
    parser.add_argument("--baseline", type=Path, default=None, help="compare against a previous report")
    parser.add_argument("--sc-gold", action="store_true", help="score the reviewed SC gold pages instead of the LHC ones")
    parser.add_argument("--sc-metadata", action="store_true", help="score SC metadata against the labels in the corpus file paths instead of the LHC gold pages")
    parser.add_argument("--sc-root", type=Path, default=SC_ROOT)
    parser.add_argument("--include-scanned", action="store_true", help="--sc-metadata only: also score scanned SC documents (slow)")
    args = parser.parse_args()

    if args.sc_gold:
        report = evaluate(args.limit, include_scanned=not args.no_scanned, records=load_sc_records())
        print(render(report))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.output}")
        if args.baseline and args.baseline.exists():
            previous = json.loads(args.baseline.read_text(encoding="utf-8"))
            print("\ndelta vs baseline (negative CER = improvement)")
            for name, values in report["slices"].items():
                before = previous.get("slices", {}).get(name)
                if before:
                    print(f"  {name:<12} CER {before['cer_mean']:.4f} -> {values['cer_mean']:.4f} ({values['cer_mean'] - before['cer_mean']:+.4f})")
        return

    if args.sc_metadata:
        report = evaluate_sc_metadata(args.sc_root, args.limit, include_scanned=args.include_scanned)
        print(render_sc(report))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.output}")
        return

    report = evaluate(args.limit, include_scanned=not args.no_scanned)
    print(render(report))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.output}")

    if args.baseline and args.baseline.exists():
        previous = json.loads(args.baseline.read_text(encoding="utf-8"))
        print("\ndelta vs baseline (negative CER = improvement)")
        for name, values in report["slices"].items():
            before = previous.get("slices", {}).get(name)
            if before:
                print(f"  {name:<12} CER {before['cer_mean']:.4f} -> {values['cer_mean']:.4f} ({values['cer_mean'] - before['cer_mean']:+.4f})")


if __name__ == "__main__":
    main()
