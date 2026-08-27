"""Small, digital-first parser for Pakistani legal PDFs.

The native PDF path is deliberately conservative:
* PyMuPDF supplies text, spans, character boxes, page geometry and tables.
* Rules add legal structure, metadata, header/footer labels and confidence.
* Arabic-script blocks keep the native text plus a rendered crop.  PyMuPDF
  commonly exposes Nastaleeq glyphs in visual order, so this module never
  silently invents a logical Urdu string.

The output is stable JSON/Markdown and is intended to be the contract for a
later OCR/layout fallback, not another engine-specific intermediate format.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import statistics
from datetime import date

from specter.courts import canonical_name, court_id
from urllib.parse import quote
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import fitz


RTL_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
# Numeric paragraph markers are followed by a sentence-style capital/opening
# quote.  This avoids mistaking citation continuations such as ``17) and ...``
# for a new judgment paragraph.
LIST_RE = re.compile(r"^\s*(?:(?:\d+\s*[.)]\s+(?=[A-Z\"“‘(]))|(?:[A-Za-z]\s*[.)]\s+))")
# Consolidated cases are numbered as a run -- "Civil Appeals No.101 & 102-P of
# 2011", "NO.616 AND 617 OF 2006", "Petitions No.43 to 46/2023".  Without the
# connector clause the match stopped at the first number and the year was
# dropped, which is what made a case number ambiguous across years: agreement
# with the SC labels was 52.3% and is 72.9% with it.
# The designator is written out ("Writ Petition") or as an initialism
# ("W.P.", "C.R.", "I.C.A.", "F.A.O.", "I.T.R."), and every court has its own
# set.  Matching the *shape* of a dotted initialism covers all of them at once
# rather than growing a list of what each abbreviation happens to mean.
CASE_RE = re.compile(
    r"(?i)\b((?:civil|criminal|constitutional|constitution|writ|appeal|revision|petition"
    r"|Crl\.?|C\.P\.?|W\.?P\.?|COS|RFA|ICA|FAO|ITR|(?:[A-Z]\.){2,4})"
    # The number runs on past spaces the court leaves around its own
    # punctuation -- "No.2475 / 2018", "No.1565 -B/ 2023" -- and stopping at
    # the first space dropped the year, which is what makes a case number
    # ambiguous across years.
    r"[^\n]{0,100}?\bNos?\.?\s*[\w()\-.]+(?:\s*[/\-]\s*[\w()\-.]+)*"
    r"(?:\s*(?:&|and|to|,)\s*[\w/()\-.]+)*"
    r"(?:\s*of\s*\d{4})?)"
)
DATE_RE = re.compile(r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b")
CITATION_RE = re.compile(r"\b(?:PLD|SCMR|CLC|YLR|MLD|PCRLJ|PCrLJ)\s+\d{4}\s+[A-Z]{2,8}\s+\d+\b", re.I)

# Judgments state several dates and only one of them is the court's own.  The
# old rule took the first "decided|announced|judgment dated|dated", which on an
# SC cover is the order under appeal -- every one opens "(Against the judgment
# dated ...)".  Asking in order of how firmly the text states a date avoids
# that without needing to reason about the impugned order at all.
#
# The hearing date ranks above a bare "dated" because, measured across the SC
# corpus, it *is* the recorded decision date in 30 of the 34 documents where
# the true date appears in the text.  Agreement with the corpus labels went
# from 1.0% to 56.7% on SC, and year-agreement on LHC from 33.7% to 68.4%.
#
# A guard that skipped dates following "against"/"impugned"/"passed by" was
# tried and measured: identical on SC, worse on LHC (64.9% vs 68.4%), because
# the patterns below already resolve those covers.  It was removed rather than
# kept as inert complexity.
#
# A bare "dated <date>" was the last rung, on the reasoning that a document
# stating one date states its own.  It does not.  Measured on the 152 documents
# where it was the rung that decided -- 23 at SC, 129 at IHC -- it was right
# once.  What it actually finds is the impugned order, an FIR, or an agreement
# recited in the facts.  Worse than being wrong: a confidently wrong date also
# blocked the corpus's own correct label from filling the field, because a
# label may only fill what extraction left empty.  A document that does not
# state its date now says so.
_DATE = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
DATE_OF_JUDGMENT_RE = re.compile(rf"(?i)date\s+of\s+(?:judgment|decision|order|announcement)\s*[:\-]?\s*({_DATE})")
PRONOUNCED_RE = re.compile(rf"(?i)\b(?:decided|announced|pronounced)\b[^0-9\n]{{0,24}}({_DATE})")
HEARING_DATE_RE = re.compile(rf"(?i)dates?\s+of\s+hearing\s*[:\-]?\s*({_DATE})")


def _decision_date(text: str) -> str | None:
    """The court's own date, in order of how firmly the text states it."""
    for pattern in (DATE_OF_JUDGMENT_RE, PRONOUNCED_RE, HEARING_DATE_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None
SECTION_RE = re.compile(r"(?i)\b(?:sections?|s\.)\s+([\w(),\-– ]{1,120})")
ACT_RE = re.compile(r"(?i)\b(?:the\s+)?([A-Z][A-Za-z ]{2,80}?(?:Act|Code|Ordinance|Rules|Constitution))\b")
# Cover-page fields ("Date of hearing:", "Petitioners in Crl.Rev.No.9027/2017
# by:", "For the State:") observed across LHC judgments.  Anchored at the
# start of a block and terminated by ':' or '.' so ordinary prose that
# happens to contain "state" or "by" does not match.
COVER_LABEL_RE = re.compile(
    r"(?i)^\s*(?P<label>"
    r"dates?\s+of\s+hearing"
    r"|(?:appellants?|petitioners?|respondents?(?:\s*\(?s\)?)?|plaintiffs?|defendants?|complainants?|state)"
    r"(?:\s+(?:in|no\.?)\s*[\w .\-/]{0,40})?"
    r"\s+by"
    # SC covers write the role with an explicit plural marker -- "For the
    # Petitioner(s):" -- which the bare "petitioners?" alternative cannot
    # match, so those counsel rows were being missed entirely and the caption
    # scan walked past the label into the counsel block.
    r"|for\s+the\s+(?:appellants?|petitioners?|respondents?|applicants?|state|complainants?)(?:\s*\(\s*s\s*\))?"
    r")\s*[:.]\s*(?P<inline>.*)$"
)
# The ORDER SHEET proceedings-log header (E5/E6): a single-row, 3-column form
# box that PyMuPDF's find_tables() detects correctly but whose bottom border
# genuinely closes after the header -- there are no body rows to recover.
ORDER_SHEET_RE = re.compile(r"(?i)order\s*/?\s*proceeding|signature of (?:the )?judge")


def _is_rtl(char: str) -> bool:
    cp = ord(char)
    return any(start <= cp <= end for start, end in RTL_RANGES)


def _script(text: str) -> str:
    rtl = sum(_is_rtl(c) for c in text)
    latin = sum(("A" <= c <= "Z") or ("a" <= c <= "z") for c in text)
    if rtl and latin:
        return "mixed"
    if rtl:
        return "ur"
    return "en"


def _bbox(values: Iterable[Iterable[float]]) -> list[float]:
    boxes = [list(map(float, b)) for b in values if b and len(b) >= 4]
    if not boxes:
        return []
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]) if len(box) == 4 else 0.0


def _intersection(a: list[float], b: list[float]) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _overlap_ratio(inner: list[float], outer: list[float]) -> float:
    area = _area(inner)
    return _intersection(inner, outer) / area if area else 0.0


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\u00a0", " ")).strip()


def _normal_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _line_text(line: dict[str, Any]) -> str:
    return "".join(ch.get("c", "") for span in line.get("spans", []) for ch in span.get("chars", []))


def _line_chars(line: dict[str, Any]) -> list[dict[str, Any]]:
    return [ch for span in line.get("spans", []) for ch in span.get("chars", [])]


SAME_LINE_GAP_LIMIT = 20.0


