"""LI-3: version timelines, from the amendment footnotes each statute carries.

The corpus holds one text of each statute: the consolidated current one.  The
words a 2010 judgment construed may since have been substituted, and nothing
here can say what they were.  What the consolidated text *does* carry is a
record of every change, as numbered footnotes:

    497. ... he shall not be so released if ... guilty of 2[an offence punishable ...]
    2 Subs. by the Law Reforms Ordinance, 1972 (XII of 1972), s. 2 and Sch.

So for every section this module reads its amendment events -- what kind
(substituted, inserted, added, omitted, repealed, renumbered), by which
instrument, in which year, and from which date where the note says ("w.e.f.").
A marker at the very start of a section means the change is to the whole
section; one inside it, to some of its words.

Joined to the judgments that cite a section, that gives each ``CONCERNS``
edge a version status:

    current_text_applies -- no change to the section after the decision
    amended_since        -- some of its words changed after the decision
    replaced_since       -- the whole section was substituted after the decision
    not_yet_in_force     -- the whole section was inserted after the decision;
                            the judgment cannot have applied it as it now reads
    repealed             -- the section is repealed in the current text
    undated_change       -- a change whose year the note does not state

Footnote numbers restart on every page, so a marker is matched to the note of
the same number on the page the marker is printed on.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

EXTRACTOR = "intel.versions"
VERSION = "3"

MARKER = re.compile(r"(?<![\w\d])(\d{1,2})\s?\[")
# A note starts with its number glued to a capital (or an opening quote): "3Added by", "4The words".
NOTE_START = re.compile(r"(?:(?<=^)|(?<=[\s.;)\]]))([1-9l]\d?)\s?(?=[A-Z“\"'])")
KINDS = (
    ("substituted", r"\bsubs(?:tituted)?\b"),
    ("inserted", r"\bins(?:erted)?\b"),
    ("added", r"\badded\b"),
    ("omitted", r"\bomitted\b|\bdeleted\b"),
    ("repealed", r"\brep(?:ealed)?\b\.?"),
    ("renumbered", r"\bre-?numbered\b"),
)
KIND_RES = [(name, re.compile(pattern, re.I)) for name, pattern in KINDS]
# "Act No. VIII of 1993", "Act X (Second Amdt.) of 2016", "Act I of 05", "Ord. No. XXXVII of 2001",
# "(XII of 1972)", "(Act No. XXV of 2016)": the year of the amending instrument.
INSTRUMENT_YEAR = re.compile(r"(?i)\b(?:[IVXLC]+|\d{1,3})\b(?:\s*\([^)]{0,30}\))?\s+of\s+((?:18|19|20)\d{2}|\d{2})\b")
TITLE_YEAR = re.compile(r"(?i)\b(?:Act|Ordinance|Order|A\.\s?O\.)\s*,?\s*((?:18|19|20)\d{2})\b")
EFFECTIVE = re.compile(r"(?i)w\.?\s?e\.?\s?f\.?\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+day\s+of)?[\s./-]+"
                       r"(\d{1,2}|[A-Za-z]+)[\s.,/-]+(\d{2,4})")
BY = re.compile(r"(?i)\bby\s+(?P<instrument>.+?)(?:,\s*ss?\.\s*[\d\-]+.*)?$")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
# A marker this close to the start of a section's text is on the section itself.
WHOLE_SECTION_SPAN = 6


def _year(value: str) -> int:
    year = int(value)
    if year < 100:
        year += 2000 if year <= 30 else 1900
    return year


def _effective(note: str) -> str | None:
    match = EFFECTIVE.search(note)
    if not match:
        return None
    day, month, year = match.groups()
    month_value = int(month) if month.isdigit() else next(
        (value for name, value in MONTHS.items() if name.startswith(month.lower()[:3])), 0)
    try:
        return date(_year(year), month_value, int(day)).isoformat()
    except ValueError:
        return None


def split_notes(text: str) -> dict[str, str]:
    """A page's footnote text split into numbered notes: {"3": "Added by Act No. VIII of 1993 ..."}."""
    text = " ".join(text.split())
    starts = [(m.start(), m.group(1).replace("l", "1")) for m in NOTE_START.finditer(text)]
    notes: dict[str, str] = {}
    for index, (start, number) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        body = text[start + len(number):end].strip()
        notes.setdefault(number, body)
    return notes


