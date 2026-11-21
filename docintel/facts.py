"""The output the fact-intelligence pipeline reads: one JSON per file, per page.

A view of the parse result, written for a reader that must never be given invented text:

* text is the page's own words; model markup is removed, nothing is corrected or normalised
  (Urdu is put in NFC, which changes no letter);
* text no engine could read is an ``illegible`` block with its position and no text;
* every block has a box as page fractions ``[x, y, w, h]`` from the top left;
* a page that could not be read fails alone, with a reason.
"""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import fitz

from docintel import __version__
from docintel.engines.base import Policy
from docintel.engines.marks_pass import MarkReader, read_marks
from docintel.engines.urdu_local import Reader
from docintel.ingest.email import sidecar_path
from docintel.parsed import documents

PARSER = "docintel"
SECOND_LOOK_ZOOM = 2.0
PAGE_IMAGE_ZOOM = 1.5
SKEW_FLAG_DEGREES = 1.0
BLOCK_TYPES = {"heading": "heading", "list": "list_item", "footnote": "footnote", "table": "table", "stamp": "stamp",
               "handwriting": "handwriting", "illegible": "illegible", "signature": "signature"}
# Sources that are the PDF's own text layer; any other source is a model or an OCR engine reading pixels.
TEXT_LAYER_SOURCES = {"pymupdf-native", "pymupdf-find_tables"}
# Blocks that are part of the page but not of its body text.
APART = {"stamp", "handwriting", "signature"}
PRINTED_NUMBER = (re.compile(r"(?i)\bpage\s+(\d{1,4})\b"), re.compile(r"(?i)page\s*(\d{1,4})\s*$"),re.compile(r"^\W*(\d{1,4})\W*$"), re.compile(r"^\s*(\d{1,3})\s+[A-Za-z]"))
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
HEADING_MARK = re.compile(r"(?m)^#{1,6}\s+")
TABLE_SEPARATOR = re.compile(r"^[\s|:\-]+$")


def _unread_urdu(block: dict[str, Any]) -> bool:
    """Urdu the engine returned but cannot read, and no vision stage has replaced."""
    reasons = (block.get("confidence") or {}).get("reasons") or []
    return "urdu_unreliable_for_engine" in reasons and not (block.get("rtl") or {}).get("vision")


def _clean(text: str, from_ocr: bool) -> str:
    if from_ocr:
        text = HEADING_MARK.sub("", BOLD.sub(lambda match: match.group(1) or match.group(2), text))
    return unicodedata.normalize("NFC", text) if any(ord(char) > 0x590 for char in text) else text


LATEX_ROW_END = re.compile(r"\\\\\s*(?:\n|$)")
LATEX_HEAD = re.compile(r"\\thead\{(.*?)\}", re.DOTALL)
LATEX_ESCAPE = re.compile(r"\\([%&$#_])")


def _latex_rows(text: str) -> list[list[dict[str, Any]]]:
    """Rows of a ``tabular`` the model wrote as LaTeX: markup removed, the cell words untouched."""
    body = re.sub(r"\\(?:begin|end)\{tabular\}(?:\{[^}]*\})?", "", text)
    rows = []
    for line in LATEX_ROW_END.split(body):
        if line.strip():
            cells = [LATEX_ESCAPE.sub(r"\1", LATEX_HEAD.sub(r"\1", cell)).strip() for cell in line.split("&")]
            rows.append([{"text": cell, "bbox": []} for cell in cells])
    return rows


def _rows(text: str) -> list[list[dict[str, Any]]]:
    if "\\begin{tabular}" in text:
        return _latex_rows(text)
    lines =[line.strip() for line in text.splitlines() if "|" in line and not TABLE_SEPARATOR.match(line)]
    return [[{"text": cell.strip(), "bbox": []} for cell in line.strip("|").split("|")] for line in lines]


def _fractions(bbox: list[float], width: float, height: float) -> list[float]:
    x0, y0, x1, y1 = bbox
    return [round(x0 / width, 4), round(y0 / height, 4), round((x1 - x0) / width, 4), round((y1 - y0) / height, 4)]


def _printed_number(*texts: str) -> str | None:
    for text in texts:
        for pattern in PRINTED_NUMBER:
            if match := pattern.search(text.strip()):
                return match.group(1)
    return None


