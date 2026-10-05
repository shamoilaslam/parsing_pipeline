"""Compatibility alias for the ``docintel`` package (ADR 0001).

``specter.<old module>`` resolves to the *same module object* as its new home,
so ``import specter.citations`` and ``import docintel.legal.citations`` share
state and monkeypatches.  Imports stay lazy: nothing here loads a parser.
"""

import importlib
import importlib.abc
import importlib.util
import sys

__version__ = "0.1.0"

MOVED = {
    "specter_parser": "docintel.engines.native",
    "scanned_parser": "docintel.engines.scanned",
    "urdu_ocr": "docintel.engines.urdu_ocr",
    "urdu_vision": "docintel.engines.urdu_vision",
    "citations": "docintel.legal.citations",
    "courts": "docintel.legal.courts",
    "statutes": "docintel.legal.statutes",
    "statute_sections": "docintel.legal.statute_sections",
    "statute_links": "docintel.legal.statute_links",
    "ingest_pdfs": "docintel.pipeline",
    "benchmark": "docintel.benchmark",
    "report": "docintel.report",
    "inspect_html": "docintel.inspect_html",
    "validate_document": "docintel.validate",
}


class _Alias(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module) -> None:
        """The target is already executed."""


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        head, _, leaf = fullname.partition(".")
        if head != "specter" or leaf not in MOVED:
            return None
        return importlib.util.spec_from_loader(fullname, _Alias(MOVED[leaf]))


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())
