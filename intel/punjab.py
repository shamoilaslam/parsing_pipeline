"""The Punjab Code as a second statute corpus, beside the federal one.

1,973 of the 2,509 statute mentions that named a provincial Act named a Punjab
one, and an LHC judgment applies Punjab law constantly -- rented premises,
pre-emption, local government, the Punjab Civil Servants Act.  The statutes are
in ``D:\\open-pak-law\\data``: PDFs stored by SHA-256 under ``blobs/``, and a
catalogue (``l1/documents/punjab-code/*.parquet``) that gives each one its title,
Act number, date, department and whether it is repealed.

Both are read only.  Each PDF goes through the same parser and the same statute
logic the federal code went through -- structure detection, statute metadata,
section records -- and everything is written under an output folder of its own.

Two facts shape the identity:

* **Jurisdiction comes from the catalogue, not the title.**  "Administrator
  General's Act, 1913" names no province, but this copy is the Punjab one; the
  federal code has its own.  Every statute here is Punjab's.
* **The catalogue repeats some files** (12 PDFs are listed twice, e.g. "THE
  PUNJAB COURT OF WARDS ACT, 1903" and "PUNJAB COURT OF WARDS ACT, 1903").  One
  statute per file.
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path
from typing import Any

OPEN_PAK_LAW = Path(r"D:\open-pak-law\data")
CATALOGUE_RUN = "run-9710d22e691d43d6.parquet"   # the run that lists all 1,361 entries
PREFIX = "pb_"
JUDGMENT_ONLY = frozenset({
    "parties", "petitioner", "respondent", "judges", "bench", "counsel", "case_number", "court", "court_id",
    "hearing_date", "hearing_date_iso", "decision_date", "decision_date_iso", "sections"})


def slug(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", title.lower().replace("\u2019", "")).strip("_")
    return value[:90].rstrip("_")


def catalogue(root: Path = OPEN_PAK_LAW) -> list[dict[str, Any]]:
    """One entry per distinct PDF, with a stable statute id and its catalogue fields."""
    import pyarrow.parquet as pq

    path = next(iter(glob.glob(str(root / "l1" / "documents" / "punjab-code" / CATALOGUE_RUN))), None)
    if path is None:
        raise FileNotFoundError(f"Punjab catalogue {CATALOGUE_RUN} not found under {root}")
    rows = pq.read_table(path).to_pandas().to_dict("records")
    entries: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for row in sorted(rows, key=lambda r: (r["blob_sha256"], r["status_raw"] == "Repealed", r["title"])):
        if row["blob_sha256"] in entries:
            continue
        base = PREFIX + slug(row["title"])
        statute_id = base if base not in used else f"{base}_{row['blob_sha256'][:6]}"
        used.add(statute_id)
        year = re.search(r"\b((?:18|19|20)\d{2})\b", row["act_number_raw"] or "") or re.search(
            r"\b((?:18|19|20)\d{2})\b", row["title"])
        entries[row["blob_sha256"]] = {
            "statute_id": statute_id,
            "title": " ".join(row["title"].split()),
            "act_number": row["act_number_raw"] or None,
            "year": int(year.group(1)) if year else None,
            "instrument": row["instrument_type_raw"] or None,
            "category": row["category_raw"] or None,
            "repealed": row["status_raw"] == "Repealed",
            "jurisdiction": "Punjab",
            "source_url": row["source_url"],
            "pdf": str(root / "blobs" / row["blob_rel_path"]),
            "blob_sha256": row["blob_sha256"],
        }
    return sorted(entries.values(), key=lambda e: e["statute_id"])


def parse_one(entry: dict[str, Any], out: Path) -> dict[str, Any]:
    """Parse one Punjab statute and write its canonical JSON and its section record.  Returns a status row."""
    from specter.ingest_pdfs import parse_pdf
    from specter.specter_parser import write_result
    from specter.statute_sections import build
    from specter.statutes import detect_structure, extract_statute_metadata

    stem = entry["statute_id"]
    try:
        result = parse_pdf(Path(entry["pdf"]), out, stem=stem)
        pages = result.get("pages", [])
        structure = detect_structure(pages)
        text = "\n".join(block["text"] for page in pages for block in page.get("blocks", []))
        found = extract_statute_metadata(text, structure)
        document = result["document"]
        document["document_kind"] = "statute"
        metadata = {k: v for k, v in (document.get("metadata") or {}).items() if k not in JUDGMENT_ONLY}
        # The page's own reading wins where it has one; the catalogue fills the gaps and fixes jurisdiction.
        metadata.update({k: v for k, v in found.items() if v not in (None, "", [])})
        metadata.setdefault("title", entry["title"])
        metadata.update({"statute_id": stem, "category": entry["category"], "jurisdiction": "Punjab",
                         "repealed": entry["repealed"] or bool(found.get("repealed")),
                         "act_number": metadata.get("act_number") or entry["act_number"],
                         "act_year": metadata.get("act_year") or (str(entry["year"]) if entry["year"] else None),
                         "catalogue_title": entry["title"], "source_url": entry["source_url"]})
        document["metadata"] = metadata
        document["structure"] = structure
        write_result(result, out)
        sections = build(result)
        (out / "sections").mkdir(parents=True, exist_ok=True)
        (out / "sections" / f"{stem}.json").write_text(json.dumps(sections, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
        return {"statute_id": stem, "status": "ok", "pages": document.get("page_count"),
                "extraction_mode": document.get("extraction_mode"), "sections": sections["section_count"]}
    except Exception as error:  # one unreadable PDF must not end the run
        return {"statute_id": stem, "status": "failed", "error": f"{type(error).__name__}: {error}"[:300]}