def _coalesce_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join same-baseline line fragments emitted as separate rawdict lines.

    A font/style change mid-sentence is exported as adjacent rawdict lines
    that share a baseline with no real gap between them.  A label/value form
    (``Petitioner by:`` ... ``M/s. Dr. Malik ...``) shares the same baseline
    too, but the two fragments sit in different columns tens of points apart.
    Without the horizontal gap check, both were joined into one fragment: the
    resulting bbox spanned the blank gutter between the columns, and the
    label and value could never be told apart again downstream (E5/E6).
    """
    groups: list[list[dict[str, Any]]] = []
    for line in lines:
        bbox = line.get("bbox", (0, 0, 0, 0))
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        if groups:
            previous = groups[-1]
            previous_bbox = previous[-1].get("bbox", (0, 0, 0, 0))
            px0, py0, px1, py1 = float(previous_bbox[0]), float(previous_bbox[1]), float(previous_bbox[2]), float(previous_bbox[3])
            overlap = max(0.0, min(y1, py1) - max(y0, py0)) / max(1.0, min(y1 - y0, py1 - py0))
            same_row = abs(y0 - py0) <= 1.5 or overlap >= 0.8
            gap = max(0.0, max(x0, px0) - min(x1, px1))
            if same_row and gap <= SAME_LINE_GAP_LIMIT:
                previous.append(line)
                continue
        groups.append([line])

    result = []
    for group in groups:
        if not any(_clean(_line_text(line)) for line in group):
            continue
        spans = [span for line in group for span in line.get("spans", [])]
        has_rtl = any(_is_rtl(ch.get("c", "")) for span in spans for ch in span.get("chars", []))
        if not has_rtl:
            spans.sort(key=lambda span: float(span.get("bbox", (0, 0, 0, 0))[0]))
        result.append({
            "bbox": _bbox([line.get("bbox", (0, 0, 0, 0)) for line in group]),
            "spans": spans,
        })
    return result


# PyMuPDF exposes styling as two separate bitfields on a span: ``flags``
# carries the font's own traits, ``char_flags`` the decorations applied to it.
# Underline lives in char_flags and is the strongest available signal for case
# citations -- LHC judgments underline them -- so it is worth carrying through
# even though PyMuPDF exports no named constant for it.
FONT_SUPERSCRIPT, FONT_ITALIC, FONT_BOLD = 1, 2, 16
CHAR_UNDERLINE = 8


# Fonts whose embedded ToUnicode tables misreport character identities.  The
# Nastaleeq family renders correctly on screen -- a reader sees valid Urdu --
# but extracts as transposed ligature clusters, so its native text is not
# citable.  Measured across 70 RTL documents: all 33 with broken Urdu use a
# Nastaleeq font, and none of the 15 with clean Urdu does.
UNRELIABLE_FONT_RE = re.compile(r"(?i)nastaleeq|nastaliq|noori")
PUA_CHAR_RE = re.compile("[\ue000-\uf8ff]")
# Symbol-font bullets leave a few private-use codepoints in an otherwise sound
# block (measured: 6 such blocks across 250 documents, every one under 10%),
# while a genuinely unreadable block is almost entirely private-use.  The gap
# between the two is wide, so the exact threshold is not delicate.
PUA_BLOCK_RATIO = 0.30


def _unreliable_text_reasons(spans: list[dict[str, Any]], text: str) -> list[str]:
    """Report why a block's native text cannot be trusted, if it cannot.

    This never edits the text.  A legal answer built on silently-wrong text is
    worse than one that admits a gap, so the block keeps its extracted
    characters and carries the warning alongside them.
    """
    reasons = []
    solid = [char for char in text if not char.isspace()]
    if solid and len(PUA_CHAR_RE.findall(text)) / len(solid) >= PUA_BLOCK_RATIO:
        reasons.append("private_use_glyphs")
    if any(UNRELIABLE_FONT_RE.search(str(span.get("font") or "")) for span in spans):
        reasons.append("unreliable_font_encoding")
    return reasons


def _emphasis(span: dict[str, Any]) -> dict[str, bool]:
    flags = int(span.get("flags") or 0)
    char_flags = int(span.get("char_flags") or 0)
    return {
        "bold": bool(flags & FONT_BOLD),
        "italic": bool(flags & FONT_ITALIC),
        "underline": bool(char_flags & CHAR_UNDERLINE),
        "superscript": bool(flags & FONT_SUPERSCRIPT),
    }


def _span_record(span: dict[str, Any]) -> dict[str, Any]:
    chars = span.get("chars", [])
    text = "".join(ch.get("c", "") for ch in chars)
    record = {
        "text": text,
        "bbox": list(map(float, span.get("bbox", (0, 0, 0, 0)))),
        "font": span.get("font"),
        "size": round(float(span.get("size", 0)), 3),
        "flags": span.get("flags"),
        "script": _script(text),
    }
    emphasis = _emphasis(span)
    if any(emphasis.values()):
        # Stored only when something is set: most spans are plain, and an
        # all-false dict on every span would bloat the document for nothing.
        record["emphasis"] = {key: value for key, value in emphasis.items() if value}
    if any(_is_rtl(ch.get("c", "")) for ch in chars):
        record["char_bboxes"] = [list(map(float, ch.get("bbox", (0, 0, 0, 0)))) for ch in chars]
    return record


def _text_block(raw: dict[str, Any]) -> dict[str, Any]:
    lines = raw.get("lines", [])
    text = "\n".join(_clean(_line_text(line)) for line in lines if _clean(_line_text(line)))
    spans = [_span_record(span) for line in lines for span in line.get("spans", [])]
    chars = _line_chars(lines[0]) if lines else []
    all_chars = [ch for line in lines for ch in _line_chars(line)]
    rtl_chars = [ch for ch in all_chars if _is_rtl(ch.get("c", ""))]
    rtl_lines = [line for line in lines if any(_is_rtl(c.get("c", "")) for c in _line_chars(line))]
    visual = False
    for line in rtl_lines:
        non_space = [c for c in _line_chars(line) if c.get("c", "").strip()]
        if len(non_space) > 1:
            x0 = [float(c.get("bbox", (0,))[0]) for c in non_space]
            visual = sum(x0[i] < x0[i + 1] for i in range(len(x0) - 1)) > len(x0) / 2
            break
    return {
        "text": text,
        "bbox": list(map(float, raw.get("bbox", (0, 0, 0, 0)))),
        "spans": spans,
        "line_bboxes": [list(map(float, line.get("bbox", (0, 0, 0, 0)))) for line in lines if _line_text(line).strip()],
        "font_sizes": [float(s.get("size", 0)) for s in spans if s.get("size")],
        "contains_rtl": bool(rtl_chars),
        "rtl_visual_order": visual,
        "language": "ur" if rtl_chars and len(rtl_chars) >= max(1, len(all_chars) * 0.6) else ("mixed" if rtl_chars else "en"),
    }


def _repeated_bands(pages: list[list[dict[str, Any]]], heights: list[float]) -> tuple[set[str], set[str]]:
    """Find repeated text signatures inside page bands.

    Repetition is required across two pages so first-page court titles are not
    mistaken for running headers.  The wide bands absorb small export offsets.
    """
    top: dict[str, set[int]] = defaultdict(set)
    bottom: dict[str, set[int]] = defaultdict(set)
    for page_no, blocks in enumerate(pages):
        height = heights[page_no]
        for block in blocks:
            text = _normal_key(block["text"])
            if len(text) < 3:
                continue
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y0 <= height * 0.16:
                top[text].add(page_no)
            if y1 >= height * 0.84:
                bottom[text].add(page_no)
    return ({key for key, pages_seen in top.items() if len(pages_seen) >= 2}, {key for key, pages_seen in bottom.items() if len(pages_seen) >= 2})


def _is_page_number(text: str) -> bool:
    # Do not treat dates such as 17-06-2014 or legal citations as page
    # numbers.  Page numbers are standalone short tokens in these judgments.
    return bool(re.fullmatch(r"\s*[-–—]?\s*\d+\s*[-–—]?\s*", text))


def _classify(block: dict[str, Any], top_keys: set[str], bottom_keys: set[str], page_height: float, page_width: float, median_size: float, table_bboxes: list[list[float]]) -> tuple[str, float, list[str]]:
    text = block["text"]
    key = _normal_key(text)
    y0, y1 = block["bbox"][1], block["bbox"][3]
    centered = abs((block["bbox"][0] + block["bbox"][2]) / 2 - page_width / 2) < page_width * 0.15
    reasons: list[str] = []
    if any(_overlap_ratio(block["bbox"], tb) >= 0.20 for tb in table_bboxes):
        return "table_text_omitted", 0.99, ["covered_by_table"]
    top_like = y0 <= page_height * 0.10 and _is_page_number(text)
    if key in top_keys or top_like:
        reasons.append("top_band")
        return "header", 0.96 if key in top_keys else 0.82, reasons
    if key in bottom_keys or _is_page_number(text):
        reasons.append("bottom_band")
        return "footer", 0.96 if key in bottom_keys else 0.88, reasons
    if LIST_RE.match(text):
        return "list", 0.93, ["numbered_or_lettered_prefix"]
    sizes = block["font_sizes"]
    largest = max(sizes or [0])
    if len(text) <= 100 and (
        largest >= median_size * 1.20
        or (text.isupper() and largest >= median_size * 1.05)
        or (y0 < page_height * 0.25 and centered and largest >= median_size * 1.05 and len(text) <= 60)
    ):
        return "heading", 0.89, ["short_centered_or_large"]
    return "text", 0.88, ["native_text_block"]


def _heading_level(block: dict[str, Any], median_size: float) -> int:
    """Rank a heading by how far its type rises above the body text.

    Two levels are enough for this corpus: a court/title banner sits well
    above body size, while a case number or section label is only slightly
    raised.  Emitting every heading as ``#`` (the previous behaviour) gives a
    chunker no document outline to work with.
    """
    largest = max(block.get("font_sizes") or [0])
    return 1 if largest >= median_size * 1.20 else 2


def _is_cover_label(text: str) -> bool:
    # A real label is always short; requiring this bounds false positives from
    # ordinary sentences that happen to contain "state" or "... by" further in.
    return bool(text) and len(text) <= 60 and COVER_LABEL_RE.match(text) is not None


def _merge_native_blocks(blocks: list[dict[str, Any]], median_size: float) -> list[dict[str, Any]]:
    """Join line primitives into semantic paragraphs while retaining geometry."""
    merged: list[dict[str, Any]] = []
    numbered_re = re.compile(r"^\s*(\d{1,4})[.)]\s+(?=[A-Z\"“‘(])")
    # Normal body leading is about 0.6x the 14pt font in this corpus.  A
    # larger gap commonly denotes a new semantic paragraph (for example a
    # short party name followed by its sentencing paragraph).
    max_gap = max(12.0, median_size * 0.90)
    for block in blocks:
        if not merged:
            merged.append(block)
            continue
        previous = merged[-1]
        same_language = previous["language"] == block["language"] or "mixed" in {previous["language"], block["language"]}
        same_column = abs(previous["bbox"][0] - block["bbox"][0]) <= 12 or (
            previous["contains_rtl"] and block["contains_rtl"] and abs(previous["bbox"][2] - block["bbox"][2]) <= 24
        ) or (
            previous["type"] == block["type"] == "header" and abs(previous["bbox"][0] - block["bbox"][0]) <= 24
        )
        gap = block["bbox"][1] - previous["bbox"][3]
        can_join_type = (
            previous["type"] == block["type"] == "text"
            or previous["type"] == block["type"] == "list"
            or previous["type"] == "list" and block["type"] == "text"
        )
        can_join_header = previous["type"] == block["type"] == "header" and abs(previous["bbox"][0] - block["bbox"][0]) <= 24
        previous_number = numbered_re.match(str(previous.get("text", "")))
        block_number = numbered_re.match(str(block.get("text", "")))
        distinct_numbered_paragraphs = bool(previous_number and block_number and previous_number.group(1) != block_number.group(1))
        # A cover-page label ("Petitioner by:") is short and geometrically
        # indistinguishable from an ordinary wrapped paragraph line, so it was
        # being absorbed into whatever field preceded it (E5/E6).  Refusing to
        # merge into a block that starts a new labelled field keeps each field
        # separate without needing to know the page is a cover page at all.
        starts_new_field = _is_cover_label(str(block.get("text", "")))
        join_gap = max_gap
        if previous["type"] == block["type"] == "list":
            join_gap = max(20.0, median_size * 1.5)
        if same_language and same_column and -2.0 <= gap <= join_gap and (can_join_type or can_join_header) and not distinct_numbered_paragraphs and not starts_new_field:
            previous["text"] = f"{previous['text']} {block['text']}".strip()
            previous["markdown"] = previous["text"]
            previous["bbox"] = _bbox([previous["bbox"], block["bbox"]])
            previous["line_bboxes"].extend(block.get("line_bboxes", []))
            previous["spans"].extend(block["spans"])
            previous["font_sizes"].extend(block["font_sizes"])
            previous["contains_rtl"] = previous["contains_rtl"] or block["contains_rtl"]
            previous["rtl_visual_order"] = previous["rtl_visual_order"] or block["rtl_visual_order"]
            previous["language"] = "mixed" if previous["language"] != block["language"] else previous["language"]
            previous["confidence"]["score"] = min(previous["confidence"]["score"], block["confidence"]["score"])
            previous["confidence"]["reasons"].append("merged_adjacent_native_lines")
            continue
        merged.append(block)
    return merged


NON_BODY_TYPES = {"header", "footer", "form_header"}


def _reading_order(blocks: list[dict[str, Any]], page_width: float, page_height: float = 0.0) -> list[dict[str, Any]]:
    """Sort blocks by geometry, with a deterministic two-column fallback."""
    if not blocks:
        return []
    body = [b for b in blocks if b["type"] not in NON_BODY_TYPES]
    narrow = sorted((b for b in body if b["bbox"][2] - b["bbox"][0] < page_width * 0.70), key=lambda b: b["bbox"][0])
    columns: list[list[dict[str, Any]]] = []
    for block in narrow:
        if not columns or block["bbox"][0] - columns[-1][-1]["bbox"][0] > page_width * 0.28:
            columns.append([block])
        else:
            columns[-1].append(block)
    has_columns = len(columns) >= 2 and all(len(column) >= 3 for column in columns)
    if has_columns and page_height > 0:
        # A short signature block (2-3 names, confined to the bottom of the
        # page) can satisfy the column-count check by coincidence -- on
        # 2025LHC290.pdf p3, an indented quotation happened to share close
        # enough left edges with the signature's left column to inflate that
        # one cluster's span, but comparing the two spans to EACH OTHER is
        # not reliable: a signature block on its own, with no such neighbour,
        # has two comparably short columns and would pass a ratio check too.
        # What genuinely marks a multi-column body is that EACH column runs
        # for a large fraction of the page on its own; requiring that
        # directly (not relative to the other column) catches both cases.
        has_columns = all(
            max(b["bbox"][3] for b in column) - min(b["bbox"][1] for b in column) >= page_height * 0.40
            for column in columns
        )
    centers = [statistics.median([b["bbox"][0] for b in column]) for column in columns]

    def key(block: dict[str, Any]) -> tuple[float, float, float]:
        x0, y0, x1 = block["bbox"][0], block["bbox"][1], block["bbox"][2]
        if block["type"] in NON_BODY_TYPES or not has_columns or x1 - x0 >= page_width * 0.70:
            # A case caption ("Party1 ... Versus ... Party2") is one visual
            # row split into widely separated fragments that no longer glue
            # together once _coalesce_lines stops fusing distinct columns; a
            # sub-point baseline offset between them (0.2pt is typical) must
            # not outrank their left-to-right order.  Line spacing in this
            # corpus is always well over 10pt, so a 1.5pt bucket only ever
            # groups genuine same-row jitter, never two different lines.
            return (round(y0 / 1.5), 0.0, x0)
        column = min(range(len(centers)), key=lambda index: abs(centers[index] - x0))
        return (page_width + column * page_width + y0, float(column), x0)

    return sorted(blocks, key=key)


EMPHASIS_WRAPPERS = (("bold", "**", "**"), ("italic", "*", "*"), ("underline", "<u>", "</u>"), ("superscript", "<sup>", "</sup>"))
MARKUP_RE = re.compile(r"\*\*|\*|</?u>|</?sup>")
# A word broken across a line ("sub-" / "section") is rejoined for the
# rendered view only.  ``text`` keeps the source's own break: the gold
# transcription preserves it too, and that field is the citation source of
# truth, so it must stay byte-faithful to the PDF.
# Emphasis can end mid-word ("*quasi*- judicial"), putting a closing marker
# between the letter and the hyphen, so markers are skipped over rather than
# blocking the join.
WRAPPED_WORD_RE = re.compile(r"([a-z](?:\*\*|\*|</u>|</sup>)*)- (?=[a-z])")


def _join_wrapped_words(value: str) -> str:
    return WRAPPED_WORD_RE.sub(r"\1-", value)


def _plain_words(value: str) -> list[str]:
    return MARKUP_RE.sub("", value).split()


def _emphasis_markdown(spans: list[dict[str, Any]], text: str) -> str:
    """Render span emphasis as markdown, falling back to the plain text.

    Spans do not always reassemble into the block text -- lines are joined
    with separators the spans themselves do not carry -- so the rendered
    result is used only when it round-trips to exactly the same words.
    Emphasis is presentation; it is never worth risking the content for.
    """
    runs: list[tuple[dict[str, bool], str]] = []
    for span in spans:
        value = span.get("text", "")
        if not value:
            continue
        emphasis = span.get("emphasis") or {}
        if runs and runs[-1][0] == emphasis:
            runs[-1] = (emphasis, runs[-1][1] + value)
        else:
            runs.append((emphasis, value))

    parts = []
    for emphasis, value in runs:
        stripped = value.strip()
        if not stripped or not emphasis:
            parts.append(value)
            continue
        # Markers must hug the words: "** bold **" does not render.
        lead = value[: len(value) - len(value.lstrip())]
        trail = value[len(value.rstrip()) :]
        for key, open_marker, close_marker in EMPHASIS_WRAPPERS:
            if emphasis.get(key):
                stripped = f"{open_marker}{stripped}{close_marker}"
        parts.append(f"{lead}{stripped}{trail}")

    rendered = "".join(parts)
    return rendered if _plain_words(rendered) == _plain_words(text) else text


def _table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    # A newline inside a pipe cell terminates the row for every markdown
    # renderer, splitting one table into several; flatten to spaces so the
    # emitted table is valid.  ``rows``/``cells`` keep the original text.
    rows = [[_clean(str(cell or "").replace("\n", " ")) for cell in row] for row in rows]
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = "| " + " | ".join(rows[0]) + " |"
    divider = "| " + " | ".join("---" for _ in range(width)) + " |"
    return "\n".join([header, divider] + ["| " + " | ".join(row) + " |" for row in rows[1:]])


def _table_html(rows: list[list[str]]) -> str:
    body = []
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        body.append("<tr>" + "".join(f"<{tag}>{html.escape(str(cell or ''))}</{tag}>" for cell in row) + "</tr>")
    return "<table>" + "".join(body) + "</table>"


def _table_cells(page: fitz.Page, table: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach cell and word geometry to a native PyMuPDF table."""
    raw_cells = [list(map(float, cell)) for cell in getattr(table["object"], "cells", []) if cell]
    if not raw_cells:
        return []
    rows: list[list[list[float]]] = []
    for cell in sorted(raw_cells, key=lambda value: (value[1], value[0])):
        if not rows or abs(cell[1] - rows[-1][0][1]) > 1.5:
            rows.append([cell])
        else:
            rows[-1].append(cell)
    extracted_rows = table.get("rows", [])
    words = page.get_text("words")
    result = []
    for row_index, row in enumerate(rows):
        row.sort(key=lambda value: value[0])
        for column_index, cell in enumerate(row):
            x0, y0, x1, y1 = cell
            cell_words = []
            for word in words:
                wx0, wy0, wx1, wy1, text = map(lambda value: value, word[:5])
                center_x, center_y = (float(wx0) + float(wx1)) / 2, (float(wy0) + float(wy1)) / 2
                if x0 - 1 <= center_x <= x1 + 1 and y0 - 1 <= center_y <= y1 + 1:
                    cell_words.append({"text": str(text), "bbox": [float(wx0), float(wy0), float(wx1), float(wy1)]})
            value = ""
            if row_index < len(extracted_rows) and column_index < len(extracted_rows[row_index]):
                value = str(extracted_rows[row_index][column_index] or "")
            result.append({
                "row": row_index,
                "column": column_index,
                "row_span": 1,
                "column_span": 1,
                "bbox": cell,
                "text": value,
                "words": cell_words,
            })
    return result


