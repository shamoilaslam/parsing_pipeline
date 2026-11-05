"""One command for the whole job: a folder of PDFs in, a parsed folder out.

    python -m docintel run /path/to/pdfs

Parsed output goes to ``/path/to/pdfs_parsed`` (override with ``--out``): per
document ``<stem>.json``, ``<stem>.md`` and ``metadata/<stem>.json``, plus
``manifest.json`` and a ``report.json`` summary.  Sub-folders are included and
an interrupted run is resumed, not restarted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from docintel import pipeline, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse every PDF under a folder into a parsed folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, help="default: <folder>_parsed, beside the input folder")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1), help="documents parsed in parallel")
    parser.add_argument("--engine", choices=["auto", "digital", "scanned", "nemotron"], default="auto")
    parser.add_argument(
        "--nemotron-work", type=Path, help="folder holding the GPU session's raw.jsonl; for --engine nemotron"
    )
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    out = args.out or args.folder.with_name(f"{args.folder.name}_parsed")

    argv = [
        str(args.folder),
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
    saved = sys.argv
    sys.argv = ["docintel parse", *argv]
    try:
        pipeline.main()
    finally:
        sys.argv = saved

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    summary = report.summarise(report.load(out), manifest)
    (out / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report.render(summary))
    print(f"\nparsed folder: {out}")
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
