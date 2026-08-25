"""One entry per court: everything that differs between them, in one place.

Adding a court should be a config entry, not an edit in three files.  Each
:class:`Court` carries what the rest of the pipeline needs to know about it:

* ``court_id`` -- the canonical, filterable identifier.  A corpus that stores
  only the spelling printed on the page ends up with the same court under
  several labels, and every exact-match filter then misses rows without
  raising anything.
* ``name`` / ``spellings`` -- the canonical name, and the variants seen in the
  wild, so any of them resolve to one identifier.
* ``path_pattern`` -- some corpora state the case in the filename.  Where a
  court does, those are corroborating labels, never a replacement for what the
  page says.
* ``judge_from_folder`` -- and some file by judge.

Comparison helpers live here too, because "does the extracted value agree with
the label" is the same question for every court.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Court:
    court_id: str
    name: str
    spellings: tuple[str, ...] = ()
    path_pattern: re.Pattern[str] | None = None
    judge_from_folder: bool = False

    def keys(self) -> tuple[str, ...]:
        return tuple(_letters(value) for value in (self.name, *self.spellings))


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


COURTS: tuple[Court, ...] = (
    Court(
        court_id="supreme_court_pakistan",
        name="SUPREME COURT OF PAKISTAN",
        path_pattern=SC_PATH,
        judge_from_folder=True,
    ),
    Court(court_id="federal_shariat_court", name="FEDERAL SHARIAT COURT"),
    Court(court_id="lahore_high_court", name="LAHORE HIGH COURT", path_pattern=LHC_PATH),
    Court(court_id="islamabad_high_court", name="ISLAMABAD HIGH COURT"),
    Court(court_id="sindh_high_court", name="SINDH HIGH COURT", spellings=("HIGH COURT OF SINDH",)),
    Court(court_id="peshawar_high_court", name="PESHAWAR HIGH COURT"),
    Court(court_id="balochistan_high_court", name="BALOCHISTAN HIGH COURT"),
)

JUDGE_PREFIX_RE = re.compile(r"(?i)^\s*(?:mr\.?|mrs\.?|ms\.?|justice|hon(?:'?ble)?\.?|\s)+")
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
        match = court.path_pattern.match(path.name)
        if not match:
            continue
        found = match.groupdict()
        judge = JUDGE_PREFIX_RE.sub("", path.parent.name).strip() if court.judge_from_folder else None
        neutral = found.get("neutral_number")
        return {
            "court_id": court.court_id,
            "case_type": (found.get("case_type") or "").rstrip(".") or None,
            "case_number": found.get("case_number"),
            "year": found.get("year"),
            # Only where the path states one.  A court that names its judgments
            # by neutral citation is stating how to cite them, not which case
            # they are; conflating the two invents a disagreement on every file.
            "neutral_citation": f"{found['year']} LHC {neutral}" if neutral else None,
            "decision_date": found.get("decision_date"),
            "judge": judge or None,
            "source": "filename",
        }
    return None


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
    numbers = DIGITS_RE.findall(extracted)
    bare = DIGITS_RE.findall(labels["case_number"])
    return bool(bare) and bare[0] in numbers and labels["year"] in numbers


def matches_judge(labels: dict[str, Any], extracted: list[str] | None) -> bool:
    """True when any extracted judge corresponds to the one the path names."""
    folder = _letters(labels.get("judge"))
    if not folder:
        return False
    return any(_letters(name) and (_letters(name) in folder or folder in _letters(name))
               for name in (extracted or []))
