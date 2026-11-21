"""How far a page's text lines are tilted, from the page image alone."""

import cv2
import numpy as np

from docintel.skew import estimate_skew


def text_page(angle=0.0):
    image = np.full((1200, 900, 3), 235, np.uint8)
    for row in range(26):
        cv2.putText(image, "The Manager shall maintain separate and proper books and accounts of the venture", (70, 90 + row * 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (30, 30, 30), 1)
    if angle:
        matrix = cv2.getRotationMatrix2D((450, 600), angle, 1.0)
        image = cv2.warpAffine(image, matrix, (900, 1200), borderValue=(235, 235, 235))
    return image


def test_a_straight_page_has_no_skew():
    assert abs(estimate_skew(text_page())) <= 0.2


def test_the_tilt_is_recovered_with_its_sign_to_within_a_fifth_of_a_degree():
    for angle in (-2.2, -0.8, 0.6, 1.9):
        assert abs(estimate_skew(text_page(angle)) - angle) <= 0.2


def test_a_page_with_no_text_has_no_skew():
    assert estimate_skew(np.full((1200, 900, 3), 235, np.uint8)) == 0.0