def _is_order_sheet_header(table_block: dict[str, Any]) -> bool:
    """A short, single-row ruled box whose text names the proceedings log.

    The box genuinely ends after its header row (E6): there are no ruled body
    rows to recover, so ``find_tables()`` returning one row is correct, not a
    miss.  Restricting to a single row under 60pt keeps this from matching the
    rarer larger grids (2x3, 5x5) that are not this template.
    """
    rows = table_block.get("rows", [])
    if len(rows) != 1 or table_block["bbox"][3] - table_block["bbox"][1] >= 60:
        return False
    return bool(ORDER_SHEET_RE.search(" ".join(str(cell or "") for cell in rows[0])))


def _order_sheet_date(table: dict[str, Any], page: fitz.Page) -> dict[str, Any] | None:
    """Recover the one datum in an ORDER SHEET body: the entry date.

    The header's own column boundaries are reused rather than banding the
    body (E6 showed banding misplaces ~11.6 words per document because the
    body's margins do not align with the header's).  A word counts as the
    date only when it sits left of the header's column-2/3 boundary and
    begins at or below the header's bottom edge; this recovered the date on
    120/120 validated documents.
    """
    cell_boxes = sorted((list(map(float, cell)) for cell in getattr(table["object"], "cells", []) if cell), key=lambda cell: cell[0])
    if len(cell_boxes) != 3:
        return None
    boundary = cell_boxes[1][2]
    header_bottom = table["bbox"][3]
    candidates = []
    for word in page.get_text("words"):
        wx0, wy0, wx1, wy1, text = float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4])
        if not DATE_RE.fullmatch(text.strip(".,;")):
            continue
        if (wx0 + wx1) / 2 < boundary and wy0 > header_bottom - 3:
            candidates.append((wy0, wx0, text, [wx0, wy0, wx1, wy1]))
    if not candidates:
        return None
    candidates.sort()
    _, _, text, bbox = candidates[0]
    return {"value": text, "bbox": bbox}


