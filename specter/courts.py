"""One entry per court: everything that differs between them, in one place.

Adding a court should be a config entry, not an edit in three files.  Each
:class:`Court` carries what the rest of the pipeline needs to know about it:

* ``court_id`` -- the canonical, filterable identifier.  A corpus that stores
  only the spelling printed on the page ends up with the same court under
  several labels, and every exact-match filter then misses rows without
  raising anything.
* ``name`` / ``spellings`` -- the canonical name, and the variants seen in the
  wild, so any of them resolve to one identifier.
* ``path_pattern`` / ``path_from`` -- some corpora state the case in the path,
  in the filename or in the folder above it.  Where a court does, those are
  corroborating labels, never a replacement for what the page says.
* ``judge_from_folder`` -- and some file by judge.
* ``sidecar`` -- IHC goes further and publishes a record beside every
  judgment.  Richer than a filename, and still a label: it names no page
  region, so it fills a gap and never overwrites what the page states.
* ``case_number_format`` -- each court writes a case number its own way.

Comparison helpers live here too, because "does the extracted value agree with
the label" is the same question for every court.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Court:
    court_id: str
    name: str
    spellings: tuple[str, ...] = ()
    path_pattern: re.Pattern[str] | None = None
    # Which path component the pattern reads.  Most corpora state the case in
    # the filename; IHC gives each case a folder and puts several PDFs in it,
    # so there the case is named one level up.
    path_from: str = "name"
    judge_from_folder: bool = False
    # A few corpora ship a metadata record beside each document.  It is a
    # label like any other -- richer and more reliable than a filename, but
    # still produced by a scraper and still pointing at no page region.
    sidecar: str | None = None
    # How this court writes a case number, so the label reads the way the court
    # itself would write it.  ``None`` means the common "C.A.634/2018" form.
    case_number_format: str | None = None

    def keys(self) -> tuple[str, ...]:
        return tuple(_letters(value) for value in (self.name, *self.spellings))

    def stated_name(self, path: Path) -> str:
        """The path component that names the case."""
        return path.name if self.path_from == "name" else path.parent.name

    def judge_folder(self, path: Path) -> str:
        """The folder above the one that names the case."""
        return path.parent.name if self.path_from == "name" else path.parent.parent.name


# ``SCP_<type>.<number>_<year>_<dd-mm-yyyy>.pdf`` -- the trailing date is
# missing on a few files, so it is optional.
SC_PATH = re.compile(
    r"^SCP_(?P<case_type>.+?)\.?(?P<case_number>\d+(?:-[A-Za-z]+)?)_(?P<year>\d{4})_(?P<decision_date>\d{2}-\d{2}-\d{4})?\.pdf$",
    re.IGNORECASE,
)
# ``2023LHC5924.pdf`` is the *neutral citation* -- 2023 LHC 5924 -- which is how
# the judgment is cited, not the case number the court assigned it.  Reading it
# as a case number made every LHC document disagree with its own label while
# the extraction ("Writ Petition No.23303/13") was right all along.
LHC_PATH = re.compile(r"^(?P<year>\d{4})LHC(?P<neutral_number>\d+)", re.IGNORECASE)
# IHC gives each case a folder -- ``Criminal Appeal-31-2020 _ Citation Awaited``
# -- holding ``judgment.pdf``, any interim ``order_<ddmmyyyy>_<n>.pdf``, and a
# ``meta.json``.  The folder name matched the sidecar's own case number on all
# 400 cases sampled, so it is a sound fallback where the sidecar is missing.
IHC_PATH = re.compile(
    r"^(?P<case_type>[A-Za-z][\w .&/'()-]*?)-(?P<case_number>\d+)-(?P<year>\d{4})"
    r"(?:\s*_\s*(?P<citation>.+?))?\s*$"
)
IHC_ORDER = re.compile(r"^order_(?P<day>\d{2})(?P<month>\d{2})(?P<year>\d{4})_\d+\.pdf$", re.IGNORECASE)
# "Citation Awaited" is what 397 of 400 sampled cases carry, and it is not a
# citation.  Storing it would put the same placeholder on 99% of the corpus and
# make the field useless as a filter.
NO_CITATION_RE = re.compile(r"(?i)^\s*citation\s+awaited\s*$")
# "28-APR-2022".  ``same_date`` compares numbers, so the month name has to
# become one or every IHC date would read as a disagreement.
MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
NAMED_DATE_RE = re.compile(r"^\s*(?P<day>\d{1,2})[-/ ](?P<month>[A-Za-z]{3,9})[-/ ](?P<year>\d{4})\s*$")
# "Riaz Pervaiz VS The State etc"
VERSUS_RE = re.compile(r"(?i)\s+(?:v/?s\.?|versus|vs)\.?\s+")


def normalise_named_date(value: str | None) -> str | None:
    """Rewrite ``28-APR-2022`` as ``28-04-2022``; leave anything else alone."""
    match = NAMED_DATE_RE.match(value or "")
    if not match:
        return value or None
    month = MONTHS.get(match.group("month")[:3].lower())
    return f"{int(match.group('day')):02d}-{month:02d}-{match.group('year')}" if month else value


def _ihc_sidecar(data: dict[str, Any]) -> dict[str, Any]:
    """Read IHC's ``meta.json`` into the fields the pipeline already has.

    The court publishes the bench, the author, the parties and the date of the
    order alongside every judgment.  That is a far better label than a filename
    -- but it is still a label: it names no page region, so it fills a gap and
    records a disagreement, exactly as a filename does.
    """
    bench = [name for name in (data.get("bench") or []) if name] or (
        [data["judge"]] if data.get("judge") else [])
    sides = VERSUS_RE.split(data.get("title") or "", maxsplit=1)
    return {
        "case_number_stated": (data.get("case_no") or "").split("|")[0].strip() or None,
        "year": data.get("year"),
        "decision_date": normalise_named_date(data.get("order_date")),
        "judges": [JUDGE_PREFIX_RE.sub("", name).strip() for name in bench] or None,
        "parties": data.get("title") or None,
        "petitioner": sides[0].strip() if len(sides) == 2 else None,
        "respondent": sides[1].strip() if len(sides) == 2 else None,
        "category": data.get("category") or None,
    }


COURTS: tuple[Court, ...] = (
    Court(
        court_id="supreme_court_pakistan",
        name="SUPREME COURT OF PAKISTAN",
        path_pattern=SC_PATH,
        judge_from_folder=True,
    ),
    Court(court_id="federal_shariat_court", name="FEDERAL SHARIAT COURT"),
    Court(court_id="lahore_high_court", name="LAHORE HIGH COURT", path_pattern=LHC_PATH),
    Court(
        court_id="islamabad_high_court",
        name="ISLAMABAD HIGH COURT",
        path_pattern=IHC_PATH,
        path_from="parent",
        judge_from_folder=True,
        sidecar="meta.json",
        case_number_format="{case_type}-{case_number}-{year}",
    ),
    Court(court_id="sindh_high_court", name="SINDH HIGH COURT", spellings=("HIGH COURT OF SINDH",)),
    Court(court_id="peshawar_high_court", name="PESHAWAR HIGH COURT"),
    Court(court_id="balochistan_high_court", name="BALOCHISTAN HIGH COURT"),
)

# "Mr. Justice X" at SC, "Former Honourable Chief Justice Mr. Justice X" at IHC.
# ``hon`` alone once left "ourable" standing at the front of every IHC judge.
JUDGE_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:former|hon(?:ou?rable|'?ble)?\.?|chief|mr\.?|mrs\.?|ms\.?|justice|\s)+")
DIGITS_RE = re.compile(r"\d+")
JOINED_RUN_RE = re.compile(r"[A-Za-z]{12,}")


def _letters(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").casefold())


def court_for_name(value: str | None) -> Court | None:
    """Resolve whatever the page called the court to one registry entry."""
    if not value:
        return None
    key = _letters(value)
    for court in COURTS:
        if any(spelling in key for spelling in court.keys()):
            return court
    return None


def canonical_name(value: str | None) -> str | None:
    """Re-space a court name only when OCR ran it together.

    An already-spaced value is returned exactly as the page states it, so a
    real variant ("IN THE LAHORE HIGH COURT, LAHORE") is never flattened; only
    an unreadable run like "INTHESUPREMECOURTOFPAKISTAN" is replaced, because
    that matches nothing downstream.
    """
    if not value or not JOINED_RUN_RE.search(value):
        return value
    court = court_for_name(value)
    return court.name if court else value


def court_id(value: str | None) -> str | None:
    court = court_for_name(value)
    return court.court_id if court else None


def path_labels(pdf_path: str | Path) -> dict[str, Any] | None:
    """Read case identity from the file path, if this court's corpus states it.

    Corroborating labels only.  The scraper that produced the filename can be
    wrong, and a path carries no page region, so a label may fill a field
    extraction could not find but never overwrite one it did.
    """
    path = Path(pdf_path)
    for court in COURTS:
        if court.path_pattern is None:
            continue
        match = court.path_pattern.match(court.stated_name(path))
        if not match:
            continue
        found = match.groupdict()
        judge = JUDGE_PREFIX_RE.sub("", court.judge_folder(path)).strip() if court.judge_from_folder else None
        neutral = found.get("neutral_number")
        citation = found.get("citation")
        labels = {
            "court_id": court.court_id,
            "court": court.name,
            "case_type": (found.get("case_type") or "").rstrip(".") or None,
            "case_number": found.get("case_number"),
            "case_number_stated": _case_number(court, found),
            "year": found.get("year"),
            # Only where the path states one.  A court that names its judgments
            # by neutral citation is stating how to cite them, not which case
            # they are; conflating the two invents a disagreement on every file.
            "neutral_citation": (f"{found['year']} LHC {neutral}" if neutral else
                                 (citation if citation and not NO_CITATION_RE.match(citation) else None)),
            "decision_date": found.get("decision_date"),
            "judge": judge or None,
            "source": "filename",
        }
        return {**labels, **_sidecar_labels(court, path)} if court.sidecar else labels
    return None


def _case_number(court: Court, found: dict[str, str | None]) -> str | None:
    """Write the case number the way this court writes it.

    Not every corpus states every part.  LHC names a year and a number but no
    case type, and composing one anyway wrote "None.5924/2023" into the
    metadata -- a label worse than no label.
    """
    number, year = found.get("case_number"), found.get("year")
    if not (number and year):
        return None
    case_type = (found.get("case_type") or "").rstrip(".")
    if court.case_number_format:
        return court.case_number_format.format(case_type=case_type, case_number=number, year=year)
    return f"{case_type + '.' if case_type else ''}{number}/{year}"


def _sidecar_labels(court: Court, path: Path) -> dict[str, Any]:
    """Whatever the record beside the document states, over the path's guess.

    Both describe the same case, but the sidecar states it directly while the
    path only encodes it, so where they overlap the sidecar wins.  An absent or
    unreadable record is not an error: the path still names the case, which is
    why it is read first.
    """
    sidecar = path.parent / court.sidecar
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # IHC is the only corpus that ships one, so its schema is read directly.  A
    # second sidecar corpus would make the reader a field on ``Court``, next to
    # the filename; one does not need the indirection.
    stated = {key: value for key, value in _ihc_sidecar(data).items() if value}
    order = IHC_ORDER.match(path.name)
    if order:
        # An interim order is its own document with its own date, which the
        # filename gives and the sidecar -- describing the judgment -- does not.
        stated["decision_date"] = f"{order['day']}-{order['month']}-{order['year']}"
    return {**stated, "source": "sidecar", "sidecar": data}


def same_date(left: str | None, right: str | None) -> bool:
    """Compare two dates written in any of the corpus's several formats.

    The corpus mixes ``11-09-2025``, ``11.09.2025`` and ``2.6.2005``, so the
    numbers are compared rather than the strings.  A two-digit year is not
    guessed at: a date that does not yield a four-digit year does not match.
    """
    if not left or not right:
        return False
    a, b = DIGITS_RE.findall(str(left)), DIGITS_RE.findall(str(right))
    if len(a) != 3 or len(b) != 3 or len(a[2]) != 4 or len(b[2]) != 4:
        return False
    return [int(part) for part in a] == [int(part) for part in b]


def matches_case_number(labels: dict[str, Any], extracted: str | None) -> bool:
    """True when an extracted case number names the same case as the path.

    Both the number and the year must appear: "Civil Appeal No. 634" alone is
    ambiguous across years, and accepting it would hide the missing year rather
    than report it.
    """
    if not extracted or not labels.get("case_number") or not labels.get("year"):
        return False
    # Compared as numbers, not as strings: the court writes "No.08-B of 2026"
    # where the corpus filed it as 8, and "08" != "8" reported a disagreement
    # between two spellings of the same case.
    numbers = {int(part) for part in DIGITS_RE.findall(extracted)}
    bare = DIGITS_RE.findall(labels["case_number"])
    return bool(bare) and int(bare[0]) in numbers and int(labels["year"]) in numbers


def _same_name(left: Any, right: Any) -> bool:
    """One name written two ways: honorifics, initials and punctuation differ.

    Containment rather than equality, because the two sources genuinely differ
    in how much they print -- "Babar Sattar" against "Mr. Justice Babar Sattar,
    J." -- and demanding equality would report a disagreement on nearly every
    document while finding no real one.
    """
    a, b = _letters(left), _letters(right)
    return bool(a and b) and (a in b or b in a)


def matches_judge(labels: dict[str, Any], extracted: list[str] | None) -> bool:
    """True when an extracted judge corresponds to one the labels name.

    A bench of two is not a disagreement with a label naming one of them: the
    label is who the corpus filed the case under, and the page lists everyone
    who sat.  One name in common is agreement.
    """
    stated = [name for name in ([labels.get("judge")] + list(labels.get("judges") or [])) if name]
    return any(_same_name(name, found) for name in stated for found in (extracted or []))


def matches_name(label: Any, extracted: Any) -> bool:
    """True when a party name from a label and from the page are the same one."""
    return _same_name(label, extracted)
