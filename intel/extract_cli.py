"""The LI-4 commands: estimate, run, label and score the disposition extractor."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from intel import extract
from intel.artifacts import read_jsonl, write_json
from intel.corpus import PARSED, RAW, ParsedDocument, check_output

SEED = 20261007


def _documents(identity: Path, corpora: list[str]) -> list[tuple[ParsedDocument, dict[str, Any]]]:
    """One parsed document per judgment, in a fixed order."""
    judgments = {j["judgment_id"]: j for j in read_jsonl(identity / "judgments.jsonl")}
    chosen = []
    seen: set[str] = set()
    for doc in sorted(read_jsonl(identity / "documents.jsonl"), key=lambda d: (d["corpus"], d["stems"][0])):
        if doc["corpus"] not in corpora or doc["judgment_id"] in seen:
            continue
        seen.add(doc["judgment_id"])
        root = PARSED[doc["corpus"]]
        stem = doc["stems"][0]
        parsed = ParsedDocument(doc["corpus"], stem, root / f"{stem}.json", root / "metadata" / f"{stem}.json")
        chosen.append((parsed, {**doc, "key": judgments[doc["judgment_id"]]["key"]}))
    return chosen


def _pick(docs: list, limit: int | None, ids_file: Path | None) -> list:
    if ids_file:
        wanted = {line.strip() for line in ids_file.read_text(encoding="utf-8").splitlines() if line.strip()}
        return [d for d in docs if d[1]["judgment_id"] in wanted]
    if limit:
        rng = random.Random(SEED)
        return rng.sample(docs, min(limit, len(docs)))
    return docs


def run_extract(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    docs = _pick(_documents(args.identity, args.corpus), args.limit, args.ids)
    print(f"{len(docs)} judgments selected", file=sys.stderr)
    if not args.run:
        windows = [extract.prepare(parsed, doc["document_id"])[1] for parsed, doc in docs]
        estimate = extract.estimate(windows, list(extract.PRICES))
        full = len(_documents(args.identity, args.corpus))
        scale = full / max(1, len(windows))
        estimate["whole_corpus_projection"] = {
            "judgments": full,
            "cost": {m: {k: round(v * scale, 2) for k, v in c.items()} for m, c in estimate["cost"].items()}}
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "dry_run.json", estimate)
        preview = extract.request(windows[0], args.model) if windows else {}
        (out / "request_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(estimate, indent=2))
        print("dry run: nothing was sent. Re-run with --run to call the API.", file=sys.stderr)
        return
    if not extract.credentials_present():
        raise SystemExit("No Anthropic credentials (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN). Nothing was sent.")
    results_path = out / f"dispositions_{args.model}.jsonl"
    cache = extract.load_cache(results_path)
    out.mkdir(parents=True, exist_ok=True)
    tally: Counter[str] = Counter()
    with results_path.open("a", encoding="utf-8") as handle:
        for parsed, doc in docs:
            key = extract.cache_key(doc["document_id"], args.model)
            if key in cache:
                tally["cached"] += 1
                continue
            text, document_window = extract.prepare(parsed, doc["document_id"])
            answer, meta = extract.call(extract.request(document_window, args.model))
            row = {"cache_key": key, "judgment_id": doc["judgment_id"], "document_id": doc["document_id"],
                   "key": doc["key"], "model": args.model, "extractor": extract.EXTRACTOR,
                   "extractor_version": extract.VERSION, **meta}
            if answer is not None:
                row.update(extract.verify(answer, text))
                tally["accepted" if row["accepted"] else "rejected_quote"] += 1
            else:
                tally["failed"] += 1
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
    print(json.dumps(dict(tally), indent=2))


def disposition_sheet(args: argparse.Namespace) -> None:
    """A stratified sample of judgments for a lawyer to label, with the end of each judgment to read."""
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    docs = _documents(args.identity, ["LHC", "SC"])
    strata: dict[tuple[str, str], list] = defaultdict(list)
    for item in docs:
        key = item[1]["key"]
        year = next((part[:4] for part in key.replace("neutral/", "").split("/")[::-1] if part[:4].isdigit()), "?")
        era = "pre-2015" if year < "2015" else "2015-2019" if year < "2020" else "2020+"
        strata[(item[1]["corpus"], era)].append(item)
    rng = random.Random(SEED)
    per = max(1, args.n // len(strata))
    chosen = [item for group in strata.values() for item in rng.sample(group, min(per, len(group)))]
    out.mkdir(parents=True, exist_ok=True)
    path = out / "disposition_sheet.csv"
    outcomes = ", ".join(o.value for o in extract.Outcome)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["judgment_id", "corpus", "judgment", "end_of_judgment", "label_outcome", "label_relief",
                         "labelled_by", "notes"])
        for parsed, doc in chosen:
            text, _ = extract.prepare(parsed, doc["document_id"])
            writer.writerow([doc["judgment_id"], doc["corpus"], doc["key"], text.text[-1800:], "", "", "", ""])
    (out / "judgment_ids.txt").write_text("\n".join(doc["judgment_id"] for _, doc in chosen) + "\n", encoding="utf-8")
    print(json.dumps({"sheet": str(path), "judgments": len(chosen), "allowed_outcomes": outcomes,
                      "strata": {f"{c}/{e}": min(per, len(g)) for (c, e), g in strata.items()}}, indent=2))


def evaluate_disposition(args: argparse.Namespace) -> None:
    """Outcome accuracy, per class, of an extraction run against a labelled sheet."""
    with args.labels.open(encoding="utf-8-sig", newline="") as handle:
        labels = {row["judgment_id"]: row["label_outcome"].strip() for row in csv.DictReader(handle)
                  if row.get("label_outcome", "").strip()}
    rows = {row["judgment_id"]: row for row in read_jsonl(args.results)}
    per_class: dict[str, Counter[str]] = defaultdict(Counter)
    for judgment_id, truth in labels.items():
        row = rows.get(judgment_id)
        if row is None:
            per_class[truth]["not_run"] += 1
            continue
        predicted = row.get("outcome") if row.get("accepted") else None
        per_class[truth]["correct" if predicted == truth else "unaccepted" if predicted is None else "wrong"] += 1
    total = sum(sum(c.values()) for c in per_class.values())
    correct = sum(c["correct"] for c in per_class.values())
    result = {"labelled": total, "accuracy": round(correct / total, 4) if total else None,
              "per_class": {k: dict(v) for k, v in sorted(per_class.items())},
              "quote_verified_share": round(sum(1 for r in rows.values() if r.get("accepted")) / len(rows), 4)
              if rows else None}
    print(json.dumps(result, indent=2))


def register(commands: Any, default_out: Path) -> None:
    ext = commands.add_parser("extract", help="LI-4: disposition by model; dry run unless --run")
    ext.add_argument("--identity", type=Path, default=default_out / "identity_v3")
    ext.add_argument("--corpus", nargs="+", default=["LHC", "SC"])
    ext.add_argument("--model", default=extract.DEFAULT_MODEL, choices=list(extract.PRICES))
    ext.add_argument("--limit", type=int, default=None, help="a seeded random sample of this many judgments")
    ext.add_argument("--ids", type=Path, default=None, help="a file of judgment ids (e.g. the labelled sheet's)")
    ext.add_argument("--run", action="store_true", help="actually call the API (otherwise: estimate only)")
    ext.add_argument("--out", type=Path, default=default_out / "extract")
    sheet = commands.add_parser("disposition-sheet", help="LI-4: a stratified sample for lawyers to label")
    sheet.add_argument("--identity", type=Path, default=default_out / "identity_v3")
    sheet.add_argument("--n", type=int, default=120)
    sheet.add_argument("--out", type=Path, default=default_out / "gold" / "disposition")
    score = commands.add_parser("evaluate-disposition", help="LI-4: outcome accuracy against a labelled sheet")
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--results", type=Path, required=True)


HANDLERS = {"extract": run_extract, "disposition-sheet": disposition_sheet, "evaluate-disposition": evaluate_disposition}
