"""Case-name linkage: a cited "X v. Y" against the parties of the judgments we hold.

Blocking does most of the work.  The reporter says which court can have
decided the case (SCMR reports only the Supreme Court; CLC, YLR, MLD and
PCrLJ only High Courts), and its year bounds the decision date: a case is
reported in the year it was decided or within the next few.  That leaves a
handful of candidates per citation, so the scorer can stay transparent:
party-name similarity on both sides, a threshold, and a margin over the
runner-up.  ADR 0002 names Splink for this.  After blocking there is nothing
for its EM training to learn that a threshold does not already express, so a
plain scorer is used until a measurement says otherwise.

Generic parties ("The State", "Federation of Pakistan") match half the
criminal and constitutional docket, so a match carried only by a generic side
is not a match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

# Reporter -> courts whose judgments it reports, among the courts we hold.
SC, LHC = "supreme_court_pakistan", "lahore_high_court"
REPORTER_COURTS = {
    "SCMR": {SC},
    "CLC": {LHC}, "YLR": {LHC}, "MLD": {LHC}, "PCrLJ": {LHC},
    "PTD": {SC, LHC}, "CLD": {SC, LHC}, "PLC": {SC, LHC}, "PLC(CS)": {SC, LHC},
}
# Reporters that print the court: the court decides.
PRINTED_COURTS = {"SC": {SC}, "Lahore": {LHC}}
NAMES_COURT = {"PLD", "PLJ", "NLR", "KLR"}
# Reported in the decision year or up to this many years later.
REPORT_LAG = 3

THRESHOLD = 88
MARGIN = 8

# Parties that name an office of the state, not a litigant.  They appear on half
# the docket, so they can never carry a match on their own.
#
# Single words are generic only as the whole name: "State Life Insurance
# Corporation" is a litigant.  Multi-word heads are generic whatever follows,
# because the corpus prints the office with its branch and place --
# "Commissioner Inland Revenue, Zone-II, RTO Lahore" -- and those details differ
# between the citation and the judgment for one and the same case.
GENERIC = {"state", "pakistan", "punjab", "sindh", "secretary", "union of india"}
GENERIC_HEADS = (
    "federation of pakistan", "government of pakistan", "government of punjab", "government of the punjab",
    "province of punjab", "province of the punjab", "province of sindh", "government of sindh",
    "commissioner inland revenue", "commissioner of inland revenue", "deputy commissioner inland revenue",
    "additional commissioner inland revenue", "assistant commissioner inland revenue",
    "commissioner of income tax", "commissioner income tax", "collector of customs", "collector of sales tax",
    "federal board of revenue", "chairman nab", "national accountability bureau",
)
_NOISE = [
    r"\b(?:and|&)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|another|others?)\b(?:\s+others?)?",
    r"\betc\b\.?", r"\bthrough\b.*$", r"\bthr\b\.?.*$", r"\(since\s+dec(?:ease)?d\)", r"\blrs\b\.?",
    r"\bm/s\b\.?", r"\bmessrs\b\.?", r"\bmst\b\.?", r"\bmr\b\.?", r"\bsyed\b", r"\bch\b\.?", r"^\s*the\s+",
    r"\b(?:pvt|private|ltd|limited)\b\.?",
]
# Words that carry no identity; dropped before comparing, so they cannot make two names look alike.
STOPWORDS = {"and", "of", "the", "through", "thr", "v", "vs", "versus", "others", "another", "etc", "in", "for"}
MIN_INFORMATIVE = 2


def normalise_party(name: str | None) -> str:
    if not name:
        return ""
    value = name.lower().replace("�", " ")
    for pattern in _NOISE:
        value = re.sub(pattern, " ", value)
    # Digits go too: a stray "2" or "18" left from a list number is not a party.
    value = re.sub(r"[^a-z ]+", " ", value)
    return " ".join(word for word in value.split() if word not in STOPWORDS)


# Compared after normalising, so "The Federation of Pakistan" and "federation pakistan" are one.
_GENERIC_NORMALISED = {normalise_party(name) for name in GENERIC}
_HEADS_NORMALISED = sorted({normalise_party(head) for head in GENERIC_HEADS}, key=len, reverse=True)


def generic_head(normalised: str) -> str | None:
    """The office a generic party names ("commissioner inland revenue"), or None for a litigant."""
    if normalised in _GENERIC_NORMALISED:
        return normalised
    for head in _HEADS_NORMALISED:
        if normalised == head or normalised.startswith(head + " "):
            return head
    return None


def is_generic(normalised: str) -> bool:
    return not normalised or len(normalised) < 4 or generic_head(normalised) is not None


def candidate_courts(reporter: str | None, court: str | None) -> set[str]:
    base = (reporter or "").replace("-N", "")
    if base in NAMES_COURT:
        return PRINTED_COURTS.get(court or "", set())
    return REPORTER_COURTS.get(base, set())


@dataclass(frozen=True)
class Candidate:
    judgment_id: str
    court_id: str
    year: int | None
    petitioner: str
    respondent: str


def _informative(name: str) -> int:
    return sum(len(word) >= 3 for word in name.split())


def _covered(cited: str, held: str) -> bool:
    """Every informative word of the cited name appears, allowing a misspelling, in the held name."""
    held_words = held.split()
    return all(max((fuzz.ratio(word, other) for other in held_words), default=0) >= 85
               for word in cited.split() if len(word) >= 3)


def _side_score(cited: str, held: str) -> float:
    """Similarity of one side, cited name against held party.  Asymmetric on purpose.

    ``token_set_ratio`` scores 100 whenever one name's words are a subset of
    the other's, which made "2" match "Arbab Tasleem" and "Muhammad Ajmal Khan"
    match "Khan Muhammad".  The subset allowance is kept only in one direction:
    the held party may carry words the citation drops ("Bank of Punjab" against
    "Bank of Punjab through its President"), but every informative word the
    citation prints must be in the held party, and both must be specific.
    Otherwise the whole names must match (``token_sort_ratio``).
    """
    if not cited or not held:
        return 0.0
    if min(_informative(cited), _informative(held)) >= MIN_INFORMATIVE and _covered(cited, held):
        return float(fuzz.token_set_ratio(cited, held))
    return float(fuzz.token_sort_ratio(cited, held))


def _generic_or_side(cited: str, held: str) -> float:
    """A generic side agrees when both name the same office, whatever branch or place follows."""
    head = generic_head(cited) if cited else None
    if head is not None and generic_head(held) == head:
        return 100.0
    return _side_score(cited, held)


def score(cited_petitioner: str, cited_respondent: str, candidate: Candidate) -> tuple[float, bool]:
    """Similarity of a cited name to a candidate's parties (0-100), and whether only generic sides carry it.

    Both sides must agree, generic or not: "Muhammad Aslam v The State" is not
    "Muhammad Aslam v WAPDA".  A generic side cannot *carry* a match, so when
    one side is generic the other must match almost exactly and be specific.
    Sides are compared as printed, never swapped: on a 40-match sample,
    swapping produced wrong matches and no right ones.
    """
    pair = (_generic_or_side(cited_petitioner, candidate.petitioner),
            _generic_or_side(cited_respondent, candidate.respondent))
    generic = (is_generic(cited_petitioner), is_generic(cited_respondent))
    if all(generic):
        return 0.0, True
    value = min(pair)
    if any(generic):
        specific_side = cited_respondent if generic[0] else cited_petitioner
        specific_score = pair[1] if generic[0] else pair[0]
        if specific_score < 95 or _informative(specific_side) < MIN_INFORMATIVE:
            value = min(value, THRESHOLD - 1)
    return value, False


def link(cited: dict[str, str], reporter: str | None, court: str | None, year: int | None,
         by_court_year: dict[tuple[str, int], list[Candidate]]) -> dict[str, object] | None:
    """The one held judgment a cited case name names, or None.  Never a guess between two."""
    petitioner, respondent = normalise_party(cited["petitioner"]), normalise_party(cited["respondent"])
    if not (petitioner and respondent) or year is None:
        return None
    scored = []
    for court_id in candidate_courts(reporter, court):
        for decided in range(year - REPORT_LAG, year + 1):
            for candidate in by_court_year.get((court_id, decided), []):
                value, generic_only = score(petitioner, respondent, candidate)
                if not generic_only:
                    scored.append((value, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].judgment_id))
    best, top = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best < THRESHOLD or best - runner_up < MARGIN:
        return None
    return {"judgment_id": top.judgment_id, "score": round(best, 1), "runner_up": round(runner_up, 1),
            "candidates": len(scored), "matched": {"petitioner": top.petitioner, "respondent": top.respondent}}


def candidates(cited: dict[str, str], reporter: str | None, court: str | None, year: int | None,
               by_court_year: dict[tuple[str, int], list[Candidate]], k: int = 5,
               lag: int = REPORT_LAG + 2) -> list[dict[str, object]]:
    """The k closest held judgments for a cited name, with no threshold: what a labeller chooses from.

    The window is wider than ``link`` uses, so a label can show whether the
    true judgment sat just outside the blocking window.
    """
    petitioner, respondent = normalise_party(cited.get("petitioner")), normalise_party(cited.get("respondent"))
    if year is None:
        return []
    scored = []
    for court_id in candidate_courts(reporter, court):
        for decided in range(year - lag, year + 1):
            for candidate in by_court_year.get((court_id, decided), []):
                pair = (_side_score(petitioner, candidate.petitioner), _side_score(respondent, candidate.respondent))
                scored.append((min(pair), candidate))
    scored.sort(key=lambda item: (-item[0], item[1].judgment_id))
    return [{"judgment_id": c.judgment_id, "year": c.year, "score": round(s, 1),
             "petitioner": c.petitioner, "respondent": c.respondent} for s, c in scored[:k]]
