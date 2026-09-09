"""PDFs to a searchable index, in one command, on any machine.

Three stages -- **parse, chunk, index** -- run in order under one working
directory, so a fresh machine needs a folder of PDFs and nothing else:

    python -m rag pipeline "/path/to/pdfs" --out work

Everything about it is built for a run that will be interrupted, because at this
corpus size every run is. Parsing 9,512 judgments took hours; embedding 200,000
chunks is hours on Apple Silicon and days on a CPU. So each stage records what
it completed and a rerun continues rather than restarting:

* **parse** skips a PDF whose JSON already exists;
* **chunk** re-runs only when the parsed document count has changed, since
  chunks derived from fewer documents are stale;
* **index** writes a shard every few hundred chunks and resumes from the last
  whole one.

The parse stage runs `specter parse` as a subprocess rather than importing its
loop. That is deliberate: the command already handles worker pooling, filename
collisions, resume and the Windows spawn guard, and reimplementing any of that
here would be a second copy to keep correct -- one earlier attempt at exactly
this produced a `BrokenProcessPool` on Windows.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

STAGES = ("parse", "chunk", "index")
STATE = "pipeline.json"


def detect_device() -> str:
    """The fastest backend this machine actually has.

    Named rather than guessed, because the difference is hours: BGE-M3 is 568M
    parameters, and on an 8 GB Apple Silicon machine Metal is the difference
    between running and swapping.
    """
    try:
        import torch  # noqa: PLC0415 -- optional; only the dense stage needs it
    except ImportError:
        return "cpu"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _load_state(work: Path) -> dict[str, Any]:
    path = work / STATE
    if not path.exists():
        return {"stages": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"stages": {}}


def _save_state(work: Path, state: dict[str, Any]) -> None:
    (work / STATE).write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def _parsed_count(parsed: Path) -> int:
    return sum(1 for path in parsed.glob("*.json") if path.name != "manifest.json")


def run(pdfs: Sequence[str | Path], work: Path, *, source: str | None = None,
        stages: Sequence[str] = STAGES, embedder: str = "bge-m3",
        device: str | None = None, fp16: bool = False, workers: int = 1,
        batch_size: int = 8, shard_size: int = 512, deduplicate: bool = True,
        limit: int | None = None, recursive: bool = True, force: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Take PDFs through parse, chunk and index under one working directory."""
    from rag.build import assemble, completed, embed_chunks
    from rag.normalise import source_of

    say = on_event or (lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    parsed, chunk_file, index_dir = work / "parsed", work / "chunks.jsonl", work / "index"
    state = _load_state(work)
    state.setdefault("stages", {})
    state["work"] = str(work)
    state["inputs"] = [str(p) for p in pdfs]

    # --- parse ---------------------------------------------------------------
    if "parse" in stages:
        started = time.time()
        say({"stage": "parse", "status": "running", "out": str(parsed)})
        command = [sys.executable, "-m", "specter", "parse", *[str(p) for p in pdfs],
                   "--out", str(parsed), "--workers", str(workers), "--quiet"]
        if recursive:
            command.append("--recursive")
        if not force:
            command.append("--skip-existing")
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            state["stages"]["parse"] = {"status": "failed",
                                        "error": (result.stderr or result.stdout)[-2000:]}
            _save_state(work, state)
            say({"stage": "parse", "status": "failed"})
            return state
        state["stages"]["parse"] = {"status": "done", "documents": _parsed_count(parsed),
                                    "seconds": round(time.time() - started, 1)}
        _save_state(work, state)
        say({"stage": "parse", "status": "done", **state["stages"]["parse"]})

    # --- chunk ---------------------------------------------------------------
    if "chunk" in stages:
        from rag.__main__ import _chunk_dir

        documents = _parsed_count(parsed)
        previous = state["stages"].get("chunk") or {}
        # Chunks built from fewer documents than are now parsed are stale, and a
        # stale chunk file would be embedded without complaint.
        fresh = (not force and previous.get("status") == "done"
                 and previous.get("documents") == documents and chunk_file.exists())
        if fresh:
            # The spread comes first: `previous` carries its own "done", and
            # letting it land last reported a reuse as a fresh run.
            say({**previous, "stage": "chunk", "status": "reused"})
        else:
            started = time.time()
            say({"stage": "chunk", "status": "running", "documents": documents})
            corpus = source or source_of(Path(pdfs[0]).resolve())
            chunks, duplicates = _chunk_dir(parsed, corpus, limit, deduplicate)
            chunk_file.write_text(
                "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks),
                encoding="utf-8")
            state["stages"]["chunk"] = {
                "status": "done", "documents": documents, "chunks": len(chunks),
                "source": corpus, "duplicate_documents_skipped": duplicates,
                "seconds": round(time.time() - started, 1)}
            _save_state(work, state)
            say({"stage": "chunk", "status": "done", **state["stages"]["chunk"]})

    # --- index ---------------------------------------------------------------
    if "index" in stages:
        from rag.__main__ import _embedder

        chunks = [json.loads(line) for line
                  in chunk_file.read_text(encoding="utf-8").splitlines() if line]
        if not chunks:
            state["stages"]["index"] = {"status": "skipped", "reason": "no chunks"}
            _save_state(work, state)
            say({"stage": "index", "status": "skipped", "reason": "no chunks"})
            return state
        chosen = device or detect_device()
        shards = index_dir / "shards"
        already = 0 if force else completed(shards, len(chunks), shard_size)
        started = time.time()
        say({"stage": "index", "status": "running", "chunks": len(chunks),
             "embedder": embedder, "device": chosen, "resuming_from": already})
        embed_chunks(chunks, _embedder(embedder, chosen, fp16), shards,
                     batch_size=batch_size, shard_size=shard_size, resume=not force,
                     on_shard=lambda done, total: say({"stage": "index", "embedded": done,
                                                       "of": total}))
        assemble(chunks, shards, shard_size).save(index_dir)
        state["stages"]["index"] = {"status": "done", "chunks": len(chunks),
                                    "embedder": embedder, "device": chosen,
                                    "out": str(index_dir),
                                    "seconds": round(time.time() - started, 1)}
        _save_state(work, state)
        say({"stage": "index", "status": "done", **state["stages"]["index"]})

    return state
