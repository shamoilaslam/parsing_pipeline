"""Score Nemotron Parse's boxes on digital pages, where the PDF already knows the answer.

A born-digital page carries exact text boxes in its own text layer, so the model
can be scored on rendered pages of those PDFs with no annotation: the native
parser's blocks stand in for gold.  Layout scores come from ``eval.layout_metrics``.

    python -m eval.box_check case.pdf --work work/

reads ``work/raw.jsonl`` as written by ``docintel.engines.nemotron_gpu``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from docintel.engines.local import NativeEngine
from docintel.engines.nemotron_batch import load_raw, render_jobs
from docintel.engines.nemotron_parse import build_page
from eval.layout_metrics import evaluate_page


# The layout release gate from docs/evaluation.md; every score must be strictly above.
GATE = {"f1": 0.90, "mean_iou": 0.85, "tau": 0.95}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean of each score over the pages where it is defined, and whether the gate is met."""
    summary: dict[str, Any] = {}
    for name in GATE:
        defined = [row[name] for row in rows if row[name] is not None]
        summary[name] = sum(defined) / len(defined) if defined else None
    summary["passes_gate"] = all(summary[name] is not None and summary[name] > limit for name, limit in GATE.items())
    return summary


def check_pdf(pdf: Path, raw_by_page: dict[int, str], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    native = {page["page_number"]: page["blocks"] for page in NativeEngine().parse(pdf, None)["pages"]}
    rows = []
    for job in jobs:
        number = job["page_number"]
        if number not in raw_by_page:
            continue
        predicted = build_page(
            raw_by_page[number], page_number=number, image_size=job["image_size"], page_size=job["page_size"]
        )["blocks"]
        rows.append({"page_number": number, **asdict(evaluate_page(predicted, native[number]))})
    return rows


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)

    raw = load_raw(args.work / "raw.jsonl")
    jobs = render_jobs(args.pdf, sorted(raw), args.work / "images")
    rows = check_pdf(args.pdf, raw, jobs)
    for row in rows:
        scores = " ".join(f"{name}={_fmt(row[name])}" for name in ("precision", "recall", "f1", "mean_iou", "tau"))
        print(f"page {row['page_number']}: {scores}")
    summary = summarise(rows)
    print(
        "summary: "
        + " ".join(f"{name}={_fmt(summary[name])}" for name in GATE)
        + f" passes_gate={summary['passes_gate']}"
    )
    return 0 if summary["passes_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