def _cover_fields_from_table(table_block: dict[str, Any]) -> list[dict[str, Any]]:
    """A 2-column ruled table in this corpus is always a label/value cover grid.

    Restricting to exactly 2 columns keeps schedules and sentencing grids
    (3+ columns) and the ORDER SHEET header (handled separately) from being
    misread as cover fields.
    """
    rows = table_block.get("rows", [])
    if not rows or max(len(row) for row in rows) != 2:
        return []
    # OCR table cells carry a bbox but no row/column index, so a cell without
    # one is simply not addressable here; the value falls back to the table's
    # own box rather than crashing the parse.
    cells_by_position = {
        (cell["row"], cell["column"]): cell
        for cell in table_block.get("cells", [])
        if "row" in cell and "column" in cell
    }
    fields = []
    for row_index, row in enumerate(rows):
        if len(row) != 2 or not str(row[0] or "").strip():
            continue
        value_cell = cells_by_position.get((row_index, 1))
        fields.append({
            # Stripped the same way as the text-derived path (COVER_LABEL_RE
            # excludes the terminator) so a consumer can key on "label"
            # consistently regardless of which template a field came from.
            "label": _clean(str(row[0])).rstrip(":.").strip(),
            "value": _clean(str(row[1] or "")),
            "bbox": value_cell["bbox"] if value_cell else table_block["bbox"],
            "source": "cover_table",
        })
    return fields


# Every sampled judgment states the deciding judge's name immediately after
# the cover block, in this exact shape ("NAME, J.- ..." / "NAME, J:- ...").
# It is the reliable boundary between cover fields and the judgment body, and
# catches the one shape a plain type check cannot: an un-numbered opening
# paragraph that would otherwise look like more field continuation text.
JUDGE_LINE_RE = re.compile(r"(?i)^[A-Z][A-Za-z .'–\-]{2,60},\s*J[.:–\-]")
MAX_COVER_VALUE_BLOCKS = 8


