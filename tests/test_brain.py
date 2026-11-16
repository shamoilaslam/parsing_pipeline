from docintel.agent.brain import first_json_object


def test_the_json_object_is_found_inside_chatter_and_fences():
    assert first_json_object('Sure!\n```json\n{"a": {"b": [1, 2]}}\n```\nDone.') == {"a": {"b": [1, 2]}}


def test_braces_inside_strings_do_not_end_the_object():
    assert first_json_object('{"q": "a } b"} trailing') == {"q": "a } b"}


def test_no_json_or_broken_json_is_none():
    assert first_json_object("no object here") is None
    assert first_json_object('{"a": ') is None
