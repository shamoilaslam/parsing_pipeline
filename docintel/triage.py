"""Cheap, deterministic facts about one page, read before any engine runs.

A profile only describes the page; deciding what to do with it is the router's
job (``docintel.router``), so the two can be tested and changed separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

from docintel.engines.native import PUA_CHAR_RE
from docintel.engines.scanned import _dominant_page_image


@dataclass(frozen=True)
class PageProfile:
    text_chars: int
    dominant_image: bool = False
    pua_ratio: float = 0.0
    sensitive: bool = False


def profile_page(page: fitz.Page) -> PageProfile:
    text = page.get_text("text").strip()
    solid = "".join(text.split())
    pua_ratio = len(PUA_CHAR_RE.findall(solid)) / len(solid) if solid else 0.0
    return PageProfile(
        text_chars=len(text),
        dominant_image=_dominant_page_image(page),
        pua_ratio=pua_ratio,
        sensitive=bool(sensitivity(text)),
    )


# Patterns and keywords that mark a page as too personal to leave the machine.
# Each name is reported on the page profile so a reviewer can see why.
_SENSITIVE = {
    "cnic": re.compile(r"\b\d{5}-?\d{7}-?\d\b"),
    "phone": re.compile(r"(?<!\d)(?:\+92[\s-]?|0)3\d{2}[\s-]?\d{7}\b"),
    "medical": re.compile(r"(?i)medico-?\s?legal|post-?\s?mortem|autopsy|forensic|میڈیکو\s*لیگل|پوسٹ\s*مارٹم|فرانزک"),
    "victim": re.compile(r"(?i)\bvictim|متاثرہ"),
}


def sensitivity(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _SENSITIVE.items() if pattern.search(text))
