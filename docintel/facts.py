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

import cv2
import fitz

from docintel import __version__
from docintel.engines.base import Policy
from docintel.engines.marks_pass import MarkReader, read_marks
from docintel.engines.urdu_local import Reader
from docintel.ingest.email import sidecar_path
from docintel.ingest.office import docx_sidecar
from docintel.parsed import documents
from docintel.lines import line_boxes
from docintel.skew import estimate_skew

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


def _split_by_width(text: str, boxes: list[list[float]]) -> list[str]:
    """Hand the words of one long line to the visual lines in proportion to their widths.  No word is changed or lost."""
    words = text.split()
    widths = [box[2] - box[0] for box in boxes]
    total, cumulative, parts, start = sum(widths), 0.0, [], 0
    for index, width in enumerate(widths):
        cumulative += width
        end = len(words) if index == len(widths) - 1 else round(len(words) * cumulative / total)
        parts.append(" ".join(words[start:end]))
        start = end
    return parts


def _lines(block: dict[str, Any], box: list[float], text: str, page: dict[str, Any], picture: Any) -> list[dict[str, Any]]:
    """Each line of the block's text with a box: the text layer's own, else measured on the page image, else an even split."""
    texts = [line.strip() for line in text.split(chr(10)) if line.strip()]
    if not texts:
        return []
    width, height = page["width"], page["height"]
    own = block.get("line_bboxes") or []
    if len(own) == len(texts) > 1:
        return [{"text": line, "bbox": _fractions(bbox, width, height)} for line, bbox in zip(texts, own)]
    x0, y0, x1, y1 = block["bbox"]
    measured = None  # boxes in page points, found from the image
    if picture is not None:
        sx, sy = picture.shape[1] / width, picture.shape[0] / height
        left, top, right, bottom = int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)
        if right > left and bottom > top:
            found = line_boxes(picture[max(0, top) : bottom, max(0, left) : right], len(texts) if len(texts) > 1 else None)
            if found:
                measured = [[(left + a) / sx, (top + b) / sy, (left + c) / sx, (top + d) / sy] for a, b, c, d in found]
    if len(texts) > 1:
        if measured:
            return [{"text": line, "bbox": _fractions(bbox, width, height)} for line, bbox in zip(texts, measured)]
    else:
        boxes = own if len(own) > 1 else measured
        if boxes and len(boxes) > 1:
            return [{"text": part, "bbox": _fractions(bbox, width, height), "approximate": True}
                    for part, bbox in zip(_split_by_width(texts[0], boxes), boxes)]
        return [{"text": texts[0], "bbox": box}]
    step = (y1 - y0) / len(texts)
    return [{"text": line, "bbox": _fractions([x0, y0 + index * step, x1, y0 + (index + 1) * step], width, height), "approximate": True}
            for index, line in enumerate(texts)]


def _block(block: dict[str, Any], order: int, page: dict[str, Any], from_ocr: bool, picture: Any = None) -> dict[str, Any]:
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
    if kind in ("paragraph", "heading", "list_item", "footnote"):
        return {**base, "type": kind, "text": text, "lines": _lines(block, box, text, page, picture)}
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


