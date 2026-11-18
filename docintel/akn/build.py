"""Akoma Ntoso-style JSON from a parsed document.

Follows Akoma Ntoso's structure -- meta/identification with FRBR work, expression and
manifestation, then a body by document type -- as plain JSON.  Every node that comes
from the page names its source blocks, so a clause can be shown on its page.

Caselaw gets an ECLI-style id (ECLI identifies case law only); an act gets an ELI-style
id built from its year and number.  An id is never invented: without the facts it needs,
the work URI falls back to the document's content hash.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from docintel.akn.ecli import ecli
from docintel.legal.courts import COURTS
from docintel.legal.statute_sections import section_bodies
from docintel.parsed import documents

PAGE_FURNITURE = {"header", "footer", "footnote"}
PARAGRAPH_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s")


def _expression_and_manifestation(work_uri: str, version: str | None, document: dict[str, Any]) -> dict[str, Any]:
    expression_uri = f"{work_uri}/eng@{version}" if version else f"{work_uri}/eng"
    return {
        "FRBRExpression": {"this": expression_uri, "language": "eng"},
        "FRBRManifestation": {
            "this": f"{expression_uri}/main.json",
            "sourceSha256": document.get("id"),
            "sourceName": document.get("source_name"),
        },
    }


def _hash_uri(kind: str, document_id: str | None) -> str:
    return f"/akn/pk/{kind}/{(document_id or 'unknown')[:16]}"


def _judgment_identification(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata") or {}
    identifier = ecli(metadata)
    date = metadata.get("decision_date_iso")
    if identifier and date:
        _, _, code, _, ordinal = identifier.split(":")
        work_uri = f"/akn/pk/judgment/{code.lower()}/{date}/{ordinal.lower()}"
    else:
        work_uri = _hash_uri("judgment", document.get("id"))
    court = metadata.get("court_id")
    return {
        "FRBRWork": {
            "this": work_uri,
            "ecli": identifier,
            "date": {"value": date, "name": "decision"} if date else None,
            "author": f"#{court}" if court else None,
            "country": "pk",
            "number": metadata.get("case_number"),
        },
        **_expression_and_manifestation(work_uri, date, document),
    }


def _act_identification(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata") or {}
    year, number = metadata.get("act_year"), metadata.get("act_number_value")
    known = bool(year and number)
    work_uri = f"/akn/pk/act/{year}/{number}" if known else _hash_uri("act", document.get("id"))
    return {
        "FRBRWork": {
            "this": work_uri,
            "eli": f"eli/pk/act/{year}/{number}" if known else None,
            "date": {"value": year, "name": "enactment"} if year else None,
            "author": None,
            "country": "pk",
            "number": metadata.get("act_number"),
            "name": metadata.get("title"),
        },
        # The corpus is a snapshot of one version, whose date the file does not give.
        **_expression_and_manifestation(work_uri, None, document),
    }


def _court(court_id: str | None) -> dict[str, str] | None:
    court = next((court for court in COURTS if court.court_id == court_id), None)
    return {"eId": court.court_id, "showAs": court.name.title()} if court else None


def _people(metadata: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """References to the parties and judges, and the header's party and judge entries."""
    references, parties, judges = [], [], []
    for index, (role, name) in enumerate(
        ((role, metadata.get(role)) for role in ("petitioner", "respondent") if metadata.get(role)), 1
    ):
        references.append({"eId": f"party_{index}", "showAs": name})
        parties.append({"role": role, "refersTo": f"#party_{index}", "showAs": name})
    for index, name in enumerate(metadata.get("judges") or [], 1):
        references.append({"eId": f"judge_{index}", "showAs": name})
        judges.append({"refersTo": f"#judge_{index}", "showAs": name})
    return references, parties, judges


