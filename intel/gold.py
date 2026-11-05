"""A labelled sample of citation edges, and precision and recall estimated from it (ADR 0002, Decision 9).

The unit is a ``CITES`` edge to a Pakistani reporter: one citing judgment, one
cited alias.  Edges are sampled by **stratum**, because the strata differ by
two orders of magnitude (about 2,300 resolved against 37,000 unresolved) and a
uniform sample would contain almost no resolved edges:

    resolved_alias, resolved_parallel, resolved_case_name,
    unresolved_named   -- unresolved, but a case name is printed beside it
    unresolved_unnamed -- unresolved, nothing printed to search by

Each sampled edge is shown with the case name the judgment printed, the
current link, and the closest held judgments from a wider, threshold-free
search.  A labeller writes one of:

    correct          -- the current link is the cited case
    wrong            -- the current link is not the cited case
    candidate:<id>   -- the cited case is this held judgment
    not_held         -- the cited case is not among the held judgments
    unsure

Precision is the share of resolved edges labelled ``correct``.  Recall is the
share of edges whose cited case *is* held that the resolver linked correctly.
Both are weighted by stratum size, so they estimate the population, not the
sample.  ``unresolved_unnamed`` has nothing to search by, so a labeller cannot
tell "held" from "not held" there.  It is reported as unmeasured, and the
recall figure is stated as recall over edges that print a case name.
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from intel import linkage
from intel.resolve import NEUTRAL_REPORTERS, _year

STRATA = ("resolved_alias", "resolved_parallel", "resolved_case_name", "unresolved_named", "unresolved_unnamed")
LABELS = ("correct", "wrong", "not_held", "unsure")
SEED = 20261006


def stratum(edge: dict[str, Any]) -> str:
    if edge.get("to_judgment"):
        return f"resolved_{edge['rung']}"
    return "unresolved_named" if edge.get("printed_name") else "unresolved_unnamed"


def population(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["jurisdiction"] == "PK" and e["reporter"] not in NEUTRAL_REPORTERS]


def sample(edges: list[dict[str, Any]], sizes: dict[str, int], judgments: list[dict[str, Any]],
           reads: list[dict[str, Any]], documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """A stratified sample of edges, each with what a labeller needs to decide it."""
    pool = population(edges)
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in pool:
        by_stratum[stratum(edge)].append(edge)
    sizes_seen = {name: len(by_stratum[name]) for name in STRATA}

    stem_to_doc = {(d["corpus"], stem): d for d in documents for stem in d["stems"]}
    parties = {}
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc:
            parties.setdefault(doc["judgment_id"], (item.get("petitioner"), item.get("respondent")))
    by_id = {j["judgment_id"]: j for j in judgments}
    index: dict[tuple[str, int], list[linkage.Candidate]] = defaultdict(list)
    for judgment in judgments:
        year = _year(judgment)
        petitioner, respondent = parties.get(judgment["judgment_id"], (None, None))
        if year is not None and petitioner and respondent:
            index[(judgment["court_id"], year)].append(linkage.Candidate(
                judgment["judgment_id"], judgment["court_id"], year,
                linkage.normalise_party(petitioner), linkage.normalise_party(respondent)))

    rng = random.Random(SEED)
    rows = []
    for name in STRATA:
        chosen = sorted(by_stratum[name], key=lambda e: (e["from_judgment"], e["alias"]))
        rng.shuffle(chosen)
        for edge in chosen[:sizes.get(name, 0)]:
            printed = edge.get("printed_name") or {}
            target = by_id.get(edge.get("to_judgment") or "")
            mention = (edge.get("evidence") or [{}])[0]
            rows.append({
                "sample_id": f"{name}:{len(rows):04d}",
                "stratum": name,
                "citing_judgment": by_id[edge["from_judgment"]]["key"],
                "alias": edge["alias"],
                "printed_petitioner": printed.get("petitioner"),
                "printed_respondent": printed.get("respondent"),
                "linked_judgment_id": edge.get("to_judgment"),
                "linked_key": target["key"] if target else None,
                "linked_parties": " v ".join(p or "" for p in parties.get(edge.get("to_judgment") or "", ())) or None,
                "rung": edge.get("rung"),
                "candidates": linkage.candidates(printed, edge["reporter"], edge.get("court"), edge.get("year"), index)
                if printed else [],
                "citing_page": mention.get("page"),
                "label": "",
                "labelled_by": "",
                "notes": "",
            })
    for row in rows:
        for candidate in row["candidates"]:
            candidate["key"] = by_id[candidate["judgment_id"]]["key"]
    return rows, sizes_seen


def write_sheet(path: Path, rows: list[dict[str, Any]]) -> None:
    """The sample as a spreadsheet a lawyer can label: one row per edge, candidates spelled out."""
    columns = ["sample_id", "stratum", "citing_judgment", "alias", "printed_petitioner", "printed_respondent",
               "linked_key", "linked_parties", "candidate_1", "candidate_2", "candidate_3", "candidate_4",
               "candidate_5", "label", "labelled_by", "notes"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = {key: row.get(key) for key in columns}
            for index, candidate in enumerate(row["candidates"][:5], start=1):
                flat[f"candidate_{index}"] = (f"{candidate['judgment_id']} | {candidate['key']} | "
                                              f"{candidate['petitioner']} v {candidate['respondent']} | {candidate['score']}")
            writer.writerow(flat)


def read_labels(path: Path) -> dict[str, dict[str, str]]:
    """Labels from a labelled sheet (CSV) or a JSON {sample_id: {label, labelled_by, notes}}."""
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["sample_id"]: row for row in csv.DictReader(handle) if row.get("label")}


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """The 95% Wilson interval for a proportion; honest at 0 and n, where the normal interval is not."""
    if n == 0:
        return None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def evaluate(rows: list[dict[str, Any]], labels: dict[str, dict[str, str]], sizes: dict[str, int]) -> dict[str, Any]:
    """Weighted precision and recall from a labelled sample."""
    labelled = [(row, labels[row["sample_id"]]["label"].strip()) for row in rows
                if labels.get(row["sample_id"], {}).get("label", "").strip()]
    # "unsure" is a missing label, not an answer: it is left out of both counts and weights.
    labelled = [(row, label) for row, label in labelled if label != "unsure"]
    sampled = Counter(row["stratum"] for row, _ in labelled)
    weight = {name: sizes[name] / sampled[name] for name in sampled}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row, label in labelled:
        kind = "candidate" if label.startswith("candidate:") else label
        if kind == "candidate" and row["linked_judgment_id"] == label.split(":", 1)[1]:
            kind = "correct"
        counts[row["stratum"]][kind] += 1

    def total(kinds: tuple[str, ...], strata: tuple[str, ...]) -> float:
        return sum(weight[s] * counts[s][k] for s in strata if s in weight for k in kinds)

    resolved = tuple(s for s in STRATA if s.startswith("resolved_"))
    true_positive = total(("correct",), resolved)
    # A link to a case that is not held at all is as wrong as a link to the wrong held one.
    false_positive = total(("wrong", "candidate", "not_held"), resolved)
    # A held case the resolver missed: an unresolved edge labelled with a candidate, or
    # a resolved edge linked to the wrong judgment when the right one is held.
    missed = total(("candidate",), ("unresolved_named",)) + total(("candidate",), resolved)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else None
    recall = true_positive / (true_positive + missed) if true_positive + missed else None
    resolved_n = sum(sampled[s] for s in resolved)
    resolved_ok = sum(counts[s]["correct"] for s in resolved)
    named_n = sampled.get("unresolved_named", 0)
    named_missed = counts["unresolved_named"]["candidate"]
    precision_ci = _wilson(resolved_ok, resolved_n)
    missed_ci = _wilson(named_missed, named_n)
    population_named = sizes.get("unresolved_named", 0)
    recall_ci = None
    if precision_ci and missed_ci:
        # Pessimistic recall uses the most links missed and fewest right; optimistic the reverse.
        low = true_positive * precision_ci[0] / (true_positive * precision_ci[0] + missed_ci[1] * population_named)
        high = true_positive / (true_positive + missed_ci[0] * population_named)
        recall_ci = [round(low, 4), round(high, 4)]
    return {
        "labelled": len(labelled),
        "precision_95ci": [round(v, 4) for v in precision_ci] if precision_ci else None,
        "recall_95ci": recall_ci,
        "by_stratum": {s: dict(sorted(counts[s].items())) for s in STRATA if counts[s]},
        "population": sizes,
        "precision": round(precision, 4) if precision is not None else None,
        "recall_over_named_edges": round(recall, 4) if recall is not None else None,
        "estimated_edges": {"correct_links": round(true_positive), "wrong_links": round(false_positive),
                            "held_but_missed": round(missed)},
        "unmeasured": {"unresolved_unnamed": sizes.get("unresolved_unnamed", 0)},
        "labelled_by": dict(Counter(labels[row["sample_id"]].get("labelled_by") or "?" for row, _ in labelled)),
    }