def _page(page: dict[str, Any], image: str | None, image_file: Path | None = None) -> dict[str, Any]:
    extraction = page.get("extraction")
    out: dict[str, Any] = {
        "page": page["page_number"], "width": page["width"], "height": page["height"], "unit": "pt",
        "rotation": page.get("rotation", 0), "image": image,
    }
    if extraction in ("native_text_pending", "failed"):
        return {**out, "status": "failed", "reason": page.get("failure") or "no engine read this page", "source": "ocr", "blocks": [],
                "text": "", "language": {}, "quality": {"score": 0.0, "ocr_confidence": None, "flags": [], "skew_degrees": None}}

    ordered = sorted(page["blocks"], key=lambda block: block.get("reading_order", 0))
    edge = {kind: " ".join(block["text"] for block in ordered if block.get("type") == kind) for kind in ("header", "footer")}
    body = [block for block in ordered if block.get("type") not in ("header", "footer")]
    def from_ocr(block: dict[str, Any]) -> bool:
        return block["source"] not in TEXT_LAYER_SOURCES if block.get("source") else extraction != "digital"

    picture = cv2.imread(str(image_file)) if image_file is not None else None
    blocks = [_block(block, order, page, from_ocr(block), picture) for order, block in enumerate(body)]
    ocr_blocks = [block for block in body if from_ocr(block)]
    kind = "mixed" if ocr_blocks and len(ocr_blocks) < len(body) else "ocr" if ocr_blocks or extraction != "digital" else "text_layer"
    read = [block for block in blocks if block["type"] != "illegible" and _body_text(block).strip()]
    weight = sum(len(_body_text(block)) for block in read)
    score = round(sum(block["confidence"] * len(_body_text(block)) for block in read) / weight, 3) if weight else 0.0

    # What the model or OCR engine read, apart from what the text layer gave: marks (stamps, notes) count here too.
    ocr_read = [(each, raw) for each, raw in zip(blocks, body) if from_ocr(raw) and each["type"] != "illegible" and (_body_text(each).strip() or each["type"] in APART)]
    weight_ocr = sum(max(len(_body_text(each)), 1) for each, _ in ocr_read)
    ocr_confidence = round(sum(each["confidence"] * max(len(_body_text(each)), 1) for each, _ in ocr_read) / weight_ocr, 3) if weight_ocr else None

    flags = []
    if not any(block["type"] not in APART for block in blocks):
        flags.append("blank")
    if any(_unread_urdu(block) for block in body):
        flags.append("urdu_unread")
    if any(block.get("type") == "illegible" for block in body):
        flags.append("unaccounted_ink")
    flags += [kind for kind in ("stamp", "handwriting", "signature") if any(block["type"] == kind for block in blocks)]
    skew = None
    if image_file is not None and kind != "text_layer":
        skew = estimate_skew(picture) if picture is not None else None
    if skew is None:
        skew = ((page.get("scan") or {}).get("preprocessing") or {}).get("estimated_skew_degrees")
    if skew is not None and abs(skew) > SKEW_FLAG_DEGREES:
        flags.append("skewed")
    engine = next((block["source"] for block in ocr_blocks if block.get("source")), None)
    return {
        **out, "status": "ok", "source": kind,
        **({"engine": engine} if engine else {}),
        "language": _shares(blocks),
        "quality": {"score": score, "ocr_confidence": ocr_confidence, "flags": flags,
                    "skew_degrees": skew},
        "printed_page_number": _printed_number(edge["footer"], edge["header"]),
        "header_text": edge["header"], "footer_text": edge["footer"],
        "text": "\n\n".join(
            text for block in blocks if block["type"] not in APART and (text := _body_text(block).strip())
        ),
        "blocks": blocks,
    }


EMAIL_FIELDS = ("from", "to", "cc", "date", "subject", "attachments")


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _mime(email_meta: dict[str, Any] | None, docx_meta: dict[str, Any] | None) -> str:
    return "message/rfc822" if email_meta else DOCX_MIME if docx_meta else "application/pdf"


def facts_file(result: dict[str, Any], *, pdf: Path | None = None, folder: Path | None = None,
               seconds: float | None = None, email_meta: dict[str, Any] | None = None,
               docx_meta: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "file": {"name": (document.get("source_name") or "").removesuffix(".pdf") if (email_meta or docx_meta) else document.get("source_name"),
                 "sha256": document.get("id"), "mime": _mime(email_meta, docx_meta),
                 "pages": len(result["pages"]), "parser": PARSER, "parser_version": __version__, "seconds": seconds},
        "metadata": {"email": {key: email_meta.get(key) for key in EMAIL_FIELDS} if email_meta else None, "docx": docx_meta or None},
        "pages": [
            _page(page, images.get(page["page_number"]), folder / images[page["page_number"]] if page["page_number"] in images else None)
            for page in result["pages"]
        ],
    }


def _seconds(parsed: Path, stem: str) -> float | None:
    """How long the parse of this file took, as the run recorded it."""
    path = parsed / "timings" / f"{stem}.json"
    return json.loads(path.read_text(encoding="utf-8")).get("seconds") if path.exists() else None


def _docx_meta(pdf: Path) -> dict[str, Any] | None:
    sidecar = docx_sidecar(pdf)
    return json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None


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
        path.write_text(json.dumps(facts_file(document, pdf=pdf, folder=folder, email_meta=_email_meta(pdf), docx_meta=_docx_meta(pdf), seconds=_seconds(parsed, stem)), ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(path)
    return written