def _cover_fields_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover borderless cover fields (E5 Template C, ~30% of first pages).

    These carry no rules for a table detector to key off, but once
    ``_coalesce_lines``/``_merge_native_blocks`` stop gluing a label to its
    value (E5/E6), each field starts as one block (label, or label with its
    value inline).  A value can still wrap across several physical blocks (a
    multi-counsel list), so continuation blocks are absorbed until a new
    field starts, a non-text block is hit, or the judgment body itself
    begins -- never by counting a fixed number of lines, since a two-name
    field and a six-name field are geometrically identical here.
    """
    fields = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if block["type"] != "text" or len(block["text"]) > 200:
            continue
        match = COVER_LABEL_RE.match(block["text"])
        if not match:
            continue
        label = _clean(match.group("label"))
        inline_value = _clean(match.group("inline"))
        value_parts = [inline_value] if inline_value else []
        bbox = block["bbox"] if inline_value else None
        absorbed = 0
        while index < len(blocks) and absorbed < MAX_COVER_VALUE_BLOCKS:
            following = blocks[index]
            if following["type"] != "text" or COVER_LABEL_RE.match(following["text"]) or JUDGE_LINE_RE.match(following["text"]):
                break
            value_parts.append(following["text"])
            bbox = following["bbox"] if bbox is None else _bbox([bbox, following["bbox"]])
            index += 1
            absorbed += 1
        value = _clean(" ".join(value_parts))
        if value:
            fields.append({"label": label, "value": value, "bbox": bbox or block["bbox"], "source": "cover_text"})
    return fields


# Roles are written singular, plural, or with an explicit "(s)" on the same
# page ("...Appellant(s)", "…Petitioner (s)"), so all three are one pattern.
ROLES = (r"(?:appellant|petitioner|respondent|applicant|complainant|plaintiff|defendant"
         r"|accused|opposite\s+part(?:y|ies))(?:\s*\(\s*s\s*\)|s)?")
# A caption line is often the role marker itself, or a parenthetical reference
# to the consolidated case, rather than anybody's name.
ROLE_ONLY_RE = re.compile(rf"(?i)^[.…\s]*(?:{ROLES}|the\s+state)[.…\s()]*$")
ROLE_SUFFIX_RE = re.compile(rf"(?i)\s*[.…]+\s*{ROLES}\s*[.…]*\s*$")
CASE_REF_ONLY_RE = re.compile(r"(?i)^\s*\(?\s*in\s+(?:civil|criminal|crl|c\.|appeal|petition|both)\b.*$")
CASE_REF_SUFFIX_RE = re.compile(r"(?i)\s*\(\s*in\s+[^)]*\)\s*\.?\s*$")


def _party_side(value: str) -> str:
    """Trim caption punctuation, treating a wordless remainder as absent."""
    trimmed = CASE_REF_SUFFIX_RE.sub("", value.strip(" :-"))
    trimmed = ROLE_SUFFIX_RE.sub("", trimmed).strip(" :-")
    if ROLE_ONLY_RE.match(trimmed) or CASE_REF_ONLY_RE.match(trimmed):
        return ""
    return trimmed if re.search(r"\w", trimmed) else ""


def _party_near(lines: list[str], start: int, step: int) -> str:
    """Walk away from the caption separator to the nearest real party name.

    "VS" usually sits on its own line, so the adjacent line is frequently the
    role marker or a "(in Civil Appeal No...)" reference rather than a party.
    Taking it produced captions like "...Appellants. VS Syed Munawar Ali" --
    naming only one of the two sides.  Walking outward past those lines finds
    the name the caption is actually about.
    """
    for offset in range(1, 6):
        index = start + offset * step
        if not 0 <= index < len(lines):
            break
        # The counsel block follows the caption.  Walking into it returns a
        # label ("For the Petitioner(s)") as if it were the opposing party.
        if COVER_LABEL_RE.match(lines[index]):
            break
        candidate = _party_side(lines[index])
        if not candidate:
            continue
        # A party name wraps across lines, and the break is marked the way any
        # printed list marks it: the previous line ends in a comma.
        previous = index - 1
        while 0 <= previous < len(lines) and lines[previous].rstrip().endswith(","):
            head = _party_side(lines[previous])
            if not head:
                break
            candidate = f"{head} {candidate}"
            previous -= 1
        return candidate
    return ""


BENCH_HEADING_RE = re.compile(r"(?i)^\s*(?:present|coram|before)\s*:?\s*$")
JUSTICE_LINE_RE = re.compile(
    r"(?i)^\s*(?:mr\.?|mrs\.?|ms\.?|hon'?ble|the\s+hon'?ble)?\s*"
    r"(?:chief\s+)?justice\s+(?P<name>[A-Z][\w'’.\- ]{2,60}?)"
    r"\s*(?:,\s*(?:CJ|HCJ|JJ?)\.?)?\s*$"
)


# A judgment is signed at the foot: the author's name in capitals inside
# brackets, and the office on the next line.  IHC uses this on nearly every
# document, including the single-page order sheets that carry no cover at all
# and state the judge nowhere else.
SIGNATURE_NAME_RE = re.compile(r"^\s*\(\s*(?P<name>[A-Z][A-Z .'’\-]{3,60}?)\s*\)[.,]?\s*$")
SIGNATURE_OFFICE_RE = re.compile(r"(?i)^\s*(?:chief\s+)?(?:justice|judge)\s*\.?\s*$")
SIGNATURE_GAP = 2


def _signed_judges(lines: list[str]) -> list[str]:
    """Read the names signed above "JUDGE" at the foot of the document.

    Worth reading separately because it is often the only statement of who
    decided: an order sheet has no cover, no "Present:" list and no "NAME, J.-"
    attribution, so without the signature the judge is simply unknown -- which
    it was on every IHC order measured.
    """
    names: list[str] = []
    for index, line in enumerate(lines):
        match = SIGNATURE_NAME_RE.match(line)
        if not match:
            continue
        if any(SIGNATURE_OFFICE_RE.match(following)
               for following in lines[index + 1: index + 1 + SIGNATURE_GAP]):
            names.append(_clean(match.group("name")))
    return names


def _bench_judges(head_lines: list[str]) -> list[str]:
    """Read the bench listed under "Present:" / "Coram:" / "Before:".

    The opinion author signs the body as "NAME, J.-", which is the only judge
    the body regex can see.  On a five-judge bench that reports one name and
    drops four, so the cover's own list is read as well.
    """
    names: list[str] = []
    for index, line in enumerate(head_lines):
        if not BENCH_HEADING_RE.match(line):
            continue
        for candidate in head_lines[index + 1: index + 12]:
            match = JUSTICE_LINE_RE.match(candidate)
            if match:
                names.append(_clean(match.group("name")).rstrip(",."))
            elif names:
                break  # the list has ended; anything after it is not a judge
    return names


# Court naming lives in the registry (``specter/courts.py``) so that adding a
# court is a config entry rather than an edit here.  Both fields are kept: the
# verbatim name for quoting, a canonical id for querying -- a corpus that
# stores only what the page says ends up with one court under several
# spellings, and every filter then misses rows without erroring.
_normalise_court = canonical_name
_court_id = court_id


def _iso_date(value: str | None) -> str | None:
    """Reformat a corpus date as ISO 8601, or None if it is not unambiguous.

    This corpus writes dates day-first in several styles -- ``17.02.2016``,
    ``2.6.2005``, ``11-09-2025`` -- which no date index can range over.  Day
    order is not assumed blindly: it is the local convention, and it is what
    the filename labels agree with on the documents where both exist.

    A two-digit year is refused rather than guessed at a century, and an
    impossible date returns nothing rather than a wrong day.
    """
    parts = re.findall(r"\d+", str(value or ""))
    if len(parts) != 3 or len(parts[2]) != 4:
        return None
    day, month, year = (int(part) for part in parts)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_metadata(text: str, pdf_metadata: dict[str, Any]) -> dict[str, Any]:
    head = text[:12000]
    dates = DATE_RE.findall(head)
    # Preferring a match from the first 25 lines -- the caption, where a
    # document names itself -- over one from the head was tried and changed
    # nothing on 250 IHC documents, so it was not kept.  Of the case numbers
    # that disagree with the corpus label, 48 of 60 are the document's own
    # caption naming a different case from the folder it was filed in; that is
    # the corpus disagreeing with itself, and ``label_check`` reports it.
    case_match = CASE_RE.search(head)
    citations = sorted(set(CITATION_RE.findall(text)))
    section_numbers: set[str] = set()
    for match in re.finditer(r"(?i)\b(?:sections?|s\.)\s+([^.;\n]{1,100})", text):
        section_numbers.update(re.findall(r"\b\d{1,4}(?:-[A-Za-z])?(?:\([^)]+\))?", match.group(1)))
    section_numbers.difference_update({"1860", "1898", "1984", "1997"})
    known_acts = re.compile(
        r"(?i)\b(?:Pakistan Penal Code(?:,?\s*1860)?|Anti-Terrorism Act(?:,?\s*1997)?|"
        r"Code of Criminal Procedure(?:,?\s*1898)?|Qanun-e-Shahadat Order(?:,?\s*1984)?|"
        r"Constitution of Pakistan|Control of Narcotic Substances Act(?:,?\s*1997)?)\b"
    )
    acts = sorted(set(_clean(x) for x in known_acts.findall(text)))
    court = next((
        line.strip()
        for line in head.splitlines()
        if len(line.strip()) <= 100
        # Spaces are optional throughout: OCR routinely drops them in centred
        # headings, so the court line arrives as "INTHESUPREMECOURTOFPAKISTAN".
        # Measured on scanned SC judgments, requiring them lost the court on a
        # quarter of documents -- and the fallback then matched a sentence from
        # the body that merely mentioned a High Court.
        and re.match(r"^\s*(?:IN\s*THE\s*)?(?:[A-Z][A-Z &.\-]{2,60}?\s*)?(?:HIGH|SUPREME)\s*COURT", line, re.I)
    ), None)
    bench = next((line.strip() for line in head.splitlines() if "BENCH" in line.upper()), None)
    # "NAME, J.-" at LHC and SC; "NAME, J:-" at IHC.  Requiring the period lost
    # the author on every IHC judgment that writes the colon.
    judge_matches = re.findall(r"(?i)([A-Z][A-Za-z .'-]{3,60}),\s*J\s*[.:]", text)
    hearing_match = HEARING_DATE_RE.search(text)
    decision_value = _decision_date(text)
    head_lines = [re.sub(r"\s+", " ", line).strip() for line in head.splitlines() if line.strip()]
    # The cover's bench list first, then the opinion author from the body; the
    # bench is the fuller answer and its order is the court's own.
    bench_names = _bench_judges(head_lines)
    authored = [_clean(name) for name in judge_matches]
    # The signature is read last: it is the surest single name, but the cover's
    # list is the fuller one and its order is the court's own.
    signed = _signed_judges([re.sub(r"\s+", " ", line).strip() for line in text.splitlines()])
    seen: set[str] = set()
    judges = []
    for name in bench_names + authored + signed:
        key = re.sub(r"[^a-z]", "", name.casefold())
        if key and key not in seen:
            seen.add(key)
            judges.append(name)
    # The trailing period belongs to the separator ("Vs."), not to the party
    # that follows it, so the match consumes it rather than leaving it to open
    # the right-hand side.
    party_marker = re.compile(r"\bv\s*e\s*r\s*s\s*u\s*s\b|\bversus\b|\bvs?\b\.?", re.I)
    parties = None
    petitioner = respondent = None
    for index, line in enumerate(head_lines[:80]):
        # The caption sits above the judgment body.  Past its opening line a
        # bare "v." is almost always a cited case inside prose, not this
        # case's parties, so the scan stops rather than matching one.
        # "J U D G M E N T" standing alone opens the body.  The word must end
        # the line: these pages also carry a "JUDGMENT SHEET" / "ORDER SHEET"
        # form label *above* the caption, and breaking on that would abandon
        # the scan before the parties are ever reached.
        if JUDGE_LINE_RE.match(line) or re.match(r"(?i)^\s*(?:j\s*u\s*d\s*g\s*m\s*e\s*n\s*t|o\s*r\s*d\s*e\s*r)\s*[.:]?\s*$", line):
            break
        marker = party_marker.search(line)
        if not marker:
            continue
        # Periods are kept -- they belong to "etc." and to initials, and the
        # value has to stay quotable against the page it came from -- so a
        # side is judged empty by having no word character rather than by
        # being stripped down to nothing.
        left = _party_side(line[:marker.start()]) or _party_near(head_lines, index, -1)
        right = _party_side(line[marker.end():]) or _party_near(head_lines, index, +1)
        if left and right and len(left) <= 180 and len(right) <= 180:
            # The source's own separator is preserved ("Vs.", "VS", "versus")
            # so the field can be quoted and located verbatim.
            parties = f"{left} {_clean(marker.group(0))} {right}"
            petitioner, respondent = left, right
            break
    return {
        "court": _normalise_court(court),
        "court_id": _court_id(_normalise_court(court)),
        "bench": bench,
        "case_number": _clean(case_match.group(1)) if case_match else None,
        "decision_date": decision_value,
        "decision_date_iso": _iso_date(decision_value),
        "hearing_date": hearing_match.group(1) if hearing_match else None,
        "hearing_date_iso": _iso_date(hearing_match.group(1) if hearing_match else None),
        "dates_found": sorted(set(dates)),
        "judges": judges,
        "parties": parties,
        # The caption is kept verbatim for quoting, and each side is also
        # reported on its own: "X Versus Y" cannot answer "which cases named
        # the State as respondent", which is an ordinary thing to ask of a
        # case-law index.
        "petitioner": petitioner,
        "respondent": respondent,
        "citations": citations,
        "acts": acts,
        "sections": sorted(section_numbers),
        "pdf_title": pdf_metadata.get("title") or None,
        "pdf_author": pdf_metadata.get("author") or None,
    }


def _estimate_columns(blocks: list[dict[str, Any]], page_width: float) -> int:
    """Estimate visual columns from repeated left edges, conservatively."""
    lefts = sorted(
        float(block["bbox"][0])
        for block in blocks
        if block.get("text", "").strip() and float(block["bbox"][2]) - float(block["bbox"][0]) < page_width * 0.72
    )
    clusters: list[list[float]] = []
    for left in lefts:
        if not clusters or left - statistics.median(clusters[-1]) > page_width * 0.12:
            clusters.append([left])
        else:
            clusters[-1].append(left)
    stable = [cluster for cluster in clusters if len(cluster) >= 2]
    return max(1, min(4, len(stable)))


def _document_fingerprint(
    doc: fitz.Document,
    raw_pages: list[list[dict[str, Any]]],
    page_widths: list[float],
    page_heights: list[float],
    top_keys: set[str],
    bottom_keys: set[str],
    image_pages: list[bool],
) -> dict[str, Any]:
    """Return stable, explainable template features; no court-name guessing."""
    page_areas = [width * height for width, height in zip(page_widths, page_heights)]
    text_chars = [sum(len(block.get("text", "")) for block in blocks) for blocks in raw_pages]
    margins = []
    column_counts = []
    font_counts: Counter[str] = Counter()
    for blocks, width, height in zip(raw_pages, page_widths, page_heights):
        boxes = [block["bbox"] for block in blocks if block.get("text", "").strip()]
        if boxes:
            margins.append({
                "left": round(min(box[0] for box in boxes), 2),
                "right": round(width - max(box[2] for box in boxes), 2),
                "top": round(min(box[1] for box in boxes), 2),
                "bottom": round(height - max(box[3] for box in boxes), 2),
            })
        column_counts.append(_estimate_columns(blocks, width))
        for block in blocks:
            for size in block.get("font_sizes", []):
                font_counts[str(round(float(size), 1))] += 1
    median_margin = {}
    for key in ("left", "right", "top", "bottom"):
        values = [margin[key] for margin in margins]
        median_margin[key] = round(statistics.median(values), 2) if values else None
    fingerprint = {
        "page_size": {
            "width": round(statistics.median(page_widths), 2) if page_widths else None,
            "height": round(statistics.median(page_heights), 2) if page_heights else None,
            "variants": sorted({f"{width:.1f}x{height:.1f}" for width, height in zip(page_widths, page_heights)}),
        },
        "margins": median_margin,
        "header_band": sorted(top_keys),
        "footer_band": sorted(bottom_keys),
        "font_distribution": dict(font_counts.most_common(12)),
        "column_count": int(statistics.median(column_counts)) if column_counts else 1,
        "logo_or_image_present": bool(any(image_pages)),
        "text_density": round(sum(text_chars) / max(1.0, sum(page_areas)), 6),
        "page_text_density": [round(chars / max(1.0, area), 6) for chars, area in zip(text_chars, page_areas)],
    }
    fingerprint["template_key"] = hashlib.sha1(
        json.dumps(fingerprint, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return fingerprint


# Matches the caption separator in any of its observed spellings -- "Versus",
# "Vs.", "VS", and a bare "v" -- so a parties value can be split back into the
# two sides it was assembled from.
PARTY_SEPARATOR_RE = re.compile(r"(?i)\s+(?:versus|vs?\.?)\s+")
MAX_SPAN_SCAN = 200
HEARING_LABEL_RE = re.compile(r"(?i)\bdate\(?s?\)?\b.{0,12}\bhearing")


def _counsel_role(label: str) -> str | None:
    """Name the party a cover-page counsel row belongs to, or reject the row.

    ``cover_fields`` reports any two-column table it finds, which on some
    judgments includes deposition tables and Urdu schedules.  Those are
    legitimate structure but not case metadata, so the semantic filter lives
    here rather than narrowing the structural extraction.  Stripping to ASCII
    letters also rejects rows whose label is unreadable Urdu.
    """
    key = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", label).casefold()).strip()
    if not key:
        return None
    if key.startswith("for the "):
        return key[len("for the "):].strip() or None
    if key.endswith(" by"):
        return key[: -len(" by")].strip() or None
    return None


def _cover_metadata(cover_fields: list[dict[str, Any]], order_dates: list[dict[str, Any]]) -> dict[str, Any]:
    """Read case metadata off the page's own structure rather than a text blob.

    A cover row already pairs a label with its value and carries the value's
    bbox, so these fields arrive located and unambiguous -- no guessing which
    of several dates on the page is the hearing date.
    """
    hearing = next((field for field in cover_fields if HEARING_LABEL_RE.search(field["label"])), None)
    counsel = []
    for field in cover_fields:
        role = _counsel_role(field["label"])
        if role and field.get("value"):
            counsel.append({
                "role": role,
                "names": field["value"],
                "page": field.get("page"),
                "bbox": field.get("bbox", []),
                "source": field.get("source", "cover"),
            })
    result: dict[str, Any] = {"counsel": counsel}
    if hearing:
        match = DATE_RE.search(hearing["value"])
        if match:
            result["hearing_date"] = match.group(0)
            result["hearing_date_bbox"] = hearing.get("bbox", [])
            result["hearing_date_page"] = hearing.get("page")
    if order_dates:
        first = min(order_dates, key=lambda item: (item.get("page") or 0))
        result["order_date"] = first["value"]
        result["order_date_bbox"] = first.get("bbox", [])
        result["order_date_page"] = first.get("page")
    return result


def _narrow_to_spans(block: dict[str, Any], target_key: str) -> list[float]:
    """Tighten a block bbox to the spans that actually carry the value.

    A block is a whole paragraph, so its box is useless for highlighting a
    date or a case number inside it.  Spans are sub-line, so narrowing gives a
    box a PDF viewer can draw tightly around the value itself.
    """
    spans = [span for span in block.get("spans") or [] if span.get("bbox")][:MAX_SPAN_SCAN]
    if not spans:
        # An OCR block has no style spans, but its per-line results carry text
        # and a box.  Lines are the finest granularity the OCR engine reports,
        # so they narrow a paragraph box as far as the evidence honestly allows.
        spans = [line for line in (block.get("ocr") or {}).get("line_results") or [] if line.get("bbox")][:MAX_SPAN_SCAN]
    direct = [span["bbox"] for span in spans if _contains_value(_normal_space(span.get("text", "")), target_key)]
    if direct:
        return _bbox(direct)
    # The value can straddle a style change; take the shortest run of
    # consecutive spans whose concatenation contains it.
    for start in range(len(spans)):
        accumulated = ""
        for end in range(start, len(spans)):
            accumulated += spans[end].get("text", "")
            if _contains_value(_normal_space(accumulated), target_key):
                return _bbox([spans[index]["bbox"] for index in range(start, end + 1)])
    return block.get("bbox", [])


def _normal_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).casefold()


def _contains_value(haystack: str, target_key: str) -> bool:
    """Substring match that refuses to find a value inside a larger token.

    Section "13" must not match the "13" inside "2013", or the field's box
    ends up pointing at a year instead of the section reference.
    """
    return re.search(rf"(?<![0-9a-z]){re.escape(target_key)}(?![0-9a-z])", haystack) is not None


def _locate_value(target: str, pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find a page region whose text genuinely contains the whole value.

    Tried in order of how much can be claimed about the result: inside one
    block (narrowed to spans), then -- for a value the extractor assembled
    from separate blocks, such as ``X Versus Y`` -- the union of the blocks
    holding its parts.  A box that does not contain what it points at is
    worse than no box, so anything else returns nothing.
    """
    target_key = _normal_space(target)
    if not target_key:
        return None
    for page in pages:
        for block in page.get("blocks", []):
            if _contains_value(_normal_space(block.get("text", "")), target_key):
                return {
                    "page": page["page_number"],
                    "bbox": _narrow_to_spans(block, target_key),
                    "confidence": round(float(block.get("confidence", {}).get("score", 0.8)), 3),
                    "source": "native_text",
                    "matched_value": target,
                }

    match = PARTY_SEPARATOR_RE.search(target)
    parts = [part for part in PARTY_SEPARATOR_RE.split(target) if _normal_space(part)]
    if len(parts) < 2:
        return None
    for page in pages:
        boxes, scores = [], []
        for part in parts:
            part_key = _normal_space(part)
            hit = next((b for b in page.get("blocks", []) if _contains_value(_normal_space(b.get("text", "")), part_key)), None)
            if not hit:
                boxes = []
                break
            boxes.append(_narrow_to_spans(hit, part_key))
            scores.append(float(hit.get("confidence", {}).get("score", 0.8)))
        if boxes and all(box for box in boxes):
            separator = _bbox(boxes)
            # The caption's separator is sometimes its own block set below
            # two side-by-side parties rather than between them, leaving it
            # outside the union of the parts.  Pull it in so the box covers
            # the whole caption a reader would highlight.
            if match:
                key = _normal_space(match.group(0)).strip(" .")
                reach = max(12.0, max(box[3] - box[1] for box in boxes)) * 1.5
                for candidate in page.get("blocks", []):
                    box = candidate.get("bbox")
                    if not box or _normal_space(candidate.get("text", "")).strip(" .") != key:
                        continue
                    if separator[1] - reach <= box[1] and box[3] <= separator[3] + reach:
                        boxes.append(box)
                        break
            return {
                "page": page["page_number"],
                "bbox": _bbox(boxes),
                "confidence": round(min(scores) * 0.9, 3),
                "source": "native_text_parts",
                "matched_value": target,
            }
    return None


