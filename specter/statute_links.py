"""Joining a statute a judgment names to the statute the corpus holds.

``specter/specter_parser.py`` reads the statutes a judgment relies on as the
page names them. This module answers the next question -- *do we hold that
statute* -- and, just as importantly, says why not when the answer is no.

Two facts about the corpus shape everything here, and both are reported rather
than papered over:

**Jurisdiction.** `pakistancode.gov.pk` is the *federal* code. Of its 1,039
statutes exactly one names Punjab and two name Sindh. But a Lahore High Court
judgment applies Punjab law constantly -- the Punjab Pre-emption Act, the
Punjab Rented Premises Act, PEEDA -- and a Sindh judgment applies Sindh law.
So an unmatched name is usually not a matching failure; it is a statute we do
not have. Guessing a federal statute for a provincial name would attach the
wrong law to the judgment, which is worse than attaching none, so every name is
tagged with the jurisdiction its own title states and an unmatched provincial
name is reported as *not held* rather than as a miss.

**Amendments.** Two different things go by that word:

* *A different enactment with a similar name.* The Companies Ordinance, 1984
  and the Companies Act, 2017 are not versions of one statute; the second
  repealed the first. The year is therefore part of the identity, and a link is
  refused where both sides state a year and the years differ. Without that
  rule the index matched "Companies Act, 1913" to the 2017 Act.
* *A different version of the same enactment.* What the corpus holds is the
  consolidated current text. A judgment from 2010 applied the text as it stood
  in 2010, and the section it construes may since have been substituted. There
  is no version history in this corpus and none is invented. What the statute
  *does* carry is its own amendment footnotes -- "Subs. by Act No. XLI of 2025,
  s.2" -- so the latest amending year is read from them and reported as
  ``text_current_to``. Where a judgment predates that year the link carries
  ``version_risk``, meaning: this is the right statute, but not necessarily the
  words the judge read.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Pakistan states a statute's jurisdiction in its own title, so that is where
# it is read from.  Order matters: "West Pakistan" is checked before the
# provinces that succeeded it, and "Indian" only as an adjective, so that the
# Government of India Act -- which was Pakistan's own interim constitution --
# is not filed under India.
JURISDICTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("West Pakistan", r"\bwest\s+pakistan\b"),
    ("Punjab", r"\bpunjab\b"),
    ("Sindh", r"\bsind[h]?\b"),
    ("Khyber Pakhtunkhwa", r"\bkhyber\s+pakhtunkhwa\b|\bn\.?\s*w\.?\s*f\.?\s*p\b|\bnorth[\s-]west\s+frontier\b"),
    ("Balochistan", r"\bbal[ou]chistan\b"),
    ("Azad Jammu and Kashmir", r"\bazad\s+jammu\b|\baj&k\b|\bajk\b"),
    ("Gilgit-Baltistan", r"\bgilgit\b|\bbaltistan\b"),
    ("Islamabad Capital Territory", r"\bislamabad\b|\bcapital\s+territory\b"),
    ("India", r"\bindian\b|\bconstitution\s+of\s+india\b"),
    ("United Kingdom", r"\bengland\b|\benglish\b|\bunited\s+kingdom\b"),
)
FEDERAL = "Federal"
# `pakistancode.gov.pk` publishes primary legislation.  Rules, policy orders
# and SROs are made *under* an Act and are not in it, so an unmatched one is
# a different fact from a federal Act we ought to hold and do not.
SUBORDINATE_RE = re.compile(
    r"(?i)\b(?:rules|regulations|policy\s+order|s\.?r\.?o\.?|notification|by[\s-]?laws)\b")

# "of" and "the" are dropped from both sides before matching: the corpus writes
# "Constitution of the Islamic Republic of Pakistan" and judgments also write
# it without the "the".
MATCH_STOPWORDS = frozenset({"of", "the"})
YEAR_RE = re.compile(r"\b((?:1[6-9]|20)\d{2})\b")
WORD_RE = re.compile(r"[a-z0-9]+")
# The scraper appended notes to some filenames -- "under review", "same as on
# the official website of FBR dated 30 06 2025" -- which are not part of a
# title and must not be matched against.
SLUG_NOISE_RE = re.compile(
    r"(?i)\b(?:under\s+review|same\s+as\s+on\s+the\s+official.*|repealed?(?:\s+by)?\b.*"
    r"|extracted.*|as\s+amended\s+up\s+to.*)$")
# "Subs. by Act No. XLI of 2025, s.2." -- the amending instrument and its year.
AMENDING_ACT_RE = re.compile(
    r"(?i)\b(?:act|ord(?:inance)?|p\.?o\.?|order)\.?\s*(?:no\.?\s*)?"
    r"[IVXLCDM\d]{1,12}\s+of\s+((?:1[89]|20)\d{2})\b")
# A title wraps across lines in the text sidecar, so a few are joined until the
# year that closes almost every Pakistani title appears.
MAX_TITLE_LINES = 3
# What a Pakistani statute title is: a line naming the kind of instrument.
TITLE_LINE_RE = re.compile(
    r"(?i)\b(?:act|ordinance|order|code|rules|regulations|constitution)\b"
    r"|^\s*THE\b[^\n]*\b(?:1[6-9]|20)\d{2}\b")
# Some copies state their own currency -- "Updated till 7.10.2022".
UPDATED_TILL_RE = re.compile(
    r"(?i)\b(?:updated|amended)\s+(?:till|up\s*to)\b"
    r"[^\n]{0,40}?(?P<year>(?:19|20)\d{2})")


def jurisdiction(name: str) -> str:
    """Which legislature a statute's own title says enacted it."""
    lowered = (name or "").lower()
    for label, pattern in JURISDICTION_MARKERS:
        if re.search(pattern, lowered):
            return label
    return FEDERAL


