"""Amendment timelines: notes read, markers matched by page, and the version status of a cited section."""

from intel import versions


def test_notes_split_and_parse_with_ibid_and_an_effective_date():
    notes = versions.split_notes("3Added by Act No. VIII of 1993, s.3, (w.e.f. 20-11-93, SRO). "
                                 "4Subs. by the Law Reforms Ordinance, 1972 (XII of 1972), s. 2. 5Proviso added ibid.")
    third = versions.parse_note(notes["3"])
    assert (third["kinds"], third["year"], third["effective"]) == (["added"], 1993, "1993-11-20")
    fourth = versions.parse_note(notes["4"])
    fifth = versions.parse_note(notes["5"], fourth)
    assert (fifth["kinds"], fifth["year"]) == (["added"], 1972)


def _payload_and_sections(section_text, footnote):
    payload = {"pages": [{"page_number": 7, "blocks": [
        {"id": "p7_b1", "type": "text", "text": section_text},
        {"id": "p7_b9", "type": "footnote", "text": footnote}]}]}
    sections = {"sections": [{"number": "489F", "text": section_text, "block_ids": ["p7_b1"], "page_start": 7}]}
    return payload, sections


def test_a_marker_opening_the_section_is_a_whole_section_change():
    payload, sections = _payload_and_sections("1[489-F. Dishonestly issuing a cheque. Whoever ...]",
                                              "1Ins. by Ord. LXXXV of 2002, s. 3.")
    events = versions.timelines(payload, sections)["sections"]["489F"]["events"]
    assert len(events) == 1 and events[0]["whole_section"] and events[0]["year"] == 2002


def test_version_status():
    inserted = [{"kinds": ["inserted"], "year": 2002, "whole_section": True, "instrument": "x", "effective": None}]
    restructured = [{"kinds": ["substituted", "inserted", "omitted"], "year": 2025, "whole_section": True,
                     "instrument": "x", "effective": None}]
    words = [{"kinds": ["substituted"], "year": 2016, "whole_section": False, "instrument": "x", "effective": None}]
    assert versions.status(inserted, False, 1999)["version_status"] == "not_yet_in_force"
    assert versions.status(inserted, False, 2010)["version_status"] == "current_text_applies"
    assert versions.status(restructured, False, 2019)["version_status"] == "replaced_since"
    assert versions.status(words, False, 2014)["version_status"] == "amended_since"
    assert versions.status([], True, 2014)["version_status"] == "repealed"