def _metadata_provenance(metadata: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach a verified source region to every extracted metadata field."""
    provenance: dict[str, Any] = {}
    for field, value in metadata.items():
        if field == "counsel":
            # Each counsel row already carries the bbox of its own value.
            continue
        targets = value if isinstance(value, list) else [value]
        targets = [target.strip() for target in targets if isinstance(target, str) and target.strip()]
        occurrences = [found for found in (_locate_value(target, pages) for target in targets) if found]
        first = occurrences[0] if occurrences else {
            "page": None,
            "bbox": [],
            "confidence": 0.6 if value not in (None, "", []) else 0.0,
            "source": "pdf_metadata" if field.startswith("pdf_") else "no_source_span",
        }
        provenance[field] = {
            "value": value,
            # A list field (citations, sections) reports many values but one
            # box, and the box belongs to whichever value could be located --
            # not necessarily the first.  Naming it keeps the geometry
            # unambiguous; ``occurrences`` carries the rest.
            "matched_value": first.get("matched_value"),
            "page": first["page"],
            "bbox": first["bbox"],
            "confidence": first["confidence"],
            "source": first["source"],
        }
        if len(occurrences) > 1:
            provenance[field]["occurrences"] = occurrences
    return provenance


def _md_link(path: str) -> str:
    """Percent-encode an asset path for use inside a Markdown link.

    Source filenames in this corpus contain spaces and parentheses
    (``2018LHC1986 (1).pdf``), and an unencoded ``)`` closes the link early,
    so the image silently fails to render in every Markdown viewer.
    """
    return quote(path, safe="/")


def _attach_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Embed validator output in the canonical document without changing content."""
    from specter.validate_document import validate

    report = validate(result)
    scores = [float(score) for score in report["quality_profile"].values()]
    failed = list(report.get("retry_components", []))
    result["document"]["diagnostics"] = {
        "status": "PASS" if not failed else "REVIEW",
        "failed_components": failed,
        "quality_score": round(statistics.mean(scores), 3) if scores else 0.0,
        "quality_profile": report["quality_profile"],
        "policy": "diagnostic only; failed components are retried independently",
    }
    return result