def _block(block: dict[str, Any], order: int, page: dict[str, Any], from_ocr: bool) -> dict[str, Any]:
    box = _fractions(block["bbox"], page["width"], page["height"])
    score = round(float((block.get("confidence") or {}).get("score", 0.0)), 3)
    base = {"id": block.get("id"), "order": order, "bbox": box, "confidence": score,
            "language": _language(block.get("text") or "") if block.get("text") else block.get("language", "unknown")}
    if _unread_urdu(block) or block.get("type") == "illegible":
        return {**base, "type": "illegible", "text": "", "confidence": 0.0}
    kind = BLOCK_TYPES.get(block.get("type"), "paragraph")
    text = _clean(block["text"], from_ocr)
    if kind == "table" and (rows := _rows(text)):
        return {**base, "type": kind, "rows": rows}
    return {**base, "type": kind, "text": text}


def _letters(text: str) -> tuple[int, int]:
    """(Urdu-script letters, other letters) in the text."""
    urdu = sum(1 for char in text if char.isalpha() and ord(char) >= 0x600)
    return urdu, sum(1 for char in text if char.isalpha()) - urdu


def _language(text: str) -> str:
    urdu, other = _letters(text)
    return "ur" if urdu > other else "en"


def _shares(blocks: list[dict[str, Any]]) -> dict[str, float]:
    """The share of letters in each language, from the text itself: a label can be stale after Urdu is read."""
    urdu = other = 0
    for block in blocks:
        block_urdu, block_other = _letters(block.get("text") or "")
        urdu, other = urdu + block_urdu, other + block_other
    total = urdu + other
    shares = {"en": round(other / total, 3), "ur": round(urdu / total, 3)} if total else {}
    return {language: share for language, share in shares.items() if share}


def _body_text(block: dict[str, Any]) -> str:
    if "rows" in block:
        return "\n".join("\t".join(cell["text"] for cell in row) for row in block["rows"])
    return block.get("text", "")


def _page(page: dict[str, Any], image: str | None) -> dict[str, Any]:
    extraction = page.get("extraction")
    out: dict[str, Any] = {
        "page": page["page_number"], "width": page["width"], "height": page["height"], "unit": "pt",
        "rotation": page.get("rotation", 0), "image": image,
    }
    if extraction == "native_text_pending":
        return {**out, "status": "failed", "reason": "no engine read this page", "source": "ocr", "blocks": [],
                "text": "", "language": {}, "quality": {"score": 0.0, "ocr_confidence": None, "flags": []}}

    ordered = sorted(page["blocks"], key=lambda block: block.get("reading_order", 0))
    edge = {kind: " ".join(block["text"] for block in ordered if block.get("type") == kind) for kind in ("header", "footer")}
    body = [block for block in ordered if block.get("type") not in ("header", "footer")]
    def from_ocr(block: dict[str, Any]) -> bool:
        return block["source"] not in TEXT_LAYER_SOURCES if block.get("source") else extraction != "digital"

    blocks = [_block(block, order, page, from_ocr(block)) for order, block in enumerate(body)]
    ocr_blocks = [block for block in body if from_ocr(block)]
    kind = "mixed" if ocr_blocks and len(ocr_blocks) < len(body) else "ocr" if ocr_blocks or extraction != "digital" else "text_layer"
    read = [block for block in blocks if block["type"] != "illegible" and _body_text(block).strip()]
    weight = sum(len(_body_text(block)) for block in read)
    score = round(sum(block["confidence"] * len(_body_text(block)) for block in read) / weight, 3) if weight else 0.0

    flags = []
    if not any(block["type"] not in APART for block in blocks):
        flags.append("blank")
    if any(_unread_urdu(block) for block in body):
        flags.append("urdu_unread")
    if any(block.get("type") == "illegible" for block in body):
        flags.append("unaccounted_ink")
    flags += [kind for kind in ("stamp", "handwriting", "signature") if any(block["type"] == kind for block in blocks)]
    skew = ((page.get("scan") or {}).get("preprocessing") or {}).get("estimated_skew_degrees") or 0.0
    if abs(skew) > SKEW_FLAG_DEGREES:
        flags.append("skewed")
    engine = next((block["source"] for block in ocr_blocks if block.get("source")), None)
    return {
        **out, "status": "ok", "source": kind,
        **({"engine": engine} if engine else {}),
        "language": _shares(blocks),
        "quality": {"score": score, "ocr_confidence": score if kind == "ocr" and read else None, "flags": flags},
        "printed_page_number": _printed_number(edge["footer"], edge["header"]),
        "header_text": edge["header"], "footer_text": edge["footer"],
        "text": "\n\n".join(
            text for block in blocks if block["type"] not in APART and (text := _body_text(block).strip())
        ),
        "blocks": blocks,
    }


