"""Retrieval over the parsed corpus: normalise, chunk, embed, retrieve, evaluate.

Kept apart from ``specter`` on purpose.  ``specter`` reads a page and says what
is printed on it; this reads what ``specter`` wrote and prepares it for search.
The parser must not acquire opinions about chunk sizes, and the retriever must
not acquire opinions about PDFs.
"""