class SpecterParser:
    def __init__(self, rtl_mode: str = "image", image_scale: float = 2.0, urdu_ocr: Any = None):
        if rtl_mode not in {"raw", "image", "both"}:
            raise ValueError("rtl_mode must be raw, image, or both")
        self.rtl_mode = rtl_mode
        self.image_scale = image_scale
        self.urdu_ocr = urdu_ocr

    def parse(self, pdf_path: str | Path, output_dir: str | Path | None = None,
              stem: str | None = None) -> dict[str, Any]:
        """``stem`` names this document's output where its filename cannot.

        A corpus that calls every file ``judgment.pdf`` needs a name assigned
        from outside, or 21,712 documents share one set of asset files.
        """
        pdf_path = Path(pdf_path)
        stem = stem or pdf_path.stem
        doc = fitz.open(pdf_path)
        try:
            page_widths = [float(page.rect.width) for page in doc]
            page_heights = [float(page.rect.height) for page in doc]
            raw_pages: list[list[dict[str, Any]]] = []
            table_pages: list[list[dict[str, Any]]] = []
            image_pages: list[bool] = []
            rejected_table_candidates = 0
            for page in doc:
                blocks: list[dict[str, Any]] = []
                rawdict = page.get_text("rawdict")
                image_pages.append(bool(page.get_images(full=True)) or any(raw.get("type") == 1 for raw in rawdict.get("blocks", [])))
                for raw in rawdict.get("blocks", []):
                    if raw.get("type") == 0:
                        # Use lines as the primitive.  Native exporters often
                        # store every visual line as a block; paragraph fusion
                        # below rebuilds semantic units from these primitives.
                        for line in _coalesce_lines(raw.get("lines", [])):
                            if _clean(_line_text(line)):
                                parsed = _text_block({"bbox": line.get("bbox"), "lines": [line]})
                                if parsed["text"]:
                                    blocks.append(parsed)
                raw_pages.append(blocks)
                tables: list[dict[str, Any]] = []
                try:
                    finder = page.find_tables()
                    for table_index, table in enumerate(finder.tables):
                        rows = table.extract()
                        widths = [len(row) for row in rows]
                        expected_cells = sum(widths)
                        nonempty_cells = sum(bool(str(value or "").strip()) for row in rows for value in row)
                        fill_ratio = nonempty_cells / expected_cells if expected_cells else 0.0
                        # PyMuPDF can interpret aligned prose as a table.  A
                        # low-fill candidate is rejected so its native words
                        # remain ordinary text blocks instead of disappearing
                        # behind a false table bbox.
                        if expected_cells and fill_ratio < 0.65:
                            rejected_table_candidates += 1
                            continue
                        tables.append({"index": table_index, "bbox": list(map(float, table.bbox)), "rows": rows, "object": table})
                except Exception:
                    tables = []
                table_pages.append(tables)

            top_keys, bottom_keys = _repeated_bands(raw_pages, page_heights)
            body_text = "\n".join(block["text"] for blocks in raw_pages for block in blocks)
            all_sizes = [size for blocks in raw_pages for block in blocks for size in block["font_sizes"] if size]
            median_size = statistics.median(all_sizes) if all_sizes else 10.0
            output_dir = Path(output_dir) if output_dir else None
            asset_dir = output_dir / "assets" / stem if output_dir else None
            if asset_dir:
                asset_dir.mkdir(parents=True, exist_ok=True)

            pages: list[dict[str, Any]] = []
            total_blocks = 0
            rtl_blocks = 0
            ocr_blocks = 0
            tables_count = 0
            form_headers_stripped = 0
            unreliable_blocks = 0
            cover_fields: list[dict[str, Any]] = []
            for page_index, page in enumerate(doc):
                page_no = page_index + 1
                page_blocks: list[dict[str, Any]] = []
                table_bboxes = [table["bbox"] for table in table_pages[page_index]]
                candidates: list[tuple[float, dict[str, Any]]] = []
                for source_index, raw in enumerate(raw_pages[page_index]):
                    kind, confidence, reasons = _classify(raw, top_keys, bottom_keys, page_heights[page_index], float(page.rect.width), median_size, table_bboxes)
                    if kind == "table_text_omitted":
                        continue
                    block = {
                        "id": f"p{page_no}_b{len(page_blocks)}",
                        "type": kind,
                        "text": raw["text"],
                        "markdown": raw["text"],
                        "bbox": raw["bbox"],
                        "spans": raw["spans"],
                        "line_bboxes": raw.get("line_bboxes", [raw["bbox"]]),
                        "font_sizes": raw["font_sizes"],
                        "contains_rtl": raw["contains_rtl"],
                        "rtl_visual_order": raw["rtl_visual_order"],
                        "reading_order": 0,
                        "decorative": bool(_is_page_number(raw["text"]) and raw["bbox"][1] < page_heights[page_index] * 0.12),
                        "language": raw["language"],
                        "source": "pymupdf-native",
                        "confidence": {"score": round(confidence, 3), "reasons": reasons},
                        "rtl": {
                            "contains_rtl": raw["contains_rtl"],
                            "visual_order_detected": raw["rtl_visual_order"],
                            "text_status": "visual_source" if raw["contains_rtl"] else "logical_native",
                        },
                    }
                    if raw["contains_rtl"]:
                        rtl_blocks += 1
                        block["rtl"]["fallback_policy"] = self.rtl_mode
                        if asset_dir and self.rtl_mode in {"image", "both"}:
                            image_name = f"page_{page_no:03d}_block_{len(page_blocks):03d}.png"
                            image_path = asset_dir / image_name
                            clip = fitz.Rect(*raw["bbox"]) & page.rect
                            pix = page.get_pixmap(matrix=fitz.Matrix(self.image_scale, self.image_scale), clip=clip, alpha=False)
                            pix.save(str(image_path))
                            block["rtl"]["image"] = str(image_path.relative_to(output_dir)).replace("\\", "/")
                            block["rtl"]["image_alt"] = "Rendered Urdu source crop; native text is retained for later correction."
                        if self.rtl_mode == "image":
                            block["markdown"] = f"![Urdu source crop]({_md_link(block['rtl'].get('image', ''))})\n\n> Native Urdu extraction (visual order): {raw['text']}"
                        if self.urdu_ocr and asset_dir:
                            line_paths = []
                            ocr_dir = asset_dir / "ocr_lines"
                            ocr_dir.mkdir(parents=True, exist_ok=True)
                            for line_index, line_bbox in enumerate(raw["line_bboxes"]):
                                line_path = ocr_dir / f"page_{page_no:03d}_block_{len(page_blocks):03d}_line_{line_index:02d}.png"
                                clip = fitz.Rect(*line_bbox) & page.rect
                                pix = page.get_pixmap(matrix=fitz.Matrix(max(4.0, self.image_scale * 2), max(4.0, self.image_scale * 2)), clip=clip, alpha=False)
                                pix.save(str(line_path))
                                line_paths.append(line_path)
                            ocr_lines = self.urdu_ocr.recognize(line_paths)
                            logical_text = "\n".join(item["text"] for item in ocr_lines if item.get("text"))
                            ocr_scores = [item.get("score") for item in ocr_lines if isinstance(item.get("score"), (int, float))]
                            if logical_text and any(_is_rtl(char) for char in logical_text):
                                ocr_blocks += 1
                                block["rtl"]["ocr"] = {
                                    "engine": self.urdu_ocr.name,
                                    "logical_text": logical_text,
                                    "lines": ocr_lines,
                                    "mean_score": round(statistics.mean(ocr_scores), 4) if ocr_scores else None,
                                    "accepted": True,
                                    "source": "rendered_line_ocr",
                                }
                                if self.rtl_mode == "image":
                                    block["markdown"] = f"![Urdu source crop]({_md_link(block['rtl'].get('image', ''))})\n\n{logical_text}"
                    candidates.append((raw["bbox"][1], block))

                candidates.sort(key=lambda item: (item[0], item[1]["bbox"][0]))
                native_blocks = [block for _, block in candidates]
                native_blocks = _merge_native_blocks(native_blocks, median_size)
                for block in native_blocks:
                    if block["type"] == "heading":
                        block["heading_level"] = _heading_level(block, median_size)
                    # Checked after merging so the reasons cover every span the
                    # finished paragraph ended up carrying.
                    reasons = _unreliable_text_reasons(block["spans"], block["text"])
                    if reasons:
                        block["text_status"] = {"status": "unreliable_native", "reasons": reasons}
                        unreliable_blocks += 1
                    # RTL blocks carry a rendered crop plus a caveat in their
                    # markdown; emphasis must not overwrite that.  Rendering is
                    # applied after merging so a paragraph's spans are complete.
                    if not block["contains_rtl"]:
                        block["markdown"] = _join_wrapped_words(_emphasis_markdown(block["spans"], block["text"]))
                candidates = [(block["bbox"][1], block) for block in native_blocks]

                for table in table_pages[page_index]:
                    rows = table["rows"]
                    md = _table_markdown(rows)
                    cells = _table_cells(page, table)
                    table_block = {
                        "id": f"p{page_no}_table_{table['index']}",
                        "type": "table",
                        "text": "\n".join(" | ".join(str(cell or "") for cell in row) for row in rows),
                        "markdown": md,
                        "bbox": table["bbox"],
                        "rows": rows,
                        "cells": cells,
                        "html": _table_html(rows),
                        "reading_order": 0,
                        "language": "en",
                        "source": "pymupdf-find_tables",
                        "table_strategy": "pymupdf-native-find_tables",
                        "confidence": {"score": 0.98, "reasons": ["native_table_detector"]},
                        "rtl": {"contains_rtl": False, "text_status": "logical_native"},
                    }
                    if _is_order_sheet_header(table_block):
                        # The header box ends after its own row; there is no
                        # ruled body to recover (E6).  Strip it from the prose
                        # stream as boilerplate and pull the one datum in its
                        # unruled body -- the order date -- geometrically.
                        table_block["type"] = "form_header"
                        table_block["confidence"] = {"score": 0.95, "reasons": ["order_sheet_boilerplate"]}
                        order_date = _order_sheet_date(table, page)
                        if order_date:
                            table_block["order_date"] = order_date
                        form_headers_stripped += 1
                    else:
                        cover_fields.extend({**field, "page": page_no} for field in _cover_fields_from_table(table_block))
                        tables_count += 1
                    candidates.append((table["bbox"][1], table_block))
                ordered_blocks = _reading_order([block for _, block in candidates], float(page.rect.width), float(page.rect.height))
                for order, block in enumerate(ordered_blocks):
                    block["reading_order"] = order
                    block["id"] = f"p{page_no}_b{order}"
                    page_blocks.append(block)
                cover_fields.extend({**field, "page": page_no} for field in _cover_fields_from_blocks(page_blocks))
                page_markdown = self._page_markdown(page_blocks)
                cursor = 0
                for block in page_blocks:
                    if block["type"] == "form_header":
                        # Excluded from page_markdown below, so it has no
                        # position in it; a null index is honest, an
                        # inferred one would silently misreport later blocks.
                        block["start_index"] = None
                        block["end_index"] = None
                        continue
                    value = block["markdown"]
                    start = page_markdown.find(value, cursor) if value else cursor
                    start = cursor if start < 0 else start
                    block["start_index"] = start
                    block["end_index"] = start + len(value)
                    cursor = block["end_index"]
                total_blocks += len(page_blocks)
                page_score = statistics.mean(float(b["confidence"]["score"]) for b in page_blocks) if page_blocks else 0.0
                pages.append({
                    "page_number": page_no,
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "rotation": int(page.rotation),
                    "blocks": page_blocks,
                    "markdown": page_markdown,
                    "confidence": round(page_score, 3),
                    "extraction": "digital" if page.get_text().strip() else "scanned_or_image_only",
                })

            document_order = 0
            for page in pages:
                for block in page["blocks"]:
                    block["document_reading_order"] = document_order
                    document_order += 1
            document_markdown = "\n\n---\n\n".join(page["markdown"] for page in pages if page["markdown"])
            digital_pages = sum(page["extraction"] == "digital" for page in pages)
            mode = "digital" if digital_pages == len(pages) else ("mixed" if digital_pages else "scanned_or_image_only")
            scores = [page["confidence"] for page in pages if page["blocks"]]
            metadata = _extract_metadata(body_text, dict(doc.metadata or {}))
            order_dates = [
                {**block["order_date"], "page": page["page_number"]}
                for page in pages
                for block in page["blocks"]
                if block["type"] == "form_header" and block.get("order_date")
            ]
            structured = _cover_metadata(cover_fields, order_dates)
            metadata["counsel"] = structured["counsel"]
            # The page's own structure beats a regex over the whole document:
            # a cover row states which date is the hearing date, where the
            # regex can only guess from among every date on the page.
            if structured.get("hearing_date"):
                metadata["hearing_date"] = structured["hearing_date"]
            # An ORDER SHEET states its date in the form body rather than in
            # the "decided on" phrasing the regex looks for, so it fills the
            # gap only where the regex found nothing.
            if not metadata.get("decision_date") and structured.get("order_date"):
                metadata["decision_date"] = structured["order_date"]
            result = {
                "schema_version": "specter.v1",
                "document": {
                    "source_file": str(pdf_path),
                    "source_name": pdf_path.name,
                    "engine": "pymupdf-native",
                    "extraction_mode": mode,
                    "page_count": len(pages),
                    "pdf_metadata": dict(doc.metadata or {}),
                    "metadata": metadata,
                    "metadata_provenance": _metadata_provenance(metadata, pages),
                    "cover_fields": cover_fields,
                    "fingerprint": _document_fingerprint(doc, raw_pages, page_widths, page_heights, top_keys, bottom_keys, image_pages),
                    "confidence": round(statistics.mean(scores), 3) if scores else 0.0,
                    "confidence_policy": "heuristic; validate low-confidence pages with a layout/OCR fallback",
                    "stats": {"blocks": total_blocks, "rtl_blocks": rtl_blocks, "ocr_blocks": ocr_blocks, "tables": tables_count, "table_candidates_rejected": rejected_table_candidates, "form_headers_stripped": form_headers_stripped, "unreliable_text_blocks": unreliable_blocks, "digital_pages": digital_pages},
                    "urdu_ocr": self.urdu_ocr.name if self.urdu_ocr else None,
                    "warnings": (["Arabic-script text is preserved as native visual-source text plus rendered crops."] if rtl_blocks else []) + (["One or more pages have no extractable text; OCR is not enabled in the digital-first path."] if mode != "digital" else []) + ([f"{unreliable_blocks} block(s) carry unreliable native text (see block.text_status); their characters are retained but must not be indexed or cited without OCR/VLM review."] if unreliable_blocks else []),
                },
                "pages": pages,
                "markdown": document_markdown,
            }
            return result
        finally:
            doc.close()

    @staticmethod
    def _page_markdown(blocks: list[dict[str, Any]]) -> str:
        parts = []
        for block in blocks:
            if block["type"] == "form_header":
                # Boilerplate stamped on nearly every page (E5/E6); keeping it
                # out of the prose stream keeps every RAG chunk from repeating it.
                continue
            if block["type"] == "table":
                parts.append(block["markdown"])
            elif block["type"] == "heading":
                parts.append("#" * block.get("heading_level", 1) + " " + block["markdown"])
            else:
                parts.append(block["markdown"])
        return "\n\n".join(part for part in parts if part.strip())


