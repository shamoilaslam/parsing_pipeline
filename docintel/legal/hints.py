"""The Pakistani-court hints, built from the legal registry."""

from docintel.engines.hints import LegalHints
from docintel.legal.citations import find_citations
from docintel.legal.courts import canonical_name, court_id, sits_in_benches

LEGAL_HINTS = LegalHints(
    canonical_court=canonical_name,
    court_id=court_id,
    sits_in_benches=sits_in_benches,
    find_citations=find_citations,
)
