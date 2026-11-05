"""LI-5, rules only: the citator.  Treatment on CITES edges, results on APPEAL_FROM edges, a status per judgment.

A judgment's status is computed from evidence it can show:

* ``negative``: a held judgment says it was overruled, is no longer good law,
  was per incuriam or was reversed, or the appellate judgment we hold set it
  aside.
* ``caution``: a held judgment doubted it; its appeal modified it or sent it
  back; or a provision it interpreted, or whose vires it decided, has been
  replaced or repealed since.
* ``positive``: a held judgment followed it, and nothing above applies.
* ``no_treatment_seen``: it was only referred to, distinguished, cited by
  counsel, or not cited at all.

Every status carries the edges it rests on and how many citing judgments were
examined.  ``no_treatment_seen`` is not "good law".  It means that the 9,029
judgments held say nothing against this one.

Precedent weight is recorded but does not enter the status: the court's
level (Articles 189 and 201 of the Constitution), the number of judges named
and the decision date.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from intel import treatment
from intel.evidence import Fact

EXTRACTOR = "intel.citator"
VERSION = "3"
COURT_LEVEL = {"supreme_court_pakistan": 2, "lahore_high_court": 1}
APPEAL_RESULT = {"allowed": "set_aside", "partly_allowed": "modified", "remanded": "remanded",
                 "dismissed": "upheld", "leave_refused": "leave_refused", "disposed_of": "disposed_of"}
CHANGED = {"replaced_since", "repealed"}
CONSTRUED = {"interpreted", "vires_challenged"}


def _with_document(evidence: dict[str, Any], document_id: str) -> dict[str, Any]:
    return {**evidence, "document_id": document_id}


def build(reads: list[dict[str, Any]], documents: list[dict[str, Any]], judgments: list[dict[str, Any]],
          cites: list[dict[str, Any]], appeal_edges: list[dict[str, Any]], concerns: list[dict[str, Any]],
          concerns_versions: list[dict[str, Any]]) -> dict[str, Any]:
    stem_to_doc = {(d["corpus"], stem): d for d in documents for stem in d["stems"]}
    by_judgment: dict[str, dict[str, Any]] = {}
    for item in reads:
        doc = stem_to_doc.get((item["corpus"], item["stem"]))
        if doc and doc["judgment_id"] not in by_judgment:
            by_judgment[doc["judgment_id"]] = {**item, "document_id": doc["document_id"]}

    # Treatment per (citing judgment, alias): the strongest reading of all the times it was cited.
    readings: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for judgment_id, item in by_judgment.items():
        for reading in item["readings"]:
            readings[(judgment_id, reading["alias"])].append(reading)
    rank = {label: i for i, label in enumerate(treatment.PRIORITY)}
    edges = []
    for edge in cites:
        found = readings.get((edge["from_judgment"], edge["alias"]), [])
        best = min(found, key=lambda r: rank[r["treatment"]]) if found else None
        document_id = by_judgment.get(edge["from_judgment"], {}).get("document_id", "")
        label = best["treatment"] if best else "not_read"
        evidence = [_with_document(r["evidence"], document_id) for r in found if r["treatment"] == label][:3]
        edges.append({"from_judgment": edge["from_judgment"], "alias": edge["alias"], "to_judgment": edge["to_judgment"],
                      "treatment": label, "cue": best["cue"] if best else None,
                      "readings": dict(Counter(r["treatment"] for r in found)),
                      **Fact(value=label, method="deterministic", extractor=treatment.EXTRACTOR,
                             extractor_version=treatment.VERSION, confidence=1.0 if found else 0.0,
                             evidence=evidence).model_dump()})

    appeals = []
    for edge in appeal_edges:
        if not edge["to_judgment"]:
            continue
        upper = by_judgment.get(edge["from_judgment"], {})
        found = upper.get("disposition")
        result = APPEAL_RESULT[found["outcome"]] if found else "unknown"
        appeals.append({"from_judgment": edge["from_judgment"], "to_judgment": edge["to_judgment"],
                        "lower_case": edge["lower_case"], "result": result, "cue": found["cue"] if found else None,
                        **Fact(value=result, method="deterministic", extractor=treatment.EXTRACTOR,
                               extractor_version=treatment.VERSION, confidence=1.0 if found else 0.0,
                               evidence=[_with_document(found["evidence"], upper["document_id"])] if found else []
                               ).model_dump()})

    roles = {(c["judgment_id"], c["statute_id"], c.get("section")): c.get("role") for c in concerns}
    changed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in concerns_versions:
        role = roles.get((row["judgment_id"], row["statute_id"], row.get("section")))
        if role in CONSTRUED and row["version_status"] in CHANGED:
            changed[row["judgment_id"]].append({
                "statute_id": row["statute_id"], "section": row.get("section"), "role": role,
                "version_status": row["version_status"], "changes": row.get("changes_after_decision", [])})

    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge["to_judgment"]:
            incoming[edge["to_judgment"]].append(edge)
    appealed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in appeals:
        appealed[edge["to_judgment"]].append(edge)

    records = []
    for judgment in judgments:
        jid = judgment["judgment_id"]
        cited = incoming.get(jid, [])
        treatments = Counter(e["treatment"] for e in cited)
        negative = [e for e in cited if e["treatment"] in treatment.NEGATIVE]
        negative += [e for e in appealed.get(jid, []) if e["result"] == "set_aside"]
        caution = [e for e in cited if e["treatment"] == "doubted"]
        caution += [e for e in appealed.get(jid, []) if e["result"] in {"modified", "remanded"}]
        status = ("negative" if negative else "caution" if caution or changed.get(jid)
                  else "positive" if treatments["followed"] else "no_treatment_seen")
        own = by_judgment.get(jid, {})
        records.append({
            "judgment_id": jid, "status": status,
            "citing_judgments_examined": len({e["from_judgment"] for e in cited}),
            "treatments": dict(sorted(treatments.items())),
            "negative": [{k: e[k] for k in ("from_judgment", "treatment", "result", "evidence") if k in e}
                         for e in negative],
            "caution": [{k: e[k] for k in ("from_judgment", "treatment", "result", "evidence") if k in e}
                        for e in caution],
            "statutory_change": changed.get(jid, []),
            "appeal_history": [{k: e[k] for k in ("from_judgment", "result")} for e in appealed.get(jid, [])],
            "disposition": (own.get("disposition") or {}).get("outcome"),
            "weight": {"court_level": COURT_LEVEL.get(judgment["court_id"], 0),
                       "judges_named": own.get("judges_named"), "decision_date": judgment.get("decision_date")},
            "extractor": EXTRACTOR, "extractor_version": VERSION})

    resolved = [e for e in edges if e["to_judgment"]]
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "cites_edges": len(edges),
        "treatment_all_edges": dict(Counter(e["treatment"] for e in edges).most_common()),
        "treatment_resolved_edges": dict(Counter(e["treatment"] for e in resolved).most_common()),
        "appeal_results": dict(Counter(e["result"] for e in appeals).most_common()),
        "dispositions_read": dict(Counter((r.get("disposition") or {}).get("outcome") for r in by_judgment.values()
                                          ).most_common()),
        "status": dict(Counter(r["status"] for r in records).most_common()),
        "judgments_with_statutory_change": len(changed),
        "judgments_cited_by_a_held_judgment": sum(1 for r in records if r["citing_judgments_examined"]),
    }
    return {"cites": edges, "appeals": appeals, "records": records, "report": report}
