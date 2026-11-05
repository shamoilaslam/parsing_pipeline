"""Case numbers to canonical keys: "W.P.No.6233/2013" and "Writ Petition No. 6233 of 2013" are one case.

A case number is the identity a court gives a matter, and an appeal recital
names the lower court's case by it.  The corpus writes one type many ways:
"Writ Petition No.", "W.P.No.", "WP No.", "WRIT PETITION NO."; and the year
four ways: "of 2013", "/2013", "/13", "-2013".  A canonical key is
``TYPE/NUMBER/YEAR``, so two spellings of one case compare equal.

Types are a fixed table, not derived: "C.R." is a civil revision and "C.R.P."
a civil review petition, and no rule over the letters can know that.  A type
the table does not know is kept as its normalised prefix rather than dropped,
so it still compares equal to itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Normalised prefix (lower case, no dots or spaces, plural "s" dropped) -> canonical type.
TYPES = {
    "writpetition": "WP", "wp": "WP",
    "criminalappeal": "CRL.A", "crlappeal": "CRL.A", "crla": "CRL.A", "cra": "CRL.A", "crappeal": "CRL.A",
    "criminalpetition": "CRL.P", "crlp": "CRL.P", "crlpla": "CRL.P",
    "criminalmiscellaneou": "CRL.MISC", "criminalmisc": "CRL.MISC", "crlmisc": "CRL.MISC", "crlm": "CRL.MISC",
    "criminalrevision": "CRL.REV", "crlrevision": "CRL.REV", "crlrev": "CRL.REV",
    "civilrevision": "C.R.", "cr": "C.R.",
    "civilreviewpetition": "C.R.P.", "crp": "C.R.P.",
    "civilpetition": "C.P.", "cp": "C.P.", "cpla": "C.P.", "civilpetitionforleavetoappeal": "C.P.",
    "civilappeal": "C.A.", "ca": "C.A.",
    "regularfirstappeal": "RFA", "rfa": "RFA",
    "regularsecondappeal": "RSA", "rsa": "RSA",
    "firstappealfromorder": "FAO", "fao": "FAO",
    "intracourtappeal": "ICA", "ica": "ICA",
    "executionfirstappeal": "EFA", "efa": "EFA",
    "secondappeal": "S.A.", "sa": "S.A.",
    "incometaxreference": "ITR", "itr": "ITR",
    "civilmiscellaneou": "C.M.", "cm": "C.M.",
    "civiloriginal": "C.O.", "co": "C.O.",
    "suomotucase": "SMC", "smc": "SMC",
    "constitutionpetition": "CONST.P", "constp": "CONST.P",
    "murderreference": "M.R.", "mr": "M.R.",
    "customsreferenceapplication": "CUSTOMS.REF", "specialcustomsreferenceapplication": "CUSTOMS.REF",
    "servicesappeal": "SERVICE.APPEAL", "serviceappeal": "SERVICE.APPEAL",
    "appeal": "APPEAL",
    "petition": "PETITION",
}

# "No.", "Nos." between the type and the digits; each one starts a case-number segment.
NUMBER_WORD = re.compile(r"(?i)\bnos?\b\.?")
# The words printed just before "No.": letters, dots, brackets and spaces.
TYPE_BEFORE = re.compile(r"([A-Za-z][A-Za-z.()\s]{0,60}?)\s*$")
CONNECTORS = re.compile(r"(?i)^(?:(?:and|with|in|&|also|respectively)\s+)+")
MAX_TYPE_WORDS = 6
# Police and revenue records carry numbers too, but they are not cases a court decided.
NOT_A_CASE = re.compile(r"(?i)\bF\.?\s*I\.?\s*R\b|\bcrime\b|\bkhewat\b|\bkhatauni\b|\bmutation\b")
# A trailing "of 2018" gives the year to every number before it that has none of its own.
TRAILING_YEAR = re.compile(r"(?i)of\s*((?:19|20)\d{2}|\d{2})\b")
# One number: an optional registry prefix ("D-1306", "S-60"), the digits, a letter suffix
# ("5864-B" bail, "374-J" jail, "1054-K" Karachi registry), its own year, and the
# "254424-25" shorthand for the next number in a run.
ITEM = re.compile(r"(?:\b(?P<pre>[A-Z])\s*-\s*)?(?P<num>\d{1,6})(?:\s*-\s*(?P<suf>[A-Za-z]{1,2})\b)?"
                  r"(?:\s*(?P<sep>/|-)\s*(?P<yr>(?:19|20)\d{2}|\d{2})\b)?")
# "1113 TO 1155 OF 2017", "28724 to 28750/25".
RANGE = re.compile(r"(?i)\b(\d{1,6})\s*to\s*(\d{1,6})\b(?:\s*/\s*((?:19|20)\d{2}|\d{2})\b)?")


@dataclass(frozen=True)
class CaseNumber:
    type: str
    number: str
    year: int | None

    @property
    def key(self) -> str:
        return f"{self.type}/{self.number}/{self.year if self.year is not None else '?'}"


def _known(prefix: str) -> bool:
    norm = re.sub(r"[^a-z]", "", prefix.lower())
    return norm in TYPES or (norm.endswith("s") and norm[:-1] in TYPES)


def case_type(prefix: str) -> str:
    """The canonical type for a printed prefix such as "Crl. Misc." or "WRIT PETITIONS"."""
    norm = re.sub(r"[^a-z]", "", prefix.lower())
    if norm in TYPES:
        return TYPES[norm]
    if norm.endswith("s") and norm[:-1] in TYPES:
        return TYPES[norm[:-1]]
    return norm.upper() or "?"


def _type_text(run: str) -> str:
    """The case type at the end of the words before "No.": the longest known type, else the last three words.

    The words before "No." can run back into the sentence ("passed by the
    learned Single Judge in Chamber whereby Writ Petition"); the type is only
    the end of that run.
    """
    words = run.split()
    for size in range(min(MAX_TYPE_WORDS, len(words)), 0, -1):
        candidate = " ".join(words[-size:])
        if _known(candidate):
            return candidate
    return " ".join(words[-3:])


def _year(value: str) -> int:
    year = int(value)
    if year < 100:
        # Two-digit years in this corpus run from the 1950s to today.
        year += 2000 if year <= 30 else 1900
    return year


def _segment(kind: str, body: str) -> list[CaseNumber]:
    """The cases one type's numbers name: "599, 600 & 602 of 2020", "10398/08, 17812/09"."""
    trailing = list(TRAILING_YEAR.finditer(body))
    default = _year(trailing[-1].group(1)) if trailing else None
    if trailing:
        body = body[:trailing[-1].start()]
    cases: list[CaseNumber] = []
    for lo, hi, range_year in RANGE.findall(body):
        if 0 < int(hi) - int(lo) <= 100:
            year = _year(range_year) if range_year else default
            cases.extend(CaseNumber(kind, str(n), year) for n in range(int(lo), int(hi) + 1))
    body = RANGE.sub(" ", body)
    for item in ITEM.finditer(body):
        number = str(int(item.group("num")))
        year = default
        if item.group("yr"):
            two_digit_dash = item.group("sep") == "-" and len(item.group("yr")) == 2
            if two_digit_dash and default is not None and cases:
                # "254424-25 ... of 2018": the next number in a run, not a year.
                tail = item.group("yr")
                cases.append(CaseNumber(kind, number, year))
                number = number[:-len(tail)] + tail
            else:
                year = _year(item.group("yr"))
        if item.group("pre"):
            number = f"{item.group('pre')}-{number}"
        if item.group("suf"):
            number = f"{number}-{item.group('suf').upper()}"
        cases.append(CaseNumber(kind, number, year))
    # "281, 303/2012": a number printed without a year takes the year of the next one that has it.
    carried: int | None = None
    for index in range(len(cases) - 1, -1, -1):
        if cases[index].year is not None:
            carried = cases[index].year
        elif carried is not None:
            cases[index] = CaseNumber(kind, cases[index].number, carried)
    return list(dict.fromkeys(cases))


def parse(text: str | None) -> list[CaseNumber]:
    """Every case a printed case-number string names; empty if it names none.

    "Criminal Appeals No. 599, 600, 601 & 602 of 2020" names four cases.  A
    string can name several types ("Crl.A.No.130/2005 and M.R.No.213/2005"),
    and each number can carry its own year ("WPs Nos. 10398/08, 17812/09 &
    20670/2010").  A closing year covers every number before it that has none
    ("Crl.A.Nos.304, 305 & M.R.No.39 of 2014").  "1113 TO 1155 OF 2017" is a
    range, expanded only when short enough to be one and not a misread.  FIR,
    crime and revenue-record numbers are not cases and are skipped.
    """
    if not text:
        return []
    text = " ".join(text.split())
    starts = []
    for word in NUMBER_WORD.finditer(text):
        typed = TYPE_BEFORE.search(text[:word.start()])
        run = CONNECTORS.sub("", typed.group(1)).strip() if typed else ""
        starts.append((word, typed.start() if typed else word.start(), _type_text(run)))
    cases: list[CaseNumber] = []
    for index, (word, _, kind_text) in enumerate(starts):
        if not re.search(r"[A-Za-z]", kind_text) or NOT_A_CASE.search(kind_text):
            continue
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        cases.extend(_segment(case_type(kind_text), text[word.end():end]))
    trailing = list(TRAILING_YEAR.finditer(text))
    if trailing:
        closing = _year(trailing[-1].group(1))
        cases = [CaseNumber(c.type, c.number, closing) if c.year is None else c for c in cases]
    return list(dict.fromkeys(cases))


def from_sc_label(case_type_label: str | None, number: str | None, year: str | None) -> CaseNumber | None:
    """The case an SC filename names ("SCP_Crl.A.599_2020_..." -> CRL.A/599/2020)."""
    if not (case_type_label and number):
        return None
    suffix = re.match(r"(\d+)(?:-?([A-Za-z]{1,2}))?$", number.strip())
    canonical = f"{int(suffix.group(1))}-{suffix.group(2).upper()}" if suffix and suffix.group(2) else (
        str(int(suffix.group(1))) if suffix else number.strip())
    return CaseNumber(case_type(case_type_label), canonical, _year(year) if year and year.isdigit() else None)
