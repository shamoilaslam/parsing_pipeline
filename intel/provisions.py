"""LI-3: provision-level law.  Every "section 497(2) Cr.P.C." a judgment prints, linked to the section we hold.

A judgment names law at the level a lawyer reasons at -- the section -- and in
four ways, all of them in the corpus:

* by abbreviation:  "section 337-F(i) PPC", "U/S 9-C CNSA-1997", "S.12(2) CPC"
* by title:         "Section 54 of the Contract Act", "section 15(3) of the
                    Punjab Pre-emption Act, 1913"
* the Constitution: "Article 199(1)(a) of the Constitution"
* by reference back: "section 10(1)(i) of the Act" -- the statute the judgment
                    is already discussing

Each mention is resolved to a statute (``statute_id``, the stem of the parsed
statute) and, where the section index holds it, to the section.  A title
naming a provincial statute is kept with its jurisdiction and marked not held:
attaching the federal statute with a similar name would attach the wrong law.

Every mention also gets a **role**, read by rules from its sentence:

    vires_challenged -- its validity is in question: declared, argued or held
                        not to be unconstitutional or ultra vires
    benchmark        -- a constitutional provision a law is measured against
                        in that same sentence
    interpreted      -- its scope, meaning or requirements are in question
    invoked          -- the proceeding is brought under it (Article 199, s. 12(2) CPC)
    charged_under    -- an FIR or charge names it; penal provisions only
    mentioned        -- none of the above

A vires sentence ("the Act ... is ultra vires Article 140A") is about the
impugned law; the Article in it is the standard, not the thing struck down,
and the sentence is as often an argument, or a holding that the law is *not*
ultra vires.  Hence "challenged" and "benchmark", never "struck down".

Rules give a high-precision first pass; the residue in ``mentioned`` is what a
model looks at later (ADR 0002, Decision 5).
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from intel.corpus import ParsedDocument
from intel.evidence import Evidence, Fact
from rag.normalise import flatten
from specter import statute_links

EXTRACTOR = "intel.provisions"
VERSION = "7"

# One provision number: digits with an optional letter suffix ("337-F", "9-C",
# "10A") and any sub-clauses ("(2)", "(1)(a)", "(i)"); or a roman numeral, which
# is kept only after "Order" (CPC Orders), since "S.I" is a sub-inspector.
NUM = r"(?:\d{1,4}(?:\s*-\s*[A-Z]{1,2}\b|[A-Z]{1,2}\b)?(?:\s*\(\s*[0-9a-zA-Z]{1,4}\s*\))*|[IVXL]{1,7}\b)"
# ", and" as well as "and": "Sections 409, 467, 477-A, and 109 of the PPC".
SEP = r"\s*(?:,\s*(?:and|&)|,|&|and|or|/|to)\s*"
ANCHOR = re.compile(
    r"(?i)(?<![A-Za-z])(?P<kind>sections?|ss?\.|u\s*/\s*ss?\.?|articles?|arts?\.|order|rules?)\s*"
    r"(?P<nums>" + NUM + r"(?:" + SEP + NUM + r")*)")
NUM_RE = re.compile(NUM)
TAIL = 140
HEAD = 160
# No section of a statute in this corpus is numbered in the thousands; "Police Rules 1934" is a year.
MAX_NUMBER = 999

QSO_NAME = r"Qanun-?\s?e-?\s?Shahadat(?:\s+Order)?|Q\.?\s?S\.?(?:\s?O\.?)?"
ABBREVIATION = re.compile(
    r"(?i)^[\s,]*(?:of\s+(?:the\s+)?)?(?P<abbr>P\.?\s?P\.?\s?C|Cr\.?\s?P\.?\s?C|C\.?\s?P\.?\s?C|" + QSO_NAME + r"|"
    r"CNSA|ATA|NAO)(?![A-Za-z])\.?")
# Pakistan's Constitution, not "the Constitution of UAE" or a club's constitution.
CONSTITUTION = re.compile(r"(?i)^[\s,]*of\s+(?:the\s+)?Constitution\b(?!\s+of\s+(?!(?:the\s+)?(?:Islamic\s+Republic|Pakistan)))")
# "of the Contract Act, 1872" or ", Qanun-e-Shahadat, 1984" style: the title follows the number.
TITLE = re.compile(
    r"^[\s,]*(?:of\s+(?:the\s+)?|,\s*(?:the\s+)?)(?P<title>(?:[A-Z][\w'’&.\-]*\s+|of\s+|and\s+|the\s+|for\s+|"
    r"\([A-Za-z.]+\)\s+){0,12}?(?:Act|Ordinance|Order|Code|Rules|Regulations)\b(?:\s*,?\s*(?:19|20)\d{2})?)")
# "of the Act", "of the said Ordinance", "of the Code ibid", "of the Act of 1997" -- but not
# "of the Code of Criminal Procedure", which is a title.
ANAPHORA = re.compile(r"(?i)^[\s,]*of\s+(?:the\s+|this\s+)?(?:said\s+|ibid\.?\s+|same\s+|above\s+)?"
                      r"(?P<noun>Act|Ordinance|Code|Order|Rules|Regulations|law|statute)\b"
                      r"(?!\s+of\s+(?!(?:19|20)\d{2}))(?!\s*,?\s*(?:19|20)\d{2})")
# Something is named after an Article that is none of the instruments we know.
OF_SOMETHING = re.compile(r"(?i)^[\s,]*(?:of|in)\s+(?:the\s+|this\s+)?[\"'“”‘’]?\s*[A-Za-z]")
# A bare Article in a sentence about Indian law is the Indian Constitution's ("Article 21 ... (2014) 3 SCC 1").
FOREIGN_SENTENCE = re.compile(r"(?i)\bS\.?C\.?C\b|\bAIR\s+\d|\bIndian\b|\bIndia\b")
# Provisions that cannot be charged: procedure, evidence and the Constitution.
NOT_PENAL = ("4_code_of_criminal_procedure", "6_code_of_civil_procedure", "945_qanun_e_shahadat",
             "1_constitution_of_the_islamic_republic")
ORDER_RULES = re.compile(r"(?i)^\s*,?\s*rules?\s*(" + NUM + r"(?:\s*(?:,|&|and|to)\s*" + NUM + r")*)")
# The statute named just *before* the number: "PPC section 302", a headnote's
# "Limitation Act (IX of 1908)--- Ss. 5 & 29(2)", "Pakistan Penal Code (Act XLV
# of 1860), namely:- Sections 201, ...".
BEFORE_JOIN = r"(?:\s*\([^)]{1,40}\))?[\s,:;\-\u2013\u2014]*(?:namely)?[\s,:;\-\u2013\u2014]*$"
ABBREVIATION_BEFORE = re.compile(
    r"(?i)(?<![A-Za-z])(?P<abbr>P\.?\s?P\.?\s?C|Cr\.?\s?P\.?\s?C|C\.?\s?P\.?\s?C|Q\.?\s?S\.?\s?O|CNSA)\.?" + BEFORE_JOIN)
TITLE_BEFORE = re.compile(
    r"(?P<title>(?:[A-Z][\w'’&.\-]*\s+|of\s+|and\s+|the\s+){1,10}(?:Act|Ordinance|Order|Code)\b"
    r"(?:\s*,?\s*(?:19|20)\d{2})?)" + BEFORE_JOIN)
QSO_IN_SENTENCE = re.compile(r"(?i)Qanun|Q\.\s?S\.\s?O\b|\bQSO\b")
# How far back a bare Article looks for the Qanun-e-Shahadat being discussed.
QSO_WINDOW = 800
# A statute title printed anywhere in prose: capitalised words ending in the kind of instrument,
# with its year if printed ("Punjab Rented Premises Act, 2009", "Land Acquisition Act (I of 1894)").
PROSE_TITLE = re.compile(
    r"(?P<title>(?:[A-Z][\w'’&.\-]*\s+(?:of\s+|and\s+|the\s+|for\s+)?){1,10}(?:Act|Ordinance|Order|Code)\b"
    r"(?:\s*,?\s*\(?\s*(?:[IVXLC]+\s+of\s+)?(?:19|20)\d{2}\)?)?)")
PROSE_CODE = re.compile(r"(?P<title>Code\s+of\s+(?:Civil|Criminal)\s+Procedure(?:\s*,?\s*(?:18|19)\d{2})?)")
# Words every title has; a title made only of these and a year names no statute.
UNDISTINCTIVE = frozenset({"the", "and", "for", "act", "ordinance", "order", "code", "rules", "regulations",
                           "west", "pakistan", "punjab", "federal", "provincial", "amendment", "said", "this"})


# Role cues, checked in this order on the mention's sentence.
ROLES = (
    ("vires_challenged", re.compile(r"(?i)\b(?:ultra\s+vires|unconstitutional|struck\s+down|strike\s+(?:it\s+)?down|"
                               r"declared\s+(?:to\s+be\s+)?void|repugnant\s+to\s+the\s+(?:constitution|injunctions))")),
    ("interpreted", re.compile(r"(?i)\b(?:interpret\w*|constru\w+|scope\s+of|meaning\s+of|purview\s+of|ambit\s+of|"
                               r"mandate\s+of|requirements?\s+of|conditions?\s+(?:laid\s+down\s+)?(?:in|of|under)|"
                               r"(?:bare|plain|conjoint)\s+reading|proviso\s+to|attracted|applicab\w+\s+of|"
                               r"does\s+not\s+apply|not\s+applicable)")),
    ("invoked", re.compile(r"(?i)\b(?:(?:petition|application|appeal|revision|suit|reference|complaint)\s+"
                           r"(?:was\s+|is\s+|has\s+been\s+)?(?:filed\s+|moved\s+|instituted\s+|preferred\s+)?under|"
                           r"in\s+exercise\s+of\s+(?:its\s+|the\s+|our\s+)?(?:\w+\s+)?(?:jurisdiction|powers?)\s+under|"
                           r"invok\w+|jurisdiction\s+under|constitutional\s+petition)")),
    ("charged_under", re.compile(r"(?i)\b(?:F\.?\s*I\.?\s*R|registered\s+under|offen[cs]es?\s+(?:punishable\s+)?under|"
                                 r"charged\s+under|convicted\s+under|sentenced\s+under|booked\s+under|"
                                 r"punishable\s+under|u\s*/\s*s)")),
)


def section_index(sections_dir: Path, *more: Path) -> dict[str, Any]:
    """Statutes the section corpora hold: abbreviations, section numbers, and the Constitution's stem.

    The first directory is the federal code; any others (the Punjab Code) add
    their statutes' sections and kinds.  Abbreviations come from the federal
    code only: "PPC" in a Lahore judgment is the Pakistan Penal Code.
    """
    by_short: dict[str, list[str]] = defaultdict(list)
    sections: dict[str, set[str]] = {}
    kinds: dict[str, str | None] = {}
    constitution = None
    paths = sorted(sections_dir.glob("*.json"))
    extra = [path for directory in more for path in sorted(directory.glob("*.json"))]
    for path in paths + extra:
        record = json.loads(path.read_text(encoding="utf-8"))
        stem = path.stem
        sections[stem] = {s["number"] for s in record.get("sections", [])}
        kinds[stem] = instrument(record.get("act"))
        if record.get("act_short") and path in paths:
            by_short[record["act_short"].upper()].append(stem)
        if stem.startswith("1_constitution_of_the_islamic_republic"):
            constitution = stem
    # An abbreviation two statutes share names neither.
    short = {abbr: stems[0] for abbr, stems in by_short.items() if len(stems) == 1}
    return {"short": short, "sections": sections, "kinds": kinds, "constitution": constitution}


def _abbr_key(value: str) -> str:
    key = re.sub(r"[^A-Za-z]", "", value).upper()
    if key.startswith("QANUN") or key in {"QS", "QSO"}:
        return "QSO"
    return key


def _numbers(raw: str, kind: str = "section") -> list[tuple[str, str]]:
    """Each provision number in a list, as (section key, pinpoint): "337-F(i)" -> ("337F", "(i)")."""
    found = []
    for match in NUM_RE.finditer(raw):
        token = re.sub(r"\s+", "", match.group(0))
        if not token:
            continue
        base = re.match(r"^(\d{1,4}(?:-?[A-Z]{1,2})?|[IVXL]+)", token)
        if not base:
            continue
        key = base.group(1).replace("-", "")
        roman = not key[0].isdigit()
        if roman != (kind == "order"):
            continue
        if not roman and int(re.match(r"\d+", key).group(0)) > MAX_NUMBER:
            continue
        found.append((key, token[len(base.group(1)):]))
    return found


def _kind(printed: str) -> str:
    word = re.sub(r"\s+", "", printed.lower())
    if word.startswith("art"):
        return "article"
    if word.startswith("order"):
        return "order"
    if word.startswith("rule"):
        return "rule"
    return "section"


def _role(sentence: str, statute: str | None = None) -> str:
    role = next((name for name, pattern in ROLES if pattern.search(sentence)), "mentioned")
    if role == "vires_challenged" and statute and statute.startswith(NOT_PENAL[-1]):
        return "benchmark"
    if role == "charged_under" and statute and statute.startswith(NOT_PENAL):
        return "mentioned"
    return role


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start), start - 300)
    right_candidates = [p for p in (text.find(". ", end), text.find("; ", end)) if p != -1]
    right = min(right_candidates + [end + 300])
    return text[max(0, left + 1):right].strip()


def instrument(name: str | None) -> str | None:
    """What kind of instrument a title names: "act", "ordinance", "code", "order", "rules", "constitution".

    "Of the Act" can only mean an Act, so a reference back is resolved against
    the last statute of that kind, not the last statute of any kind.  A title
    ends with its kind ("... Ordinance, 1962"), except a Code, which leads with
    it ("Code of Criminal Procedure").
    """
    if not name:
        return None
    if re.match(r"(?i)^\s*(?:the\s+)?code\b", name):
        return "code"
    kinds = re.findall(r"(?i)\b(act|ordinance|code|order|rules|regulations|constitution)\b", name)
    return kinds[-1].lower() if kinds else None


def distinctive(title: str) -> bool:
    """Does a title name a statute, or only a kind and a year?  "Ordinance 1984" names nothing."""
    words = re.findall(r"[A-Za-z]{3,}", title.lower())
    return any(word not in UNDISTINCTIVE and not re.fullmatch(r"[ivxlc]+", word) for word in words)


class _Titles:
    """Statute titles resolved once per judgment: a title printed fifty times is looked up once.

    Two corpora answer a title.  A name that states Punjab is looked up in the
    Punjab Code only.  A name that states no province is looked up in the
    federal code first; only if nothing there matches is the Punjab Code tried,
    and the link is marked ``jurisdiction_inferred``.  A name stating any other
    province is not held.
    """

    def __init__(self, titles: list[Any], index: dict[str, Any], punjab: list[Any] | None = None) -> None:
        self.titles, self.index, self.punjab, self.cache = titles, index, punjab or [], {}

    def _link(self, name: str) -> dict[str, Any]:
        stated = statute_links.jurisdiction(name)
        if stated == "Punjab" and self.punjab:
            finding = statute_links.link(self.punjab, name)
            if not finding.get("matched"):
                finding["reason"] = "not_found_in_punjab_code"
            return finding
        finding = statute_links.link(self.titles, name)
        # A bare "Finance Act" is almost always the federal one, which the code does not hold.
        if (not finding.get("matched") and stated == statute_links.FEDERAL and self.punjab
                and not re.search(r"\bfinance\b", name, re.I)):
            punjab = statute_links.link(self.punjab, name)
            if punjab.get("matched"):
                return {**punjab, "jurisdiction": "Punjab", "jurisdiction_inferred": True}
        return finding

    def resolve(self, name: str) -> tuple[str | None, dict[str, Any], str | None]:
        """(statute stem or None, the finding, kind of instrument) for a printed title."""
        key = " ".join(name.split()).lower()
        if key not in self.cache:
            if not distinctive(name):
                finding = {"name": name, "reason": "title_names_no_statute"}
                self.cache[key] = (None, finding, instrument(name))
            else:
                finding = self._link(name)
                statute = Path(finding["statute_id"]).stem if finding.get("matched") else None
                self.cache[key] = (statute, finding, self.index["kinds"].get(statute or "") or instrument(name))
        return self.cache[key]


def named_in_prose(text: str, resolver: _Titles) -> list[tuple[int, str, str | None, dict[str, Any]]]:
    """Every statute title the judgment prints, anywhere: (position, kind, stem, finding), in order.

    A judgment names its statute in prose -- "the Punjab Rented Premises Act,
    2009 (the Act)" -- and then cites "section 12 of the Act".  The reference
    back resolves against these, not only against titles printed beside a
    section number.
    """
    named = []
    for pattern in (PROSE_TITLE, PROSE_CODE):
        for match in pattern.finditer(text):
            statute, finding, kind = resolver.resolve(match.group("title"))
            if kind:
                named.append((match.start(), kind, statute, finding))
    return sorted(named, key=lambda item: item[0])


def punjab_titles(catalogue_rows: list[dict[str, Any]]) -> list[Any]:
    """The Punjab Code as title-matcher entries.  Jurisdiction is Punjab whatever the title says, and each
    title is also searched with "Punjab" in front, since the catalogue often omits it
    ("ADMINISTRATOR GENERAL'S ACT, 1913")."""
    entries = []
    for row in catalogue_rows:
        words = statute_links._words(row["title"])
        prefixed = words if "punjab" in words else ("punjab",) + words
        entries.append(statute_links.Statute(
            statute_id=row["statute_id"], title=row["title"], year=row["year"], jurisdiction="Punjab",
            category=row.get("category") or "", path=Path(row["pdf"]), haystacks=(words, prefixed)))
    return entries


def mentions(text: str, index: dict[str, Any], titles: list[Any],
             punjab: list[Any] | None = None) -> list[dict[str, Any]]:
    """Every provision mention in a flattened judgment, resolved as far as the corpus allows."""
    resolver = _Titles(titles, index, punjab)
    prose = named_in_prose(text, resolver)
    prose_starts = [position for position, *_ in prose]
    found: list[dict[str, Any]] = []
    # Statutes named beside a section number, per kind of instrument, with where they were named.
    beside: dict[str, tuple[int, str | None, dict[str, Any] | None]] = {}
    consumed = 0
    for match in ANCHOR.finditer(text):
        if match.start() < consumed:
            continue
        kind = _kind(match.group("kind"))
        numbers = _numbers(match.group("nums"), kind)
        if not numbers:
            continue
        tail = text[match.end():match.end() + TAIL]
        pinpoint_rules = ""
        if kind == "order":
            rules = ORDER_RULES.match(tail)
            if rules:
                pinpoint_rules = "R." + re.sub(r"\s*(?:,|&|and)\s*", ",", rules.group(1).strip())
                consumed = match.end() + rules.end()
                tail = tail[rules.end():]
        statute, method, finding, named_kind = None, "unstated", None, None
        abbr = ABBREVIATION.match(tail)
        anaphora = ANAPHORA.match(tail)
        if abbr:
            statute, method = index["short"].get(_abbr_key(abbr.group("abbr"))), "abbreviation"
            if statute is None:
                finding = {"name": abbr.group("abbr"), "reason": "abbreviation_not_in_index"}
            named_kind = index["kinds"].get(statute or "")
        elif CONSTITUTION.match(tail):
            statute, method, named_kind = index["constitution"], "constitution", "constitution"
        elif anaphora:
            noun = anaphora.group("noun").lower()
            method = "reference_back"
            if noun in {"act", "ordinance", "code", "order"}:
                # The latest same-kind statute named before this point, beside a number or in prose.
                candidates = [beside[noun]] if noun in beside else []
                cut = bisect_left(prose_starts, match.start())
                for position, prose_kind, stem, found_ in reversed(prose[:cut]):
                    if prose_kind == noun:
                        candidates.append((position, stem, found_))
                        break
                if candidates:
                    _, statute, earlier = max(candidates, key=lambda item: item[0])
                    finding = None if statute else {**(earlier or {}), "reason": "refers_back_to_unheld_statute"}
                else:
                    finding = {"reason": "refers_back_to_nothing_named"}
            else:
                # "of the Rules", "of this law": no statute in the code is named by that.
                finding = {"reason": "refers_back_to_rules_or_unnamed_law"}
        else:
            title = TITLE.match(tail)
            head = text[max(0, match.start() - HEAD):match.start()]
            before_abbr = ABBREVIATION_BEFORE.search(head) if not title else None
            before_title = TITLE_BEFORE.search(head) if not (title or before_abbr) else None
            if title or before_title:
                statute, finding, named_kind = resolver.resolve((title or before_title).group("title"))
                method = "title" if title else "named_before"
            elif before_abbr:
                statute, method = index["short"].get(_abbr_key(before_abbr.group("abbr"))), "named_before"
                named_kind = index["kinds"].get(statute or "")
            elif kind == "article":
                if OF_SOMETHING.match(tail) or FOREIGN_SENTENCE.search(_sentence(text, match.start(), match.end())):
                    # "Article 2(ii) of the PCB Constitution", or an Indian article: named, but not by
                    # anything we hold.
                    finding = {"reason": "article_of_an_unrecognised_instrument"}
                else:
                    # A bare "Article 124" is the Constitution's, unless the passage around it is
                    # about the Qanun-e-Shahadat, whose provisions are Articles too.
                    passage = text[max(0, match.start() - QSO_WINDOW):match.end() + QSO_WINDOW // 4]
                    passage_qso = QSO_IN_SENTENCE.search(passage)
                    statute = index["short"].get("QSO") if passage_qso else index["constitution"]
                    method = "article_default_qso" if passage_qso else "article_default"
        if named_kind and method in {"abbreviation", "constitution", "title", "named_before"}:
            beside[named_kind] = (match.start(), statute, finding)
        sentence = _sentence(text, match.start(), match.end())
        role = _role(sentence, statute)
        for key, pinpoint in numbers:
            if kind == "order":
                key, pinpoint = f"O.{key}", pinpoint_rules
            held_sections = index["sections"].get(statute or "", set())
            section_found, clause_reading = None, None
            if statute and kind in {"section", "article"}:
                section_found = key in held_sections
                if not section_found and re.match(r"^\d+[A-Z]$", key) and key[:-1] in held_sections:
                    # "9-C" may be clause 9(c) misprinted, or a real section 9-C the index lacks
                    # ("382-B Cr.P.C." is real).  The printed section is kept; the clause reading
                    # is offered, not taken.
                    clause_reading = f"{key[:-1]}({key[-1].lower()})"
            found.append({
                "kind": kind, "statute_id": statute, "section": key, "pinpoint": pinpoint or None,
                "method": method, "section_found": section_found, "clause_reading": clause_reading, "role": role,
                "not_held": None if statute else (finding or {"reason": "statute_unstated"}),
                "jurisdiction_inferred": bool(statute and finding and finding.get("jurisdiction_inferred")),
                "start": match.start(), "end": match.end(), "quote": " ".join(text[match.start():match.end() + 40].split()),
                "sentence": sentence[:400],
            })
    return found


def read(document: ParsedDocument, index: dict[str, Any], titles: list[Any],
         punjab: list[Any] | None = None) -> dict[str, Any]:
    """The provision mentions of one judgment, each with its page and box.  Pure."""
    text = flatten(document.payload(), document.stem, document.corpus)
    rows = []
    for row in mentions(text.text, index, titles, punjab):
        where = text.provenance(row["start"], row["end"])
        rows.append({**row, "page": where["page_start"], "bboxes": where["bboxes"], "block_ids": where["block_ids"]})
    return {"corpus": document.corpus, "stem": document.stem, "mentions": rows}


ROLE_PRIORITY = ("vires_challenged", "interpreted", "invoked", "charged_under", "benchmark", "mentioned")


def build(reads: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """``CONCERNS`` edges, one per judgment and provision, with every mention as evidence."""
    stem_to_doc = {(d["corpus"], stem): d for d in documents for stem in d["stems"]}
    edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    methods, roles, unheld = Counter(), Counter(), Counter()
    total = resolved = section_found = 0
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc is None:
            continue
        for mention in item["mentions"]:
            total += 1
            methods[mention["method"]] += 1
            roles[mention["role"]] += 1
            if not mention["statute_id"]:
                reason = (mention["not_held"] or {}).get("reason") or "unresolved"
                where = (mention["not_held"] or {}).get("jurisdiction")
                unheld[f"{reason}:{where}" if where and reason == "not_held_for_this_jurisdiction" else reason] += 1
                continue
            resolved += 1
            section_found += bool(mention["section_found"])
            key = (doc["judgment_id"], mention["statute_id"], mention["kind"], mention["section"])
            evidence = Evidence(document_id=doc["document_id"], page=mention["page"], bboxes=mention["bboxes"],
                                block_ids=mention["block_ids"], quote=mention["quote"])
            edge = edges.get(key)
            if edge is None:
                edge = edges[key] = {
                    "judgment_id": doc["judgment_id"], "statute_id": mention["statute_id"], "kind": mention["kind"],
                    "section": mention["section"], "section_found": mention["section_found"],
                    "pinpoints": [], "roles": Counter(), "methods": Counter(),
                    **Fact(value=f"{mention['statute_id']}:{mention['section']}", method="deterministic",
                           extractor=EXTRACTOR, extractor_version=VERSION, confidence=1.0, evidence=[]).model_dump(),
                }
            edge["evidence"].append(evidence.model_dump())
            edge["roles"][mention["role"]] += 1
            edge["methods"][mention["method"]] += 1
            if mention["pinpoint"] and mention["pinpoint"] not in edge["pinpoints"]:
                edge["pinpoints"].append(mention["pinpoint"])
    rows = []
    for edge in edges.values():
        edge["role"] = next(role for role in ROLE_PRIORITY if edge["roles"].get(role))
        # Reference-back alone is weaker than a statute named at least once in the judgment.
        if set(edge["methods"]) <= {"reference_back", "article_default", "article_default_qso"}:
            edge["confidence"] = 0.7
        edge["roles"], edge["methods"] = dict(edge["roles"]), dict(edge["methods"])
        rows.append(edge)
    rows.sort(key=lambda e: (e["judgment_id"], e["statute_id"], e["kind"], e["section"]))
    statutes = Counter(e["statute_id"] for e in rows)
    provisions = Counter(f"{e['statute_id']}:{e['section']}" for e in rows)
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "judgments_read": len(reads),
        "mentions": total,
        "mentions_resolved_to_a_held_statute": resolved,
        "mentions_resolved_to_a_held_section": section_found,
        "by_method": dict(sorted(methods.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unresolved": dict(sorted(unheld.items(), key=lambda kv: (-kv[1], kv[0]))),
        "mention_roles": dict(sorted(roles.items(), key=lambda kv: (-kv[1], kv[0]))),
        "concerns_edges": len(rows),
        "edge_roles": dict(sorted(Counter(e["role"] for e in rows).items(), key=lambda kv: (-kv[1], kv[0]))),
        "judgments_with_an_edge": len({e["judgment_id"] for e in rows}),
        "top_statutes": dict(statutes.most_common(15)),
        "top_provisions": dict(provisions.most_common(25)),
    }
    return {"edges": rows, "report": report}
