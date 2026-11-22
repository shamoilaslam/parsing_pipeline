"""What is wrong or missing in a facts output, in one place.

    python -m docintel.facts_report <parsed folder>/facts

Counts what was read and how, and lists every page that failed, holds unreadable ink or unread Urdu, scores
low, lacks its image or breaks the output format.  Exit code 1 if any page failed or breaks the format.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

LOW_QUALITY = 0.5
PAGE_FIELDS = ("page", "status", "source", "width", "height", "unit", "rotation", "image", "language", "quality", "text", "blocks")
BLOCK_FIELDS = ("id", "type", "order", "bbox", "confidence", "language")
FILE_FIELDS = ("name", "sha256", "mime", "pages", "parser", "parser_version", "seconds")


def summarise(facts: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"files": 0, "pages": 0, "status": collections.Counter(), "sources": collections.Counter(),
                               "flags": collections.Counter(), "lines": collections.Counter(), "items": []}
    for path in sorted(facts.glob("*/*.json")):
        if path.parent.name.startswith("_") or path.stem != path.parent.name:
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        summary["files"] += 1
        name = path.parent.name
        for field in FILE_FIELDS:
            if document["file"].get(field) is None and field != "seconds":
                summary["items"].append(("spec", f"{name}: file.{field} missing"))
        if document["file"].get("mime") == "message/rfc822" and not (document.get("metadata") or {}).get("email"):
            summary["items"].append(("spec", f"{name}: email file without metadata.email"))
        for page in document["pages"]:
            summary["pages"] += 1
            where = f"{name} p{page.get('page')}"
            summary["status"][page.get("status")] += 1
            summary["sources"][page.get("source")] += 1
            for field in PAGE_FIELDS:
                if field not in page:
                    summary["items"].append(("spec", f"{where}: page.{field} missing"))
            if page.get("status") == "failed":
                summary["items"].append(("failed", f"{where}: failed: {page.get('reason')}"))
                continue
            for flag in (page.get("quality") or {}).get("flags", []):
                summary["flags"][flag] += 1
            if page.get("image") and not (path.parent / page["image"]).exists():
                summary["items"].append(("image", f"{where}: image {page['image']} not found"))
            score = (page.get("quality") or {}).get("score")
            if score is not None and score < LOW_QUALITY and page.get("blocks"):
                summary["items"].append(("quality", f"{where}: quality {score}"))
            illegible = sum(1 for block in page.get("blocks", []) if block.get("type") == "illegible")
            if illegible:
                summary["items"].append(("illegible", f"{where}: {illegible} illegible block(s)"))
            for block in page.get("blocks", []):
                for field in BLOCK_FIELDS:
                    if field not in block:
                        summary["items"].append(("spec", f"{where}: block {block.get('id')} has no {field}"))
                for line in block.get("lines", []):
                    summary["lines"]["approximate" if line.get("approximate") else "measured"] += 1
    return summary


def problems(summary: dict[str, Any]) -> list[str]:
    return [f"{kind}: {text}" if kind != "spec" else f"spec: {text}" for kind, text in summary["items"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facts", type=Path)
    args = parser.parse_args()
    summary = summarise(args.facts)
    print(f"files {summary['files']}  pages {summary['pages']}  status {dict(summary['status'])}")
    print(f"sources {dict(summary['sources'])}")
    print(f"flags {dict(summary['flags'])}")
    print(f"lines {dict(summary['lines'])}")
    found = problems(summary)
    print(f"\n{len(found)} thing(s) to look at:" if found else "\nnothing to look at")
    for item in found:
        print(" -", item)
    return 1 if any(kind in ("failed", "spec") for kind, _ in summary["items"]) else 0


if __name__ == "__main__":
    sys.exit(main())
