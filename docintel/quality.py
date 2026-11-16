"""A quality score for text read by an engine that reports no confidence of its own.

Not a measure of accuracy: it catches the ways a model's output visibly breaks (it loops,
it emits symbols or replacement characters) and says why.  A page that is only fluent
prose scores high, which is all this can promise.
"""

from __future__ import annotations

import re

BASE = 0.9  # fluent text from a model we cannot check against the image
LOOP_RUN = 4  # the same word this many times in a row
LOOP_NGRAM_REPEATS = 4  # the same three words this many times
MIN_ALNUM_RATIO = 0.5


def _loops(words: list[str]) -> bool:
    run = 1
    for before, word in zip(words, words[1:]):
        run = run + 1 if word == before else 1
        if run >= LOOP_RUN:
            return True
    grams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
    return any(grams.count(gram) >= LOOP_NGRAM_REPEATS for gram in set(grams))


def text_quality(text: str) -> tuple[float, list[str]]:
    stripped = text.strip()
    if not stripped:
        return 0.0, ["empty"]
    score, reasons = BASE, []
    visible = re.sub(r"\s", "", stripped)
    if sum(char.isalnum() for char in visible) / len(visible) < MIN_ALNUM_RATIO:
        score -= 0.5
        reasons.append("mostly_symbols")
    if "�" in stripped:
        score -= 0.3
        reasons.append("replacement_characters")
    if _loops(stripped.lower().split()):
        score -= 0.4
        reasons.append("repeated_tokens")
    return max(0.0, round(score, 3)), reasons
