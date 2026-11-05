"""Run Nemotron Parse in a GPU session without a GPU here.

The model needs an NVIDIA GPU this machine may not have for the whole day, so
the work is split: ``render_jobs`` prepares page images and a manifest on this
machine, the GPU script turns them into raw model output, and ``ingest`` reads
that back.  Only the middle step needs the GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz

from docintel.engines.nemotron_parse import build_page

# 2x is 144 dpi: an A4 or legal page fits the model's canvas without shrinking
# and stays above its minimum input size.
RENDER_ZOOM = 2.0


def render_jobs(pdf: Path, pages: list[int], out_dir: Path) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    with fitz.open(pdf) as doc:
        for page_number in pages:
            page = doc[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
            image = f"{Path(pdf).stem}_p{page_number}.png"
            pixmap.save(out_dir / image)
            jobs.append(
                {
                    "page_number": page_number,
                    "image": image,
                    "image_size": (pixmap.width, pixmap.height),
                    "page_size": (float(page.rect.width), float(page.rect.height)),
                }
            )
    return jobs


def ingest(
    jobs: list[dict[str, Any]], raw_by_page: dict[int, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pages for the jobs that have model output, and the jobs that do not yet."""
    pages = [
        build_page(
            raw_by_page[job["page_number"]],
            page_number=job["page_number"],
            image_size=job["image_size"],
            page_size=job["page_size"],
        )
        for job in jobs
        if job["page_number"] in raw_by_page
    ]
    pending = [job for job in jobs if job["page_number"] not in raw_by_page]
    return pages, pending


def append_raw(log: Path, page_number: int, raw: str) -> None:
    """Record one page's output the moment it exists, so a cut-off session loses nothing."""
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"page_number": page_number, "raw": raw}, ensure_ascii=False) + "\n")


def load_raw(log: Path) -> dict[int, str]:
    if not log.exists():
        return {}
    raws = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # the line a killed session was writing
        raws[record["page_number"]] = record["raw"]
    return raws
