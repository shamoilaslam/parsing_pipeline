"""Snapshot gate: has the parser's output changed at all?

The CER benchmark scores ``text`` only, so a change to a bbox, a block type, the
Markdown or a metadata field passes it silently.  This gate hashes everything a
run writes -- the canonical JSON, the Markdown and the metadata record -- and
fails on any difference.  An intended change is landed by re-recording the
baseline in the same commit, with a decision-log entry saying why it moved.

Only two things are removed before hashing, because they differ between two
runs of identical code: OCR wall-clock timings (``elapsed``) and the absolute
location of the input and output folders.  Key order is *not* normalised: it
is part of the bytes a consumer receives.

Each document is hashed whole and also per part (every ``document.*`` key,
every page, the joined Markdown), so a failure names where it moved rather
than only that it did.  Every document is also validated against the
``specter.v1`` contract, so a run that hashes the same but breaks the schema
cannot pass either.

    python -m eval.snapshot record data/pdfs                # write the baseline
    python -m eval.snapshot check data/pdfs                 # parse and compare
    python -m eval.snapshot check data/pdfs --run <dir>     # compare an existing run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from docintel.schema import SpecterV1

BASELINE = Path("eval/baselines/snapshot_data_pdfs.json")
VOLATILE_KEYS = frozenset({"elapsed"})


def _scrub(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(item, replacements) for key, item in value.items() if key not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
    return value


def _replacements(*roots: Path) -> list[tuple[str, str]]:
    """Every spelling of a root that appears in output, longest first."""
    pairs = []
    for label, root in zip(("<run>", "<inputs>"), roots):
        resolved = root.resolve()
        for spelling in {str(root), str(resolved), resolved.as_posix(), root.as_posix()}:
            if spelling not in (".", ""):
                pairs.append((spelling, label))
    return sorted(pairs, key=lambda pair: -len(pair[0]))


def _digest(value: Any) -> str:
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def snapshot_run(run: Path, inputs: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Hash every document a run wrote, whole and by part; list any that break the schema."""
    replacements = _replacements(run, inputs)
    documents: dict[str, dict[str, str]] = {}
    invalid: list[str] = []
    for path in sorted(run.glob("*.json")):
        if path.name == "manifest.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            SpecterV1.model_validate(raw)
        except ValidationError as error:
            invalid.append(f"{path.stem}: breaks specter.v1 ({error.error_count()} errors): {str(error)[:300]}")
        result = _scrub(raw, replacements)
        parts = {"<whole>": _digest(result)}
        for key, value in (result.get("document") or {}).items():
            parts[f"document.{key}"] = _digest(value)
        for index, page in enumerate(result.get("pages") or []):
            parts[f"pages[{index}]"] = _digest(page)
        parts["markdown"] = _digest(result.get("markdown", ""))
        for name, sidecar in (("<md file>", run / f"{path.stem}.md"),
                              ("<metadata file>", run / "metadata" / path.name)):
            if sidecar.exists():
                text = sidecar.read_text(encoding="utf-8")
                parts[name] = _digest(_scrub(json.loads(text), replacements) if sidecar.suffix == ".json"
                                      else _scrub(text, replacements))
        documents[path.stem] = parts
    return documents, invalid


def parse(inputs: Path, run: Path, workers: int) -> None:
    """Run the production CLI, so the gate covers routing and enrichment too."""
    subprocess.run([sys.executable, "-m", "specter", "parse", str(inputs), "--out", str(run),
                    "--workers", str(workers), "--quiet"], check=True)


def compare(baseline: dict[str, dict[str, str]], current: dict[str, dict[str, str]]) -> list[str]:
    problems = []
    for stem in sorted(set(baseline) | set(current)):
        if stem not in current:
            problems.append(f"{stem}: missing from this run")
        elif stem not in baseline:
            problems.append(f"{stem}: not in the baseline")
        elif baseline[stem]["<whole>"] != current[stem]["<whole>"]:
            moved = [part for part in sorted(set(baseline[stem]) | set(current[stem]))
                     if part != "<whole>" and baseline[stem].get(part) != current[stem].get(part)]
            problems.append(f"{stem}: changed in {', '.join(moved) or 'key order'}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Record or check a byte-level snapshot of parser output.")
    parser.add_argument("action", choices=["record", "check"])
    parser.add_argument("inputs", type=Path, help="folder of PDFs the snapshot covers")
    parser.add_argument("--run", type=Path, default=None, help="use this existing run instead of parsing")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        run = args.run or Path(tmp) / "run"
        if args.run is None:
            parse(args.inputs, run, args.workers)
        current, invalid = snapshot_run(run, args.inputs)

    if invalid:
        # A baseline must not be recorded from output that breaks the contract.
        print("\n".join(invalid))
        sys.exit(1)
    if args.action == "record":
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"recorded": len(current), "baseline": str(args.baseline)}))
        return

    problems = compare(json.loads(args.baseline.read_text(encoding="utf-8")), current)
    for line in problems:
        print(line)
    print(json.dumps({"documents": len(current), "changed": len(problems)}))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
