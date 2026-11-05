"""The legal-intelligence layer: identity, citations, law and treatment over a parsed corpus.

Reads parser output and never changes it (ADR 0002).  Everything this package
writes goes to an output folder of its own, and ``intel.corpus`` refuses an
output folder that sits inside an input corpus.
"""
