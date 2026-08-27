#!/usr/bin/env python3
"""Turn gold_lhc_benchmark.json into something an eval script can actually
run against: strip the synthetic Court:/Case No:/Parties: block that some
corrections still carry, and attach the PDF path + stratification info
(court, mode, rtl/pua, table, length bucket) from whichever manifest
file(s) you give it.

A doc_id not found in any manifest is left un-enriched (path=null) and
listed at the end - it can still be used for text-only comparisons, but
can't be run through the pipeline until you find the manifest it came
from and re-run this.

Usage:
    python enrich_gold_benchmark.py gold_lhc_benchmark.json gold_50.json --output gold_lhc_benchmark_enriched.json

Pass as many manifest files as you have; later ones fill in whatever
earlier ones didn't cover.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_META_PREFIX_RE = re.compile(
    r"^(?:(?:Court|Case No|Parties):[^\n]*\n)+\n?",
)


def strip_meta_block(text: str) -> str:
    """Removes leading Court:/Case No:/Parties: lines (in any combination,
    any order) plus the blank line after them, if present. Leaves
    everything else untouched. Safe to run on already-clean text - it's a
    no-op if the pattern isn't there."""
    return _META_PREFIX_RE.sub("", text, count=1)


def load_manifest_lookup(manifest_files: list[Path]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for fp in manifest_files:
        data = json.loads(fp.read_text(encoding="utf-8"))
        for d in data.get("documents", []):
            doc_id = d["doc_id"]
            if doc_id in lookup:
                continue  # first manifest to mention a doc_id wins; later ones fill gaps only
            page_features = {p["page_number"]: p["features"] for p in d.get("selected_pages", [])}
            lookup[doc_id] = {
                "path": d.get("path"),
                "court": d.get("court"),
                "year": d.get("year"),
                "mode": d.get("mode"),
                "has_native_rtl": d.get("has_native_rtl"),
                "has_native_pua": d.get("has_native_pua"),
                "length_bucket": d.get("length_bucket"),
                "page_features": page_features,
                "source_manifest": fp.name,
            }
    return lookup


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich and clean the merged gold benchmark.")
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("manifests", type=Path, nargs="+", help="gold_50.json / gold_manifest.json / any manifest with a documents[] list")
    parser.add_argument("--output", type=Path, default=Path("gold_lhc_benchmark_enriched.json"))
    args = parser.parse_args()

    records: list[dict[str, Any]] = json.loads(args.benchmark.read_text(encoding="utf-8"))
    lookup = load_manifest_lookup(args.manifests)

    missing_doc_ids: set[str] = set()
    n_cleaned = 0
    for r in records:
        cleaned = strip_meta_block(r["text"])
        if cleaned != r["text"]:
            n_cleaned += 1
        r["text"] = cleaned

        meta = lookup.get(r["doc_id"])
        if meta is None:
            missing_doc_ids.add(r["doc_id"])
            r["path"] = None
            r["court"] = None
            r["mode"] = None
            r["has_native_rtl_doc"] = None
            r["has_native_pua_doc"] = None
            r["length_bucket"] = None
            r["page_features"] = None
            continue

        r["path"] = meta["path"]
        r["court"] = meta["court"]
        r["mode"] = meta["mode"]
        r["has_native_rtl_doc"] = meta["has_native_rtl"]
        r["has_native_pua_doc"] = meta["has_native_pua"]
        r["length_bucket"] = meta["length_bucket"]
        r["page_features"] = meta["page_features"].get(r["page_number"])

    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    n_enriched = len(records) - sum(1 for r in records if r["path"] is None)
    print(f"{len(records)} records -> {args.output}")
    print(f"  {n_cleaned} had the meta-block prefix stripped")
    print(f"  {n_enriched} enriched with path/court/mode from: {', '.join(sorted({m.name for m in args.manifests})) }")
    if missing_doc_ids:
        n_missing_records = sum(1 for r in records if r["doc_id"] in missing_doc_ids)
        print(f"  {len(missing_doc_ids)} doc_ids ({n_missing_records} records) NOT found in any manifest given - path is null for these:")
        for doc_id in sorted(missing_doc_ids):
            src = next((r["source_name"] for r in records if r["doc_id"] == doc_id), "?")
            print(f"    {doc_id}  ({src})")
    else:
        print("  all doc_ids matched - every record has a path.")


if __name__ == "__main__":
    main()
