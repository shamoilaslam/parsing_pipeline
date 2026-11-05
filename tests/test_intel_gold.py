"""Precision and recall from a labelled sample: weighting by stratum, and what each label counts as."""

from intel import gold


def _row(sid, stratum, linked=None):
    return {"sample_id": sid, "stratum": stratum, "linked_judgment_id": linked}


ROWS = [_row("a", "resolved_case_name", "j1"), _row("b", "resolved_case_name", "j2"),
        _row("c", "unresolved_named"), _row("d", "unresolved_named"), _row("e", "unresolved_named")]
SIZES = {"resolved_case_name": 100, "unresolved_named": 3000, "unresolved_unnamed": 50}


def test_weighted_precision_and_recall():
    labels = {"a": {"label": "correct"}, "b": {"label": "wrong"},
              "c": {"label": "candidate:j9"}, "d": {"label": "not_held"}, "e": {"label": "not_held"}}
    result = gold.evaluate(ROWS, labels, SIZES)
    assert result["precision"] == 0.5
    # 50 correct links against 1000 held-but-missed (1 of 3 unresolved, weighted to 3000).
    assert result["estimated_edges"] == {"correct_links": 50, "wrong_links": 50, "held_but_missed": 1000}
    assert result["unmeasured"] == {"unresolved_unnamed": 50}


def test_unsure_is_left_out_not_counted_as_wrong():
    labels = {"a": {"label": "correct"}, "b": {"label": "unsure"}}
    result = gold.evaluate(ROWS, labels, SIZES)
    assert result["precision"] == 1.0 and result["labelled"] == 1


def test_a_link_to_a_case_not_held_is_a_wrong_link():
    result = gold.evaluate(ROWS, {"a": {"label": "not_held"}}, SIZES)
    assert result["precision"] == 0.0


def test_wilson_is_not_degenerate_at_the_edges():
    low, high = gold._wilson(134, 134)
    assert 0.97 < low < 1.0 and high == 1.0
