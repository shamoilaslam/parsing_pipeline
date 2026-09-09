"""Section-addressable statutes: the text a lawyer actually cites.

`detect_structure` says *where* every section starts. This says what each one
**says**, which is what a retrieval system needs: nobody asks for page 106 of
the Penal Code, they ask for section 302.

A section's text is the run of blocks from its heading up to the next
structural marker. Four kinds of block must be kept out of that run, and each
was found contaminating a real section:

* **Page footers.** `Page 36 of 179` sat inside section 34.
* **Amendment footnotes.** `1Subs. by the Law Reforms Ordinance, 1972` sat
  inside section 511. These are only excludable because the parser now types
  them; header/footer detection works by repetition and a footnote differs on
  every page.
* **The next section's own marker.** A repealed section prints as
  `325. 2[* * *]`, which carries no title, so it ran on into section 324.
* **A sub-part heading.** `Of Fraudulent Deeds and Dispositions of Property`
  ran on into section 420.

The heading is taken from the structure entry rather than from the first
block, because a heading wraps: reading only the first block gave "Every member
of unlawful assembly guilty of offence committed in prosecution of".
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from collections import Counter
from typing import Any, Iterable

from specter.specter_parser import AMENDMENT_NOTE_RE
from specter.statutes import _heading_title

# A section ends where the next piece of structure begins.  A chapter or part
# heading is a boundary as much as the next section is.
BOUNDARY_KINDS = frozenset({"section", "definitions", "chapter", "part", "schedule",
                            "schedule_item"})
SECTION_KINDS = frozenset({"section", "definitions"})
# Blocks that are on the page but are not the law.
NOT_THE_LAW = frozenset({"header", "footer", "footnote"})
# "5_pakistan_penal_code_ppc1860_under_review" -> PPC.  A short form is a run
# of letters in the filename that is not a word of the title; judgments cite
# statutes that way ("302 PPC"), so it is worth recovering where it is certain.
SHORT_FORM_RE = re.compile(r"(?:^|_)([a-z]{2,6})(?:\d{4})?(?=_|$)")
SHORT_FORM_STOP = frozenset({
    "act", "the", "of", "and", "for", "code", "order", "rules", "law", "laws",
    "under", "review", "same", "as", "on", "official", "website", "dated",
    "repealed", "by", "with", "to", "in", "no", "amended", "up", "date",
})


def _short_form(slug: str, title: str, printed_title: bool) -> str | None:
    """The abbreviation the filename carries, if it carries one.

    Compared against the *printed* title only.  Where the statute prints none
    the title falls back to the filename, which contains the abbreviation, so
    every short form excluded itself.
    """
    words = ({word.lower() for word in re.findall(r"[A-Za-z]+", title or "")}
             if printed_title else set())
    for candidate in SHORT_FORM_RE.findall(slug.lower()):
        if candidate in SHORT_FORM_STOP or candidate in words:
            continue
        # A short form is consonant-dense: "ppc", "crpc", "qso", "ata".
        if sum(letter in "aeiou" for letter in candidate) <= 1:
            return candidate.upper()
    return None


def _category(source: Path) -> str | None:
    """The area of law, from the folder the corpus files the statute under."""
    folder = source.parent.name
    if not folder or folder in {"", ".", "pakistancode"}:
        return None
    return re.sub(r"_laws?$", "", folder, flags=re.IGNORECASE).replace("_", " ").strip().title() or None


def _ordered_blocks(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every block of the document in reading order, with its page."""
    blocks = []
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            blocks.append({**block, "page": page["page_number"]})
    return blocks


# A section number set in the margin is its own block, and reading order puts
# it on either side of the line it belongs to: the Penal Code returns section
# 1 as "Title and extent of operation of the Code..." *then* "1.", and section
# 2 on the same page the other way round.  Started at the number, section 1
# lost its heading outright.  Geometry settles it -- the heading is the block
# sharing the number's line and set to its right, whichever side of it reading
# order puts it on.
NUMBER_ONLY_RE = re.compile(r"^\s*\d{1,3}[A-Z]{0,2}\.\s*$")
# The parser types a footnote from its size and its place on the page, and in
# most statutes that is decisive.  The House Building Finance Corporation Act
# is set at 7pt throughout, so its amendment notes are the same size as the law
# and no size rule can separate them -- 17 of its sections ended with the note
# that follows them.  What the note still carries is its shape: a footnote
# marker opening the block, and an amendment citation inside it.  Requiring
# both catches the run of notes merged into one block, where only the first
# begins with the verb, and it fires on 19 blocks across the sample -- every
# one of them a footnote.
FOOTNOTE_MARKER_RE = re.compile(r'^\s*\d{1,3}(?:\s+(?!\d)|(?=[A-Z][a-z]))')
OBJECTS_NOTE_RE = re.compile(r'^\s*\d{1,3}\s+For\s+Statement\s+of\s+Objects\b')
AMENDMENT_HEAD = 200


