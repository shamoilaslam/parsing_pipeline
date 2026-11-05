"""The last check before a page's text or pixels leave the machine."""

from __future__ import annotations

from typing import get_args

from docintel.engines.base import Policy
from docintel.triage import sensitivity

# Narrowest reach first; an engine is allowed when it reaches no further than the ceiling.
_REACH = {policy: rank for rank, policy in enumerate(get_args(Policy))}


def may_send(*, engine: Policy, ceiling: Policy, text: str) -> bool:
    """True when ``engine`` may be given this page's text.

    Text that local OCR found to be sensitive stays on the machine whatever the
    ceiling said before OCR, because a scanned page could not be checked then.
    """
    if _REACH[engine] > _REACH[ceiling]:
        return False
    return engine == "local" or not sensitivity(text)
