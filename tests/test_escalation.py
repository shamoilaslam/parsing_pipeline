"""The escalation seam: may this text leave the machine for this engine?

The router fixes a page's ceiling before any OCR runs, but a scanned page has no
text layer to check.  The decision is made again here, on the text local OCR
read, immediately before anything is sent.
"""

from docintel.escalation import may_send

CLEAN = "The petition is dismissed."
SENSITIVE = "Complainant CNIC No. 35202-1234567-1"


def test_a_local_engine_may_read_anything():
    assert may_send(engine="local", ceiling="local", text=SENSITIVE)


def test_a_third_party_engine_may_read_clean_text_when_the_ceiling_allows_it():
    assert may_send(engine="third_party", ceiling="third_party", text=CLEAN)


def test_text_that_turns_out_sensitive_stays_off_third_party_engines_despite_the_ceiling():
    assert not may_send(engine="third_party", ceiling="third_party", text=SENSITIVE)


def test_text_that_turns_out_sensitive_stays_off_self_hosted_engines_too():
    assert not may_send(engine="self_hosted", ceiling="third_party", text=SENSITIVE)


def test_an_engine_beyond_the_pages_ceiling_is_refused_even_for_clean_text():
    assert not may_send(engine="third_party", ceiling="self_hosted", text=CLEAN)
    assert not may_send(engine="self_hosted", ceiling="local", text=CLEAN)


def test_a_self_hosted_engine_may_read_clean_text_within_a_self_hosted_ceiling():
    assert may_send(engine="self_hosted", ceiling="self_hosted", text=CLEAN)
