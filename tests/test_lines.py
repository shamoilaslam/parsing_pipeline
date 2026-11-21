"""Where each line of a block sits, so a fact can be highlighted line by line."""

import cv2
import numpy as np

from docintel.lines import line_boxes


def block_image(rows, width=500, line_height=34):
    image = np.full((rows * line_height + 20, width, 3), 235, np.uint8)
    for row in range(rows):
        cv2.putText(image, "The Manager shall maintain proper books", (12, 30 + row * line_height), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)
    return image


def test_each_line_of_text_is_found_as_its_own_box_top_to_bottom():
    boxes = line_boxes(block_image(3), 3)
    assert len(boxes) == 3
    assert all(box[3] > box[1] and box[2] > box[0] for box in boxes)
    assert boxes[0][1] < boxes[1][1] < boxes[2][1]


def test_the_boxes_hug_the_ink_horizontally():
    [box] = line_boxes(block_image(1), 1)
    assert 5 <= box[0] <= 20 and box[2] < 500


def test_when_the_image_does_not_show_as_many_lines_as_the_text_has_the_answer_is_none():
    assert line_boxes(block_image(3), 5) is None
    assert line_boxes(np.full((100, 300, 3), 235, np.uint8), 2) is None


def test_without_an_expected_count_every_line_found_is_returned():
    assert len(line_boxes(block_image(4), None)) == 4