def _words(text: str) -> tuple[str, ...]:
    return tuple(word for word in WORD_RE.findall((text or "").lower())
                 if word not in MATCH_STOPWORDS and not YEAR_RE.fullmatch(word))


def _year(text: str) -> int | None:
    years = YEAR_RE.findall(text or "")
    return int(years[-1]) if years else None


@dataclass(frozen=True)
class Statute:
    """One statute the corpus holds."""

    statute_id: str
    title: str
    year: int | None
    jurisdiction: str
    category: str
    path: Path
    # Both the printed title and the filename slug are searched: each is noisy
    # in a way the other is not.
    haystacks: tuple[tuple[str, ...], ...]
    # What the copy itself says it is current to, where it says so.
    updated_till: int | None = None

    def contains(self, needle: tuple[str, ...]) -> bool:
        """Do these words appear as a run inside either name for this statute?

        A run, not a subsequence: "companies act" appears in order inside
        "companies profits workers participation act" and is not that statute.
        """
        if not needle:
            return False
        for words in self.haystacks:
            for start in range(len(words) - len(needle) + 1):
                if words[start:start + len(needle)] == needle:
                    return True
        return False


def _title_from_text(path: Path) -> tuple[str, int | None]:
    """The title as the statute prints it, and the date it says it is current to.

    The title is not always the first line.  The FBR's copies open with three
    lines of letterhead, and the Companies Act, 2017 opens with "Updated till
    7.10.2022" -- which is not a title and, read as one, gave that statute the
    year 2022 and made it refuse to match a judgment citing it by its own year.
    So the title is looked for by what a Pakistani statute title *is*: a line
    naming the kind of instrument.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace").split("\n")[:20]
    except OSError:
        return "", None
    updated = None
    lines: list[str] = []
    for line in head:
        stripped = line.strip()
        if not stripped:
            continue
        currency = UPDATED_TILL_RE.search(stripped)
        if currency and updated is None:
            updated = int(currency.group("year"))
        if not lines:
            if re.match(r"(?i)^(?:contents|preamble|part\b|chapter\b|sections?\b)", stripped):
                break
            if not TITLE_LINE_RE.search(stripped) or currency:
                continue
        lines.append(stripped)
        if YEAR_RE.search(stripped) or len(lines) >= MAX_TITLE_LINES:
            break
    return " ".join(lines), updated


def build_index(root: str | Path) -> list[Statute]:
    """Read every statute the corpus holds into something a name can be looked
    up in.  The text sidecar beside each PDF states the title as printed."""
    statutes: list[Statute] = []
    for pdf in sorted(Path(root).rglob("*.pdf")):
        slug = SLUG_NOISE_RE.sub("", re.sub(r"^\d+_", "", pdf.stem).replace("_", " ")).strip()
        printed, updated = _title_from_text(pdf.with_suffix(".txt"))
        title = printed or slug
        statutes.append(Statute(
            statute_id=pdf.stem,
            title=title,
            year=_year(printed) or _year(pdf.stem),
            jurisdiction=jurisdiction(f"{printed} {slug}"),
            category=pdf.parent.name,
            path=pdf,
            haystacks=(_words(printed), _words(slug)),
            updated_till=updated,
        ))
    return statutes


def link(index: Iterable[Statute], name: str) -> dict[str, Any]:
    """Match one statute named in a judgment against the corpus.

    Returns the finding either way: an unmatched provincial name is a gap in
    the corpus, not a failure of the match, and the two must stay tellable
    apart.
    """
    needle, stated_year = _words(name), _year(name)
    where = jurisdiction(name)
    matches = [statute for statute in index if statute.contains(needle)]
    if stated_year is not None:
        # A year on both sides has to agree.  The Companies Ordinance, 1984 and
        # the Companies Act, 2017 are different statutes, and without this the
        # index answered "Companies Act, 1913" with the 2017 Act.
        agreed = [s for s in matches if s.year is None or s.year == stated_year]
        matches = agreed or []
    finding: dict[str, Any] = {"name": name, "jurisdiction": where, "matched": False}
    if not matches:
        if where != FEDERAL:
            finding["reason"] = "not_held_for_this_jurisdiction"
        elif SUBORDINATE_RE.search(name or ""):
            finding["reason"] = "subordinate_legislation_not_in_the_code"
        else:
            finding["reason"] = "not_found_in_federal_code"
        return finding
    # The shortest title that contains the name is the statute itself rather
    # than one that merely mentions it.
    best = min(matches, key=lambda s: (len(s.haystacks[0]) or 999, len(s.title)))
    finding.update(matched=True, statute_id=best.statute_id, statute_title=best.title,
                   statute_year=best.year, category=best.category,
                   candidates=len(matches))
    return finding


def text_current_to(statute: Statute) -> int | None:
    """The latest year any amendment footnote in this statute names.

    This is the corpus's own statement of how current its consolidated text is.
    It is not a promise that nothing changed after it -- only that nothing
    before it is missing.
    """
    if statute.updated_till:
        # The copy states its own currency, which beats inferring it.
        return statute.updated_till
    sidecar = statute.path.with_suffix(".txt")
    try:
        text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    years = [int(year) for year in AMENDING_ACT_RE.findall(text)]
    return max(years) if years else None


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def link_run(documents: list[dict[str, Any]], index: list[Statute]) -> dict[str, Any]:
    """Link every statute named across a parsed corpus."""
    currency: dict[str, int | None] = {}
    rows, held, gaps = [], collections.Counter(), collections.Counter()
    # A statute names other statutes too -- the Companies Act, 2017 names the
    # Ordinance it repealed -- and those relations are real but are not
    # judgment-to-statute ones.  Blending them makes the headline meaningless,
    # so the two are counted apart.
    by_kind: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    risky = 0
    for document in documents:
        metadata = document.get("metadata") or {}
        year = _year(metadata.get("decision_date_iso") or metadata.get("decision_date") or "")
        links = []
        kind = document.get("document_kind") or "unknown"
        for name in metadata.get("acts") or []:
            finding = link(index, name)
            if finding["matched"]:
                statute = next(s for s in index if s.statute_id == finding["statute_id"])
                if statute.statute_id not in currency:
                    currency[statute.statute_id] = text_current_to(statute)
                current_to = currency[statute.statute_id]
                finding["text_current_to"] = current_to
                # The right statute is not the right words: the judge read the
                # text as it stood, and what the corpus holds is consolidated.
                finding["version_risk"] = bool(year and current_to and year < current_to)
                risky += finding["version_risk"]
                held[finding["jurisdiction"]] += 1
            else:
                gaps[finding["reason"]] += 1
            by_kind[kind]["mentions"] += 1
            by_kind[kind]["linked"] += finding["matched"]
            links.append(finding)
        if links:
            by_kind[kind]["documents"] += 1
            rows.append({"document": document.get("output_stem"), "document_kind": kind,
                         "decision_year": year, "court_id": metadata.get("court_id"),
                         "statutes": links})
    total = sum(held.values()) + sum(gaps.values())
    return {
        "documents": len(rows),
        "mentions": total,
        "linked": sum(held.values()),
        "not_held": sum(gaps.values()),
        "version_risk": risky,
        "linked_by_jurisdiction": dict(held),
        "not_held_by_reason": dict(gaps),
        "by_document_kind": {kind: dict(counts) for kind, counts in by_kind.items()},
        "links": rows,
    }


def render_run(result: dict[str, Any]) -> str:
    lines = [
        f"documents naming a statute : {result['documents']}",
        *(f"  {kind:24s} {counts['documents']:4d} documents, {counts['mentions']:4d} mentions,"
          f" {counts['linked']:4d} linked"
          for kind, counts in sorted(result["by_document_kind"].items())),
        f"statute mentions           : {result['mentions']}",
        f"  linked to one we hold    : {result['linked']}",
        f"  not held                 : {result['not_held']}",
        f"  linked but text is newer : {result['version_risk']}",
        "",
        "not held, by reason (the corpus is the federal code, primary legislation only):",
    ]
    for where, count in sorted(result["not_held_by_reason"].items(), key=lambda r: -r[1]):
        lines.append(f"  {where:30s} {count}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Link the statutes a judgment names to the statutes the corpus holds.")
    parser.add_argument("output_dir", type=Path, help="the --out folder of a `specter parse` run")
    parser.add_argument("--statutes", type=Path, required=True,
                        help="root of the statute corpus (pakistancode)")
    parser.add_argument("--output", type=Path, default=None,
                        help="where to write links.json (default: <output_dir>/links.json)")
    args = parser.parse_args()
    documents = []
    for path in sorted((args.output_dir / "metadata").glob("*.json")):
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if not documents:
        parser.error(f"no metadata files under {args.output_dir / 'metadata'}")
    result = link_run(documents, build_index(args.statutes))
    print(render_run(result))
    destination = args.output or args.output_dir / "links.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {destination}")


if __name__ == "__main__":
    main()
