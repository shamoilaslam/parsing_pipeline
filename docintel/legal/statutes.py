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
#
# The year is not part of every title.  "THE PAKISTAN PENAL CODE" prints none,
# and requiring one left 10 of 77 statutes falling back to their filename slug
# -- so every section of the Penal Code was labelled "pakistan penal code
# ppc1860 under review".  Making the year and a trailing full stop optional
# recovers 7 of those 10 and changes not one title that was already found.
TITLE_RE = re.compile(r"(?ims)^[ \t]*(THE\s+.{4,140}?(?:ACT|ORDINANCE|ORDER|REGULATION|CODE)"
                      r"(?:[ \t]*,?[ \t]*\d{4})?)[ \t]*\.?[ \t]*$")
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
# A chapter designation stands alone or carries a title; it never runs on
# into a sentence.  Section 109 of the Penal Code says "...an offence referred
# to in Chapter XVI shall be liable to punishment of ta'zir", and read as a
# heading that put every abetment section under the homicide chapter.
CHAPTER_RE = re.compile(r"(?i)^\s*CHAPTER\s+(?P<number>[IVXLCDM]+|\d+[A-Z]?)\b(?P<title>\s*$|\s+(?-i:(?![a-z]))[^\n]*)$")
PART_RE = re.compile(r"(?i)^\s*PART\s+(?P<number>[IVXLCDM]+|\d+[A-Z]?)\b\s*(?P<title>.*)$")
# The number is printed on either side of the word -- "FIRST SCHEDULE" but also
# "SCHEDULE 1" -- and an amended schedule carries its footnote marker glued to
# the front, exactly as an amended section does: the Stamp Act heads its duty
# table "1[SCHEDULE 1".  Allowing neither, the heading was not found, so
# ``in_schedule`` never turned on and the schedule's own entries -- which
# number from 1 -- were read as sections of the Act.  That put 123 of the Stamp
# Act's 144 "sections" in collision with a real one, and did the same to 15
# other statutes.
#
# The heading also states which section calls the schedule up -- "THE SCHEDULE
# (See section 41)", "SCHEDULE [see SECTION 5]" -- and the rule printed above it
# is returned inside the same block: "_____________ THE SCHEDULE (See section
# 41)".  Both are standard, and neither was allowed.  Measured over 465 parsed
# statutes, allowing them matches 18 further headings in 17 statutes, every one
# of them a real schedule.
SCHEDULE_RE = re.compile(
    r"(?i)^\s*[_\-—–]*\s*(?:\d{1,3}\s*\[\s*)?(?:THE\s+)?"
    r"(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH"
    r"|\d+(?:ST|ND|RD|TH)?)?\s*SCHEDULE\b"
    r"(?:\s*[-—–]?\s*(?:[IVXLCDM]+|\d+))?\s*\]?"
    r"(?:\s*[\[(]\s*See[^)\]]{0,60}[\])])?\s*[_\-—–]*\s*$"
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
# What has to remain once a footnote marker is stripped: the section number the
# marker was standing in front of.
NUMBER_LEAD_RE = re.compile(r"^\s*\[?\s*\d")


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
    if not (smaller or lead.strip().endswith("[") or plain_before_heading):
        return line
    # A footnote marker stands *before* the section number, so whatever is left
    # after stripping one must still begin with a number.  Where the number was
    # set plain and only its title emphasised -- "7" then ". Chief Executive
    # Officer" -- the number itself matched the marker shape, and stripping it
    # left the line starting at its own full stop.  Section 7 of the Public
    # Private Partnership Authority Act was lost exactly that way.
    stripped = line[len(lead):]
    return stripped if NUMBER_LEAD_RE.match(stripped) else line


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
        # A section heading opens a sentence, so what precedes it on the line has
        # to have finished.  The Succession Act prints "Explanation 1.- A married
        # woman may dispose by will..." with the number set bold, and read as a
        # heading that invented sections 1, 2 and 3 in the middle of Chapter II.
        # The run this exists for -- a heading following a section omitted in
        # print, "325. 2[* * *] 326. ..." -- ends in punctuation and survives.
        before = "".join(span.get("text", "") for span in spans[:index]).rstrip()
        # An amendment marker sits between the two -- "9." then "4" then
        # "[10. Power to make rules" -- and it is not the end of the sentence.
        before = re.sub(r"\d{1,3}$", "", before).rstrip()
        if before and before[-1] not in ".]*:;—–-":
            continue
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


# --- Where the enacted text begins ---------------------------------------
# A statute prints its contents list *before* the enacting formula and its
# sections after it, and both number from 1.  Read as sections, the index
# entries duplicate every real one and carry a dotted leader where the law
# should be -- 91 of them in the Sales Tax Act, and one stray "120B" in the
# Penal Code that made every section after it look like a step backwards.
#
# The formula itself is the boundary: the act number, the commencement date,
# the long title, the preamble, or the title with its year.  Several of those
# are also printed on the cover, ahead of the contents, so the *last* one is
# taken -- but only while it still leaves most of the sections after it, which
# is what stops a stray match deep in the body from swallowing the statute.
# The title is deliberately NOT one of these.  A statute states its own name
# inside section 1 -- "This Ordinance may be called the Islamabad Rent
# Restriction Ordinance, 2001." -- and that sentence matches the title pattern,
# so it anchored the body *after* section 1 and cut the section away.  An
# enacting formula is a formula; a title is a heading that also occurs in prose.
ENACTING_ANCHORS = (ACT_NUMBER_RE, COMMENCEMENT_RE, LONG_TITLE_RE, PREAMBLE_RE)
BODY_SHARE = 0.5


def _body_start(blocks: list[dict[str, Any]], sections: list[tuple[int, str]]) -> int:
    """Index of the last block of the front matter, or -1 if there is none.

    ``sections`` pairs each section marker's block position with its number.

    This only says where the body begins.  What is dropped ahead of it is
    decided marker by marker, in ``detect_structure``.
    """
    if not sections:
        return -1
    best = -1
    for index, block in enumerate(blocks):
        text = block.get("text") or ""
        if not any(anchor.search(text) for anchor in ENACTING_ANCHORS):
            continue
        if sum(1 for mark, _ in sections if mark > index) >= BODY_SHARE * len(sections):
            best = index
    return best


def _drop_back_references(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a section number that goes backwards over one already seen.

    The Penal Code prints "127. Receiving property taken by war or depredation
    mentioned in sections 125 and" / "126. Whoever receives any property...".
    The "126." completes the cross-reference, but it opens a block and is set
    like a heading, so it read as one -- inventing a second section 126 and
    taking section 127's text away from it along the way.

    A repeat is what makes it safe to act on: a number never seen before is
    kept wherever it appears, so a section genuinely printed out of order
    survives.
    """
    kept: list[dict[str, Any]] = []
    highest = 0
    seen: set[str] = set()
    for entry in entries:
        if entry["kind"] not in {"section", "definitions"}:
            kept.append(entry)
            continue
        number = _leading_number(entry.get("number"))
        if number is not None and number < highest and entry["number"] in seen:
            continue
        kept.append(entry)
        if number is not None:
            highest = max(highest, number)
            seen.add(entry["number"])
    return kept


def _leading_number(value: str | None) -> int | None:
    """The digits a section number opens with: "302A" advances the run at 302."""
    match = re.match(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _restarts(entries: list[dict[str, Any]]) -> int:
    """How many times the section run begins again at 1.

    Estacode is 1,044 pages holding some sixty separate rule-sets, each
    numbered from 1.  Read as one Act it reported 1,179 sections in which
    "section 1" resolved to 62 different texts.  Every real statute in the
    corpus numbers its sections once; the count separates the two outright
    rather than by a threshold.
    """
    return sum(1 for entry in entries
               if entry["kind"] in {"section", "definitions"} and entry.get("number") == "1")


def detect_structure(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find the statute's numbered structure, in document order.

    Returns one entry per marker -- chapter, part, section, definitions,
    schedule or schedule item -- each naming the page and block it starts at,
    so a consumer can go from "section 12" to the region of the page that
    states it.
    """
    found: list[dict[str, Any]] = []
    # A schedule runs to the end of the document, and it may carry structure of
    # its own.  The Carriage by Air Act reproduces the Montreal Convention as
    # its Fourth Schedule, chapters and all, and treating "CHAPTER I" as the end
    # of the schedule put the Convention's 106 numbered Articles back in
    # collision with the Act's own sections.
    in_schedule = False
    seen_section = False
    for page in pages:
        for block in page.get("blocks", []):
            line = _heading_line(block)
            if not line:
                continue
            marked: list[tuple[dict[str, Any], Any]] = []
            box = block.get("bbox")
            chapter = CHAPTER_RE.match(line)
            part = PART_RE.match(line)
            # A footnote is not a section heading: "1Subs. by Act XIV of 2011,
            # s.63" opens with a number and would read as one.  Header and
            # footer blocks are skipped for the same reason -- but a chapter
            # designation is structural wherever it is set, and the Penal Code
            # prints CHAPTER V and CHAPTER XIX as running headers, so skipping
            # those blocks outright lost two of its twenty-three chapters.
            if block.get("type") in {"header", "footer", "footnote"} and not (chapter or part):
                continue
            if chapter:
                marked = [({"kind": "chapter", "number": chapter.group("number"),
                            "title": chapter.group("title").strip()}, box)]
            elif part:
                marked = [({"kind": "part", "number": part.group("number"),
                            "title": part.group("title")}, box)]
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
    blocks = [block for page in pages for block in page.get("blocks", [])]
    position = {block["id"]: index for index, block in enumerate(blocks) if block.get("id")}
    marks = [(position[entry["block_id"]], entry.get("number") or "")
             for entry in found
             if entry["kind"] in {"section", "definitions"} and entry.get("block_id") in position]
    start = _body_start(blocks, marks)
    if start >= 0:
        # A contents list is a *duplicate* of the body: what it names is printed
        # again below it.  So a marker ahead of the body is dropped only where
        # the body repeats it, and one that appears nowhere else is kept
        # whatever sits above it.  Cutting wholesale instead removed section 1
        # from 77 statutes -- section 1 is the one that recites the statute's
        # own name, and that sentence reads as a title.
        repeated = {(entry["kind"], entry.get("number")) for entry in found
                    if position.get(entry.get("block_id"), -1) > start}
        found = [entry for entry in found
                 if position.get(entry.get("block_id"), start + 1) > start
                 or (entry["kind"], entry.get("number")) not in repeated]
    # In a compendium a number going backwards opens the next rule-set rather
    # than completing a cross-reference, so the back-reference rule is only
    # safe where the run is numbered once.
    if _restarts(found) <= 1:
        found = _drop_back_references(found)
    return found + _sections_numbered_on_their_own_line(pages, found)


BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*$")


def _sections_numbered_on_their_own_line(
        pages: list[dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover a section whose number is set alone, with no full stop after it.

    Some prints put the number on its own line and the title on the next --
    "3" then "Establishment of the Authority.-- (1) there shall be..." -- so
    the heading pattern, which requires "3.", never matches and the section is
    lost. A bare number is far too common to read as a heading on its own; a
    page number looks exactly the same.

    What makes this safe is that it only ever fills a hole the run itself
    reports. The numbers already detected fix the range, and only a number
    missing from inside it is looked for, so this can neither invent a section
    beyond the last nor overwrite one that was found properly.
    """
    sequence = section_sequence(found)
    missing = set(sequence["missing"])
    if not missing:
        return []
    recovered: dict[int, dict[str, Any]] = {}
    for page in pages:
        # Header and footer blocks are *not* skipped here, unlike everywhere
        # else: a section number standing alone at the edge of the page is
        # exactly what a page number looks like, so the layout engine types it
        # as one.  That misreading is the reason the section went missing.
        blocks = page.get("blocks", [])
        for index, block in enumerate(blocks):
            number_match = BARE_NUMBER_RE.match(block.get("text", ""))
            if not number_match or int(number_match.group(1)) not in missing:
                continue
            if index + 1 >= len(blocks):
                continue
            following = blocks[index + 1]
            # What follows a real section number is its heading, set bold or
            # underlined like every other heading in the document.  A page
            # number followed by ordinary prose is not.
            if not _is_heading_block(following):
                continue
            title = _heading_title(_first_line(following.get("text", "")))
            if not title or not title[:1].isupper():
                continue
            number = int(number_match.group(1))
            # A later occurrence is the body; an earlier one is the contents
            # page, whose box points at the index rather than the section.
            recovered[number] = {
                "kind": "definitions" if DEFINITIONS_RE.match(title) else "section",
                "number": str(number),
                "title": title[:MAX_SECTION_TITLE],
                "page": page["page_number"],
                "block_id": block.get("id"),
                "bbox": block.get("bbox"),
            }
    return list(recovered.values())


def section_sequence(structure: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the section run for holes, which is how a miss shows up.

    A statute numbers its sections 1..N.  Lettered insertions (3A) sit beside
    their parent and do not advance the run, and a section repeated in the
    table of contents is counted once.  A schedule's own numbered entries are
    excluded -- they restart at 1 and run past the last section, which both
    invented members of the run and punched holes in it.  A gap therefore means
    a section was not detected -- a correctness signal that needs no annotation.
    """
    numbers = sorted({_leading_number(item["number"])
                      for item in structure
                      if item["kind"] in {"section", "definitions"}
                      and _leading_number(item.get("number")) is not None})
    restarts = _restarts(structure)
    if not numbers:
        return {"sections": 0, "highest": 0, "missing": [], "complete": None, "restarts": restarts}
    missing = [n for n in range(1, numbers[-1] + 1) if n not in set(numbers)]
    return {
        "sections": len(numbers),
        "highest": numbers[-1],
        "missing": missing[:50],
        # A compendium numbers each of its rule-sets from 1, so it has no single
        # run to be complete or incomplete.  Saying so is more honest than
        # reporting a hole for every number the collection happens to skip.
        "complete": None if restarts > 1 else not missing,
        "restarts": restarts,
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
        "numbering_restarts": sequence["restarts"],
        "section_count": sequence["sections"],
        "highest_section": sequence["highest"],
        "sections_complete": sequence["complete"],
        "missing_sections": sequence["missing"],
    }
