"""Small, discoverable command dispatcher for the docintel package."""

from __future__ import annotations

import argparse
import sys


COMMANDS = {
    "run": ("docintel.run", "One step: parse every PDF under a folder into a parsed folder, then summarise it"),
    "parse": ("docintel.pipeline", "Parse a folder (or files) into JSON, Markdown, and metadata"),
    "report": ("docintel.report", "Summarise what a corpus run produced: routes, failures, disagreements, worst documents"),
    "inspect": ("docintel.inspect_html", "Render parsed output beside the source pages, with bounding boxes"),
    "graph": ("docintel.legal.citations", "Build the citation graph over a parsed corpus"),
    "sections": ("docintel.legal.statute_sections", "Build a section-addressable statute corpus from a parsed run"),
    "link": ("docintel.legal.statute_links", "Link the statutes a judgment names to the statutes the corpus holds"),
    "benchmark": ("docintel.benchmark", "Score the parser against the gold pages and the corpus's own labels"),
    "validate": ("docintel.validate", "Validate a canonical Specter JSON document"),
    "urdu": ("docintel.engines.urdu_vision", "Transcribe Urdu crops through a vision model, budgeted and resumable"),
}


def main(prog: str = "docintel") -> None:
    parser = argparse.ArgumentParser(prog=prog, description="Bilingual document parsing tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        subparsers.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args()
    module = __import__(COMMANDS[args.command][0], fromlist=["main"])
    sys.argv = [f"{prog} {args.command}", *rest]
    exit_code = module.main()
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
