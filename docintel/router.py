"""Choose how a page is read.  A pure function of its ``PageProfile``.

The router also fixes how far a page's pixels and text may travel
(``max_policy``): the pipeline only offers engines whose policy is at or below
it, so a sensitive page cannot reach a third-party API by a routing mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from docintel.engines.base import Policy
from docintel.triage import PageProfile

# A text layer this far into the private-use area is a legacy InPage/Nastaleeq
# encoding: it renders on screen but extracts as unusable codepoints.
PUA_TEXT_RATIO = 0.5

Route = Literal["native", "ocr_cpu"]


@dataclass(frozen=True)
class RoutePlan:
    route: Route
    reasons: tuple[str, ...]
    max_policy: Policy


def _read_route(profile: PageProfile) -> tuple[Route, str]:
    """Decide per page, not per document, whether its own text layer is readable.

    That is what stops a scanned page inside an otherwise digital PDF from being
    emitted empty.  Any one signal is enough:

    * A page-sized image is authoritative evidence of a scan even when the page
      also carries text -- that text is some other scanner's OCR, and trusting
      it over our own was measured to be worse.
    * A page whose own text is missing, or almost entirely private-use glyphs,
      has nothing readable to extract regardless of what images it has.
    """
    if profile.dominant_image:
        return "ocr_cpu", "dominant_image"
    if not profile.text_chars:
        return "ocr_cpu", "no_text_layer"
    if profile.pua_ratio >= PUA_TEXT_RATIO:
        return "ocr_cpu", "private_use_text"
    return "native", "readable_text_layer"


def _ceiling(profile: PageProfile, tenant_ceiling: Policy, opted_in: bool) -> tuple[Policy, tuple[str, ...]]:
    if profile.sensitive:
        return "local", ("sensitive_page",)
    if not opted_in:
        return "local", ()
    return tenant_ceiling, ()


def route(profile: PageProfile, *, tenant_ceiling: Policy = "local", opted_in: bool = False) -> RoutePlan:
    chosen, reason = _read_route(profile)
    ceiling, policy_reasons = _ceiling(profile, tenant_ceiling, opted_in)
    return RoutePlan(chosen, (reason, *policy_reasons), ceiling)
