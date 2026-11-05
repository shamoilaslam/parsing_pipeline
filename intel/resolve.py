"""LI-2: resolve citations to the judgments we hold.

Every citation a judgment prints becomes a ``CITES`` edge from the citing
judgment to the cited alias.  An edge resolves through a fixed ladder, and
records which rung resolved it:

1. ``alias`` -- the cited alias is held: a neutral citation, or the header of
   a held reporter print.
2. ``parallel`` -- the alias was printed as the same case as another alias
   ("1993 PTD 1108 = 1993 SCMR 1108") and that one resolves.  Parallel groups
   are merged across the whole corpus with union-find.
3. ``case_name`` -- the "X v. Y" printed before the citation matches the
   parties of exactly one held judgment the reporter and year allow
   (``intel.linkage``).

A group whose members resolve to different judgments is marked ambiguous and
resolved to none.  The edges are written whether or not they resolve: "which
of our judgments rely on 2008 SCMR 598" is answerable from the citing side
alone.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from intel import linkage
from intel.context import case_name, citation_spans, parallel_groups
from intel.corpus import ParsedDocument
from intel.evidence import Evidence, Fact

EXTRACTOR = "intel.resolve"
VERSION = "4"
NEUTRAL_REPORTERS = {"LHC", "PHC"}


def read(document: ParsedDocument) -> dict[str, Any]:
    """Citations with their printed case names and parallel groups, plus this document's own parties.  Pure."""
    record = document.metadata()
    payload = document.payload()
    names: dict[str, dict[str, str]] = {}
    groups: list[list[str]] = []
    for page in payload.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") in {"header", "footer"}:
                continue
            text = " ".join((block.get("text") or "").split())
            if not text:
                continue
            spans = citation_spans(text)
            if not spans:
                continue
            block_groups = parallel_groups(text)
            groups.extend(block_groups)
            previous_end = 0
            for start, end, citation in spans:
                found = case_name(text, start, previous_end)
                previous_end = end
                if found and citation["id"] not in names:
                    names[citation["id"]] = {**found, "quote": text[max(0, start - 200):end + 1].strip()}
            # The second member of "A = B" shares A's printed name.
            for group in block_groups:
                shared = next((names[i] for i in group if i in names), None)
                if shared:
                    for member in group:
                        names.setdefault(member, shared)
    metadata = record.get("metadata") or {}
    return {"corpus": document.corpus, "stem": document.stem, "citations": record.get("citations") or [],
            "names": names, "parallel": groups,
            "petitioner": metadata.get("petitioner"), "respondent": metadata.get("respondent")}


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def _year(judgment: dict[str, Any]) -> int | None:
    match = re.search(r"neutral/(\d{4})", judgment["key"]) or re.search(r"/(\d{4})-\d\d-\d\d$", judgment["key"])
    if match:
        return int(match.group(1))
    decided = judgment.get("decision_date") or ""
    return int(decided[:4]) if decided[:4].isdigit() else None


