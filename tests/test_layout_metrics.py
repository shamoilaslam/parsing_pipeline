"""The layout-metrics seam: predicted and gold blocks in, geometry scores out."""

import pytest

from eval.layout_metrics import evaluate_page


def block(bbox, order):
    return {"bbox": bbox, "reading_order": order}


GOLD = [block([0, 0, 100, 20], 0), block([0, 30, 100, 50], 1), block([0, 60, 100, 80], 2)]


def test_a_perfect_prediction_scores_one_everywhere():
    score = evaluate_page(GOLD, GOLD)
    assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)
    assert score.mean_iou == 1.0
    assert score.tau == 1.0


def test_boxes_that_overlap_nothing_score_zero_and_leave_iou_and_order_undefined():
    elsewhere = [block([200, 0, 300, 20], 0)]
    score = evaluate_page(elsewhere, GOLD)
    assert (score.precision, score.recall, score.f1) == (0.0, 0.0, 0.0)
    assert score.mean_iou is None
    assert score.tau is None


def test_blocks_are_matched_by_overlap_not_by_list_position():
    score = evaluate_page(list(reversed(GOLD)), GOLD)
    assert (score.f1, score.mean_iou, score.tau) == (1.0, 1.0, 1.0)


def test_an_extra_predicted_block_costs_precision_only():
    score = evaluate_page(GOLD + [block([200, 0, 300, 20], 3)], GOLD)
    assert score.precision == 0.75
    assert score.recall == 1.0
    assert score.f1 == pytest.approx(6 / 7)


def test_a_missed_gold_block_costs_recall_only():
    score = evaluate_page(GOLD[:2], GOLD)
    assert score.precision == 1.0
    assert score.recall == pytest.approx(2 / 3)
    assert score.f1 == pytest.approx(0.8)


def test_a_shorter_box_lowers_mean_iou_by_the_overlap_ratio():
    shorter = [block([0, 0, 100, 16], 0)] + GOLD[1:]  # 100x16 inside 100x20: IoU 0.8
    assert evaluate_page(shorter, GOLD).mean_iou == pytest.approx((0.8 + 1.0 + 1.0) / 3)


def test_half_overlap_still_matches_but_less_than_half_does_not():
    half = [block([0, 0, 100, 10], 0)]  # IoU 0.5 against the first gold block
    assert evaluate_page(half, GOLD[:1]).f1 == 1.0
    below = [block([0, 0, 100, 9], 0)]  # IoU 0.45
    assert evaluate_page(below, GOLD[:1]).f1 == 0.0


def with_order(*orders):
    return [block(g["bbox"], order) for g, order in zip(GOLD, orders)]


def test_one_swapped_pair_out_of_three_gives_tau_one_third():
    assert evaluate_page(with_order(1, 0, 2), GOLD).tau == pytest.approx(1 / 3)


def test_a_fully_reversed_reading_order_gives_tau_minus_one():
    assert evaluate_page(with_order(2, 1, 0), GOLD).tau == -1.0


def test_order_is_undefined_with_fewer_than_two_matched_blocks():
    assert evaluate_page(GOLD[:1], GOLD).tau is None
