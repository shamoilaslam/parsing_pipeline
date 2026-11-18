"""Finding rubber stamps: red ink in a block, apart from black print."""

import cv2
import numpy as np

from docintel.marks import find_stamps


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
    from docintel.marks import isolate_ink

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


# --- handwriting: pen ink (blue) in thin strokes -----------------------------------------------------------

PEN = (200, 60, 30)  # BGR blue


def note(image, origin=(120, 400)):
    cv2.putText(image, "see cl. 4 - check with Khalid", origin, cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.1, PEN, 2)


def test_a_blue_pen_note_over_black_print_is_found_as_one_box():
    from docintel.marks import find_handwriting

    image = page()
    print_lines(image)
    note(image)
    [(x0, y0, x1, y1)] = find_handwriting(image)
    assert x0 <= 125 and x1 >= 440 and y0 <= 380 and y1 >= 405 and y1 - y0 < 120


def test_black_print_a_red_stamp_and_a_blue_glare_are_not_handwriting():
    from docintel.marks import find_handwriting

    image = page()
    print_lines(image)
    stamp(image)
    cv2.fillPoly(image, [np.array([[0, 0], [260, 0], [0, 300]])], (190, 90, 120))  # a smooth blob, as lens glare is
    assert find_handwriting(image) == []


def test_pen_ink_is_isolated_apart_from_red_ink():
    from docintel.marks import isolate_ink

    image = page()
    print_lines(image)
    note(image)
    stamp(image)
    blue = isolate_ink(image[360:430, 100:560], "blue")
    red = isolate_ink(image[360:430, 100:560], "red")
    assert (cv2.cvtColor(blue, cv2.COLOR_BGR2GRAY) < 128).sum() > 300
    assert (cv2.cvtColor(red, cv2.COLOR_BGR2GRAY) < 128).sum() == 0


# --- ink no block accounts for -------------------------------------------------------------------------------


def lines_of_text(image, top, rows=4):
    for row in range(rows):
        cv2.putText(image, "Black printed text on a line of the page", (150, top + row * 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)


def test_text_that_no_block_covers_is_found_and_text_a_block_covers_is_not():
    from docintel.marks import find_unaccounted

    image = page()
    lines_of_text(image, 300)
    lines_of_text(image, 600)
    found = find_unaccounted(image, covered=[(100, 270, 700, 460)])
    assert found and all(y0 >= 540 and y1 <= 780 for _, y0, _, y1 in found)
    assert min(x0 for x0, *_ in found) <= 160 and max(x1 for _, _, x1, _ in found) >= 500


def test_a_rule_a_solid_blob_a_corner_shadow_and_an_edge_band_are_not_content():
    from docintel.marks import find_unaccounted

    image = page()
    cv2.line(image, (120, 200), (120, 900), (30, 30, 30), 5)  # a margin rule
    cv2.rectangle(image, (300, 500), (420, 600), (30, 30, 30), -1)  # a solid blob
    cv2.fillPoly(image, [np.array([[0, 0], [90, 0], [0, 140]])], (60, 60, 60))  # corner shadow
    cv2.rectangle(image, (745, 0), (800, 1000), (50, 50, 50), -1)  # dark band at the edge
    assert find_unaccounted(image, covered=[]) == []


# --- signatures: ink beside a signature cue or over a signature line ----------------------------------------


def scribble(image, origin=(300, 600)):
    x, y = origin
    pts = np.array([[x, y], [x + 30, y - 40], [x + 50, y + 10], [x + 90, y - 50], [x + 110, y + 5], [x + 160, y - 30], [x + 200, y]], np.int32)
    cv2.polylines(image, [pts], False, (40, 30, 30), 3)
    cv2.polylines(image, [pts + np.array([0, 12], np.int32)], False, (40, 30, 30), 3)


def test_ink_over_a_signature_line_or_beside_the_word_signature_is_a_signature_context():
    from docintel.marks import near_signature

    over_a_line = page()
    scribble(over_a_line)
    cv2.line(over_a_line, (260, 640), (560, 640), (30, 30, 30), 2)
    box = (295, 540, 510, 625)
    assert near_signature(over_a_line, box, labels=[])
    labelled = page()
    scribble(labelled)
    assert near_signature(labelled, box, labels=[((120, 590, 290, 620), "Signature:")])


def test_ink_with_no_line_and_no_cue_is_not_a_signature_context():
    from docintel.marks import near_signature

    image = page()
    scribble(image)
    assert not near_signature(image, (295, 540, 510, 625), labels=[((120, 100, 290, 130), "ARTICLE 4 - ACCOUNTS")])
