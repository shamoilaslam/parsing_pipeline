"""Small, discoverable command dispatcher for the Specter package."""

from __future__ import annotations

import argparse
import sys


COMMANDS = {
    "parse": ("specter.ingest_pdfs", "Parse a folder (or files) into JSON, Markdown, and metadata"),
    "report": ("specter.report", "Summarise what a corpus run produced: routes, failures, disagreements, worst documents"),
    "inspect": ("specter.inspect_html", "Render parsed output beside the source pages, with bounding boxes"),
    "benchmark": ("specter.benchmark", "Score the parser against the gold pages and the corpus's own labels"),
    "validate": ("specter.validate_document", "Validate a canonical Specter JSON document"),
    "urdu": ("specter.urdu_vision", "Transcribe Urdu crops through a vision model, budgeted and resumable"),
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="specter", description="Deterministic legal PDF parsing tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        subparsers.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args()
    module = __import__(COMMANDS[args.command][0], fromlist=["main"])
    sys.argv = [f"specter {args.command}", *rest]
    module.main()


if __name__ == "__main__":
    main()
