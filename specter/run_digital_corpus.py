"""Run the deterministic digital parser on only fully digital PDFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from specter.ingest_pdfs import classify_pdf
from specter.specter_parser import SpecterParser, write_result
from specter.validate_document import validate


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse every fully digital PDF in a directory.")
    parser.add_argument("--input", type=Path, default=Path("data/pdfs"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/digital"))
    parser.add_argument("--rtl-mode", choices=["raw", "image", "both"], default="image")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    validation_dir = args.out / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    parser_engine = SpecterParser(rtl_mode=args.rtl_mode)
    summary = []
    skipped = []

    for pdf in sorted(args.input.glob("*.pdf")):
        route = classify_pdf(pdf)
        if route["mode"] != "digital":
            skipped.append({"file": pdf.name, "mode": route["mode"], "scan_pages": route["scan_pages"]})
            continue
        result = parser_engine.parse(pdf, args.out)
        result["document"]["router"] = {"requested": "digital-only", "selected": "digital", **route}
        report = validate(result)
        json_path, md_path = write_result(result, args.out)
        validation_path = validation_dir / f"{pdf.stem}.json"
        validation_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        diagnostics = result["document"]["diagnostics"]
        summary.append({
            "file": pdf.name,
            "json": str(json_path),
            "markdown": str(md_path),
            "validation": str(validation_path),
            "pages": result["document"]["page_count"],
            "blocks": result["document"]["stats"]["blocks"],
            "tables": result["document"]["stats"]["tables"],
            "rtl_blocks": result["document"]["stats"]["rtl_blocks"],
            "confidence": result["document"]["confidence"],
            "quality_score": diagnostics["quality_score"],
            "status": diagnostics["status"],
            "failed_components": diagnostics["failed_components"],
            "template_key": result["document"]["fingerprint"]["template_key"],
        })
        print(json.dumps(summary[-1], ensure_ascii=False))

    corpus = {
        "input": str(args.input),
        "output": str(args.out),
        "processed_digital": len(summary),
        "skipped_non_digital": skipped,
        "documents": summary,
    }
    (args.out / "corpus_summary.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"processed_digital": len(summary), "skipped": len(skipped), "summary": str(args.out / 'corpus_summary.json')}))


if __name__ == "__main__":
    main()
