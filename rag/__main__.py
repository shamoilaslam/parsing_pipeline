"""`python -m rag <command>` -- chunk, index, evaluate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from rag.chunk import chunk_document
from rag.build import assemble, completed, embed_chunks
from rag.embed import BGEM3Embedder, HashingEmbedder
from rag.evaluate import build_queries, score
from rag.normalise import source_of
from rag.retrieve import ALL_LEGS, HybridIndex


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _chunk_dir(root: Path, source: str, limit: int | None,
               deduplicate: bool = True) -> tuple[list[dict], int]:
    """Chunk a parsed corpus, skipping documents already seen.

    The Lahore corpus holds 9,512 PDFs and **8,243 unique documents**: 1,269 are
    byte-identical second copies, mostly named "2011LHC4385 (1).pdf" by whatever
    downloaded them. Indexed as they stand, a search returns the same judgment
    twice and pushes a real second result off the page.

    Matched on the document's own text rather than on the filename, so a copy
    saved under an unrelated name is caught too.
    """
    chunks: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    for index, path in enumerate(sorted(root.glob("*.json"))):
        if path.name == "manifest.json":
            continue
        if limit is not None and index >= limit:
            break
        payload = _load(path)
        if payload is None:
            continue
        made = [chunk.as_dict() for chunk in chunk_document(payload, path.stem, source)]
        if deduplicate and made:
            digest = hashlib.blake2b("".join(c["text"] for c in made).encode("utf-8"),
                                     digest_size=16).hexdigest()
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
        chunks.extend(made)
    return chunks, duplicates


def cmd_chunk(args: argparse.Namespace) -> int:
    source = args.source or source_of(args.input.resolve())
    chunks, duplicates = _chunk_dir(args.input, source, args.limit, args.deduplicate)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks),
                        encoding="utf-8")
    kinds = {c["document_kind"] for c in chunks}
    print(json.dumps({"chunks": len(chunks), "source": source, "kinds": sorted(kinds),
                      "duplicate_documents_skipped": duplicates, "out": str(args.out)}))
    return 0


def _read_chunks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _embedder(name: str, device: str | None = None, fp16: bool = False):
    # The hashing embedder is not a semantic model; it exists so the index and
    # the evaluation can be exercised where BGE-M3 cannot be run.
    if name == "hashing":
        return HashingEmbedder()
    return BGEM3Embedder(device=device, use_fp16=fp16)


def cmd_index(args: argparse.Namespace) -> int:
    chunks = _read_chunks(args.chunks)
    shards = args.out / "shards"
    already = completed(shards, len(chunks)) if args.resume else 0
    if already:
        print(json.dumps({"resuming_from": already, "of": len(chunks)}), flush=True)

    def progress(done: int, total: int) -> None:
        print(json.dumps({"embedded": done, "of": total}), flush=True)

    embed_chunks(chunks, _embedder(args.embedder, args.device, args.fp16), shards,
                 batch_size=args.batch_size, resume=args.resume, on_shard=progress)
    index = assemble(chunks, shards)
    index.save(args.out)
    print(json.dumps({"chunks": len(chunks), "embedder": args.embedder,
                      "device": args.device or "auto", "out": str(args.out)}))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    statutes = _read_chunks(args.statutes)
    judgments = _read_chunks(args.judgments)
    queries = build_queries(judgments, statutes, limit=args.limit)
    if not queries:
        print(json.dumps({"queries": 0,
                          "note": "no judgment in this sample cites a section we hold"}))
        return 0
    index = HybridIndex.load(args.index) if args.index else HybridIndex(statutes)
    embedder = _embedder(args.embedder) if args.index else None
    # Each leg on its own, then the blend.  Fusion weights legs equally, so a
    # weak leg drags a strong one down and only a per-leg table shows it.
    combinations = [("bm25",), ("exact",), ("exact", "bm25")] if embedder is None else [
        ("bm25",), ("exact",), ("exact", "bm25"), ("dense",), ("sparse",),
        ("exact", "bm25", "dense"), ALL_LEGS]
    results = {}
    for legs in combinations:
        results["+".join(legs)] = score(
            queries, lambda text, legs=legs: index.search(text, embedder, limit=args.k, use=legs))
    print(json.dumps({"queries": len(queries), "corpus_chunks": len(statutes),
                      "results": results}, indent=1))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Ask the index a question and show where each answer is printed.

    The point of this pipeline is that an answer can be traced to a page
    region, so the boxes are part of the output rather than an implementation
    detail. `legs` says which retrieval leg found each hit, which is how a bad
    leg becomes visible on a real query rather than only in the metrics.
    """
    index = HybridIndex.load(args.index)
    embedder = _embedder(args.embedder, args.device) if index.dense is not None else None
    hits = index.search(args.query, embedder, limit=args.k)
    if args.json:
        print(json.dumps([{"rank": hit.rank, "score": round(hit.score, 5),
                           "citation": hit.chunk.get("citation"),
                           "legs": hit.legs,
                           "pages": [hit.chunk.get("page_start"), hit.chunk.get("page_end")],
                           "bboxes": hit.chunk.get("bboxes"),
                           "document": hit.chunk.get("document_id"),
                           "text": hit.chunk["text"]} for hit in hits], indent=1))
        return 0
    for hit in hits:
        where = f"p{hit.chunk.get('page_start')}"
        if hit.chunk.get("page_end") != hit.chunk.get("page_start"):
            where += f"-{hit.chunk.get('page_end')}"
        legs = " ".join(f"{leg}#{rank}" for leg, rank in sorted(hit.legs.items()))
        print(f"{hit.rank:2d}. {hit.chunk.get('citation') or hit.chunk['document_id']}"
              f"   [{where}, {len(hit.chunk.get('bboxes') or [])} boxes]   {legs}")
        print(f"    {hit.chunk['text'][:220].strip()}")
    if not hits:
        print("no match")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    from rag.pipeline import STAGES, run

    stages = tuple(args.stages.split(",")) if args.stages else STAGES
    unknown = set(stages) - set(STAGES)
    if unknown:
        print(json.dumps({"error": f"unknown stage(s): {sorted(unknown)}",
                          "known": list(STAGES)}))
        return 2
    state = run([args.input], args.out, source=args.source, stages=stages,
                embedder=args.embedder, device=args.device, fp16=args.fp16,
                workers=args.workers, batch_size=args.batch_size,
                shard_size=args.shard_size, deduplicate=args.deduplicate,
                limit=args.limit, recursive=not args.no_recursive, force=args.force)
    failed = [name for name, entry in state.get("stages", {}).items()
              if entry.get("status") == "failed"]
    return 1 if failed else 0