def _judgment_body(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Headings and paragraphs in reading order, each pointing at its source block."""
    body: list[dict[str, Any]] = []
    counts = {"heading": 0, "paragraph": 0}
    for page in payload.get("pages", []):
        for block in page.get("blocks", []):
            text = (block.get("text") or "").strip()
            if not text or block.get("type") in PAGE_FURNITURE or block.get("decorative"):
                continue
            kind = "heading" if block.get("type") == "heading" else "paragraph"
            counts[kind] += 1
            node = {"eId": f"{kind if kind == 'heading' else 'para'}_{counts[kind]}", "kind": kind}
            if kind == "paragraph":
                number = PARAGRAPH_NUMBER_RE.match(text)
                node["num"] = number[1] if number else None
            body.append(
                {**node, "text": text, "blocks": [block["id"]], "page": page["page_number"], "bbox": block["bbox"]}
            )
    return body


def _judgment(payload: dict[str, Any]) -> dict[str, Any]:
    document = payload["document"]
    metadata = document.get("metadata") or {}
    court = _court(metadata.get("court_id"))
    people, parties, judges = _people(metadata)
    return {
        "type": "judgment",
        "meta": {
            "identification": _judgment_identification(document),
            "references": {"TLCOrganization": [court] if court else [], "TLCPerson": people},
        },
        "judgment": {
            "header": {
                "court": {"refersTo": f"#{court['eId']}", "showAs": court["showAs"]} if court else None,
                "docNumber": metadata.get("case_number"),
                "docDate": metadata.get("decision_date_iso"),
                "parties": parties,
                "judges": judges,
            },
            "judgmentBody": _judgment_body(payload),
        },
    }


def _section_node(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "eId": f"sec_{section['number']}",
        "kind": "section",
        "num": section["number"],
        "heading": section["heading"],
        "text": section["text"],
        "blocks": section["block_ids"],
        "pages": [section["page_start"], section["page_end"]],
    }


def _nested(sections: list[dict[str, Any]], titles: dict[tuple[str, str], str | None]) -> list[dict[str, Any]]:
    """Sections under their part and chapter, as Akoma Ntoso nests them."""
    body: list[dict[str, Any]] = []
    open_nodes: dict[str, dict[str, Any] | None] = {"part": None, "chapter": None}
    for section in sections:
        siblings = body
        for kind in ("part", "chapter"):
            number = section.get(kind)
            current = open_nodes[kind]
            if number is None:
                open_nodes[kind] = None
                continue
            if current is None or current["num"] != number:
                current = {
                    "eId": f"{'part' if kind == 'part' else 'chp'}_{number}",
                    "kind": kind,
                    "num": number,
                    "heading": titles.get((kind, str(number))),
                    "children": [],
                }
                siblings.append(current)
                open_nodes[kind] = current
                if kind == "part":
                    open_nodes["chapter"] = None
            siblings = current["children"]
        siblings.append(_section_node(section))
    return body


def _act(payload: dict[str, Any]) -> dict[str, Any]:
    document = payload["document"]
    structure = document.get("structure") or []
    titles = {
        (entry["kind"], str(entry.get("number"))): entry.get("title")
        for entry in structure
        if entry.get("kind") in ("part", "chapter")
    }
    return {
        "type": "act",
        "meta": {"identification": _act_identification(document)},
        "act": {"body": _nested(section_bodies(payload, structure), titles)},
    }


def to_akn(payload: dict[str, Any]) -> dict[str, Any]:
    """Akoma Ntoso-style JSON for a parsed judgment (or order) or act."""
    if payload["document"].get("document_kind") == "statute":
        return _act(payload)
    return _judgment(payload)


COURT_KINDS = {"judgment", "order"}
# A court document names its court at the top.  A licence that merely mentions one does not.
COURT_HEADING = re.compile(
    r"(?i)^\s*(?:in\s+the\s+)?(?:[A-Za-z.,'-]+\s+){0,4}?"
    r"(?:high\s+court|supreme\s+court|sessions\s+judge|court\s+of|tribunal|judicial\s+department)\b"
)
HEADING_BLOCKS = 4


def names_a_court(payload: dict[str, Any]) -> bool:
    """Does the page open with a court's name (a heading), not merely mention one?"""
    first = payload["pages"][0]["blocks"][:HEADING_BLOCKS] if payload.get("pages") else []
    return any(COURT_HEADING.match(block["text"]) for block in first)


def write_akn(parsed: Path, types: dict[str, dict[str, Any]]) -> list[str]:
    """``akn/<stem>.json`` for every judgment, order and act in a parsed folder.

    ``types`` is the classifier's call for each document: a pleading or a contract
    is not a judgment and gets no judgment record.
    """
    written = []
    for stem, payload in documents(parsed):
        is_act = payload["document"].get("document_kind") == "statute"
        if not is_act and not (types.get(stem, {}).get("kind") in COURT_KINDS and names_a_court(payload)):
            continue
        target = parsed / "akn" / f"{stem}.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(to_akn(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(stem)
    return written
