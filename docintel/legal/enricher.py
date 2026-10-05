"""The legal enricher: what a judgment or statute is, who it cites, what the corpus says about it.

Runs after an engine has read the pages.  Engines produce layout; everything
here is Pakistani-legal knowledge applied to that layout (ADR 0001, decision 2).
"""

from __future__ import annotations

from pathlib import Path

from docintel.legal.citations import cited_authorities
from docintel.legal.courts import (IHC_ORDER, court_id, matches_case_number, matches_judge,
                                   path_labels, same_date)
from docintel.legal.statutes import detect_structure, extract_statute_metadata, statute_labels


# An Act has no court, no parties and no decision date.  Reporting them as
# empty makes a consumer decide which fields are meaningless for which kind of
# document; dropping them says it once, here.  Citations and referenced acts
# stay: a statute cites other statutes.
JUDGMENT_ONLY_FIELDS = frozenset({
    "parties", "petitioner", "respondent", "judges", "bench", "counsel",
    "case_number", "court", "court_id", "hearing_date", "hearing_date_iso",
    "decision_date", "decision_date_iso",
    # In a judgment "sections" means the provisions it cites.  Beside a
    # statute's own section_count that reads as a contradiction, and the
    # statute's real sections are in ``structure`` with their pages and boxes.
    "sections",
})


def apply_document_kind(result: dict[str, object], pdf: Path) -> dict[str, object]:
    """Say what kind of document this is, and give a statute a statute's fields.

    Three kinds so far.  A judgment and a statute answer different questions:
    running the judgment extractor over an Act reports no parties, no bench and
    no decision date, because an Act has none -- and says nothing about the
    thing a lawyer actually cites, which is a numbered section.  An interim
    order is a judgment's near relative but not the same document, and is
    marked so a reader can ask for one and not the other.

    Done here rather than in the parser so the parser stays about the page and
    the router stays the one place that knows what kind of document this is.
    """
    if not statute_labels(pdf):
        # An interim order is not the judgment: it decides a step in the case,
        # is often a single page, and states no final disposition.  A corpus
        # that files the two together and calls both "judgment" would answer
        # "what did the court hold" with an adjournment.
        result["document"].setdefault(
            "document_kind", "order" if IHC_ORDER.match(pdf.name) else "judgment")
        return result
    document = result["document"]
    structure = detect_structure(result.get("pages", []))
    text = "\n".join(block["text"] for page in result.get("pages", []) for block in page.get("blocks", []))
    statute = extract_statute_metadata(text, structure)
    document["document_kind"] = "statute"
    # Judgment fields an Act does not have are dropped rather than reported as
    # empty; a consumer should not have to know which are meaningless here.
    metadata = {key: value for key, value in (document.get("metadata") or {}).items()
                if key not in JUDGMENT_ONLY_FIELDS}
    document["metadata"] = {**metadata, **statute}
    document["structure"] = structure
    return result


def apply_citations(result: dict[str, object]) -> dict[str, object]:
    """Attach every authority the document relies on, with where it says so.

    Read from the blocks rather than from the flat text, so each citation
    carries the page and box it was printed in -- the difference between a
    citation graph you can check and one you have to trust.

    An Act cites no case law, so statutes are skipped rather than reporting an
    empty list that would read as "none found".
    """
    document = result["document"]
    if document.get("document_kind") == "statute":
        return result
    citations = cited_authorities(result.get("pages", []))
    document["citations"] = citations
    document.setdefault("metadata", {})["citations"] = [entry["id"] for entry in citations]
    return result


