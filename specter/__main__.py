"""Small, discoverable command dispatcher for the Specter package."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="specter", description="Deterministic legal PDF parsing tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("parse", "Parse a folder (or files) into JSON, Markdown, and metadata"),
        ("digital", "Parse fully digital PDFs in a directory"),
        ("validate", "Validate a canonical Specter JSON document"),
        ("evaluate", "Evaluate text and layout against a reference"),
        ("benchmark", "Score the parser against the LHC gold pages"),
        ("inspect", "Render parsed output beside the source pages, with bounding boxes"),
    ):
        subparsers.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args()
    if args.command == "parse":
        from specter.ingest_pdfs import main as command_main
    elif args.command == "digital":
        from specter.run_digital_corpus import main as command_main
    elif args.command == "validate":
        from specter.validate_document import main as command_main
    elif args.command == "benchmark":
        from specter.benchmark import main as command_main
    elif args.command == "inspect":
        from specter.inspect_html import main as command_main
    else:
        from specter.evaluate_document import main as command_main
    sys.argv = [f"specter {args.command}", *rest]
    command_main()


if __name__ == "__main__":
    main()

