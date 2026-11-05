"""LI-4: judgment metadata by model, every field held to a verbatim quote (ADR 0002, Decisions 2 and 4).

The first extractor is the **disposition**: how the case ended and what relief
was given.  It is the field a lawyer reads first, deterministic code cannot
read reliably (the outcome is a sentence, not a label), and it is cheap: the
model sees only the last 30% of the judgment, where an outcome verb occurs in
88.9% of 90 sampled judgments.

Three rules hold for every model call:

* **Structured output.**  The response is JSON against a fixed schema
  (``output_config.format``), validated with pydantic.
* **Evidence or nothing.**  Each answer carries the sentence it rests on.
  The quote is looked up in the judgment's own text (``intel.evidence.locate``);
  a quote that is not found rejects the extraction.  A model cannot add a
  disposition the judgment does not state.
* **Nothing is sent without being asked for.**  ``--dry-run`` builds every
  request and estimates tokens and cost; only a run without it calls a
  provider.  Results are cached per document, model and extractor version, so
  a re-run pays only for what changed.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from intel.corpus import ParsedDocument
from intel.evidence import locate
from rag.normalise import DocumentText, flatten

EXTRACTOR = "intel.extract.disposition"
VERSION = "1"

TAIL_SHARE = 0.30
# A long judgment's last 30% can still be long; the order is at its very end.
MAX_WINDOW_CHARS = 14000
MIN_WINDOW_CHARS = 3000
# Rough English/legal-text ratio for the dry run.  An estimate, labelled as one;
# the provider's token counter gives the real figure once a key is configured.
CHARS_PER_TOKEN = 4.0
EXPECTED_OUTPUT_TOKENS = 350

# Anthropic first-party prices, USD per million tokens (input, output), from the
# Claude API reference as cached on 2026-09-25.  Batch processing halves both.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_MODEL = "claude-opus-5-5"


class Outcome(str, Enum):
    allowed = "allowed"
    partly_allowed = "partly_allowed"
    dismissed = "dismissed"
    disposed_of = "disposed_of"
    remanded = "remanded"
    withdrawn = "withdrawn"
    converted = "converted"
    bail_granted = "bail_granted"
    bail_refused = "bail_refused"
    conviction_upheld = "conviction_upheld"
    acquitted = "acquitted"
    sentence_modified = "sentence_modified"
    not_stated = "not_stated"
    other = "other"


class Disposition(BaseModel):
    """What the model returns.  Quotes must be copied from the judgment, character for character."""

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    outcome_quote: str | None = Field(description="The sentence of the judgment that states the outcome, verbatim")
    relief: str | None = Field(description="The relief granted or refused, in a few words; null if none is stated")
    relief_quote: str | None = Field(description="The sentence stating the relief, verbatim; null if none")
    proceeding: str | None = Field(description="What was decided: e.g. writ petition, criminal appeal, bail petition")


SYSTEM = """You read the final part of a Pakistani court judgment and report how the case was disposed of.

Return JSON only, matching the schema.

- outcome: one of the listed values. Use "not_stated" if this text does not state an outcome, and "other" only if it states one that fits none of the values.
- outcome_quote: copy the sentence that states the outcome exactly as printed, character for character, including its punctuation and spelling. Do not paraphrase, correct or shorten it inside the quote.
- relief and relief_quote: what the court granted or refused, and the sentence that says so, copied the same way. Null if no relief is stated.
- proceeding: the kind of proceeding decided, in a few words, as the judgment names it.

If the text covers several connected matters decided together, report the outcome of the main matter. If you cannot find a sentence to quote, set the quote to null; never invent one."""


def window(text: DocumentText) -> str:
    """The last 30% of the judgment, bounded so a long judgment does not cost a long request."""
    size = min(MAX_WINDOW_CHARS, max(MIN_WINDOW_CHARS, int(len(text.text) * TAIL_SHARE)))
    return text.text[-size:]


def output_schema() -> dict[str, Any]:
    schema = Disposition.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(Disposition.model_fields)
    return schema


def request(document_window: str, model: str) -> dict[str, Any]:
    """The Messages API request for one judgment.  The system prompt is identical across
    judgments and marked for caching."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 2000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": f"<judgment_end>\n{document_window}\n</judgment_end>"}],
        "output_config": {"format": {"type": "json_schema", "schema": output_schema()}},
    }
    if model != "claude-haiku-4-5":
        # Extraction from a short window is routine work; effort is the cost lever on these models.
        params["output_config"]["effort"] = "low"
    return params


def verify(answer: Disposition, text: DocumentText) -> dict[str, Any]:
    """Accept each answer only if its quote is in the judgment; return the record with evidence."""
    record: dict[str, Any] = {"outcome": answer.outcome.value, "relief": answer.relief, "proceeding": answer.proceeding}
    checks = {}
    for field, quote in (("outcome", answer.outcome_quote), ("relief", answer.relief_quote)):
        if not quote:
            checks[field] = "no_quote"
            continue
        found = locate(text, quote)
        checks[field] = "verified" if found else "quote_not_found"
        record[f"{field}_evidence"] = [e.model_dump() for e in found[:3]]
    record["checks"] = checks
    record["accepted"] = checks["outcome"] == "verified" or answer.outcome == Outcome.not_stated
    return record


def prepare(document: ParsedDocument, document_id: str) -> tuple[DocumentText, str]:
    text = flatten(document.payload(), document_id, document.corpus)
    return text, window(text)


def estimate(windows: list[str], models: list[str]) -> dict[str, Any]:
    """Tokens and cost for running these windows, per model, standard and batch.  An estimate."""
    system_tokens = len(SYSTEM) / CHARS_PER_TOKEN
    input_tokens = sum(len(w) / CHARS_PER_TOKEN + system_tokens + 40 for w in windows)
    output_tokens = EXPECTED_OUTPUT_TOKENS * len(windows)
    costs = {}
    for model in models:
        price_in, price_out = PRICES[model]
        standard = input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out
        costs[model] = {"standard_usd": round(standard, 2), "batch_usd": round(standard / 2, 2)}
    return {"judgments": len(windows), "estimated_input_tokens": round(input_tokens),
            "estimated_output_tokens": output_tokens, "cost": costs,
            "basis": f"{CHARS_PER_TOKEN} characters per token, {EXPECTED_OUTPUT_TOKENS} output tokens per judgment, "
                     "prices as cached 2026-09-25; effort low; caching of the system prompt not credited"}


def call(params: dict[str, Any]) -> tuple[Disposition | None, dict[str, Any]]:
    """One Messages API call.  Returns the parsed answer (or None) and what happened."""
    import anthropic

    client = anthropic.Anthropic()
    extra: dict[str, Any] = {}
    if params["model"] in {"claude-opus-5-5", "claude-sonnet-5-5"}:
        # A classifier decline is re-run server-side on the model Anthropic recommends for it.
        # anthropic 0.107 has no typed `fallbacks` parameter, so it travels in the body.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default"}}
    response = client.beta.messages.create(**params, **extra)
    meta = {"model_served": response.model, "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0)}
    if response.stop_reason == "refusal":
        return None, {**meta, "error": "refusal"}
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return Disposition.model_validate(json.loads(text)), meta
    except (json.JSONDecodeError, ValidationError) as error:
        return None, {**meta, "error": f"invalid_output: {str(error)[:200]}"}


def credentials_present() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def cache_key(document_id: str, model: str) -> str:
    return f"{document_id}:{model}:{EXTRACTOR}:{VERSION}"


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["cache_key"]: row for row in rows}
