"""``python -m intel <command>``: the legal-intelligence layer's commands.

Inputs are the parsed corpora, read only.  Every command takes ``--out`` and
refuses a folder inside any input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from functools import partial
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from intel import appeals, citator, extract_cli, gold, identity, provisions, punjab, resolve, treatment, versions
from intel.artifacts import read_jsonl, write_json, write_jsonl
from intel.corpus import PARSED, RAW, check_output, documents

DEFAULT_OUT = Path("artifacts/intel")


def _identity(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    jobs = []
    for corpus in args.corpus:
        found = list(documents(corpus))
        jobs.extend(found[:args.limit] if args.limit else found)
    print(f"reading {len(jobs)} parsed documents from {', '.join(args.corpus)}", file=sys.stderr)
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            reads = list(pool.map(identity.read, jobs, chunksize=16))
    else:
        reads = [identity.read(job) for job in jobs]
    result = identity.build(reads)
    for name in ("documents", "judgments", "aliases"):
        write_jsonl(out / f"{name}.jsonl", result[name])
    write_json(out / "report.json", result["report"])
    print(json.dumps({k: v for k, v in result["report"].items() if k != "alias_conflicts"}, indent=2))
    print(f"alias conflicts: {len(result['report']['alias_conflicts'])}; written to {out}", file=sys.stderr)


def _resolve(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    ident = args.identity
    documents_rows, aliases = read_jsonl(ident / "documents.jsonl"), read_jsonl(ident / "aliases.jsonl")
    corpora = sorted({d["corpus"] for d in documents_rows})
    # One stem per document: a byte-identical copy would only repeat its mentions.
    wanted = {(d["corpus"], d["stems"][0]) for d in documents_rows}
    jobs = [doc for corpus in corpora for doc in documents(corpus) if (doc.corpus, doc.stem) in wanted]
    if args.reads:
        # Reads depend only on the parsed corpus and the context extractor, not on scoring.
        reads = read_jsonl(args.reads)
        print(f"reusing {len(reads)} document reads from {args.reads}", file=sys.stderr)
    else:
        print(f"reading citations from {len(jobs)} documents", file=sys.stderr)
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                reads = list(pool.map(resolve.read, jobs, chunksize=32))
        else:
            reads = [resolve.read(job) for job in jobs]
        write_jsonl(out / "reads.jsonl", reads)
    result = resolve.build(reads, documents_rows, aliases, read_jsonl(ident / "judgments.jsonl"))
    write_jsonl(out / "cites.jsonl", result["edges"])
    write_json(out / "report.json", result["report"])
    print(json.dumps(result["report"], indent=2))


def _appeals(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    ident = args.identity
    documents_rows = read_jsonl(ident / "documents.jsonl")
    if args.reads:
        reads = read_jsonl(args.reads)
        print(f"reusing {len(reads)} document reads from {args.reads}", file=sys.stderr)
    else:
        wanted = {(d["corpus"], d["stems"][0]) for d in documents_rows}
        jobs = [doc for corpus in sorted({d["corpus"] for d in documents_rows})
                for doc in documents(corpus) if (doc.corpus, doc.stem) in wanted]
        print(f"reading appeal recitals from {len(jobs)} documents", file=sys.stderr)
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                reads = list(pool.map(appeals.read, jobs, chunksize=32))
        else:
            reads = [appeals.read(job) for job in jobs]
        write_jsonl(out / "reads.jsonl", reads)
    result = appeals.build(reads, documents_rows, read_jsonl(ident / "aliases.jsonl"),
                           read_jsonl(ident / "judgments.jsonl"))
    write_jsonl(out / "appeal_from.jsonl", result["edges"])
    write_json(out / "report.json", result["report"])
    print(json.dumps(result["report"], indent=2))


def _sample(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    sizes = {"resolved_alias": 30, "resolved_parallel": 10, "resolved_case_name": 100, "unresolved_named": 120}
    rows, population = gold.sample(read_jsonl(args.resolve / "cites.jsonl"), sizes,
                                   read_jsonl(args.identity / "judgments.jsonl"), read_jsonl(args.resolve / "reads.jsonl"),
                                   read_jsonl(args.identity / "documents.jsonl"))
    write_jsonl(out / "sample.jsonl", rows)
    write_json(out / "population.json", population)
    gold.write_sheet(out / "sample.csv", rows)
    print(json.dumps({"sampled": dict(sorted(Counter(r["stratum"] for r in rows).items())), "population": population}, indent=2))


def _evaluate(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.sample / "sample.jsonl")
    population = json.loads((args.sample / "population.json").read_text(encoding="utf-8"))
    result = gold.evaluate(rows, gold.read_labels(args.labels), population)
    write_json(args.sample / "evaluation.json", result)
    print(json.dumps(result, indent=2))


def _provisions(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    documents_rows = read_jsonl(args.identity / "documents.jsonl")
    if args.reads:
        reads = read_jsonl(args.reads)
        print(f"reusing {len(reads)} document reads from {args.reads}", file=sys.stderr)
    else:
        from specter import statute_links
        punjab_dir = args.punjab if (args.punjab / "catalogue.jsonl").exists() else None
        index = provisions.section_index(PARSED["pakistancode"] / "sections",
                                         *([punjab_dir / "sections"] if punjab_dir else []))
        titles = statute_links.build_index(RAW["pakistancode"])
        punjab_titles = provisions.punjab_titles(read_jsonl(punjab_dir / "catalogue.jsonl")) if punjab_dir else None
        print(f"statute index: {len(index['sections'])} statutes"
              f"{' (with the Punjab Code)' if punjab_dir else ''}", file=sys.stderr)
        wanted = {(d["corpus"], d["stems"][0]) for d in documents_rows}
        jobs = [doc for corpus in sorted({d["corpus"] for d in documents_rows})
                for doc in documents(corpus) if (doc.corpus, doc.stem) in wanted]
        print(f"reading provision mentions from {len(jobs)} judgments", file=sys.stderr)
        reader = partial(provisions.read, index=index, titles=titles, punjab=punjab_titles)
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                reads = list(pool.map(reader, jobs, chunksize=32))
        else:
            reads = [reader(job) for job in jobs]
        write_jsonl(out / "reads.jsonl", reads)
    result = provisions.build(reads, documents_rows)
    write_jsonl(out / "concerns.jsonl", result["edges"])
    write_json(out / "report.json", result["report"])
    print(json.dumps(result["report"], indent=2))


def _versions(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    result = versions.build(read_jsonl(args.provisions / "concerns.jsonl"),
                            read_jsonl(args.identity / "judgments.jsonl"), PARSED["pakistancode"], args.punjab)
    write_jsonl(out / "concerns_versions.jsonl", result["edges"])
    write_jsonl(out / "provision_events.jsonl", result["provisions"])
    write_json(out / "report.json", result["report"])
    print(json.dumps(result["report"], indent=2))


def _citator(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values()])
    ident = args.identity
    documents_rows = read_jsonl(ident / "documents.jsonl")
    if args.reads:
        reads = read_jsonl(args.reads)
        print(f"reusing {len(reads)} document reads from {args.reads}", file=sys.stderr)
    else:
        wanted = {(d["corpus"], d["stems"][0]) for d in documents_rows}
        jobs = [doc for corpus in sorted({d["corpus"] for d in documents_rows})
                for doc in documents(corpus) if (doc.corpus, doc.stem) in wanted]
        print(f"reading treatment and disposition from {len(jobs)} judgments", file=sys.stderr)
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                reads = list(pool.map(treatment.read, jobs, chunksize=32))
        else:
            reads = [treatment.read(job) for job in jobs]
        write_jsonl(out / "reads.jsonl", reads)
    result = citator.build(reads, documents_rows, read_jsonl(ident / "judgments.jsonl"),
                           read_jsonl(args.resolve / "cites.jsonl"), read_jsonl(args.appeals / "appeal_from.jsonl"),
                           read_jsonl(args.provisions / "concerns.jsonl"),
                           read_jsonl(args.versions / "concerns_versions.jsonl"))
    write_jsonl(out / "cites_treatment.jsonl", result["cites"])
    write_jsonl(out / "appeal_results.jsonl", result["appeals"])
    write_jsonl(out / "status.jsonl", result["records"])
    write_json(out / "report.json", result["report"])
    print(json.dumps(result["report"], indent=2))


def _punjab(args: argparse.Namespace) -> None:
    out = check_output(args.out, [*PARSED.values(), *RAW.values(), punjab.OPEN_PAK_LAW])
    entries = punjab.catalogue()
    if args.limit:
        entries = entries[:args.limit]
    done = {p.stem for p in (out / "sections").glob("*.json")} if (out / "sections").exists() else set()
    todo = [e for e in entries if e["statute_id"] not in done]
    print(f"{len(entries)} Punjab statutes, {len(todo)} to parse", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "catalogue.jsonl", entries)
    worker = partial(punjab.parse_one, out=out)
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            rows = list(pool.map(worker, todo, chunksize=4))
    else:
        rows = [worker(entry) for entry in todo]
    previous = read_jsonl(out / "parse_log.jsonl") if (out / "parse_log.jsonl").exists() else []
    write_jsonl(out / "parse_log.jsonl", previous + rows)
    print(json.dumps(dict(Counter(r["status"] for r in rows)), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="intel", description="Legal-intelligence layer over parsed corpora.")
    commands = parser.add_subparsers(dest="command", required=True)
    ident = commands.add_parser("identity", help="LI-1: judgments, documents and aliases from the held corpora")
    ident.add_argument("--corpus", action="append", choices=["LHC", "SC"], help="repeatable; default LHC and SC")
    ident.add_argument("--out", type=Path, default=DEFAULT_OUT / "identity")
    ident.add_argument("--workers", type=int, default=4)
    ident.add_argument("--limit", type=int, default=None, help="per corpus, for a quick look")
    res = commands.add_parser("resolve", help="LI-2: CITES edges, resolved against held aliases")
    res.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity", help="an `intel identity` output")
    res.add_argument("--out", type=Path, default=DEFAULT_OUT / "resolve")
    res.add_argument("--workers", type=int, default=4)
    res.add_argument("--reads", type=Path, default=None, help="reuse the reads.jsonl of an earlier resolve run")
    app = commands.add_parser("appeals", help="LI-2 rung 5: APPEAL_FROM edges from appellate recitals")
    app.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity", help="an `intel identity` output")
    app.add_argument("--out", type=Path, default=DEFAULT_OUT / "appeals")
    app.add_argument("--workers", type=int, default=4)
    app.add_argument("--reads", type=Path, default=None, help="reuse the reads.jsonl of an earlier appeals run")
    prov = commands.add_parser("provisions", help="LI-3: CONCERNS edges, judgment to statute section, with roles")
    prov.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity_v3")
    prov.add_argument("--out", type=Path, default=DEFAULT_OUT / "provisions")
    prov.add_argument("--workers", type=int, default=4)
    prov.add_argument("--reads", type=Path, default=None, help="reuse the reads.jsonl of an earlier provisions run")
    prov.add_argument("--punjab", type=Path, default=DEFAULT_OUT / "punjab", help="an `intel punjab` output, used if present")
    ver = commands.add_parser("versions", help="LI-3: amendment timelines, and a version status on every CONCERNS edge")
    ver.add_argument("--provisions", type=Path, default=DEFAULT_OUT / "provisions_v5")
    ver.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity_v3")
    ver.add_argument("--out", type=Path, default=DEFAULT_OUT / "versions")
    ver.add_argument("--punjab", type=Path, default=DEFAULT_OUT / "punjab")
    pb = commands.add_parser("punjab", help="LI-3: parse the Punjab Code into a statute corpus of its own")
    pb.add_argument("--out", type=Path, default=DEFAULT_OUT / "punjab")
    pb.add_argument("--workers", type=int, default=6)
    pb.add_argument("--limit", type=int, default=None)
    cit = commands.add_parser("citator", help="LI-5 (rules): treatment, appeal results and a status per judgment")
    cit.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity_v3")
    cit.add_argument("--resolve", type=Path, default=DEFAULT_OUT / "resolve_v4")
    cit.add_argument("--appeals", type=Path, default=DEFAULT_OUT / "appeals_v2")
    cit.add_argument("--provisions", type=Path, default=DEFAULT_OUT / "provisions_v5")
    cit.add_argument("--versions", type=Path, default=DEFAULT_OUT / "versions")
    cit.add_argument("--out", type=Path, default=DEFAULT_OUT / "citator")
    cit.add_argument("--workers", type=int, default=4)
    cit.add_argument("--reads", type=Path, default=None, help="reuse the reads.jsonl of an earlier citator run")
    extract_cli.register(commands, DEFAULT_OUT)
    smp = commands.add_parser("sample", help="a stratified sample of citation edges to label")
    smp.add_argument("--resolve", type=Path, default=DEFAULT_OUT / "resolve_v3")
    smp.add_argument("--identity", type=Path, default=DEFAULT_OUT / "identity_v3")
    smp.add_argument("--out", type=Path, default=DEFAULT_OUT / "gold" / "citation_resolution")
    ev = commands.add_parser("evaluate", help="precision and recall from a labelled sample")
    ev.add_argument("--sample", type=Path, default=DEFAULT_OUT / "gold" / "citation_resolution")
    ev.add_argument("--labels", type=Path, required=True, help="the labelled sample.csv, or a labels JSON")
    args = parser.parse_args()
    if args.command == "identity":
        args.corpus = args.corpus or ["LHC", "SC"]
        _identity(args)
    elif args.command == "resolve":
        _resolve(args)
    elif args.command == "appeals":
        _appeals(args)
    elif args.command == "provisions":
        _provisions(args)
    elif args.command == "versions":
        _versions(args)
    elif args.command == "citator":
        _citator(args)
    elif args.command == "punjab":
        _punjab(args)
    elif args.command in extract_cli.HANDLERS:
        extract_cli.HANDLERS[args.command](args)
    elif args.command == "sample":
        _sample(args)
    elif args.command == "evaluate":
        _evaluate(args)


if __name__ == "__main__":
    main()
