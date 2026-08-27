"""Statutes: what a legislative document has that a judgment does not.

A judgment is argued and decided; a statute is *enacted and numbered*.  It has
no parties, no bench and no decision date.  What it has instead is a title, an
enactment number, a commencement date, a preamble, and -- the part that matters
for retrieval -- a numbered run of sections, because a lawyer cites section 302
of the Penal Code, not page 14 of it.

Sections are found from the blocks rather than from a flat string.  The parser
already knows a heading's font, weight and box, and in this corpus a section
heading is set bold or underlined; using that as corroboration keeps a
sub-section reference inside a sentence from being read as a new section.

Detection is checkable without any annotation: a statute numbers its sections
1..N with no gaps, so a hole in the run is a miss.  Measured over 60 statutes,
57 of the 58 that number their sections at all have a complete run, and the one
hole is a section the printed Act omits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ``269_asian_development_bank_ordinance_1971.pdf`` in a category folder: the
# scraper's id, a slug of the title, and usually the year.
STATUTE_PATH = re.compile(r"^(?P<statute_id>\d+)_(?P<slug>.+?)\.pdf$", re.IGNORECASE)
SLUG_YEAR = re.compile(r"_(?P<year>1[6-9]\d{2}|20\d{2})(?:_|$)")
CATEGORY_SUFFIX = re.compile(r"_laws?$", re.IGNORECASE)

# "ACT NO. XLVII OF 1971", "ORDINANCE NO. IX of 1971".  Roman numerals are the
# norm; a few modern acts use Arabic.
#
# Two passes.  The number is usually printed on a line of its own, and that
# line is unambiguous.  Where it is not, it is set inside the title block --
# but the same words also introduce *other* acts once the body starts ("under
# the Companies Act No. VII of 1913"), so the unanchored search is confined to
# the opening block.  Anchoring alone missed the number on 8 of 24 statutes
# that do print one.  The lookbehind is deliberately narrower than a word
# boundary: the line is routinely set as ``1ACT No. LXIV OF 1975``, the ``1``
# being a footnote marker glued to the word, and no word boundary exists
# between two word characters -- so ``\b`` skipped every statute that prints
# its number that way, silently.
_ACT_NUMBER = (r"(?<![A-Za-z])(?P<kind>ACT|ORDINANCE|ORDER|REGULATION|RESOLUTION)\s+NO\.?\s*"
               r"(?P<number>[IVXLCDM]+|\d+)\s*(?:OF|of)\s*(?P<year>\d{4})")
ACT_NUMBER_RE = re.compile(r"(?im)^\s*\d{0,3}\s*" + _ACT_NUMBER)
ACT_NUMBER_LOOSE_RE = re.compile(r"(?i)" + _ACT_NUMBER)
TITLE_BLOCK_CHARS = 2000
# The commencement date is printed on its own in square brackets.
COMMENCEMENT_RE = re.compile(r"(?m)^\s*\[\s*(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+\w+,?\s+\d{4})\s*\]")
LONG_TITLE_RE = re.compile(r"(?im)^\s*(An\s+(?:Act|Ordinance|Order|Regulation)\s+(?:further\s+)?to\b.*)$")
PREAMBLE_RE = re.compile(r"(?m)^\s*(?:AND\s+)?WHEREAS\b")
# The title is centred and often wraps: "THE CHEMICAL WEAPONS CONVENTION
# IMPLEMENTATION" / "ORDINANCE, 2000".  Requiring one line lost 1 in 6 titles,
# so the match may cross a single break -- but no more, or it runs on into the
# contents list below it.
TITLE_RE = re.compile(r"(?ims)^[ \t]*(THE\s+.{4,140}?(?:ACT|ORDINANCE|ORDER|REGULATION|CODE)[ \t]*,?[ \t]*\d{4})[ \t]*$")
MAX_TITLE_BREAKS = 1
# A repealed law is published as a stub with no content at all.  That is a real
# state of the corpus, not a parse failure, and saying so is more useful than
# reporting every field as missing.
REPEALED_RE = re.compile(r"(?im)^\s*THIS\s+LAW\s+HAS\s+BEEN\s+REPEALED")
CONTENTS_RE = re.compile(r"(?im)^\s*CONTENTS?\s*$")

# Structural markers.  Chapters and Parts are uncommon here (18% and 5%) but
# when present they group the sections beneath them.
# The number may open an amendment bracket -- "5[3. Interpretation-clause" --
# and the bracket belongs to the footnote, not to the section.  A footnote may
# also be marked with a symbol set in the same run as the number ("*11."),
# where no styling separates the two and only the character itself does.
# Where the whole heading is set in one run, "4[10. Power to make rules" has no
# span boundary for the marker stripper to use either, so the digit-then-bracket
# form is allowed here as well; the bracket is what distinguishes it from the
# section number, which never carries one.
SECTION_RE = re.compile(
    r"^\s*(?:\d{1,3}\s*\[\s*)?[*†‡§]?\s*\[?\s*(?P<number>\d{1,3}[A-Z]{0,2})\.\s*(?P<title>.*)$")
CHAPTER_RE = re.compile(r"(?i)^\s*CHAPTER\s+(?P<number>[IVXLCDM]+|\d+[A-Z]?)\b\s*(?P<title>.*)$")
PART_RE = re.compile(r"(?i)^\s*PART\s+(?P<number>[IVXLCDM]+|\d+[A-Z]?)\b\s*(?P<title>.*)$")
SCHEDULE_RE = re.compile(
    r"(?i)^\s*(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH"
    r"|\d+(?:ST|ND|RD|TH)?)?\s*SCHEDULE\b\s*$"
)
DEFINITIONS_RE = re.compile(r"(?i)^\s*definitions?\b|^\s*interpretation\b")

MAX_SECTION_TITLE = 200
# A section reads "12. Title of the section.— body text...".  The heading ends
# at the dash, or at the first sentence stop that is followed by the body; a
# title carrying its own first paragraph is not a title.
HEADING_END_RE = re.compile(r"\s*\.?\s*(?:[—–]|_{2,}|-{2,})\s*|\s*\.\s*-\s*|\.\s+(?=[A-Z(]|\d+\[)")


def _heading_title(value: str) -> str:
    """Trim a section heading to the heading, dropping the body that follows."""
    split = HEADING_END_RE.search(value)
    title = value[: split.start()] if split else value
    return title.strip(" .—–-:")


def statute_labels(pdf_path: str | Path) -> dict[str, Any] | None:
    """Read what the corpus layout states about a statute.

    The category is the folder, and the filename carries the scraper's id, a
    slug of the title and usually the year.  Corroborating labels only, exactly
    as for judgments: they fill a gap or record a disagreement, never overwrite
    what the page says.
    """
    path = Path(pdf_path)
    match = STATUTE_PATH.match(path.name)
    if not match:
        return None
    slug = match.group("slug")
    year = SLUG_YEAR.search("_" + slug + "_")
    category = CATEGORY_SUFFIX.sub("", path.parent.name).replace("_", " ").strip()
    return {
        "document_kind": "statute",
        "statute_id": match.group("statute_id"),
        "title_slug": slug,
        "year": year.group("year") if year else None,
        "category": category.title() or None,
        "source": "filename",
    }


def _roman_to_int(value: str) -> int | None:
    numerals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not value or any(ch not in numerals for ch in value.upper()):
        return None
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = numerals[char]
        total = total - current if current < previous else total + current
        previous = max(previous, current)
    return total or None


def _is_heading_block(block: dict[str, Any]) -> bool:
    """Does this block look like a heading rather than running prose?

    In this corpus a section heading is set bold or underlined.  Requiring that
    is what stops a sub-section reference mid-sentence, or a numbered list item
    inside a section, from being read as a new section.
    """
    if block.get("type") in {"heading", "form_header"}:
        return True
    # Blank spans carry no styling and are not the heading; a block whose first
    # spans are stray whitespace was being judged unemphasised and dropped.
    for span in _visible_spans(block)[:3]:
        emphasis = span.get("emphasis") or {}
        if emphasis.get("bold") or emphasis.get("underline"):
            return True
    return False


def _visible_spans(block: dict[str, Any]) -> list[dict[str, Any]]:
    """The block's spans that actually print something."""
    return [span for span in (block.get("spans") or []) if (span.get("text") or "").strip()]


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