EMAIL_FIELDS = ("from", "to", "cc", "date", "subject", "attachments")


def facts_file(result: dict[str, Any], *, pdf: Path | None = None, folder: Path | None = None,
               seconds: float | None = None, email_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    document = result["document"]
    images: dict[int, str] = {}
    if pdf and folder:
        (folder / "pages").mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf) as source:
            for page in source:
                name = f"pages/{page.number + 1:04d}.png"
                page.get_pixmap(matrix=fitz.Matrix(PAGE_IMAGE_ZOOM, PAGE_IMAGE_ZOOM)).save(folder / name)
                images[page.number + 1] = name
    return {
        "file": {"name": (document.get("source_name") or "").removesuffix(".pdf") if email_meta else document.get("source_name"),
                 "sha256": document.get("id"), "mime": "message/rfc822" if email_meta else "application/pdf",
                 "pages": len(result["pages"]), "parser": PARSER, "parser_version": __version__, "seconds": seconds},
        "metadata": {"email": {key: email_meta.get(key) for key in EMAIL_FIELDS} if email_meta else None, "docx": None},
        "pages": [_page(page, images.get(page["page_number"])) for page in result["pages"]],
    }


def _email_meta(pdf: Path) -> dict[str, Any] | None:
    sidecar = sidecar_path(pdf)
    return json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None


def _second_look(document: dict[str, Any], pdf: Path, reader: MarkReader) -> None:
    """Stamps, notes and pasted scans on digital pages: only pages that carry an image are looked at."""
    with fitz.open(pdf) as source, tempfile.TemporaryDirectory() as scratch:
        for page in document["pages"]:
            source_page = source[page["page_number"] - 1]
            if page.get("extraction") != "digital" or not source_page.get_images(full=True):
                continue
            path = Path(scratch) / f"p{page['page_number']}.png"
            source_page.get_pixmap(matrix=fitz.Matrix(SECOND_LOOK_ZOOM, SECOND_LOOK_ZOOM)).save(path)
            read_marks(page, {"image_path": str(path), "page_size": (source_page.rect.width, source_page.rect.height)}, reader)


def write_folder(parsed: Path, out: Path, *, urdu_ceiling: Policy = "local", mark_reader: MarkReader | None = None,
                 urdu_reader: Reader | None = None) -> list[Path]:
    """One ``<stem>/<stem>.json`` plus ``pages/`` images per parsed file, built from the parsed folder.

    The parsed files are read, never changed: Urdu crops go to the vision route (Gemini, then
    LlamaParse) on a copy, and only as far as ``urdu_ceiling`` allows.  Crops a page's sensitivity
    holds back stay illegible.
    """
    written = []
    for stem, document in documents(parsed):
        folder = out / stem
        folder.mkdir(parents=True, exist_ok=True)
        pdf = Path(document["document"]["source_file"])
        if urdu_ceiling != "local" or urdu_reader is not None:
            from docintel.engines import urdu_vision

            work = folder / "_urdu_work"
            work.mkdir(exist_ok=True)
            copy = work / f"{stem}.json"
            copy.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            urdu_vision.run(pdf, copy, work, ceiling=urdu_ceiling, allow_llamaparse=True, local_reader=urdu_reader)
            document = json.loads(copy.read_text(encoding="utf-8"))
        if mark_reader:
            _second_look(document, pdf, mark_reader)
        path = folder / f"{stem}.json"
        path.write_text(json.dumps(facts_file(document, pdf=pdf, folder=folder, email_meta=_email_meta(pdf)), ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(path)
    return written
