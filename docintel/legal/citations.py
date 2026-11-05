"""Case citations: what a judgment relies on, and where on the page it says so.

A citation is the one piece of a judgment that points *outside* it. Extracted
across a corpus it is the only structure that connects judgments to each other,
which is what makes "what else followed this rule" answerable at all.

Pakistan writes citations in two grammars, and the corpus uses both:

* **Year first** -- ``2008 SCMR 598``. The reporter carries the court, so none
  is printed.
* **Reporter first** -- ``PLD 2015 SC 123``. PLD (and PLJ, NLR, KLR) span every
  court, so the court is named between the year and the page.

Both are common: across 450 documents SCMR alone accounts for 1,108 of the
2,516 citations found, and PLD -- the reporter-first form -- for 807.

Both are matched against a **whitelist of reporters**, not against a general
shape. A shape like ``<year> <word> <number>`` also matches "2018 AND 3",
"Crl. Appeal No. 2014 ... 9", and "Dated 2019 ... 5" -- all of which the survey
found in quantity. Pakistan's law reports are a closed, known set, so naming
them is both possible and the only way to keep precision high. A wrong edge in
a citation graph asserts an authority the judge never relied on, which is worse
than a missing one -- the same trade this codebase makes for bounding boxes.

Indian and English citations are extracted too, and marked with their
jurisdiction rather than dropped: a Pakistani judgment citing ``(2015) 11
Supreme Court Cases 493`` really did rely on it, but an index of Pakistani
authority must be able to exclude it.

Citations are read from blocks rather than from a flat string, so every one
carries the page and box it was printed in. A cited authority that cannot be
pointed at on the page is not evidence of anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Reporter:
    """One law report series, and every way the corpus spells it."""

    key: str
    name: str
    jurisdiction: str
    spellings: tuple[str, ...]
    # PLD and its siblings print the court between the year and the page,
    # because one series covers every court.  The others do not.
    names_court: bool = False


# Spellings are matched case-insensitively with flexible spacing (see
# ``_spelling_pattern``), so "P.Cr.LJ", "PCrLJ" and "P Cr. L J" -- all three
# observed -- need only the one canonical entry each.
REPORTERS: tuple[Reporter, ...] = (
    Reporter("SCMR", "Supreme Court Monthly Review", "PK", ("SCMR", "S.C.M.R.")),
    Reporter("PLD", "All Pakistan Legal Decisions", "PK", ("PLD", "P.L.D."), names_court=True),
    Reporter("CLC", "Civil Law Cases", "PK", ("CLC",)),
    Reporter("YLR", "Yearly Law Reporter", "PK", ("YLR",)),
    Reporter("MLD", "Monthly Law Digest", "PK", ("MLD",)),
    Reporter("PTD", "Pakistan Tax Decisions", "PK", ("PTD",)),
    Reporter("PCrLJ", "Pakistan Criminal Law Journal", "PK",
             ("PCrLJ", "P.Cr.L.J.", "PCr.LJ", "P.Cr.LJ", "PCr.L.J.")),
    # The labour/service series prints its sub-series in brackets, and the
    # brackets are part of the citation: "2024 PLC (C.S.) 402" and "2015 PLC
    # 1528" are different series, so the sub-series is kept in the key.
    Reporter("PLC(CS)", "Pakistan Labour Cases (Civil Services)", "PK",
             ("PLC (C.S.)", "PLC (CS)", "PLC (C.S)", "PLC(CS)", "PLC C.S.")),
    Reporter("PLC", "Pakistan Labour Cases", "PK", ("PLC",)),
    Reporter("CLD", "Corporate Law Decisions", "PK", ("CLD",)),
    Reporter("PLJ", "Pakistan Law Journal", "PK", ("PLJ",), names_court=True),
    Reporter("NLR", "National Law Reporter", "PK", ("NLR",), names_court=True),
    Reporter("KLR", "Karachi Law Reporter", "PK", ("KLR",), names_court=True),
    # There is no separate entry for a series' Notes section: every series has
    # one, so it is read as a suffix on whichever reporter was printed.
    #
    # The neutral citation the Lahore High Court assigns itself, which is also
    # how its own corpus names its files -- so these are the citations that can
    # be resolved against documents we hold.
    Reporter("LHC", "Lahore High Court neutral citation", "PK", ("LHC",)),
    Reporter("PHC", "Peshawar High Court neutral citation", "PK", ("PHC",)),
    Reporter("SCC", "Supreme Court Cases (India)", "IN",
             ("Supreme Court Cases", "SCC")),
    Reporter("SCCONLINE", "SCC OnLine (India)", "IN",
             ("SCC Online", "SCC OnLine"), names_court=True),
    Reporter("AIR", "All India Reporter", "IN", ("AIR",), names_court=True),
    Reporter("CRILJ", "Criminal Law Journal (India)", "IN", ("CriLJ", "Cri.L.J.")),
    Reporter("AC", "Appeal Cases (England)", "UK", ("AC", "A.C.")),
)

# Every way the corpus writes a court inside a PLD-style citation.
COURTS: dict[str, tuple[str, ...]] = {
    "SC": ("SC", "S.C.", "S C", "Supreme Court", "SC (Pak.)", "SC (Pak)", "SC(Pak)", "S.C. (Pak.)"),
    "Lahore": ("Lahore", "Lah.", "Lah", "(W.P.) Lahore", "W.P. Lahore"),
    "Karachi": ("Karachi", "Kar.", "Kar", "(W.P.) Karachi", "W.P. Karachi"),
    "Sindh": ("Sindh", "Sind"),
    "Islamabad": ("Islamabad", "Isl.", "Isb"),
    "Peshawar": ("Peshawar", "Pesh.", "Pesh"),
    "Quetta": ("Quetta", "Quet."),
    "Baluchistan": ("Baluchistan", "Balochistan", "BJ"),
    # Pre-1971 and specialist benches, all observed in the corpus.
    "West Pakistan": ("West Pakistan", "W.P.", "(W.P.)"),
    "Tax Cases": ("Tax Cases (Kar.)", "Tax Cases", "Trib"),
    "Cr.C.": ("Cr.C. (Lahore)", "Cr.C."),
    "FSC": ("FSC", "Federal Shariat Court", "F.S.C."),
    "FC": ("FC", "Federal Court", "F.C."),
    "AJ&K": ("AJ&K", "AJK", "Azad J&K"),
    "Privy Council": ("Privy Council", "PC", "P.C."),
    "Dacca": ("Dacca", "Dhaka"),
    "Karachi High Court": ("Karachi High Court",),
    # Indian benches, for AIR and SCC OnLine.
    "Mad": ("Mad", "Madras"),
    "All": ("All", "Allahabad"),
    "P&H": ("P&H", "Punjab and Haryana"),
    "Ker": ("Ker", "Kerala"),
    "Del": ("Del", "Delhi"),
    "Bom": ("Bom", "Bombay"),
    "Cal": ("Cal", "Calcutta"),
    "Patna": ("Patna",),
}

# The year pattern already restricts to 1800-2099, so only the page needs a
# bound here.  A four-digit page is real; five is the ceiling any of these
# reporters has reached.
MAX_PAGE = 99999


def _spelling_pattern(spelling: str) -> str:
    """Match a reporter however the page spaces and punctuates it.

    "PCrLJ", "P.Cr.LJ" and "P Cr. L J" are one series printed three ways, and a
    scanned page adds more.  Rather than enumerate them, the letters are
    required in order with optional dots and spaces between -- and *only*
    those, so the pattern can never run on into a neighbouring word.
    """
    # Built by joining, never by stripping a trailing separator: a strip on the
    # character class ate the final letter of every spelling that ended in one
    # of its characters, so "Supreme Court Cases" matched only "...Case" and
    # every Indian citation in the corpus was silently missed.
    pieces = []
    for character in spelling:
        if character.isalnum():
            pieces.append(re.escape(character))
        elif character in "()&":
            # Bracketed sub-series ("PLC (C.S.)") must keep their brackets --
            # they are what distinguishes one series from another.  Dots are
            # *not* kept: keeping them made "PLC C.S." require its dots, so
            # "2012 PLC CS 90" -- printed exactly like that -- was missed.
            pieces.append(re.escape(character))
        # Spaces and dots are dropped here and allowed back as optional
        # separators between every piece, which is what lets one entry match
        # "PCrLJ", "P.Cr.LJ" and "P Cr. L J" alike.
    return r"[.\s]*".join(pieces) if pieces else re.escape(spelling)


def _alternatives(spellings: Iterable[str]) -> str:
    # Longest first, so "PLC (C.S.)" wins over "PLC" and "SCC Online" over "SCC".
    ordered = sorted(spellings, key=len, reverse=True)
    return "|".join(_spelling_pattern(spelling) for spelling in ordered)


_YEAR = r"(?:1[89]\d{2}|20\d{2})"
# The Indian series brackets its year -- "(2015) 11 Supreme Court Cases 493" --
# and Pakistan does not.  The brackets are taken only as a balanced pair, so
# the opening parenthesis of "...Lahore (2008 SCMR 598)" stays with the
# sentence that owns it rather than being read as part of the citation.
def _bracketed_year(name: str) -> str:
    return rf"(?:\[(?P<{name}a>{_YEAR})\]|\((?P<{name}b>{_YEAR})\)|(?P<{name}c>{_YEAR}))"


def _year_of(match: re.Match[str], name: str) -> int:
    return int(next(match.group(f"{name}{suffix}") for suffix in "abc"
                    if match.group(f"{name}{suffix}")))


_COURT_ALTERNATIVES = _alternatives([name for names in COURTS.values() for name in names])

# Every series runs a separately paginated "Notes" section, and the corpus
# writes it six ways -- "2018 YLR Note 114", "2017 CLC (N) 227", "2022 PCRLJ-N
# 64", "2023 PLC(CS)N 14".  A note is a different case from the same page of
# the main series, so it belongs in the identifier, not beside it.
_NOTE = r"(?:\s*[-(]?\s*(?:Notes?|N)\s*\)?)"
# Where a series covers every court, the reporter often annotates which one --
# "1987 CLC [Karachi] 2185".  It is an annotation, not part of the citation:
# these series paginate continuously across courts, so the court must be read
# and kept *out* of the identifier or one case becomes two nodes.
_BRACKETED_COURT = r"(?:\s*[\[(]\s*(?P<court>" + _COURT_ALTERNATIVES + r")\s*[\])])"
# The same annotation is also printed after the page -- "2009 YLR 550-Karachi",
# "2011 YLR 2393 [Lahore]".  Brackets are unambiguous; a bare hyphen is not, so
# there it must not be followed by an ordinary word: several court
# abbreviations ("All", "Kar", "PC") are also English words, and "2018 CLC 392
# - All the parties" must not read as a citation from Allahabad.
_TRAILING_COURT = (r"(?:\s*(?:\[\s*(?P<court_afterb>" + _COURT_ALTERNATIVES + r")\s*\]"
                   r"|\(\s*(?P<court_afterp>" + _COURT_ALTERNATIVES + r")\s*\)"
                   r"|-\s*(?P<court_afterh>" + _COURT_ALTERNATIVES + r")\b(?!\s+[a-z])))")

# "2008 SCMR 598", "(2015) 11 Supreme Court Cases 493" -- the optional volume
# number is how the Indian series is written and how it must be read back.
YEAR_FIRST_RE = re.compile(
    r"(?<![\w/-])" + _bracketed_year("year") + r"\s*"
    r"(?:(?P<volume>\d{1,3})\s+)?"
    r"(?P<reporter>" + _alternatives(
        [spelling for reporter in REPORTERS if not reporter.names_court
         for spelling in reporter.spellings]) + r")"
    + f"(?P<note>{_NOTE})?" + f"{_BRACKETED_COURT}?"
    # A page may be followed by a hyphenated court but never by a hyphenated
    # year: "550-Karachi" is a citation, "3098-2019" is a case number.
    + r"\s*[.,]?\s*(?P<page>\d{1,5})(?![\d/])(?!-\d)"
    + f"{_TRAILING_COURT}?",
    re.IGNORECASE,
)

# "PLD 2015 SC 123", "AIR 1918 Madras 111", "2013 SCC Online Mad 2171" -- the
# reporter may lead or follow the year, so both orders are matched here.
_COURT_NAMING = _alternatives([spelling for reporter in REPORTERS if reporter.names_court
                               for spelling in reporter.spellings])
REPORTER_FIRST_RE = re.compile(
    r"(?<![\w/-])(?:"
    r"(?P<reporter>" + _COURT_NAMING + r")\s*[.,]?\s*" + _bracketed_year("year")
    + r"|" + _bracketed_year("alt") + r"\s*(?P<reporter2>" + _COURT_NAMING + r")"
    r")"
    # No space is required before the court: "PLD 1996Supreme Court 543" is
    # printed exactly like that, and the court list is a whitelist, so nothing
    # else can slip in.  The brackets of "PLD 2016 [Lahore] 383" are taken only
    # as a matched pair, so a stray one stays with the sentence that owns it.
    r"\s*(?:\[\s*(?P<courta>" + _COURT_ALTERNATIVES + r")\s*\]"
    r"|\(\s*(?P<courtb>" + _COURT_ALTERNATIVES + r")\s*\)"
    r"|(?P<courtc>" + _COURT_ALTERNATIVES + r"))"
    r"\s*[.,]?\s*(?P<page>\d{1,5})(?![\d/-])",
    re.IGNORECASE,
)


def _reporter_for(spelling: str) -> Reporter | None:
    """Which series was printed, given whatever spelling of it was matched."""
    squashed = re.sub(r"[^a-z0-9()&]", "", spelling.casefold())
    best = None
    for reporter in REPORTERS:
        for candidate in reporter.spellings:
            if re.sub(r"[^a-z0-9()&]", "", candidate.casefold()) == squashed:
                # Prefer the longest spelling that matches exactly, so
                # "PLC (C.S.)" never resolves to plain "PLC".
                if best is None or len(candidate) > best[1]:
                    best = (reporter, len(candidate))
    return best[0] if best else None


def _court_for(spelling: str) -> str | None:
    squashed = re.sub(r"[^a-z0-9&]", "", (spelling or "").casefold())
    for canonical, names in COURTS.items():
        if any(re.sub(r"[^a-z0-9&]", "", name.casefold()) == squashed for name in names):
            return canonical
    return None


def _entry(reporter: Reporter, year: int, page: int, text: str,
           court: str | None = None, volume: int | None = None,
           note: bool = False) -> dict[str, Any] | None:
    if not 0 < page <= MAX_PAGE:
        return None
    # A note is a different case from the same page of the main series, so the
    # two must not share a node.
    key = f"{reporter.key}-N" if note else reporter.key
    # The volume belongs in the identifier wherever it is printed: the Indian
    # series numbers several volumes a year, so "(2015) 11 SCC 493" and
    # "(2015) 2 SCC 493" are different cases and must not share a node.
    series = f"{volume} {key}" if volume else key
    # The court belongs in the identifier only where the series paginates by
    # court, which is what ``names_court`` records.  CLC and its siblings run
    # one continuous pagination across every court, so "1987 CLC [Karachi]
    # 2185" and "1987 CLC 2185" are one case and must resolve to one node.
    identifier = (f"{key} {year} {court} {page}" if court and reporter.names_court
                  else f"{year} {series} {page}")
    return {
        "id": identifier,
        "reporter": key,
        "reporter_name": reporter.name,
        "jurisdiction": reporter.jurisdiction,
        "year": year,
        "court": court,
        "volume": volume,
        "page": page,
        "text": " ".join(text.split()),
    }


def find_citations(text: str) -> list[dict[str, Any]]:
    """Every citation printed in a passage, in the order it appears.

    Both grammars are tried and their results merged by position, so a document
    that mixes them -- most do -- yields one list in reading order.
    """
    found: dict[tuple[int, int], dict[str, Any]] = {}
    for match in YEAR_FIRST_RE.finditer(text):
        reporter = _reporter_for(match.group("reporter"))
        if reporter is None:
            continue
        volume = int(match.group("volume")) if match.group("volume") else None
        entry = _entry(reporter, _year_of(match, "year"), int(match.group("page")),
                       match.group(0), volume=volume,
                       court=_court_for(next(
                           (match.group(name) for name in
                            ("court", "court_afterb", "court_afterp", "court_afterh")
                            if match.group(name)), None)),
                       note=bool(match.group("note")))
        if entry:
            found[(match.start(), match.end())] = entry
    for match in REPORTER_FIRST_RE.finditer(text):
        reporter = _reporter_for(match.group("reporter") or match.group("reporter2"))
        if reporter is None:
            continue
        leading = any(match.group(f"year{suffix}") for suffix in "abc")
        entry = _entry(reporter, _year_of(match, "year" if leading else "alt"),
                       int(match.group("page")), match.group(0),
                       court=_court_for(next(
                           (match.group(f"court{suffix}") for suffix in "abc"
                            if match.group(f"court{suffix}")), None)))
        if entry:
            found[(match.start(), match.end())] = entry
    # A reporter-first match subsumes the year-first one inside it: "PLD 2015
    # SC 123" also reads as a bare "2015 ... 123" span.  The longer match wins.
    spans = sorted(found, key=lambda span: (span[0], -(span[1] - span[0])))
    kept: list[tuple[int, int]] = []
    for span in spans:
        if not any(span[0] >= start and span[1] <= end for start, end in kept):
            kept.append(span)
    return [found[span] for span in sorted(kept)]


def cited_authorities(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every citation in a document, each with the page and box it is printed in.

    Distinct authorities, not distinct mentions: a judgment that relies on one
    case four times has cited it once, and the graph should say so.  Every
    place it was mentioned is kept under ``mentions``, so a reader can be shown
    any of them.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for page in pages:
        for block in page.get("blocks", []):
            if block.get("type") in {"header", "footer"}:
                continue
            for citation in find_citations(block.get("text", "") or ""):
                mention = {"page": page.get("page_number"), "bbox": block.get("bbox"),
                           "block_id": block.get("id"), "text": citation["text"]}
                existing = by_id.get(citation["id"])
                if existing:
                    existing["mentions"].append(mention)
                else:
                    by_id[citation["id"]] = {**citation, "mentions": [mention]}
    return sorted(by_id.values(), key=lambda entry: (entry["mentions"][0]["page"] or 0,
                                                     entry["id"]))


def self_citation(metadata: dict[str, Any] | None) -> str | None:
    """The citation by which this judgment is itself known, if the corpus says.

    Only LHC states one, in its filename, and only that makes a document
    addressable by other judgments citing it.  SC and IHC judgments carry no
    reported citation at all -- the reporter assigns one after publication --
    so nothing that cites them can be linked back to the document we hold.
    """
    neutral = (metadata or {}).get("neutral_citation")
    if not neutral:
        return None
    citations = find_citations(neutral)
    return citations[0]["id"] if citations else None


# --------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------
#
# One node per authority -- whether or not the corpus holds it -- and one edge
# per "this judgment relied on that one".  Most cited authorities are *not* in
# the corpus, and that is the honest state rather than a defect: a judgment
# from a court website carries no reported citation of its own, because the
# reporter assigns one only on publication.  Only LHC, which names its files
# by neutral citation, is addressable that way today.
#
# The edges are still worth having without both endpoints.  "Which of our
# judgments rely on 2008 SCMR 598" is answerable from the citing side alone,
# and it is the question a lawyer actually asks.

import argparse
import json
import re
from collections import Counter
from pathlib import Path


TOP_AUTHORITIES = 20


def _decision_year(metadata: dict[str, Any]) -> int | None:
    iso = metadata.get("decision_date_iso")
    if iso and len(iso) >= 4 and iso[:4].isdigit():
        return int(iso[:4])
    years = re.findall(r"\b(1[89]\d{2}|20\d{2})\b", metadata.get("decision_date") or "")
    return int(years[-1]) if years else None


def build_graph(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Nodes and edges over a parsed corpus.

    ``documents`` are the payloads ``specter parse`` writes to ``metadata/``.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    anachronisms: list[dict[str, Any]] = []

    def node(identifier: str, **fields: Any) -> dict[str, Any]:
        entry = nodes.setdefault(identifier, {"id": identifier, "in_corpus": False,
                                              "cited_by": 0, "cites": 0})
        entry.update({key: value for key, value in fields.items() if value is not None})
        return entry

    for document in documents:
        metadata = document.get("metadata") or {}
        own = self_citation(metadata)
        # The filename stem is not an identifier: 21,712 IHC files are called
        # judgment.pdf, and using it would collapse them into one node.  The
        # run's own output name is distinct by construction.
        source = own or document.get("output_stem") or str(
            document.get("source_file") or document.get("source_name") or "?")
        year = _decision_year(metadata)
        node(source, in_corpus=True, source_name=document.get("source_name"),
             source_file=document.get("source_file"), court_id=metadata.get("court_id"),
             case_number=metadata.get("case_number"), parties=metadata.get("parties"),
             decision_date=metadata.get("decision_date"), year=year,
             document_kind=document.get("document_kind"))
        for citation in document.get("citations") or []:
            target = citation["id"]
            if target == own:
                # A judgment printing its own citation in its header is not
                # relying on itself.
                continue
            node(target, reporter=citation.get("reporter"),
                 jurisdiction=citation.get("jurisdiction"), year=citation.get("year"))
            first = (citation.get("mentions") or [{}])[0]
            edges.append({"from": source, "to": target,
                          "page": first.get("page"), "bbox": first.get("bbox"),
                          "text": citation.get("text")})
            nodes[source]["cites"] += 1
            nodes[target]["cited_by"] += 1
            if year and citation.get("year") and citation["year"] > year:
                # Impossible, so one of the two is wrong -- and measurement says
                # which.  Over 400 LHC judgments the citations were right and
                # the *decision date* was wrong: a 2014 judgment extracted as
                # "17.9.2004" still cites 2010-2012 cases quite properly.  A
                # smaller class is a misprint on the page itself ("2077 SCMR
                # 7354" for "2017 SCMR 1354", a 1 read as a 7).  Neither is
                # corrected here; both are reported, because the check cannot
                # tell them apart and guessing would damage one to flatter the
                # other.
                anachronisms.append({"from": source, "to": target,
                                     "citing_year": year, "cited_year": citation["year"],
                                     "text": citation.get("text")})

    held = {identifier for identifier, entry in nodes.items() if entry["in_corpus"]}
    resolved = [edge for edge in edges if edge["to"] in held]
    authorities = Counter(edge["to"] for edge in edges)
    return {
        "documents": sum(1 for entry in nodes.values() if entry["in_corpus"]),
        "nodes": len(nodes),
        "edges": len(edges),
        "edges_to_a_document_we_hold": len(resolved),
        "distinct_authorities": len(authorities),
        "anachronisms": anachronisms,
        "most_cited": [{"id": identifier, "cited_by": count,
                        "in_corpus": nodes[identifier]["in_corpus"]}
                       for identifier, count in authorities.most_common(TOP_AUTHORITIES)],
        "node_list": sorted(nodes.values(), key=lambda entry: -entry["cited_by"]),
        "edge_list": edges,
    }


def render_graph(graph: dict[str, Any]) -> str:
    lines = [
        f"{graph['documents']} documents cite {graph['distinct_authorities']} distinct authorities",
        f"{graph['nodes']} nodes, {graph['edges']} edges",
        f"{graph['edges_to_a_document_we_hold']} edges point at a document in this corpus"
        f" ({graph['edges_to_a_document_we_hold'] / max(graph['edges'], 1):.1%})",
    ]
    if graph["anachronisms"]:
        lines += ["", f"citations dated after the judgment relying on them "
                      f"({len(graph['anachronisms'])}) -- the citation or the decision date is wrong; "
                      f"measured on LHC it is usually the date"]
        for row in graph["anachronisms"][:5]:
            lines.append(f"   {row['citing_year']} judgment -> {row['text']!r}")
    lines += ["", f"most-cited authorities"]
    for row in graph["most_cited"]:
        held = "  (in corpus)" if row["in_corpus"] else ""
        lines.append(f"  {row['cited_by']:>5}  {row['id']}{held}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the citation graph over a parsed corpus.")
    parser.add_argument("output_dir", type=Path, help="the --out folder of a `specter parse` run")
    parser.add_argument("--output", type=Path, default=None,
                        help="where to write graph.json (default: <output_dir>/graph.json)")
    args = parser.parse_args()
    documents = []
    for path in sorted((args.output_dir / "metadata").glob("*.json")):
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if not documents:
        parser.error(f"no metadata files under {args.output_dir / 'metadata'}")
    graph = build_graph(documents)
    print(render_graph(graph))
    destination = args.output or args.output_dir / "graph.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {destination}")


if __name__ == "__main__":
    main()
