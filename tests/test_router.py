"""The router seam: a page profile in, a route and the reasons for it out."""

from docintel.router import route
from docintel.triage import PageProfile


def profile(**overrides) -> PageProfile:
    """A clean born-digital English page; each test changes only what it studies."""
    fields = {"text_chars": 1800}
    fields.update(overrides)
    return PageProfile(**fields)


def test_a_readable_digital_page_stays_native():
    plan = route(profile())
    assert plan.route == "native"
    assert "readable_text_layer" in plan.reasons


def test_a_page_covered_by_a_scan_image_goes_to_ocr_whatever_its_text_layer_says():
    plan = route(profile(dominant_image=True))
    assert plan.route == "ocr_cpu"
    assert "dominant_image" in plan.reasons


def test_a_page_with_no_text_layer_goes_to_ocr():
    plan = route(profile(text_chars=0))
    assert plan.route == "ocr_cpu"
    assert "no_text_layer" in plan.reasons


def test_a_text_layer_that_is_mostly_private_use_glyphs_goes_to_ocr():
    plan = route(profile(pua_ratio=0.5))
    assert plan.route == "ocr_cpu"
    assert "private_use_text" in plan.reasons


def test_a_few_stray_private_use_glyphs_do_not_leave_native():
    assert route(profile(pua_ratio=0.1)).route == "native"


def test_a_public_page_may_reach_third_party_engines_when_the_tenant_allows_it():
    plan = route(profile(), tenant_ceiling="third_party", opted_in=True)
    assert plan.max_policy == "third_party"


def test_the_default_is_offline():
    assert route(profile()).max_policy == "local"


def test_a_matter_that_has_not_opted_in_stays_local():
    assert route(profile(), tenant_ceiling="third_party", opted_in=False).max_policy == "local"


def test_a_sensitive_page_stays_local_even_when_the_matter_opted_in():
    plan = route(profile(sensitive=True), tenant_ceiling="third_party", opted_in=True)
    assert plan.max_policy == "local"
    assert "sensitive_page" in plan.reasons


def test_a_page_never_exceeds_the_tenant_ceiling():
    assert route(profile(), tenant_ceiling="self_hosted", opted_in=True).max_policy == "self_hosted"
