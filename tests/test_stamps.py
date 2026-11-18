"""Finding rubber stamps: red ink in a block, apart from black print."""

import cv2
import numpy as np

from docintel.stamps import find_stamps


def page(width=800, height=1000):
    return np.full((height, width, 3), 235, np.uint8)  # BGR paper


def print_lines(image, rows=20):
    for row in range(rows):
        cv2.putText(image, "Black printed text on a line of the page", (60, 80 + row * 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)


def stamp(image, box=(450, 60, 760, 170), colour=(60, 60, 200)):
    x0, y0, x1, y1 = box
    cv2.rectangle(image, (x0, y0), (x1, y1), colour, 4)
    cv2.putText(image, "RECEIVED", (x0 + 20, y0 + 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, colour, 4)
    cv2.putText(image, "06 OCT 1993", (x0 + 20, y0 + 100), cv2.FONT_HERSHEY_SIMPLEX, 1.1, colour, 3)


def test_a_red_stamp_is_found_as_one_box_around_all_of_it():
    image = page()
    print_lines(image)
    stamp(image)
    [(x0, y0, x1, y1)] = find_stamps(image)
    assert x0 <= 450 and y0 <= 60 and x1 >= 760 and y1 >= 170
    assert x1 - x0 < 400 and y1 - y0 < 250  # not the whole page


def test_black_print_alone_has_no_stamp():
    image = page()
    print_lines(image)
    assert find_stamps(image) == []


def test_a_speck_of_colour_is_not_a_stamp():
    image = page()
    cv2.circle(image, (400, 500), 4, (60, 60, 200), -1)
    assert find_stamps(image) == []


def test_two_stamps_come_back_top_to_bottom():
    image = page()
    stamp(image, (400, 60, 760, 170))
    stamp(image, (60, 700, 420, 810))
    boxes = find_stamps(image)
    assert len(boxes) == 2 and boxes[0][1] < boxes[1][1]


def test_isolating_a_stamp_keeps_its_ink_and_drops_the_print_under_it():
    from docintel.stamps import isolate_ink

    image = page()
    print_lines(image)
    stamp(image)
    x0, y0, x1, y1 = find_stamps(image)[0]
    only = isolate_ink(image[y0:y1, x0:x1])
    gray = cv2.cvtColor(only, cv2.COLOR_BGR2GRAY)
    assert (gray < 128).sum() > 500  # the stamp's ink is there, in black
    assert only.shape[0] >= 2 * (y1 - y0) - 1  # enlarged for the model
    # black print alone leaves nothing behind
    printed = page()
    print_lines(printed)
    assert (cv2.cvtColor(isolate_ink(printed[60:170, 60:400]), cv2.COLOR_BGR2GRAY) < 128).sum() == 0