def write_result(result: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    _attach_diagnostics(result)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = result["document"].get("output_stem") or Path(result["document"]["source_name"]).stem
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(result["markdown"], encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse digital legal PDFs into Specter JSON and Markdown.")
    parser.add_argument("pdf", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/run"))
    parser.add_argument("--rtl-mode", choices=["raw", "image", "both"], default="image")
    parser.add_argument("--urdu-ocr", choices=["none", "rapidocr", "trocr"], default="none")
    parser.add_argument("--urdu-model-dir", default="models/rapidocr_arabic")
    parser.add_argument("--urdu-model-id", default="mohammadalihumayun/trocr-ur")
    args = parser.parse_args()
    pdfs: list[Path] = []
    for requested in args.pdf:
        if requested.is_dir():
            pdfs.extend(sorted(requested.glob("*.pdf")))
        elif any(char in str(requested) for char in "*?["):
            pdfs.extend(sorted(requested.parent.glob(requested.name)))
        else:
            pdfs.append(requested)
    if not pdfs:
        parser.error("No PDF files found")

    urdu_ocr = None
    if args.urdu_ocr != "none":
        from specter.urdu_ocr import build_urdu_ocr
        urdu_ocr = build_urdu_ocr(args.urdu_ocr, args.urdu_model_dir, args.urdu_model_id)
    engine = SpecterParser(rtl_mode=args.rtl_mode, urdu_ocr=urdu_ocr)
    summary = []
    for path in pdfs:
        result = engine.parse(path, args.out)
        json_path, md_path = write_result(result, args.out)
        summary.append({"file": str(path), "json": str(json_path), "markdown": str(md_path), **result["document"]["stats"], "confidence": result["document"]["confidence"], "mode": result["document"]["extraction_mode"]})
        print(f"{path.name}: {result['document']['extraction_mode']} pages={result['document']['page_count']} blocks={result['document']['stats']['blocks']} confidence={result['document']['confidence']}")
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
