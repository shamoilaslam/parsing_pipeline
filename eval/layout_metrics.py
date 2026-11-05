"""Geometry scores for one page: block matching, IoU and reading order.

Blocks are matched on geometry alone, so a parser that labels a heading as text
is not penalised here; type agreement is a separate question.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

Block = dict[str, Any]

# A predicted block counts as the gold block it covers at this overlap.
MATCH_IOU = 0.5


@dataclass(frozen=True)
class PageLayoutScore:
    precision: float
    recall: float
    f1: float
    mean_iou: float | None
    tau: float | None


def _iou(a: list[float], b: list[float]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union


def _match(predicted: list[Block], gold: list[Block]) -> list[tuple[Block, Block, float]]:
    """One-to-one pairs, best overlap first, each at or above ``MATCH_IOU``."""
    candidates = sorted(
        ((_iou(p["bbox"], g["bbox"]), pi, gi) for pi, p in enumerate(predicted) for gi, g in enumerate(gold)),
        reverse=True,
    )
    used_p: set[int] = set()
    used_g: set[int] = set()
    pairs = []
    for iou, pi, gi in candidates:
        if iou < MATCH_IOU:
            break
        if pi not in used_p and gi not in used_g:
            used_p.add(pi)
            used_g.add(gi)
            pairs.append((predicted[pi], gold[gi], iou))
    return pairs


def _kendall_tau(pairs: list[tuple[Block, Block, float]]) -> float | None:
    """Kendall's tau between predicted and gold reading order of the matched blocks."""
    if len(pairs) < 2:
        return None
    concordant = discordant = 0
    for (p1, g1, _), (p2, g2, _) in combinations(pairs, 2):
        agreement = (p1["reading_order"] - p2["reading_order"]) * (g1["reading_order"] - g2["reading_order"])
        concordant += agreement > 0
        discordant += agreement < 0
    return (concordant - discordant) / (concordant + discordant)


def evaluate_page(predicted: list[Block], gold: list[Block]) -> PageLayoutScore:
    pairs = _match(predicted, gold)
    hits = len(pairs)
    precision = hits / len(predicted) if predicted else 0.0
    recall = hits / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if hits else 0.0
    mean_iou = sum(iou for _, _, iou in pairs) / hits if hits else None
    return PageLayoutScore(precision, recall, f1, mean_iou, _kendall_tau(pairs))
