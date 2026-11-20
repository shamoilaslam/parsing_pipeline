"""One command for the whole job: a folder of PDFs in, a parsed folder out.

    python -m docintel run /path/to/pdfs

Parsed output goes to ``/path/to/pdfs_parsed`` (override with ``--out``): per
document ``<stem>.json``, ``<stem>.md`` and ``metadata/<stem>.json``, plus
``manifest.json``, a ``report.json`` summary, ``document_types.json`` and, for each
judgment, order or act, Akoma Ntoso-style JSON in ``akn/<stem>.json``, and for the fact-intelligence
pipeline ``facts/<stem>/<stem>.json`` with the page images.  Images and
Word files are converted first.  Sub-folders are included and an interrupted run is
resumed, not restarted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from docintel import pipeline, report
from docintel.akn.build import write_akn
from docintel.facts import write_folder
from docintel.engines.nemotron_gpu import default_model
from docintel.engines.urdu_local import hf_reader
from docintel.ingest import normalize_folder
from docintel.understand.retract import retract_non_court
from docintel.understand.segment import classify_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse every PDF under a folder into a parsed folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, help="default: <folder>_parsed, beside the input folder")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1), help="documents parsed in parallel")
    parser.add_argument("--engine", choices=["auto", "digital", "scanned", "nemotron"], default="auto")
    parser.add_argument(
        "--nemotron-work", type=Path, help="folder holding the GPU session's raw.jsonl; for --engine nemotron"
    )
    parser.add_argument("--nemotron-model", help="Nemotron Parse hub id or folder; default: the hub model when a GPU is present")
    parser.add_argument(
        "--urdu-policy", choices=["local", "third_party"], default="third_party",
        help="how far Urdu crops may travel for the fact-intelligence layer: third_party sends them to Gemini, then "
        "LlamaParse (pages the sensitivity check flags stay local); local sends nothing and leaves Urdu illegible",
    )
    parser.add_argument(
        "--urdu-local-model", nargs="?", const="Qwen/Qwen3-VL-8B-Instruct",
        help="read Urdu with a vision model on this machine (a hub id or folder); it is loaded only when a crop needs it. "
        "A crop is kept only if two readings of it agree; with --urdu-policy local nothing leaves the machine",
    )
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    out = args.out or args.folder.with_name(f"{args.folder.name}_parsed")

    normalized, ingest_failures = normalize_folder(args.folder, out / "_normalized")
    argv = [
        str(args.folder),
        *([str(normalized)] if normalized else []),
        "--out",
        str(out),
        "--recursive",
        "--skip-existing",
        "--quiet",
        "--workers",
        str(args.workers),
        "--engine",
        args.engine,
    ]
    if args.nemotron_work:
        argv += ["--nemotron-work", str(args.nemotron_work)]
    model = args.nemotron_model or default_model()
    if model:
        argv += ["--nemotron-model", model]
    saved = sys.argv
    sys.argv = ["docintel parse", *argv]
    try:
        pipeline.main()
    finally:
        sys.argv = saved

    if ingest_failures:
        (out / "ingest_failures.json").write_text(
            json.dumps(ingest_failures, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    types = classify_folder(out)
    (out / "document_types.json").write_text(json.dumps(types, ensure_ascii=False, indent=2), encoding="utf-8")
    retract_non_court(out, types)
    write_akn(out, types)
    write_folder(out, out / "facts", urdu_ceiling=args.urdu_policy, mark_reader=pipeline.model_reader(model) if model else None,
                 urdu_reader=hf_reader(args.urdu_local_model) if args.urdu_local_model else None)
    summary = report.summarise(report.load(out), manifest)
    (out / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report.render(summary))
    print(f"\nparsed folder: {out}")
    return 1 if manifest["failed"] or ingest_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