SAME_LINE_TOLERANCE = 2.0
MAX_FOOTNOTE_MARKER = 4
# An amendment footnote is printed against the section number as a bare digit
# or as a digit opening a bracket -- "7" then "19.", or "1[" then "2.".
FOOTNOTE_LEAD_RE = re.compile(r"^\s*\d{0,3}\s*\[?\s*$")


def _heading_line(block: dict[str, Any]) -> str:
    """The block's first line, without a footnote marker in front of it.

    An amended section is printed with its footnote marker immediately before
    the number and nothing between them, so "11." reads as "211." and section
    11 is lost while a section 211 is invented.  The marker is set plain while
    the number begins the bold or underlined run, so the heading is taken to
    start where the emphasis starts.

    Working from the blocks is what makes this exact.  On a flat string the two
    are indistinguishable, which is why a text-only parser has to guess.
    """
    line = _first_line(block.get("text", ""))
    spans = _visible_spans(block)
    if not spans:
        return line
    sizes = [span["size"] for span in spans if span.get("size")]
    body_size = max(sizes) if sizes else 0.0
    lead, marker_spans = "", []
    for span in spans:
        text = span.get("text", "")
        if not FOOTNOTE_LEAD_RE.match(lead + text):
            break
        lead += text
        marker_spans.append(span)
    if not lead or len(lead) > MAX_FOOTNOTE_MARKER or not line.startswith(lead):
        return line

    # A marker is only stripped where the page itself distinguishes it from the
    # number: set smaller, closed with the bracket that opens an amendment
    # note, or set plain where the heading that follows is emphasised.  Digits
    # that differ in none of these ways may simply be a number split across
    # spans, and guessing there would turn section 12 into section 2.
    following = spans[len(marker_spans)] if len(marker_spans) < len(spans) else {}
    heading_emphasis = following.get("emphasis") or {}
    marker_emphasised = any((span.get("emphasis") or {}).get(key)
                            for span in marker_spans for key in ("bold", "underline"))
    smaller = any(body_size and span.get("size") and span["size"] < body_size - 1.0
                  for span in marker_spans)
    plain_before_heading = not marker_emphasised and (heading_emphasis.get("bold")
                                                      or heading_emphasis.get("underline"))
    if smaller or lead.strip().endswith("[") or plain_before_heading:
        return line[len(lead):]
    return line


