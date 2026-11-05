"""LI-1: one ``Judgment`` per decision, with every alias the held documents give it.

A citation can only resolve to a judgment that has the alias it cites.  This
module builds the identity side from what the corpus already holds:

* **The court's own identifier.** LHC prints a neutral citation in every
  filename ("2013LHC1026" -> ``2013 LHC 1026``). SC encodes the case type,
  number, year and decision date in its filename.  Either one keys a judgment.
* **Case numbers** read from the page, normalised by ``intel.case_numbers`` and
  scoped to the court, because "W.P. 100/2015" at Lahore and at Islamabad are
  different cases.
* **Reporter headers.**  Some held PDFs are reporter prints: page 1 opens with
  the reporter's own citation ("PLJ 2016 Cr.C. (Lahore) 519").  That is a
  concordance row at deterministic precision -- the bridge between the
  reporter key space judgments cite in and the court key space we hold.

A judgment's identity prefers the court's identifier over anything read from
the page: the neutral citation is assigned by the court and is unique, while a
decision date read from the text is wrong often enough (LHC: 95.7% agree with
their own neutral citation year) that keying on it would split one judgment in
two.  Documents with no court identifier at all are keyed by their own content
hash, so they are still nodes and still citable, just not yet merged.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from intel.case_numbers import CaseNumber, from_sc_label, parse
from intel.corpus import ParsedDocument
from intel.evidence import Evidence, Fact
from specter.citations import find_citations

EXTRACTOR = "intel.identity"
VERSION = "3"

# A header is the reporter's citation on its own line at the very top of page 1.
HEADER_BLOCKS = 3
HEADER_COVERAGE = 0.8
# Neutral citations are the court's identifier, not a reporter's.
NEUTRAL_REPORTERS = {"LHC", "PHC"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def judgment_id(key: str) -> str:
    return "j_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _fact(value: Any, method: str, confidence: float, evidence: list[Evidence]) -> dict[str, Any]:
    return Fact(value=value, method=method, extractor=EXTRACTOR, extractor_version=VERSION,  # type: ignore[arg-type]
                confidence=confidence, evidence=evidence).model_dump(exclude_defaults=False)


def reporter_header(payload: dict[str, Any], document_id: str) -> tuple[dict[str, Any], Evidence] | None:
    """The reporter citation a reporter print opens with, if this document is one.

    Only the first few content blocks of page 1 are looked at, and the block
    must be the citation and little else: a judgment that *cites* a case in its
    opening line is not a reporter print of that case.
    """
    pages = payload.get("pages") or []
    if not pages:
        return None
    first = pages[0]
    blocks = [b for b in first.get("blocks", [])
              if (b.get("text") or "").strip() and not b.get("decorative")][:HEADER_BLOCKS]
    for block in blocks:
        text = " ".join(block["text"].split())
        for citation in find_citations(text):
            if citation["jurisdiction"] != "PK" or citation["reporter"] in NEUTRAL_REPORTERS:
                continue
            if len(citation["text"]) >= HEADER_COVERAGE * len(re.sub(r"[\[\]().,:;\s-]+$", "", text)):
                return citation, Evidence(document_id=document_id, page=first.get("page_number", 1),
                                          bboxes=[list(block.get("bbox") or [])], block_ids=[block.get("id", "")],
                                          quote=text)
    return None


def read(document: ParsedDocument, pdf_root: Path | None = None) -> dict[str, Any]:
    """Everything identity needs from one parsed document.  Pure: reads, never writes."""
    record = document.metadata()
    source = Path(record.get("source_file") or "")
    if source.is_file():
        document_id, basis = sha256_file(source), "pdf"
    else:
        document_id, basis = sha256_file(document.json_path), "parsed_json"
    payload = document.payload()
    header = reporter_header(payload, document_id)
    return {
        "corpus": document.corpus,
        "stem": document.stem,
        "document_id": document_id,
        "id_basis": basis,
        "source_file": str(source),
        "parsed_json": str(document.json_path),
        "document_kind": record.get("document_kind"),
        "page_count": record.get("page_count"),
        "metadata": record.get("metadata") or {},
        "provenance": record.get("metadata_provenance") or {},
        "labels": (record.get("label_check") or {}).get("labels") or {},
        "header": [header[0], header[1].model_dump()] if header else None,
    }


def _key(doc: dict[str, Any]) -> tuple[str, str]:
    """The judgment key for a document, and which rung of the identity ladder produced it."""
    labels, metadata = doc["labels"], doc["metadata"]
    court = labels.get("court_id") or metadata.get("court_id") or "unknown_court"
    neutral = labels.get("neutral_citation") or metadata.get("neutral_citation")
    if neutral:
        return f"neutral/{' '.join(neutral.split()).upper()}", "neutral_citation"
    sc_case = from_sc_label(labels.get("case_type"), labels.get("case_number"), labels.get("year"))
    decided = labels.get("decision_date")
    if sc_case and decided:
        day, month, year = (decided.split("-") + ["", "", ""])[:3]
        return f"{court}/{sc_case.key}/{year}-{month}-{day}", "court_label"
    return f"document/{doc['document_id']}", "content_hash"


def build(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Documents, judgments and aliases from per-document reads, plus a report of what was found."""
    documents: dict[str, dict[str, Any]] = {}
    judgments: dict[str, dict[str, Any]] = {}
    aliases: list[dict[str, Any]] = []
    rungs: Counter[str] = Counter()
    headers: Counter[tuple[str, str]] = Counter()
    parsed_numbers = Counter[str]()

    for doc in sorted(docs, key=lambda d: (d["corpus"], d["stem"])):
        did = doc["document_id"]
        if did in documents:
            # The same bytes held twice ("2011LHC4385.pdf" and "2011LHC4385 (1).pdf").
            documents[did]["stems"].append(doc["stem"])
            continue
        key, rung = _key(doc)
        jid = judgment_id(key)
        rungs[rung] += 1
        documents[did] = {"document_id": did, "id_basis": doc["id_basis"], "corpus": doc["corpus"],
                          "stems": [doc["stem"]], "source_file": doc["source_file"],
                          "parsed_json": doc["parsed_json"], "document_kind": doc["document_kind"],
                          "page_count": doc["page_count"], "judgment_id": jid}
        metadata, labels = doc["metadata"], doc["labels"]
        court = labels.get("court_id") or metadata.get("court_id") or "unknown_court"
        judgment = judgments.setdefault(jid, {
            "judgment_id": jid, "key": key, "identity_rung": rung, "court_id": court,
            "document_ids": [], "decision_date": None, "case_numbers": []})
        judgment["document_ids"].append(did)
        judgment["decision_date"] = judgment["decision_date"] or metadata.get("decision_date_iso")

        def alias(kind: str, value: str, method: str, confidence: float, evidence: list[Evidence]) -> None:
            aliases.append({"alias_type": kind, "alias": value, "judgment_id": jid,
                            **_fact(value, method, confidence, evidence)})

        alias("document", did, "deterministic", 1.0, [Evidence(document_id=did, source="sha256")])
        neutral = labels.get("neutral_citation")
        if neutral:
            alias("neutral", " ".join(neutral.split()).upper(), "label", 1.0,
                  [Evidence(document_id=did, source=f"filename:{Path(doc['source_file']).name}")])

        sc_case = from_sc_label(labels.get("case_type"), labels.get("case_number"), labels.get("year"))
        cases: dict[str, tuple[CaseNumber, str, list[Evidence], float]] = {}
        if sc_case:
            cases[sc_case.key] = (sc_case, "label", [Evidence(document_id=did, source=f"filename:{Path(doc['source_file']).name}")], 1.0)
        printed = metadata.get("case_number")
        found = parse(printed)
        parsed_numbers["printed" if printed else "none"] += 1
        if printed and found:
            parsed_numbers["parsed"] += 1
        where = doc["provenance"].get("case_number") or {}
        for case in found:
            cases.setdefault(case.key, (case, "deterministic", [Evidence(
                document_id=did, page=where.get("page"), bboxes=[where["bbox"]] if where.get("bbox") else [],
                quote=printed)], float(where.get("confidence") or 0.5)))
        for case, method, evidence, confidence in cases.values():
            if case.key not in judgment["case_numbers"]:
                judgment["case_numbers"].append(case.key)
            alias("case_number", f"{court}:{case.key}", method, confidence, evidence)

        if doc["header"]:
            citation, header_evidence = doc["header"]
            headers[(doc["corpus"], citation["reporter"])] += 1
            alias("reported", citation["id"], "deterministic", 0.95, [Evidence(**header_evidence)])

    by_alias: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in aliases:
        by_alias[(row["alias_type"], row["alias"])].add(row["judgment_id"])
    conflicts = sorted([{"alias_type": kind, "alias": value, "judgments": sorted(ids)}
                        for (kind, value), ids in by_alias.items()
                        if len(ids) > 1 and kind in {"reported", "neutral"}],
                       key=lambda c: (c["alias_type"], c["alias"]))
    shared_case_numbers = sum(1 for (kind, _), ids in by_alias.items() if kind == "case_number" and len(ids) > 1)

    docs_by_corpus = Counter(d["corpus"] for d in documents.values())
    headers_by_corpus = Counter(corpus for corpus, _ in headers.elements())
    report = {
        "extractor": EXTRACTOR, "extractor_version": VERSION,
        "parsed_documents_read": len(docs),
        "distinct_documents": len(documents),
        "duplicate_files": len(docs) - len(documents),
        "documents_by_corpus": dict(sorted(docs_by_corpus.items())),
        "judgments": len(judgments),
        "judgments_with_more_than_one_document": sum(len(j["document_ids"]) > 1 for j in judgments.values()),
        "identity_rung": dict(sorted(rungs.items())),
        "case_number": {"printed": parsed_numbers["printed"], "parsed": parsed_numbers["parsed"],
                        "absent": parsed_numbers["none"]},
        "reporter_headers": {
            "documents": sum(headers.values()),
            "by_corpus": {corpus: {"documents": headers_by_corpus[corpus], "of": docs_by_corpus[corpus]}
                          for corpus in sorted(docs_by_corpus)},
            "by_reporter": dict(sorted(Counter(reporter for _, reporter in headers.elements()).items(),
                                       key=lambda kv: (-kv[1], kv[0]))),
        },
        "aliases_by_type": dict(sorted(Counter(row["alias_type"] for row in aliases).items())),
        "alias_conflicts": conflicts,
        "case_number_aliases_on_several_judgments": shared_case_numbers,
    }
    return {
        "documents": sorted(documents.values(), key=lambda d: d["document_id"]),
        "judgments": sorted(judgments.values(), key=lambda j: j["judgment_id"]),
        "aliases": sorted(aliases, key=lambda a: (a["alias_type"], a["alias"], a["judgment_id"])),
        "report": report,
    }
