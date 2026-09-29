"""Gold gate: does the LHC benchmark still score every page exactly as before?

``specter benchmark --baseline`` prints the slice deltas but never fails, and a
slice mean can hold still while individual pages move in opposite directions.
This compares every scored page -- CER, WER, character counts, tables found --
against a committed report and exits non-zero on any difference.

    python -m specter benchmark --output artifacts/benchmark/latest.json
    python -m eval.gold_gate artifacts/benchmark/latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASELINE = Path("eval/baselines/lhc_gold.json")


def _rows(report: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(row["file"], row["page"]): row for row in report["rows"]}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    before, after = _rows(baseline), _rows(current)
    problems = [f"{file} p{page}: no longer scored" for file, page in sorted(set(before) - set(after))]
    problems += [f"{file} p{page}: newly scored" for file, page in sorted(set(after) - set(before))]
    for key in sorted(set(before) & set(after)):
        moved = {field: (before[key].get(field), after[key].get(field))
                 for field in sorted(set(before[key]) | set(after[key]))
                 if before[key].get(field) != after[key].get(field)}
        if moved:
            detail = ", ".join(f"{field} {old!r} -> {new!r}" for field, (old, new) in moved.items())
            problems.append(f"{key[0]} p{key[1]}: {detail}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail if any LHC gold page scores differently from the baseline.")
    parser.add_argument("report", type=Path, help="a report written by `specter benchmark --output`")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    args = parser.parse_args()
    current = json.loads(args.report.read_text(encoding="utf-8"))
    problems = compare(json.loads(args.baseline.read_text(encoding="utf-8")), current)
    for line in problems:
        print(line)
    print(json.dumps({"pages": current["records"], "changed": len(problems), "slices": current["slices"]}))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
