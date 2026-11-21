"""Nemotron Parse behind the ``Engine`` protocol.

The model itself runs elsewhere (a GPU server or a batch session), so the engine
is given a ``transport``: a function that returns the model's raw output for the
pages it is asked about.  Rendering, parsing and document assembly happen here.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz

from docintel.engines.base import Policy
from docintel.engines.native import _metadata_provenance, metadata_from_pages
from docintel.engines.nemotron_batch import append_raw, ingest, load_raw, render_jobs
from docintel.engines.marks_pass import read_marks
from docintel.engines.nemotron_parse import SOURCE

@dataclass(frozen=True)
class Failure:
    """A page the model could not read this run, with why.  Not logged, so the next run tries it again."""

    reason: str


Transport = Callable[[Path, list[dict[str, Any]]], dict[int, "str | Failure"]]


def log_transport(work: Path) -> Transport:
    """Serve the raw output a GPU session left in ``<work>/raw.jsonl``."""
    return lambda pdf, jobs: load_raw(work / "raw.jsonl")


def live_transport(work: Path, read_page: Callable[[Path], str]) -> Transport:
    """Run the model now, once per page, keeping each document's output in its own log.

    ``read_page`` turns a page image into the model's raw output.  A page already
    in the document's log is not read again, so an interrupted run resumes.
    """

    def transport(pdf: Path, jobs: list[dict[str, Any]]) -> dict[int, "str | Failure"]:
        work.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(Path(pdf).resolve()).encode("utf-8", "replace")).hexdigest()[:8]
        log = work / f"{Path(pdf).stem}_{digest}.jsonl"
        done = load_raw(log)
        failed: dict[int, "str | Failure"] = {}
        for job in jobs:
            if job["page_number"] in done:
                continue
            try:
                append_raw(log, job["page_number"], read_page(Path(job["image_path"])))
            except Exception as error:  # one page the model cannot read must not lose the others
                failed[job["page_number"]] = Failure(f"{type(error).__name__}: {error}")
        return {**load_raw(log), **failed}

    return transport


class PagesPending(RuntimeError):
    """The transport has no model output yet for some pages."""

    def __init__(self, pages: list[int]) -> None:
        super().__init__(f"no model output yet for pages {pages}")
        self.pages = pages


def _failed_page(page: fitz.Page, reason: str) -> dict[str, Any]:
    return {**_unread_page(page), "extraction": "failed", "failure": reason}


def _unread_page(page: fitz.Page) -> dict[str, Any]:
    """Geometry only: a page another engine will fill in (see ``_merge_native_pages``)."""
    return {
        "page_number": page.number + 1,
        "width": float(page.rect.width),
        "height": float(page.rect.height),
        "rotation": int(page.rotation),
        "blocks": [],
        "markdown": "",
        "confidence": 0.0,
        "extraction": "native_text_pending",
    }


class NemotronEngine:
    name = "nemotron-parse"
    policy: Policy = "self_hosted"

    def __init__(self, transport: Transport, mark_reader: Callable[[Path], str] | None = None) -> None:
        self._transport = transport
        self._mark_reader = mark_reader

    def parse(
        self, pdf: Path, output_dir: Path | None, *, stem: str | None = None, pages: list[int] | None = None
    ) -> dict[str, Any]:
        with fitz.open(pdf) as doc, tempfile.TemporaryDirectory() as scratch:
            wanted = pages if pages is not None else list(range(1, doc.page_count + 1))
            jobs = render_jobs(pdf, wanted, (output_dir or Path(scratch)) / "images")
            output = self._transport(pdf, jobs)
            failures = {number: item.reason for number, item in output.items() if isinstance(item, Failure)}
            read, pending = ingest(jobs, {number: item for number, item in output.items() if not isinstance(item, Failure)})
            pending = [job for job in pending if job["page_number"] not in failures]
            if pending:
                raise PagesPending([job["page_number"] for job in pending])
            if failures and not read:
                raise RuntimeError(next(iter(failures.values())))  # nothing read at all: let the pipeline fall back
            for page in read:
                if self._mark_reader is not None:
                    read_marks(page, next(job for job in jobs if job["page_number"] == page["page_number"]), self._mark_reader)
            by_number = {page["page_number"]: page for page in read}
            document_pages = [
                by_number.get(page.number + 1)
                or (_failed_page(page, failures[page.number + 1]) if page.number + 1 in failures else _unread_page(page))
                for page in doc
            ]
            pdf_metadata = dict(doc.metadata or {})

        blocks = [block for page in document_pages for block in page["blocks"]]
        for order, block in enumerate(blocks):
            block["document_reading_order"] = order
        metadata, cover_fields = metadata_from_pages(
            document_pages, "\n".join(block["text"] for block in blocks), pdf_metadata
        )
        return {
            "schema_version": "specter.v1",
            "document": {
                "source_file": str(pdf),
                "source_name": Path(pdf).name,
                "engine": SOURCE,
                "extraction_mode": "scanned",
                "page_count": len(document_pages),
                "metadata": metadata,
                "metadata_provenance": _metadata_provenance(metadata, document_pages),
                "cover_fields": cover_fields,
                "confidence": 0.0,
                "stats": {"blocks": len(blocks)},
                "warnings": [],
            },
            "pages": document_pages,
            "markdown": "\n\n---\n\n".join(page["markdown"] for page in document_pages if page["markdown"]),
        }
