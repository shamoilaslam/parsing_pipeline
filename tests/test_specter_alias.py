"""``specter.<old>`` must stay the same module object as its new ``docintel`` home."""

import importlib

import pytest

import specter


@pytest.mark.parametrize("old,new", sorted(specter.MOVED.items()))
def test_old_name_is_the_new_module(old, new):
    assert importlib.import_module(f"specter.{old}") is importlib.import_module(new)


def test_patching_through_the_alias_reaches_the_real_module(monkeypatch):
    alias = importlib.import_module("specter.courts")
    monkeypatch.setattr(alias, "COURTS", {})
    assert importlib.import_module("docintel.legal.courts").COURTS == {}