def apply_path_labels(result: dict[str, object], pdf: Path) -> dict[str, object]:
    """Corroborate extracted metadata against what the corpus states about itself.

    Two corpora say more than the page does.  SC names the case and its
    decision date in the filename and the judge in the folder, which covers the
    20% of documents that never state their decision date in the text at all.
    IHC publishes a ``meta.json`` beside every judgment naming the parties, the
    bench, the author, the filing category and the date of the order.

    Two rules keep both honest.  A label only ever *fills* a field extraction
    left empty -- it never overwrites a value found on the page, since the
    scraper can be wrong too.  And a filled field is marked with its source and
    an empty bbox, because there is no page region to point at; the provenance
    contract stays sound.

    Deliberately applied by the CLI rather than inside ``parse_pdf``: the
    benchmark measures extraction, and folding labels in there would score the
    corpus against itself.
    """
    labels = path_labels(pdf)
    if not labels:
        return result
    document = result["document"]
    metadata = document.setdefault("metadata", {})
    provenance = document.get("metadata_provenance") or {}
    stated = {
        "case_number": labels.get("case_number_stated"),
        "decision_date": labels.get("decision_date"),
        "judges": labels.get("judges") or ([labels["judge"]] if labels.get("judge") else None),
        "neutral_citation": labels.get("neutral_citation"),
        # Filling the court matters most where the heading is the part OCR
        # damaged, which is exactly where extraction returns nothing.
        "court": labels.get("court"),
        "court_id": labels.get("court_id"),
        "parties": labels.get("parties"),
        "petitioner": labels.get("petitioner"),
        "respondent": labels.get("respondent"),
        "category": labels.get("category"),
    }
    agrees = {
        "case_number": lambda value: matches_case_number(labels, value),
        "decision_date": lambda value: same_date(labels["decision_date"], value),
        "judges": lambda value: matches_judge(labels, value),
        # Nothing in the text states a neutral citation, so it is always a
        # fill and never a disagreement.
        "neutral_citation": lambda value: value == labels.get("neutral_citation"),
        "court": lambda value: court_id(value) == labels.get("court_id"),
        "court_id": lambda value: value == labels.get("court_id"),
        # A party name is filled from a label but never checked against one.
        # The corpus writes a display title -- "FOP etc", "MD, OGDCL etc",
        # "Toyata Islamabad Moters" -- where the cause title prints the name in
        # full.  Comparing them reported a disagreement on 176 of 281
        # documents and not one of them was actionable, which is how a check
        # that fires on half a corpus stops being a check.  Party extraction is
        # measured against these labels in the benchmark, where the caveat can
        # be stated; it is not worth reporting per document.
        "parties": lambda value: True,
        "petitioner": lambda value: True,
        "respondent": lambda value: True,
        # Nothing on the page states the court's own filing category.
        "category": lambda value: value == labels.get("category"),
    }
    source = labels.get("source", "filename")
    findings = []
    for field, value in stated.items():
        if not value:
            continue
        current = metadata.get(field)
        if not current:
            metadata[field] = value
            provenance[field] = {"value": value, "matched_value": None, "page": None,
                                 "bbox": [], "confidence": 0.5, "source": source}
            findings.append({"field": field, "state": f"filled_from_{source}", "label": value})
        elif not agrees[field](current):
            findings.append({"field": field, "state": "disagrees", "label": value, "extracted": current})
    document["metadata_provenance"] = provenance
    # The record the corpus published is kept whole beside the canonical
    # fields.  It carries more than the pipeline models -- the filing category,
    # the laws discussed, every hearing and its short order -- and discarding
    # what is not modelled yet would mean re-reading 22,000 files to get it back.
    if labels.get("sidecar"):
        document["source_metadata"] = labels["sidecar"]
    document["label_check"] = {
        "source": source,
        "labels": {key: value for key, value in labels.items() if key != "sidecar"},
        "findings": findings,
    }
    return result


class LegalEnricher:
    """Kind, citations and corpus-label corroboration, in the order the pipeline always ran them."""

    name = "legal"

    def enrich(self, result: dict[str, object], pdf: Path) -> dict[str, object]:
        apply_document_kind(result, pdf)
        apply_citations(result)
        apply_path_labels(result, pdf)
        return result
