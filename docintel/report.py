"""What a finished -- or half-finished -- corpus run actually produced.

A run of tens of thousands of documents cannot be read document by document,
and its own log scrolls past. This reads the ``metadata/`` folder a run writes
and answers the three questions worth asking of it:

* **Did it work?** routes taken, failures, documents whose own diagnostics say
  ``REVIEW`` rather than ``PASS``.
* **Is it right?** where the page and the corpus's own labels disagree, by
  field, because that is the only correctness signal available at this scale
  without annotation.
* **What should I look at?** the worst documents by quality score, named, so
  ``specter inspect`` has somewhere to start.

It reads the output folder rather than the manifest on purpose: a run that is
still going, or that was interrupted, has metadata files but no manifest yet.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


WORST = 10


def _where(document: dict[str, Any]) -> str:
    """Enough of the path to find the document again."""
    source = Path(document.get("source_file") or document.get("source_name", "?"))
    return "/".join(source.parts[-2:]) if len(source.parts) > 1 else source.name


def load(output_dir: Path) -> list[dict[str, Any]]:
    """Every document the run has written so far."""
    documents = []
    for path in sorted((output_dir / "metadata").glob("*.json")):
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # A file caught mid-write by a run still in flight is not a defect
            # in the run; it will be complete by the next report.
            continue
    return documents


def summarise(documents: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    routes = collections.Counter((d.get("router") or {}).get("selected") for d in documents)
    kinds = collections.Counter(d.get("document_kind") for d in documents)
    statuses = collections.Counter((d.get("diagnostics") or {}).get("status") for d in documents)
    findings: collections.Counter = collections.Counter()
    for document in documents:
        for finding in ((document.get("label_check") or {}).get("findings") or []):
            findings[f"{finding['field']}: {finding['state']}"] += 1
    scored = [(d.get("diagnostics") or {}).get("quality_score") for d in documents]
    scored = sorted(value for value in scored if value is not None)
    worst = sorted(
        (d for d in documents if (d.get("diagnostics") or {}).get("quality_score") is not None),
        key=lambda d: d["diagnostics"]["quality_score"],
    )[:WORST]
    # A statute states its own completeness: a numbered run with a hole in it
    # means a section was missed, and that needs no annotation to know.
    statutes = [d for d in documents if d.get("document_kind") == "statute"]
    holed = [d for d in statutes if (d.get("metadata") or {}).get("sections_complete") is False]
    return {
        "documents": len(documents),
        "routes": dict(routes),
        "kinds": dict(kinds),
        "statuses": dict(statuses),
        "quality": {
            "min": scored[0] if scored else None,
            "p10": scored[len(scored) // 10] if scored else None,
            "median": scored[len(scored) // 2] if scored else None,
        },
        "label_findings": dict(findings.most_common()),
        "statutes_with_a_hole": [_where(d) for d in holed],
        # Named by the case folder as well as the file: at IHC every file is
        # called judgment.pdf, so the name alone identifies nothing.
        "worst": [{"name": _where(d),
                   "score": d["diagnostics"]["quality_score"],
                   "status": d["diagnostics"].get("status"),
                   "pages": d.get("page_count")}
                  for d in worst],
        "failures": (manifest or {}).get("failures", []),
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"{report['documents']} documents written", ""]
    for title, counts in (("route", report["routes"]), ("kind", report["kinds"]),
                          ("status", report["statuses"])):
        pairs = ", ".join(f"{key}={value}" for key, value in sorted(counts.items(), key=lambda x: -x[1]) if key)
        lines.append(f"{title:<10}{pairs}")
    quality = report["quality"]
    if quality["median"] is not None:
        lines.append(f"{'quality':<10}min {quality['min']:.2f}  p10 {quality['p10']:.2f}  median {quality['median']:.2f}")
    failures = report["failures"]
    lines.append(f"{'failures':<10}{len(failures)}")
    for failure in failures[:5]:
        lines.append(f"            {Path(failure['pdf']).name}: {failure['error'][:70]}")

    if report["label_findings"]:
        lines += ["", "where the page and the corpus's own labels differ"]
        for key, count in report["label_findings"].items():
            lines.append(f"  {count:>6}  {key}")

    if report["statutes_with_a_hole"]:
        lines += ["", f"statutes with a hole in their section run ({len(report['statutes_with_a_hole'])})"]
        for name in report["statutes_with_a_hole"][:WORST]:
            lines.append(f"          {name}")

    if report["worst"]:
        lines += ["", f"lowest quality scores -- start `specter inspect` here"]
        for row in report["worst"]:
            lines.append(f"  {row['score']:.2f}  {row['status'] or '':<7} {row['pages'] or '?':>3}p  {row['name'][-70:]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise what a corpus run produced.")
    parser.add_argument("output_dir", type=Path, help="the --out folder of a `specter parse` run")
    parser.add_argument("--json", type=Path, default=None, help="also write the summary as JSON")
    args = parser.parse_args()
    documents = load(args.output_dir)
    if not documents:
        parser.error(f"no metadata files under {args.output_dir / 'metadata'}")
    manifest_path = args.output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    report = summarise(documents, manifest)
    print(render(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