def parse_note(note: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kind, instrument, year and effective date of one amendment note."""
    kinds = [name for name, pattern in KIND_RES if pattern.search(note)]
    instrument_match = BY.search(note)
    instrument = instrument_match.group("instrument").strip(" .") if instrument_match else None
    year_match = INSTRUMENT_YEAR.search(note) or TITLE_YEAR.search(note)
    year = _year(year_match.group(1)) if year_match else None
    if re.search(r"(?i)\bibid\b", note) and previous:
        # "Proviso added ibid": same instrument as the note before it.
        instrument, year = previous.get("instrument"), previous.get("year")
    return {"kinds": kinds, "instrument": instrument, "year": year, "effective": _effective(note), "note": note[:300]}


def timelines(statute_payload: dict[str, Any], sections_record: dict[str, Any]) -> dict[str, Any]:
    """Every section's amendment events, and the latest year any note names."""
    blocks = {}
    footnotes: dict[int, list[str]] = defaultdict(list)
    for page in statute_payload.get("pages", []):
        for block in page.get("blocks", []):
            blocks[block.get("id")] = (page["page_number"], block.get("text") or "")
            if block.get("type") == "footnote":
                footnotes[page["page_number"]].append(block.get("text") or "")
    notes_by_page: dict[int, dict[str, dict[str, Any]]] = {}
    latest = None
    for page, texts in footnotes.items():
        parsed, previous = {}, None
        for number, note in split_notes(" ".join(texts)).items():
            event = parse_note(note, previous)
            if event["kinds"]:
                parsed[number] = event
                previous = event
                if event["year"]:
                    latest = max(latest or 0, event["year"])
        notes_by_page[page] = parsed

    sections = {}
    for section in sections_record.get("sections", []):
        events, cursor = [], 0
        own_text = section.get("text") or ""
        for block_id in section.get("block_ids") or []:
            page, text = blocks.get(block_id, (None, ""))
            for marker in MARKER.finditer(text):
                event = notes_by_page.get(page, {}).get(marker.group(1))
                if event:
                    position = cursor + marker.start()
                    events.append({**event, "page": page, "marker": marker.group(1),
                                   "whole_section": position <= WHOLE_SECTION_SPAN})
            cursor += len(text) + 1
        # A section number printed with its marker glued on: "2199." for footnote 2 on section 199.
        glued = re.match(r"^(\d{1,2})" + re.escape(section["number"]) + r"\.", own_text)
        if glued:
            page = section.get("page_start")
            event = notes_by_page.get(page, {}).get(glued.group(1))
            if event:
                events.append({**event, "page": page, "marker": glued.group(1), "whole_section": True})
        # One note, marked several times in a section, is one event.
        unique = {}
        for event in events:
            unique.setdefault((event["page"], event["marker"]), event)
        events = list(unique.values())
        repealed = bool(re.search(r"(?i)\brep(?:ealed)?\.?\s+by\b|^\s*\[?[^\]]{0,80}\]?\s*rep\.", own_text[:200]))
        sections[section["number"]] = {"events": events, "repealed": repealed}
    return {"sections": sections, "text_current_to": latest}


def status(events: list[dict[str, Any]], repealed: bool, decided: int | None) -> dict[str, Any]:
    """Where a section stood when a judgment was decided, against the changes since."""
    if repealed:
        return {"version_status": "repealed", "changes_after_decision": []}
    if decided is None:
        return {"version_status": "decision_year_unknown", "changes_after_decision": []}
    after = [e for e in events if e["year"] and e["year"] > decided]
    undated = [e for e in events if not e["year"]]
    # A pure insertion is a new section.  "Subs., Ins. and Omitted" on Article 189 by the
    # Twenty-seventh Amendment restructured an article in force since 1973: a replacement.
    if any(e["whole_section"] and set(e["kinds"]) <= {"inserted", "added"} and e["kinds"] for e in after):
        state = "not_yet_in_force"
    elif any(e["whole_section"] and ({"substituted", "omitted"} & set(e["kinds"])) for e in after):
        state = "replaced_since"
    elif after:
        state = "amended_since"
    elif undated:
        state = "undated_change"
    else:
        state = "current_text_applies"
    return {"version_status": state,
            "changes_after_decision": [{k: e[k] for k in ("kinds", "instrument", "year", "effective", "whole_section")}
                                       for e in after]}


def decision_year(judgment: dict[str, Any]) -> int | None:
    """The year a judgment was decided: from the court's own identifier first, the page second."""
    key = judgment["key"]
    match = re.search(r"neutral/(\d{4})", key) or re.search(r"/(\d{4})-\d\d-\d\d$", key)
    if match:
        return int(match.group(1))
    decided = judgment.get("decision_date") or ""
    return int(decided[:4]) if decided[:4].isdigit() else None


def build(concerns: list[dict[str, Any]], judgments: list[dict[str, Any]], parsed_root: Path,
          *more_roots: Path) -> dict[str, Any]:
    """Timelines for every statute a judgment cites, and a version status on every ``CONCERNS`` edge."""
    years = {j["judgment_id"]: decision_year(j) for j in judgments}
    cache: dict[str, dict[str, Any]] = {}

    def timeline(stem: str) -> dict[str, Any]:
        if stem not in cache:
            # The federal code first, then any other corpus (the Punjab Code) that holds this statute.
            root = next((r for r in (parsed_root, *more_roots) if (r / "sections" / f"{stem}.json").exists()),
                        parsed_root)
            payload_path, sections_path = root / f"{stem}.json", root / "sections" / f"{stem}.json"
            if payload_path.exists() and sections_path.exists():
                cache[stem] = timelines(json.loads(payload_path.read_text(encoding="utf-8")),
                                        json.loads(sections_path.read_text(encoding="utf-8")))
            else:
                cache[stem] = {"sections": {}, "text_current_to": None}
        return cache[stem]

    rows, states = [], Counter()
    for edge in concerns:
        record = timeline(edge["statute_id"])
        section = record["sections"].get(edge["section"]) if edge["kind"] in {"section", "article"} else None
        if section is None:
            result = {"version_status": "section_not_indexed", "changes_after_decision": []}
        else:
            result = status(section["events"], section["repealed"], years.get(edge["judgment_id"]))
        states[result["version_status"]] += 1
        rows.append({"judgment_id": edge["judgment_id"], "statute_id": edge["statute_id"], "kind": edge["kind"],
                     "section": edge["section"], "decided": years.get(edge["judgment_id"]),
                     "text_current_to": record["text_current_to"], **result,
                     "extractor": EXTRACTOR, "extractor_version": VERSION})

    provision_rows = []
    for stem, record in sorted(cache.items()):
        for number, section in sorted(record["sections"].items()):
            if section["events"] or section["repealed"]:
                provision_rows.append({"statute_id": stem, "section": number, "repealed": section["repealed"],
                                       "events": section["events"], "text_current_to": record["text_current_to"]})
    events = [e for p in provision_rows for e in p["events"]]
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "statutes_read": len(cache),
        "sections_with_events": sum(1 for p in provision_rows if p["events"]),
        "repealed_sections": sum(1 for p in provision_rows if p["repealed"]),
        "events": len(events),
        "events_with_a_year": sum(1 for e in events if e["year"]),
        "events_with_an_effective_date": sum(1 for e in events if e["effective"]),
        "whole_section_events": sum(1 for e in events if e["whole_section"]),
        "event_kinds": dict(Counter(k for e in events for k in e["kinds"]).most_common()),
        "edges": len(rows),
        "edge_version_status": dict(states.most_common()),
    }
    return {"edges": rows, "provisions": provision_rows, "report": report}
