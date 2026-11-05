"""The disposition extractor: windows, request shape, quote verification and the dry-run estimate."""

from intel import extract
from rag.normalise import flatten


def _text(*blocks):
    payload = {"document": {}, "pages": [{"page_number": 1, "blocks": [
        {"id": f"p1_b{i}", "type": "text", "text": t, "bbox": [0, i, 1, i + 1]} for i, t in enumerate(blocks)]}]}
    return flatten(payload, "d1", "LHC")


def test_the_window_is_the_end_of_the_judgment_and_bounded():
    text = _text("x" * 100_000, "For the foregoing reasons, this petition is allowed.")
    window = extract.window(text)
    assert window.endswith("this petition is allowed.") and len(window) == extract.MAX_WINDOW_CHARS


def test_a_verified_quote_is_accepted_and_located():
    text = _text("For the foregoing reasons, this petition is allowed", "and the impugned order is set aside.")
    answer = extract.Disposition(outcome="allowed", outcome_quote="this petition is allowed and the impugned order",
                                 relief="impugned order set aside", relief_quote=None, proceeding="writ petition")
    record = extract.verify(answer, text)
    assert record["accepted"] and record["checks"] == {"outcome": "verified", "relief": "no_quote"}
    assert record["outcome_evidence"][0]["block_ids"] == ["p1_b0", "p1_b1"]


def test_an_invented_quote_rejects_the_extraction():
    text = _text("The appeal is dismissed with no order as to costs.")
    answer = extract.Disposition(outcome="allowed", outcome_quote="The appeal is allowed.", relief=None,
                                 relief_quote=None, proceeding="appeal")
    assert not extract.verify(answer, text)["accepted"]


def test_the_request_caches_the_system_prompt_and_constrains_the_output():
    params = extract.request("the end of a judgment", "claude-opus-5-5")
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    schema = params["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(extract.Disposition.model_fields)
    assert params["output_config"]["effort"] == "low"
    assert "effort" not in extract.request("x", "claude-haiku-4-5")["output_config"]


def test_the_estimate_scales_with_price():
    estimate = extract.estimate(["y" * 4000] * 10, ["claude-opus-5-5", "claude-haiku-4-5"])
    opus, haiku = estimate["cost"]["claude-opus-5-5"], estimate["cost"]["claude-haiku-4-5"]
    assert opus["standard_usd"] > haiku["standard_usd"] and opus["batch_usd"] == round(opus["standard_usd"] / 2, 2)
