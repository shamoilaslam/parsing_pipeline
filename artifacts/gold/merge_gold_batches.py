#!/usr/bin/env python3
"""Merge every batch you've exported (auto-accepted + human-corrected) into
one gold benchmark file for LHC.

Doesn't care about filenames or which batch a file came from - it looks at
each entry and decides what it is:

  - has a "flagged" key                -> human correction (from the browser export)
  - has a "note" mentioning auto-accept -> auto-accepted page (no review needed)
  - has an "error" key, no page_number  -> a file-not-found placeholder, skipped
  - anything else with a "text" key     -> treated as gold text, kept, but flagged
                                            in the summary as "unrecognized shape"
                                            so you can eyeball it (this covers
                                            batch4 if it came from an older/
                                            differently-shaped export)

Corrections always win over auto-accepted for the same (doc_id, page_number).
If two corrections disagree on the same page, the LAST file passed on the
command line wins, and it's printed as a WARNING so you can check by hand -
this should be rare (would mean the same page got reviewed twice).

Usage:
    python merge_gold_batches.py auto_accepted_batch1.json gold_corrections_batch1.json \
        auto_accepted_batch2.json gold_corrections_batch2.json \
        auto_accepted_batch3.json gold_corrections_batch3.json \
        batch4_docs.json batch4_corrections.json \
        --output gold_lhc_benchmark.json

Order doesn't matter except for the rare conflict case above - pass every
file you have, in any order.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _classify(entry: dict[str, Any]) -> str:
    if "page_number" not in entry or entry.get("page_number") is None:
        return "skip_no_page"
    if "flagged" in entry:
        return "human_corrected"
    note = (entry.get("note") or "").lower()
    if "auto-accept" in note or entry.get("reviewed") is False and "text" in entry and "flagged" not in entry:
        return "auto_accepted"
    if "text" in entry:
        return "unrecognized_shape"
    return "skip_no_text"


def merge(files: list[Path]) -> tuple[dict[tuple[str, int], dict[str, Any]], list[str]]:
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    warnings: list[str] = []
    counts: dict[str, int] = {}

    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"COULD NOT READ {fp}: {exc}")
            continue
        if not isinstance(data, list):
            warnings.append(f"SKIPPED {fp}: top-level JSON is not a list (got {type(data).__name__})")
            continue

        for entry in data:
            kind = _classify(entry)
            counts[kind] = counts.get(kind, 0) + 1
            if kind in ("skip_no_page", "skip_no_text"):
                continue

            doc_id = entry.get("doc_id")
            page_number = entry.get("page_number")
            if not doc_id or page_number is None:
                warnings.append(f"SKIPPED entry in {fp.name}: missing doc_id or page_number ({entry})")
                continue
            key = (doc_id, int(page_number))

            record = {
                "doc_id": doc_id,
                "source_name": entry.get("source_name", ""),
                "page_number": int(page_number),
                "text": entry.get("text", ""),
                "status": kind,
                "flagged": entry.get("flagged", False),
                "source_file": fp.name,
            }

            existing = merged.get(key)
            if existing is None:
                merged[key] = record
                continue

            # Corrections always beat auto-accepted, regardless of file order.
            if existing["status"] == "human_corrected" and kind != "human_corrected":
                continue  # keep the existing correction, drop this auto-accepted dupe
            if existing["status"] != "human_corrected" and kind == "human_corrected":
                merged[key] = record  # correction overrides an earlier auto-accepted entry
                continue

            # Both are the same status - genuine collision, last one wins, warn loudly.
            if existing["text"] != record["text"]:
                warnings.append(
                    f"CONFLICT on {doc_id} page {page_number}: "
                    f"'{existing['source_file']}' and '{fp.name}' both have a "
                    f"{kind} entry with DIFFERENT text - kept {fp.name}, check this by hand."
                )
            merged[key] = record

    warnings.append("--- counts across all input files (before dedup) ---")
    for kind, n in sorted(counts.items()):
        warnings.append(f"  {kind}: {n}")

    return merged, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge gold review batches into one benchmark file.")
    parser.add_argument("files", type=Path, nargs="+", help="every auto_accepted_*.json and gold_corrections_*.json you have")
    parser.add_argument("--output", type=Path, default=Path("gold_lhc_benchmark.json"))
    args = parser.parse_args()

    missing = [f for f in args.files if not f.exists()]
    if missing:
        raise SystemExit(f"File(s) not found: {', '.join(str(m) for m in missing)}")

    merged, warnings = merge(args.files)
    records = sorted(merged.values(), key=lambda r: (r["doc_id"], r["page_number"]))

    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    n_docs = len({r["doc_id"] for r in records})
    n_human = sum(1 for r in records if r["status"] == "human_corrected")
    n_auto = sum(1 for r in records if r["status"] == "auto_accepted")
    n_unrec = sum(1 for r in records if r["status"] == "unrecognized_shape")
    n_flagged = sum(1 for r in records if r.get("flagged"))

    print(f"Merged {len(args.files)} files -> {args.output}")
    print(f"  {len(records)} gold pages total, across {n_docs} documents")
    print(f"    {n_human} human-corrected")
    print(f"    {n_auto} auto-accepted")
    if n_unrec:
        print(f"    {n_unrec} unrecognized shape (check these - probably batch4, worth a spot-check)")
    if n_flagged:
        print(f"  {n_flagged} pages you marked 'not sure' while reviewing - worth a second look before using as ground truth")
    print()
    print("\n".join(warnings))


if __name__ == "__main__":
    main()