COMMANDS = {"chunk": cmd_chunk, "index": cmd_index, "evaluate": cmd_evaluate,
            "search": cmd_search, "pipeline": cmd_pipeline}


def main(argv: list[str] | None = None) -> int:
    # Legal text is full of characters a legacy console encoding cannot write:
    # printing a section containing U+2212 killed `rag search` outright on a
    # Windows terminal defaulting to cp1252.  The text is UTF-8 throughout, so
    # the output is too, and an unencodable character is replaced rather than
    # allowed to end the command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="rag", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    chunk = sub.add_parser("chunk", help="chunk a parsed corpus into passages")
    chunk.add_argument("input", type=Path, help="a `specter parse` output folder")
    chunk.add_argument("--out", type=Path, required=True)
    chunk.add_argument("--source", default=None, help="LHC | SC | IHC | pakistancode")
    chunk.add_argument("--limit", type=int, default=None)
    chunk.add_argument("--keep-duplicates", dest="deduplicate", action="store_false",
                       help="index every document, including byte-identical second copies")

    index = sub.add_parser("index", help="embed chunks and build the hybrid index")
    index.add_argument("chunks", type=Path)
    index.add_argument("--out", type=Path, required=True)
    index.add_argument("--embedder", choices=("bge-m3", "hashing"), default="bge-m3")
    index.add_argument("--batch-size", type=int, default=8)
    index.add_argument("--device", default=None,
                       help="mps on Apple Silicon, cuda where there is a GPU, cpu otherwise")
    index.add_argument("--fp16", action="store_true",
                       help="half precision; halves memory, needs mps or cuda")
    index.add_argument("--no-resume", dest="resume", action="store_false",
                       help="re-embed from the start, discarding existing shards")

    evaluate = sub.add_parser("evaluate", help="measure retrieval against the corpus's own labels")
    evaluate.add_argument("--statutes", type=Path, required=True)
    evaluate.add_argument("--judgments", type=Path, required=True)
    evaluate.add_argument("--index", type=Path, default=None)
    evaluate.add_argument("--embedder", choices=("bge-m3", "hashing"), default="bge-m3")
    evaluate.add_argument("--limit", type=int, default=None)
    evaluate.add_argument("-k", type=int, default=10)

    pipeline = sub.add_parser(
        "pipeline", help="PDFs to a searchable index: parse, chunk, index")
    pipeline.add_argument("input", type=Path, help="a folder of PDFs, or one PDF")
    pipeline.add_argument("--out", type=Path, required=True, help="working directory")
    pipeline.add_argument("--source", default=None, help="LHC | SC | IHC | pakistancode")
    pipeline.add_argument("--stages", default=None,
                          help="comma-separated subset of parse,chunk,index")
    pipeline.add_argument("--embedder", choices=("bge-m3", "hashing"), default="bge-m3")
    pipeline.add_argument("--device", default=None,
                          help="mps, cuda or cpu; detected from the machine when omitted")
    pipeline.add_argument("--fp16", action="store_true")
    pipeline.add_argument("--workers", type=int, default=1, help="parse this many at once")
    pipeline.add_argument("--batch-size", type=int, default=8)
    pipeline.add_argument("--shard-size", type=int, default=512)
    pipeline.add_argument("--limit", type=int, default=None)
    pipeline.add_argument("--keep-duplicates", dest="deduplicate", action="store_false")
    pipeline.add_argument("--no-recursive", action="store_true")
    pipeline.add_argument("--force", action="store_true",
                          help="redo every stage instead of resuming")

    search = sub.add_parser("search", help="query an index and show provenance")
    search.add_argument("query")
    search.add_argument("--index", type=Path, required=True)
    search.add_argument("--embedder", choices=("bge-m3", "hashing"), default="bge-m3")
    search.add_argument("--device", default=None)
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--json", action="store_true", help="full records, boxes included")

    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