def build(reads: list[dict[str, Any]], documents: list[dict[str, Any]], aliases: list[dict[str, Any]],
          judgments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    stem_to_doc = {(d["corpus"], stem): d for d in documents for stem in d["stems"]}
    held: dict[str, set[str]] = defaultdict(set)
    for row in aliases:
        if row["alias_type"] in {"neutral", "reported"}:
            held[row["alias"]].add(row["judgment_id"])

    # Candidates for case-name linkage: every held judgment, by court and decision year.
    parties = {}
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc and doc["judgment_id"] not in parties:
            parties[doc["judgment_id"]] = (item.get("petitioner"), item.get("respondent"))
    by_court_year: dict[tuple[str, int], list[linkage.Candidate]] = defaultdict(list)
    for judgment in judgments or []:
        year = _year(judgment)
        petitioner, respondent = parties.get(judgment["judgment_id"], (None, None))
        if year is None or not (petitioner and respondent):
            continue
        by_court_year[(judgment["court_id"], year)].append(linkage.Candidate(
            judgment["judgment_id"], judgment["court_id"], year,
            linkage.normalise_party(petitioner), linkage.normalise_party(respondent)))

    union = _UnionFind()
    for item in reads:
        for group in item["parallel"]:
            for other in group[1:]:
                union.union(group[0], other)

    # Rung 3 runs once per (alias, printed name); a name printed in several judgments links once.
    linked: dict[str, dict[str, Any]] = {}
    # Every judgment any printed name for an alias linked to; more than one is a conflict.
    linked_to: dict[str, set[str]] = defaultdict(set)
    attempts: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc is None:
            continue
        citing = doc["judgment_id"]
        for citation in item["citations"]:
            alias = citation["id"]
            mentions = [Evidence(document_id=doc["document_id"], page=m.get("page"),
                                 bboxes=[m["bbox"]] if m.get("bbox") else [],
                                 block_ids=[m["block_id"]] if m.get("block_id") else [],
                                 quote=m.get("text")) for m in citation.get("mentions") or []]
            key = (citing, alias)
            if key in edges:
                edges[key]["evidence"].extend(e.model_dump() for e in mentions)
                continue
            name = item["names"].get(alias)
            if (name and citation.get("jurisdiction") == "PK"
                    and citation.get("reporter") not in NEUTRAL_REPORTERS and not held.get(alias)):
                tried = (alias, linkage.normalise_party(name["petitioner"]), linkage.normalise_party(name["respondent"]))
                if tried not in attempts:
                    attempts[tried] = linkage.link(name, citation.get("reporter"), citation.get("court"),
                                                   citation.get("year"), by_court_year)
                    if attempts[tried]:
                        linked_to[alias].add(attempts[tried]["judgment_id"])
                        linked.setdefault(alias, {**attempts[tried], "printed": {k: name[k] for k in ("petitioner", "respondent")},
                                                  "quote": name["quote"], "document_id": doc["document_id"]})
            edges[key] = {
                "from_judgment": citing, "alias": alias, "reporter": citation.get("reporter"),
                "jurisdiction": citation.get("jurisdiction"), "year": citation.get("year"),
                "printed_name": {k: name[k] for k in ("petitioner", "respondent")} if name else None,
                **Fact(value=alias, method="deterministic", extractor=EXTRACTOR, extractor_version=VERSION,
                       confidence=1.0, evidence=mentions).model_dump(),
            }

    # Resolve every alias once, through the ladder, then stamp the edges.
    resolution: dict[str, dict[str, Any]] = {}
    members: dict[str, set[str]] = defaultdict(set)
    # A group's members include partners no edge cites directly: they can still be the held alias.
    for alias in {alias for _, alias in edges} | set(union.parent):
        members[union.find(alias)].add(alias)
    for root, group in members.items():
        by_rung: dict[str, set[str]] = defaultdict(set)
        for alias in group:
            if held.get(alias):
                by_rung["alias"] |= held[alias]
        if not by_rung:
            for alias in group:
                if alias in linked:
                    by_rung["case_name"] |= linked_to[alias]
        targets = next(iter(by_rung.values()), set())
        rung = next(iter(by_rung), None)
        for alias in group:
            if len(targets) > 1:
                resolution[alias] = {"to_judgment": None, "ambiguous": sorted(targets), "rung": None}
            elif targets:
                own = (rung if (rung == "alias" and held.get(alias)) or (rung == "case_name" and alias in linked)
                       else "parallel")
                resolution[alias] = {"to_judgment": next(iter(targets)), "ambiguous": [], "rung": own,
                                     "linkage": linked.get(alias) if own == "case_name" else None,
                                     "parallel_group": sorted(group) if len(group) > 1 else []}
            else:
                resolution[alias] = {"to_judgment": None, "ambiguous": [], "rung": None,
                                     "parallel_group": sorted(group) if len(group) > 1 else []}

    rows = []
    for (citing, alias), edge in sorted(edges.items()):
        result = dict(resolution[alias])
        if result["to_judgment"] == citing:
            result = {"to_judgment": None, "ambiguous": [], "rung": None, "self_citation": True}
        row = {**edge, **result}
        if result.get("rung") == "case_name":
            row["method"], row["confidence"] = "linkage", round(result["linkage"]["score"] / 100, 3)
        rows.append(row)

    pk_reported = [e for e in rows if e["jurisdiction"] == "PK" and e["reporter"] not in NEUTRAL_REPORTERS]
    neutral = [e for e in rows if e["reporter"] in NEUTRAL_REPORTERS]
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "cites_edges": len(rows),
        "pakistani_reporter_edges": {"total": len(pk_reported),
                                     "resolved": sum(1 for e in pk_reported if e["to_judgment"])},
        "neutral_citation_edges": {"total": len(neutral), "resolved": sum(1 for e in neutral if e["to_judgment"])},
        "foreign_edges": sum(1 for e in rows if e["jurisdiction"] != "PK"),
        "resolved_by_rung": dict(sorted(Counter(e["rung"] for e in rows if e["to_judgment"]).items())),
        "distinct_aliases_resolved_by_rung": dict(sorted(Counter(
            r["rung"] for r in resolution.values() if r["to_judgment"]).items())),
        "edges_with_a_printed_case_name": sum(1 for e in pk_reported if e["printed_name"]),
        "parallel_groups": sum(1 for g in members.values() if len(g) > 1),
        "ambiguous_edges": sum(1 for e in rows if e.get("ambiguous")),
        "resolved_by_reporter": dict(sorted(Counter(e["reporter"] for e in rows if e["to_judgment"]).items(),
                                            key=lambda kv: (-kv[1], kv[0]))),
        "most_cited_unresolved": [
            {"alias": alias, "citing_judgments": n} for alias, n in
            Counter(e["alias"] for e in pk_reported if not e["to_judgment"]).most_common(25)],
    }
    return {"edges": rows, "report": report}