def _is_amendment_footnote(text: str) -> bool:
    """An amendment note the size rule could not see."""
    if not FOOTNOTE_MARKER_RE.match(text):
        return False
    return bool(OBJECTS_NOTE_RE.match(text) or AMENDMENT_NOTE_RE.search(text[:AMENDMENT_HEAD]))


SAME_LINE = 4.0


def _starts_at(blocks: list[dict[str, Any]], index: int) -> int:
    """Where a section's first line begins, which may be the block before."""
    if not NUMBER_ONLY_RE.match(blocks[index].get("text") or "") or index == 0:
        return index
    number, previous = blocks[index].get("bbox"), blocks[index - 1].get("bbox")
    if not (len(number or ()) == 4 and len(previous or ()) == 4):
        return index
    shares_the_line = abs(previous[1] - number[1]) <= SAME_LINE
    return index - 1 if shares_the_line and previous[0] > number[0] else index


def section_bodies(document: dict[str, Any], structure: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each section with the text it states, in document order."""
    blocks = _ordered_blocks(document)
    position = {block["id"]: index for index, block in enumerate(blocks) if block.get("id")}
    # Structure in document order, so "the next marker" is well defined.
    markers = sorted(
        (entry for entry in structure
         if entry.get("kind") in BOUNDARY_KINDS and entry.get("block_id") in position),
        key=lambda entry: position[entry["block_id"]])

    # A block that opens a section is the law, whatever else it resembles.  The
    # Merchandise Marks Act prints "10 & 11. [Amendment of the Sea Customs Act,
    # 1878.] Rep. by the Repealing Act, 1938" -- a repealed section that reads
    # exactly like an amendment note, and would have been dropped as one.
    opens_a_section = {position[entry["block_id"]] for entry in markers}
    sections: list[dict[str, Any]] = []
    chapter = part = None
    for index, marker in enumerate(markers):
        if marker["kind"] == "chapter":
            chapter, part = marker.get("number"), None
            continue
        if marker["kind"] == "part":
            part = marker.get("number")
            continue
        if marker["kind"] not in SECTION_KINDS:
            continue
        start = _starts_at(blocks, position[marker["block_id"]])
        # The next section may reach back over its own margin number too, so the
        # end is that adjusted start -- otherwise both sections claim the line.
        end = next((_starts_at(blocks, position[later["block_id"]])
                    for later in markers[index + 1:]), len(blocks))
        body, ids, rtl = [], [], False
        for index_of, block in enumerate(blocks[start:end], start):
            if block.get("type") in NOT_THE_LAW or block.get("decorative"):
                continue
            text = (block.get("text") or "").strip()
            if not text:
                continue
            # Reaching back over a margin number leaves it between the heading
            # and the line it continues into -- "...shall be called the 1.
            # Penal Code" -- so it is kept as provenance but not as text.
            if index_of not in opens_a_section and _is_amendment_footnote(text):
                continue
            if ids and NUMBER_ONLY_RE.match(text):
                ids.append(block["id"])
                continue
            body.append(text)
            ids.append(block["id"])
            rtl = rtl or bool(block.get("contains_rtl"))
        if not ids:
            continue
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
        # The text opens with the section number, and any amendment marker in
        # front of it.  Dropping that leaves the heading first, which is what
        # makes the heading recoverable from the text at all.
        number = re.escape(str(marker.get("number") or ""))
        text = re.sub(r"^\s*(?:\d{1,3}\s*\[\s*)?"
                      r"[*†‡§]?\s*\[?\s*" + number + r"\.\s*"
                      # "371A.__ Selling person for purposes of prostitution" puts the
                      # heading separator in front of the heading rather than after it,
                      # and the heading reader then stopped at once and returned nothing.
                      r"(?:(?:[—–]|_{2,}|-{2,})\s*)?",
                      "", text, count=1)
        sections.append({
            "number": marker.get("number"),
            # A heading wraps across blocks, so the marker's own title stops at
            # the first line -- "Every member of unlawful assembly guilty of
            # offence committed in prosecution of".  The assembled text carries
            # the whole heading followed by the body, and HEADING_END_RE knows
            # where one ends and the other starts.
            "heading": _heading_title(text) or marker.get("title") or None,
            "text": text,
            "chapter": chapter,
            "part": part,
            "page_start": blocks[start]["page"],
            "page_end": blocks[position[ids[-1]]]["page"],
            "block_ids": ids,
            "rtl": rtl,
        })
    return sections


def build(payload: dict[str, Any]) -> dict[str, Any]:
    """One statute, section by section, from a parsed document."""
    document = payload["document"] if "document" in payload else payload
    metadata = document.get("metadata") or {}
    source = Path(document.get("source_file") or document.get("source_name") or "")
    slug = re.sub(r"^\d+_", "", source.stem)
    title = metadata.get("title") or slug.replace("_", " ")
    short = _short_form(slug, title, bool(metadata.get('title')))
    sections = section_bodies(payload, document.get("structure") or [])
    # A citation has to resolve to one text.  Where a number names more than one
    # -- Estacode holds sixty rule-sets each numbered from 1, and a statute whose
    # schedule was not detected has its schedule entries colliding with its
    # sections -- the citation is withheld for those sections and no others.
    #
    # An earlier version dropped the whole document instead, on the count of
    # times the run restarts.  At corpus scale that excluded the Stamp Act, the
    # Succession Act and thirteen more real Acts, which is a far worse outcome
    # than an unciteable section: the text is legitimate law either way.
    restarts = metadata.get("numbering_restarts") or 0
    seen = Counter(section["number"] for section in sections)
    for section in sections:
        section["ambiguous"] = seen[section["number"]] > 1
        section["citation"] = None if section["ambiguous"] else (
            f"{section['number']} {short}" if short
            else f"section {section['number']} of {title}")
    return {
        "act": title,
        "act_short": short,
        "statute_id": (metadata.get("statute_id")
                       or (re.match(r"^(\d+)_", source.stem) or [None, None])[1]),
        # The corpus files a statute by area of law, and that folder is the only
        # place the category is stated -- `statute_labels` reads it at parse
        # time but the parse discards it, so it is read back off the path the
        # document already records.  It is the field a retrieval filter wants:
        # "criminal", "family", "tax".
        "category": metadata.get("category") or _category(source),
        # A repealed law is published as a stub with no sections, which is a
        # real state of the corpus rather than a parse failure.  It is what
        # separates the 51 statutes that *have* no sections from the 6 whose
        # sections were not found.
        "repealed": metadata.get("repealed"),
        "act_number": metadata.get("act_number"),
        "act_year": metadata.get("act_year"),
        "source_file": str(source),
        "corpus_version": payload.get("schema_version"),
        "section_count": len(sections),
        "numbering_restarts": restarts,
        "ambiguous_sections": sum(1 for section in sections if section["ambiguous"]),
        # The run is checkable without annotation, so the check travels with
        # the corpus rather than being something a consumer has to redo.
        "sections_complete": metadata.get("sections_complete"),
        "missing_sections": metadata.get("missing_sections") or [],
        "sections": sections,
    }


def summarise(built: Iterable[dict[str, Any]]) -> dict[str, Any]:
    acts = list(built)
    sections = [section for act in acts for section in act["sections"]]
    lengths = sorted(len(section["text"]) for section in sections)
    thin = [s for s in sections if len(s["text"]) < 40]
    return {
        "statutes": len(acts),
        "with_a_short_form": sum(1 for act in acts if act["act_short"]),
        "sections": len(sections),
        "complete_runs": sum(1 for act in acts if act["sections_complete"]),
        "median_section_chars": lengths[len(lengths) // 2] if lengths else 0,
        "sections_under_40_chars": len(thin),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a section-addressable statute corpus from a parsed run.")
    parser.add_argument("output_dir", type=Path, help="the --out folder of a `specter parse` run")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the per-statute files (default: <output_dir>/sections)")
    args = parser.parse_args()
    destination = args.out or args.output_dir / "sections"
    destination.mkdir(parents=True, exist_ok=True)

    built: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for path in sorted(args.output_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (payload.get("document") or {}).get("document_kind") != "statute":
            continue
        act = build(payload)
        if not act["sections"]:
            # Most of these are repealed stubs, which is a real state of the
            # corpus.  Six are not: their headings carry no font emphasis at
            # all, so nothing is read as a section and 50,000 characters of the
            # Seed Act go unaddressed.  Either way the document is named here
            # rather than dropped without trace -- a statute missing from the
            # corpus should be visible in it.
            missing.append(act)
            continue
        if (act.get("numbering_restarts") or 0) > 1:
            unresolved.append(act)
        built.append(act)
        (destination / f"{path.stem}.json").write_text(
            json.dumps(act, ensure_ascii=False, indent=1), encoding="utf-8")
    if not built:
        parser.error(f"no parsed statutes under {args.output_dir}")
    report = summarise(built)
    report["numbering_not_resolved"] = len(unresolved)
    report["no_sections_detected"] = len(missing)
    report["ambiguous_sections"] = sum(act["ambiguous_sections"] for act in built)
    for key, value in report.items():
        print(f"{key:26s}: {value}")
    for act in sorted(unresolved, key=lambda a: -a["ambiguous_sections"])[:10]:
        print(f"  numbering restarts {act['numbering_restarts']}, "
              f"{act['ambiguous_sections']}/{act['section_count']} sections uncitable: {act['act'][:60]}")
    (destination / "index.json").write_text(
        json.dumps({"summary": report,
                    "acts": [{k: act[k] for k in ("act", "act_short", "statute_id",
                                                  "section_count", "sections_complete")}
                             for act in built],
                    "no_sections_detected": [
                        {"act": act["act"], "source_file": act["source_file"],
                         "repealed": act.get("repealed")}
                        for act in missing],
                    "numbering_not_resolved": [
                        {"act": act["act"], "source_file": act["source_file"],
                         "numbering_restarts": act["numbering_restarts"],
                         "ambiguous_sections": act["ambiguous_sections"],
                         "section_count": act["section_count"]}
                        for act in unresolved]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {len(built)} statutes to {destination}")


if __name__ == "__main__":
    main()
