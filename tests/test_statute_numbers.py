"""Section numbers as the page means them, Orders over their Rules, omitted sections marked.

Every case here is a real one from the parsed federal code.
"""

import unittest

from specter.statutes import detect_structure


def span(text, **emphasis):
    entry = {"text": text}
    if emphasis:
        entry["emphasis"] = emphasis
    return entry


def heading(text, block_id):
    return {"id": block_id, "type": "text", "text": text, "bbox": [0, 0, 10, 10],
            "spans": [span(text, bold=True)]}


def plain(text, block_id, kind="text"):
    return {"id": block_id, "type": kind, "text": text, "bbox": [0, 0, 10, 10], "spans": [span(text)]}


def structure(*texts):
    blocks = [heading(text, f"p1_b{index}") for index, text in enumerate(texts)]
    return detect_structure([{"page_number": 1, "blocks": blocks}])


def sections(*texts):
    return [(entry["number"], entry.get("number_printed")) for entry in structure(*texts)
            if entry["kind"] == "section"]


class GluedFootnoteMarkerTests(unittest.TestCase):
    """A footnote number printed against the section number, with nothing to tell them apart."""

    def test_a_glued_marker_is_read_off_where_the_run_says_so(self):
        # CPC: "125. ..." between 24 and 26 is section 25 with footnote 1 in front.
        self.assertEqual(sections("24. Power of transfer.", "125. Commission to examine.", "26. Institution of suits."),
                         [("24", None), ("25", "125"), ("26", None)])

    def test_a_real_section_with_the_same_digits_is_left_alone(self):
        self.assertEqual(sections("124. Saving.", "125. Commission to examine.", "126. Power to make rules."),
                         [("124", None), ("125", None), ("126", None)])

    def test_a_marker_before_a_lettered_section(self):
        # CrPC: "5514." after 513 is section 514.
        self.assertEqual(sections("513. Deposit instead of recognizance.", "5514. Procedure on forfeiture of bond.",
                                  "514A. Procedure in case of insolvency."),
                         [("513", None), ("514", "5514"), ("514A", None)])


class MisreadDigitTests(unittest.TestCase):
    """The PDF's own text prints a letter where the digit belongs."""

    def test_a_lower_case_l_is_a_one(self):
        # CrPC: "4l7. Appeal in case of acquittal" was not found at all.
        self.assertEqual(sections("416. Postponement of capital sentence.", "4l7. Appeal in case of acquittal.",
                                  "418. Appeal on matter of fact."),
                         [("416", None), ("417", "4l7"), ("418", None)])

    def test_a_capital_i_is_a_one_where_the_run_needs_it(self):
        # ATA: "2IF" between 21EE and 21G is 21F.
        self.assertEqual(sections("21EE. Power to call information.", "2IF. Remand.", "21G. Trial of offences."),
                         [("21EE", None), ("21F", "2IF"), ("21G", None)])

    def test_a_capital_i_that_is_a_real_suffix_stays(self):
        # ATA also has a real section 21I, between 21H and 21J.
        self.assertEqual(sections("21H. Conditional admissibility.", "21I. Aid and abetment.", "21J. Harbouring."),
                         [("21H", None), ("21I", None), ("21J", None)])


class OmittedSectionTests(unittest.TestCase):
    """A section repealed in place still prints its number; it is marked, not dropped."""

    def test_omitted_placeholders_are_marked(self):
        found = {entry["number"]: entry.get("omitted") for entry in structure(
            "12. Public.", "13. [Definition of “Queen”.] Omitted by A.O., 1961, Art. 2 and Sch.",
            "14. Servant of Government.", "15. [Definition of “British India”.] Rep. by A. O., 1937.",
            "16. 1[Omitted].", "17. Government.")}
        self.assertEqual(found, {"12": None, "13": True, "14": None, "15": True, "16": True, "17": None})


class OrderTests(unittest.TestCase):
    """The CPC's First Schedule: Rules numbered from 1 under each Order."""

    def test_rules_carry_the_order_they_belong_to(self):
        pages = [{"page_number": 1, "blocks": [
            heading("1. Short title.", "p1_b0"),
            heading("THE FIRST SCHEDULE", "p1_b1"),
            plain("ORDER I", "p1_b2", kind="heading"),
            plain("PARTIES OF SUITS", "p1_b3", kind="heading"),
            heading("1. Who may be joined as plaintiffs.", "p1_b4"),
            heading("2. Power of Court to order separate trials.", "p1_b5"),
            plain("1[ORDER XXXIX", "p1_b6", kind="heading"),
            heading("1. Cases in which temporary injunction may be granted.", "p1_b7"),
        ]}]
        found = detect_structure(pages)
        orders = [(entry["number"]) for entry in found if entry["kind"] == "order"]
        rules = [(entry.get("order"), entry["number"]) for entry in found if entry["kind"] == "schedule_item"]
        self.assertEqual(orders, ["I", "XXXIX"])
        self.assertEqual(rules, [("I", "1"), ("I", "2"), ("XXXIX", "1")])

    def test_an_order_outside_a_schedule_is_not_structure(self):
        found = structure("1. Short title.", "ORDER XXI of the Code shall apply.")
        self.assertEqual([entry["kind"] for entry in found], ["section"])


if __name__ == "__main__":
    unittest.main()