def _inline_heading(block: dict[str, Any]) -> tuple[str, list[float]] | None:
    """A section heading that starts part-way through a block, with its box.

    A heading is not always given a block of its own: where it follows the
    previous section's closing words in the same text flow, PyMuPDF returns
    both as one block and the heading is not its first line.  Section 28 of the
    Co-operative Societies Act is printed that way, and a first-line check
    loses it -- which also means its text is folded into section 27, so a
    reader asking for section 28 would be handed the wrong law.

    The page still marks it: the heading opens an emphasised run where what
    precedes it is plain.  The box returned covers that run alone, not the
    whole block, so it still contains only what it points at.
    """
    spans = _visible_spans(block)
    for index in range(1, len(spans)):
        emphasis = spans[index].get("emphasis") or {}
        previous = spans[index - 1].get("emphasis") or {}
        if not (emphasis.get("bold") or emphasis.get("underline")):
            continue
        if previous.get("bold") or previous.get("underline"):
            continue                                  # already inside the run
        line = _first_line("".join(span.get("text", "") for span in spans[index:]))
        if not SECTION_RE.match(line):
            continue
        boxes = [span["bbox"] for span in spans[index:]
                 if len(span.get("bbox") or ()) == 4
                 and abs(span["bbox"][1] - spans[index]["bbox"][1]) < SAME_LINE_TOLERANCE]
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else []
        return line, bbox
    return None


