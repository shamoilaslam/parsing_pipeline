"""What a citation is printed with: the case name before it, and the citations printed as the same case.

Two readings of the citing block, both deterministic:

* **Parallel citations.**  "(1993 P T D 1108 = 1993 S C M R 1108)" prints one
  case under two reporters.  Only "=" joins them.  A comma, a semicolon or
  "and" between citations separates *different* cases in a string cite
  ("PLD 1970 SC 39; 1972 SCMR 47 and 1981 SCMR 758"), so treating those as
  parallel would merge three judgments into one.
* **The case name.**  Pakistani judgments print the name immediately before
  the citation: "Khuda Bakhsh Vs. Khushi Muhammad and 3 others (PLD 1976 SC
  208)".  The two sides are read from the text just before the citation, cut
  at the phrase that introduces it ("reported as", "reliance is placed on",
  "the case of") and at the previous citation.
"""

from __future__ import annotations

import re
from typing import Any

from specter.citations import find_citations

# Text between two citations that makes them one case.
PARALLEL_SEP = re.compile(r"^\s*[(\[]?\s*=\s*[(\[]?\s*$")
# How far back a case name can start.
NAME_WINDOW = 260
VERSUS = re.compile(r"\s+(?:v\.?|vs\.?|versus)\s+", re.I)
# Phrases that introduce a case name; the name starts after the last one.
INTRODUCERS = re.compile(
    r"(?i)(?:placed\s+reliance\s+(?:up)?on|reliance\s+(?:up)?on|reported\s+as|reported\s+in|case\s+of|cases\s+of|titled|tilted|in\s+re|relied\s+(?:up)?on|"
    r"reliance\s+(?:is|was|can\s+be|may\s+be)\s+placed\s+(?:up)?on|reference\s+(?:is|was|can\s+be|may\s+be)\s+made\s+to|"
    r"referred\s+to|see|vide|cited\s+as|judgments?\s+(?:of|in)|precedents?\s+(?:of|in)|in\s+the\s+cases?\s+of|"
    r"followed\s+in|held\s+in|including|namely|i\.e\.|e\.g\.|\bin\b)\s*[:,]?\s*")
QUOTES = "\"'‘’“”�"


def citation_spans(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Each citation in a block with its character span, in reading order."""
    spans, cursor = [], 0
    for citation in find_citations(text):
        start = text.find(citation["text"], cursor)
        if start < 0:
            continue
        spans.append((start, start + len(citation["text"]), citation))
        cursor = start + len(citation["text"])
    return spans


def parallel_groups(text: str) -> list[list[str]]:
    """Groups of citation ids printed as one case ("A = B"); only groups of two or more."""
    spans = citation_spans(text)
    groups: list[list[str]] = []
    current: list[str] = []
    for index, (start, end, citation) in enumerate(spans):
        if current and PARALLEL_SEP.match(text[spans[index - 1][1]:start]):
            current.append(citation["id"])
            continue
        if len(current) > 1:
            groups.append(current)
        current = [citation["id"]]
    if len(current) > 1:
        groups.append(current)
    return groups


def _clean_side(value: str) -> str:
    value = value.strip().strip(QUOTES).strip(" ,;:-(")
    # Left over from a string cite: "... (2019 Lahore 76) and Saleem Khan v. ...".
    value = re.sub(r"(?i)^(?:and|&|also)\s+", "", value).strip(QUOTES + " ")
    return " ".join(value.split())


def case_name(text: str, start: int, previous_end: int = 0) -> dict[str, str] | None:
    """The "X v. Y" printed just before a citation starting at ``start``, or None."""
    window_start = max(previous_end, start - NAME_WINDOW)
    before = text[window_start:start].rstrip(" ([" + QUOTES)
    # The name ends where the citation's bracket opens; nothing after a closing
    # bracket can be part of it.
    if ")" in before:
        before = before[before.rfind(")") + 1:]
    matches = list(VERSUS.finditer(before))
    if not matches:
        return None
    split = matches[-1]
    left, right = before[:split.start()], before[split.end():]
    # The petitioner starts after the last introducing phrase or sentence break.
    cut = 0
    for match in INTRODUCERS.finditer(left):
        cut = max(cut, match.end())
    for stop in (". ", "; ", ": "):
        position = left.rfind(stop)
        if position >= 0 and position + len(stop) > cut and not re.search(r"(?i)\b(?:mst|m/s|messrs|no|dr|mr|ms|st|co|ltd|pvt|govt)\.\s$", left[:position + 2]):
            cut = position + len(stop)
    petitioner, respondent = _clean_side(left[cut:]), _clean_side(right)
    if not (2 <= len(petitioner) <= 160 and 2 <= len(respondent) <= 160):
        return None
    return {"petitioner": petitioner, "respondent": respondent}
