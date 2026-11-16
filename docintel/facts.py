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
import unicodedata
from pathlib import Path
from typing import Any

import fitz

from docintel import __version__
from docintel.engines.base import Policy
from docintel.parsed import documents

PARSER = "docintel"
PAGE_IMAGE_ZOOM = 1.5
SKEW_FLAG_DEGREES = 1.0
BLOCK_TYPES = {"heading": "heading", "list": "list_item", "footnote": "footnote", "table": "table"}
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


def _rows(text: str) -> list[list[dict[str, Any]]]:
    lines = [line.strip() for line in text.splitlines() if "|" in line and not TABLE_SEPARATOR.match(line)]
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
            "language": block.get("language", "unknown")}
    if _unread_urdu(block):
        return {**base, "type": "illegible", "text": "", "confidence": 0.0}
    kind = BLOCK_TYPES.get(block.get("type"), "paragraph")
    text = _clean(block["text"], from_ocr)
    if kind == "table" and (rows := _rows(text)):
        return {**base, "type": kind, "rows": rows}
    return {**base, "type": kind, "text": text}


def _shares(blocks: list[dict[str, Any]]) -> dict[str, float]:
    weights: dict[str, int] = {}
    for block in blocks:
        text = block.get("text") or ""
        language = "ur" if block["language"] == "ur" else "en"
        weights[language] = weights.get(language, 0) + len(text)
    total = sum(weights.values())
    return {language: round(weight / total, 3) for language, weight in weights.items() if weight} if total else {}


def _body_text(block: dict[str, Any]) -> str:
    if "rows" in block:
        return "\n".join("\t".join(cell["text"] for cell in row) for row in block["rows"])
    return block.get("text", "")


def _page(page: dict[str, Any], image: str | None) -> dict[str, Any]:
    extraction = page.get("extraction")
    from_ocr = extraction != "digital"
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
    blocks = [_block(block, order, page, from_ocr) for order, block in enumerate(body)]
    read = [block for block in blocks if block["type"] != "illegible" and _body_text(block).strip()]
    weight = sum(len(_body_text(block)) for block in read)
    score = round(sum(block["confidence"] * len(_body_text(block)) for block in read) / weight, 3) if weight else 0.0

    flags = []
    if not blocks:
        flags.append("blank")
    if any(block["type"] == "illegible" for block in blocks):
        flags.append("urdu_unread")
    skew = ((page.get("scan") or {}).get("preprocessing") or {}).get("estimated_skew_degrees") or 0.0
    if abs(skew) > SKEW_FLAG_DEGREES:
        flags.append("skewed")
    engine = next((block.get("source") for block in ordered if block.get("source")), None) if from_ocr else None
    return {
        **out, "status": "ok", "source": "ocr" if from_ocr else "text_layer",
        **({"engine": engine} if engine else {}),
        "language": _shares(blocks),
        "quality": {"score": score, "ocr_confidence": score if from_ocr and read else None, "flags": flags},
        "printed_page_number": _printed_number(edge["footer"], edge["header"]),
        "header_text": edge["header"], "footer_text": edge["footer"],
        "text": "\n\n".join(text for block in blocks if (text := _body_text(block).strip())),
        "blocks": blocks,
    }


def facts_file(result: dict[str, Any], *, pdf: Path | None = None, folder: Path | None = None,
               seconds: float | None = None) -> dict[str, Any]:
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
        "file": {"name": document.get("source_name"), "sha256": document.get("id"), "mime": "application/pdf",
                 "pages": len(result["pages"]), "parser": PARSER, "parser_version": __version__, "seconds": seconds},
        "metadata": {"email": None, "docx": None},
        "pages": [_page(page, images.get(page["page_number"])) for page in result["pages"]],
    }


def write_folder(parsed: Path, out: Path, *, urdu_ceiling: Policy = "local") -> list[Path]:
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
        if urdu_ceiling != "local":
            from docintel.engines import urdu_vision

            work = folder / "_urdu_work"
            work.mkdir(exist_ok=True)
            copy = work / f"{stem}.json"
            copy.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            urdu_vision.run(pdf, copy, work, ceiling=urdu_ceiling, allow_llamaparse=True)
            document = json.loads(copy.read_text(encoding="utf-8"))
        path = folder / f"{stem}.json"
        path.write_text(json.dumps(facts_file(document, pdf=pdf, folder=folder), ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(path)
    return written