def detect_structure(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find the statute's numbered structure, in document order.

    Returns one entry per marker -- chapter, part, section, definitions,
    schedule or schedule item -- each naming the page and block it starts at,
    so a consumer can go from "section 12" to the region of the page that
    states it.
    """
    found: list[dict[str, Any]] = []
    in_schedule = False
    seen_section = False
    for page in pages:
        for block in page.get("blocks", []):
            if block.get("type") in {"header", "footer"}:
                continue
            line = _heading_line(block)
            if not line:
                continue
            marked: list[tuple[dict[str, Any], Any]] = []
            box = block.get("bbox")
            chapter = CHAPTER_RE.match(line)
            part = PART_RE.match(line)
            if chapter:
                marked = [({"kind": "chapter", "number": chapter.group("number"),
                            "title": chapter.group("title")}, box)]
                in_schedule = False
            elif part:
                marked = [({"kind": "part", "number": part.group("number"),
                            "title": part.group("title")}, box)]
                in_schedule = False
            elif SCHEDULE_RE.match(line):
                marked = [({"kind": "schedule", "number": None, "title": line.strip()}, box)]
                # A schedule cannot precede section 1 of the body, so one that
                # does is the table of contents announcing it.  Acting on that
                # listing swallowed the whole statute: every section of the
                # Commercial Documents Evidence Act sits after its contents
                # page, and all four were read as schedule entries.
                in_schedule = seen_section
            else:
                # Two headings can share one block -- and even one line, where a
                # section is omitted in print and the next follows immediately.
                # Both are taken; an inline run carries its own evidence, being
                # emphasised where what precedes it is not, so it does not also
                # have to begin the block.
                heads = []
                first = SECTION_RE.match(line)
                if first and _is_heading_block(block):
                    heads.append((first, box))
                inline = _inline_heading(block)
                if inline:
                    second = SECTION_RE.match(inline[0])
                    if second and (not heads or second.group("number") != heads[0][0].group("number")):
                        heads.append((second, inline[1]))
                for section, where in heads:
                    title = _heading_title(section.group("title"))
                    # A schedule numbers its own entries from 1, so past a
                    # schedule heading those numbers are not sections.  Counting
                    # them as sections both invents members of the section run
                    # and punches holes in it: the Provident Funds Act reported
                    # 11 missing sections that were listed banks in its schedule.
                    kind = "schedule_item" if in_schedule else (
                        "definitions" if DEFINITIONS_RE.match(title) else "section")
                    seen_section = seen_section or kind != "schedule_item"
                    marked.append(({"kind": kind, "number": section.group("number"),
                                    "title": title[:MAX_SECTION_TITLE]}, where))
            for entry, where in marked:
                found.append({
                    **entry,
                    "page": page["page_number"],
                    "block_id": block.get("id"),
                    "bbox": where,
                })
    return found


def section_sequence(structure: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the section run for holes, which is how a miss shows up.

    A statute numbers its sections 1..N.  Lettered insertions (3A) sit beside
    their parent and do not advance the run, and a section repeated in the
    table of contents is counted once.  A schedule's own numbered entries are
    excluded -- they restart at 1 and run past the last section, which both
    invented members of the run and punched holes in it.  A gap therefore means
    a section was not detected -- a correctness signal that needs no annotation.
    """
    numbers = sorted({int(re.match(r"\d+", item["number"]).group(0))
                      for item in structure
                      if item["kind"] in {"section", "definitions"} and item.get("number")
                      and re.match(r"\d+", item["number"])})
    if not numbers:
        return {"sections": 0, "highest": 0, "missing": [], "complete": None}
    missing = [n for n in range(1, numbers[-1] + 1) if n not in set(numbers)]
    return {
        "sections": len(numbers),
        "highest": numbers[-1],
        "missing": missing[:50],
        "complete": not missing,
    }


def _statute_title(head: str) -> str | None:
    """The statute's own name, which may be set over two centred lines."""
    for match in TITLE_RE.finditer(head):
        value = match.group(1)
        if value.count("\n") <= MAX_TITLE_BREAKS:
            return re.sub(r"\s+", " ", value).strip()
    return None


def extract_statute_metadata(text: str, structure: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Read a statute's identity from its own opening pages."""
    head = text[:20000]
    title = _statute_title(head)
    number = ACT_NUMBER_RE.search(head) or ACT_NUMBER_LOOSE_RE.search(head[:TITLE_BLOCK_CHARS])
    commencement = COMMENCEMENT_RE.search(head)
    long_title = LONG_TITLE_RE.search(head)
    sequence = section_sequence(structure or [])
    kind = number.group("kind").title() if number else None
    if not kind and title:
        for candidate in ("Ordinance", "Act", "Order", "Regulation", "Code"):
            if candidate.upper() in title.upper():
                kind = candidate
                break
    return {
        "document_kind": "statute",
        "title": title,
        "repealed": bool(REPEALED_RE.search(head)),
        "statute_kind": kind,
        "act_number": number.group("number") if number else None,
        "act_number_value": _roman_to_int(number.group("number")) if number else None,
        "act_year": number.group("year") if number else None,
        "commencement_date": commencement.group("date") if commencement else None,
        "long_title": re.sub(r"\s+", " ", long_title.group(1)).strip()[:400] if long_title else None,
        "has_preamble": bool(PREAMBLE_RE.search(head)),
        "has_contents": bool(CONTENTS_RE.search(head)),
        "section_count": sequence["sections"],
        "highest_section": sequence["highest"],
        "sections_complete": sequence["complete"],
        "missing_sections": sequence["missing"],
    }
