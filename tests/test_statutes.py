import unittest
from pathlib import Path

from specter.statutes import (
    _heading_line,
    _heading_title,
    _roman_to_int,
    detect_structure,
    extract_statute_metadata,
    section_sequence,
    statute_labels,
)


def span(text, **emphasis):
    entry = {"text": text}
    if emphasis:
        entry["emphasis"] = emphasis
    return entry


def block(text, spans=None, kind="text", bbox=(0, 0, 100, 20), block_id="p1_b0"):
    return {"text": text, "spans": spans or [], "type": kind, "bbox": list(bbox), "id": block_id}


def page(number, blocks):
    return {"page_number": number, "blocks": blocks}


class PathLabelTests(unittest.TestCase):
    """The corpus layout states the category, the id and usually the year."""

    def test_a_statute_path_is_read(self):
        got = statute_labels(r"D:\code\bankingfinancial_laws\269_asian_development_bank_ordinance_1971.pdf")
        self.assertEqual(got["document_kind"], "statute")
        self.assertEqual(got["statute_id"], "269")
        self.assertEqual(got["year"], "1971")
        self.assertEqual(got["category"], "Bankingfinancial")

    def test_a_statute_without_a_year_in_its_name_claims_none(self):
        self.assertIsNone(statute_labels(r"D:\code\general_laws\3_estacode.pdf")["year"])

    def test_a_judgment_filename_is_not_read_as_a_statute(self):
        self.assertIsNone(statute_labels(r"D:\corpus\SC\judge\SCP_C.A.634_2018_11-09-2025.pdf"))
        self.assertIsNone(statute_labels(r"D:\corpus\LHC\2023LHC5924.pdf"))


class RomanNumeralTests(unittest.TestCase):
    """Enactment numbers are Roman: "ORDINANCE NO. IX of 1971"."""

    def test_ordinary_numerals(self):
        self.assertEqual(_roman_to_int("IX"), 9)
        self.assertEqual(_roman_to_int("XLVII"), 47)
        self.assertEqual(_roman_to_int("MCMXCIV"), 1994)

    def test_a_non_numeral_is_not_forced(self):
        self.assertIsNone(_roman_to_int("1971"))
        self.assertIsNone(_roman_to_int(""))


class HeadingTitleTests(unittest.TestCase):
    """A section heading is "12. Title.— body"; the title is not the body."""

    def test_the_body_is_trimmed_at_every_separator_the_corpus_uses(self):
        cases = {
            "Short title, extent and commencement.— (1) This Ordinance": "Short title, extent and commencement",
            "Definitions. In this Ordinance, unless": "Definitions",
            "Interpretation clause.—In this Act": "Interpretation clause",
            "Penalty.-Whoever contravenes": "Penalty",
            "Auction sale.___In the case of a sale": "Auction sale",
            "Power to make rules. The 1[Federal Government] may": "Power to make rules",
        }
        for raw, want in cases.items():
            self.assertEqual(_heading_title(raw), want, raw)

    def test_a_heading_with_no_body_is_left_whole(self):
        self.assertEqual(_heading_title("Punishment for murder"), "Punishment for murder")


def sized(text, size=12.0, **emphasis):
    entry = {"text": text, "size": size}
    if emphasis:
        entry["emphasis"] = emphasis
    return entry


class FootnoteMarkerTests(unittest.TestCase):
    """An amended section prints its footnote marker against the number.

    Every one of these was a real miss: the marker joins the number with
    nothing between, so "11." reads as "211." -- section 11 is lost and a
    section 211 invented, which alone put a 200-wide hole in the Pensions Act
    run.  A flat-string parser cannot tell the two apart; the spans can.
    """

    def test_a_plain_marker_before_an_emphasised_heading(self):
        target = block("211. Exemption of pension", [sized("2"), sized("11. Exemption of pension", bold=True, underline=True)])
        self.assertEqual(_heading_line(target), "11. Exemption of pension")

    def test_a_marker_set_smaller_than_the_number(self):
        # Arms Act 1878: the marker is 8pt against the heading's 12pt, and is
        # emphasised exactly like the number, so only the size separates them.
        target = block("719. For breach of sections", [
            sized("7", 8.04, bold=True, underline=True),
            sized("19.", 12.0, bold=True, underline=True),
            sized(" For breach of sections", 12.0, bold=True),
        ])
        self.assertEqual(_heading_line(target), "19. For breach of sections")

    def test_a_marker_that_opens_an_amendment_bracket(self):
        # Dangerous Cargoes Act 1953: "1[" before "2. Definitions".
        target = block("1[2. Definitions", [sized("1"), sized("["), sized("2. Definitions", bold=True, underline=True)])
        self.assertEqual(_heading_line(target), "2. Definitions")

    def test_an_ordinary_heading_is_untouched(self):
        target = block("11. Exemption of pension", [sized("11. Exemption of pension", bold=True)])
        self.assertEqual(_heading_line(target), "11. Exemption of pension")

    def test_a_number_merely_split_across_spans_is_not_stripped(self):
        # Nothing distinguishes the "1" from the "2" here -- same size, same
        # weight, no bracket -- so stripping would turn section 12 into 2.
        target = block("12. Title", [sized("1", 12.0, bold=True), sized("2. Title", 12.0, bold=True)])
        self.assertEqual(_heading_line(target), "12. Title")

    def test_a_long_plain_lead_is_not_mistaken_for_a_marker(self):
        target = block("See section 4 above. 11. Not a heading",
                       [sized("See section 4 above. "), sized("11. Not a heading", bold=True)])
        self.assertEqual(_heading_line(target), "See section 4 above. 11. Not a heading")

    def test_a_block_with_no_spans_keeps_its_line(self):
        self.assertEqual(_heading_line({"text": "5. Plain", "spans": []}), "5. Plain")

    def test_blank_spans_do_not_stand_in_for_the_heading(self):
        # Sale of Goods 1930: section 59's block opens with three 0.96pt blank
        # spans, so the styling check looked at whitespace, found none, and
        # dropped a real section.
        target = block("59. Remedy for breach of warranty", [
            sized(" ", 0.96), sized(" ", 0.96),
            sized("59. Remedy for breach of warranty", 12.0, bold=True, underline=True),
        ])
        self.assertEqual(_heading_line(target), "59. Remedy for breach of warranty")


class StructureTests(unittest.TestCase):
    def statute(self):
        return [page(1, [
            block("CHAPTER II", [span("CHAPTER II", bold=True)]),
            block("1. Short title.— This Act may be called",
                  [span("1. Short title.", bold=True, underline=True)]),
            block("2. Definitions. In this Act", [span("2. Definitions.", bold=True, underline=True)]),
            block("(2) It extends to the whole of Pakistan.", [span("(2) It extends")]),
            block("THE SCHEDULE", [span("THE SCHEDULE", bold=True)]),
        ])]

    def test_sections_chapters_and_schedules_are_found(self):
        kinds = [item["kind"] for item in detect_structure(self.statute())]
        self.assertEqual(kinds, ["chapter", "section", "definitions", "schedule"])

    def test_a_definitions_section_is_named_as_one(self):
        # "What does X mean" is the most-asked question of a statute.
        found = detect_structure(self.statute())
        self.assertEqual([f["number"] for f in found if f["kind"] == "definitions"], ["2"])

    def test_a_sub_section_is_not_a_new_section(self):
        # "(2) It extends..." is inside section 1, not section 2.
        numbers = [f["number"] for f in detect_structure(self.statute()) if f["kind"] in {"section", "definitions"}]
        self.assertEqual(numbers, ["1", "2"])

    def test_a_number_opening_an_amendment_bracket_is_still_a_section(self):
        # Negotiable Instruments 1881: "5[3. Interpretation-clause".  Stripping
        # the marker leaves "[3." and the bracket belongs to the footnote.
        pages = [page(9, [block("5[3. Interpretation-clause.— In this Act", [
            sized("5", 8.04, bold=True, underline=True),
            sized("[3. Interpretation-clause.—", 12.0, bold=True, underline=True),
        ])])]
        found = detect_structure(pages)
        self.assertEqual([(f["kind"], f["number"]) for f in found], [("definitions", "3")])

    def test_a_symbol_footnote_marker_is_not_part_of_the_heading(self):
        # Maritime Security Agency 1994: "*11." -- the asterisk is set in the
        # same run as the number, so no styling separates them and only the
        # character itself can.
        pages = [page(7, [block("*11. Other functions of the Agency", [
            sized("*11. Other functions of the Agency", 12.0, bold=True, underline=True),
        ])])]
        self.assertEqual([f["number"] for f in detect_structure(pages)], ["11"])

    def test_a_block_whose_first_spans_are_blank_is_still_read(self):
        pages = [page(1, [block("59. Remedy for breach", [
            sized(" ", 0.96), sized("59. Remedy for breach", 12.0, bold=True, underline=True),
        ])])]
        self.assertEqual([f["number"] for f in detect_structure(pages)], ["59"])

    def test_prose_that_merely_starts_with_a_number_is_not_a_section(self):
        # Without the emphasis check every numbered list item becomes a section.
        pages = [page(1, [block("3. is the number of copies required.", [span("3. is the number")])])]
        self.assertEqual(detect_structure(pages), [])

    def test_each_marker_names_where_it_is(self):
        # A consumer must be able to go from "section 2" to the region of the
        # page that states it.
        found = detect_structure(self.statute())[1]
        self.assertEqual(found["page"], 1)
        self.assertEqual(len(found["bbox"]), 4)
        self.assertTrue(found["block_id"])

    def test_running_headers_are_not_scanned(self):
        pages = [page(1, [dict(block("1. Short title", [span("1. Short title", bold=True)]), type="header")])]
        self.assertEqual(detect_structure(pages), [])

    def test_a_heading_starting_part_way_through_a_block_is_found(self):
        # Section 28 of the Co-operative Societies Act follows section 27's
        # closing words in the same text flow, so it is not the block's first
        # line.  Missed, its text is folded into section 27 and a reader asking
        # for section 28 is handed the wrong law.
        pages = [page(9, [block(
            "(3) any endorsement upon any debenture. 1[28. Power to exempt from income tax.", [
                sized("(3) any endorsement upon any debenture. ", 12.0),
                sized("1", 8.04, bold=True, underline=True),
                sized("[28. Power to exempt from income tax.", 12.0, bold=True, underline=True),
            ])])]
        found = detect_structure(pages)
        self.assertEqual([(f["kind"], f["number"]) for f in found], [("section", "28")])

    def test_the_box_covers_the_heading_run_not_the_paragraph_before_it(self):
        pages = [page(9, [block(
            "closing words. 1[28. Power to exempt.", [
                dict(sized("closing words. ", 12.0), bbox=[50.0, 100.0, 200.0, 112.0]),
                dict(sized("1", 8.04, bold=True, underline=True), bbox=[200.0, 100.0, 205.0, 112.0]),
                dict(sized("[28. Power to exempt.", 12.0, bold=True, underline=True),
                     bbox=[205.0, 100.0, 400.0, 112.0]),
            ])])]
        self.assertEqual(detect_structure(pages)[0]["bbox"], [200.0, 100.0, 400.0, 112.0])

    def test_two_headings_on_one_line_are_both_taken(self):
        # The Agricultural Produce Cess Act prints "9." with nothing after it --
        # the section was omitted -- and section 10 begins on the same line.
        pages = [page(3, [block(
            "9. 4[10. Power to make rules.", [
                sized("9.", 12.0, bold=True, underline=True),
                sized("4", 8.04),
                sized("[10. Power to make rules.", 12.0, bold=True, underline=True),
            ])])]
        self.assertEqual([f["number"] for f in detect_structure(pages)], ["9", "10"])

    def test_numbered_entries_inside_a_schedule_are_not_sections(self):
        # The Provident Funds Act schedules 42 named banks.  Counted as
        # sections they both invented members of the run and punched 11 holes
        # in it, because the Act itself stops well before 42.
        pages = [page(1, [
            block("1. Short title.", [span("1. Short title.", bold=True, underline=True)]),
            block("THE SCHEDULE", [span("THE SCHEDULE", bold=True)]),
            block("12. The Agricultural Development Finance Corporation.",
                  [span("12. The Agricultural", bold=True, underline=True)]),
        ])]
        found = detect_structure(pages)
        self.assertEqual([(f["kind"], f["number"]) for f in found],
                         [("section", "1"), ("schedule", None), ("schedule_item", "12")])
        self.assertEqual(section_sequence(found)["highest"], 1)

    def test_a_schedule_announced_by_the_contents_page_does_not_open_one(self):
        # A schedule cannot precede section 1 of the body, so one that does is
        # the table of contents listing it.  Acting on that listing read every
        # section of the Commercial Documents Evidence Act as a schedule entry.
        pages = [page(1, [
            block("THE SCHEDULE", [span("THE SCHEDULE", bold=True)]),
            block("1. Short title.", [span("1. Short title.", bold=True, underline=True)]),
            block("2. Definitions.", [span("2. Definitions.", bold=True, underline=True)]),
        ])]
        kinds = [f["kind"] for f in detect_structure(pages)]
        self.assertEqual(kinds, ["schedule", "section", "definitions"])

    def test_a_chapter_after_a_schedule_returns_to_sections(self):
        pages = [page(1, [
            block("1. Short title.", [span("1. Short title.", bold=True, underline=True)]),
            block("THE SCHEDULE", [span("THE SCHEDULE", bold=True)]),
            block("CHAPTER II", [span("CHAPTER II", bold=True)]),
            block("2. Extent.", [span("2. Extent.", bold=True, underline=True)]),
        ])]
        found = detect_structure(pages)
        self.assertEqual([f["kind"] for f in found], ["section", "schedule", "chapter", "section"])


class SequenceTests(unittest.TestCase):
    """A statute numbers its sections 1..N; a hole means one was missed."""

    def items(self, numbers):
        return [{"kind": "section", "number": n} for n in numbers]

    def test_a_complete_run_reports_complete(self):
        got = section_sequence(self.items(["1", "2", "3"]))
        self.assertTrue(got["complete"])
        self.assertEqual(got["missing"], [])

    def test_a_hole_is_reported(self):
        got = section_sequence(self.items(["1", "2", "4"]))
        self.assertFalse(got["complete"])
        self.assertEqual(got["missing"], [3])

    def test_a_lettered_insertion_does_not_advance_the_run(self):
        # 3A sits beside section 3; it is not a section 4.
        self.assertTrue(section_sequence(self.items(["1", "2", "3", "3A"]))["complete"])

    def test_a_section_repeated_in_the_contents_is_counted_once(self):
        got = section_sequence(self.items(["1", "2", "1", "2", "3"]))
        self.assertEqual(got["sections"], 3)
        self.assertTrue(got["complete"])

    def test_a_statute_with_no_numbered_sections_says_so(self):
        self.assertIsNone(section_sequence([])["complete"])


class MetadataTests(unittest.TestCase):
    HEAD = (
        "THE ASIAN DEVELOPMENT BANK ORDINANCE, 1971\n"
        "ORDINANCE NO. IX of 1971\n"
        "[16th April, 1971]\n"
        "An Ordinance to implement the international agreement\n"
        "WHEREAS PAKISTAN is a signatory to the Agreement\n"
    )

    def test_the_statute_names_itself(self):
        got = extract_statute_metadata(self.HEAD, [])
        self.assertEqual(got["title"], "THE ASIAN DEVELOPMENT BANK ORDINANCE, 1971")
        self.assertEqual(got["statute_kind"], "Ordinance")
        self.assertEqual(got["act_number"], "IX")
        self.assertEqual(got["act_number_value"], 9)
        self.assertEqual(got["act_year"], "1971")
        self.assertEqual(got["commencement_date"], "16th April, 1971")
        self.assertTrue(got["long_title"].startswith("An Ordinance to implement"))
        self.assertTrue(got["has_preamble"])

    def test_the_section_run_is_reported_with_the_metadata(self):
        got = extract_statute_metadata(self.HEAD, [{"kind": "section", "number": n} for n in ("1", "2", "4")])
        self.assertEqual(got["section_count"], 3)
        self.assertFalse(got["sections_complete"])
        self.assertEqual(got["missing_sections"], [3])

    def test_a_title_set_over_two_centred_lines_is_read_as_one(self):
        # Requiring the title on a single line lost one in six of them.
        head = ("Page 1 of 31\nTHE CHEMICAL WEAPONS CONVENTION IMPLEMENTATION\n"
                "ORDINANCE, 2000\nCONTENTS\n")
        self.assertEqual(extract_statute_metadata(head, [])["title"],
                         "THE CHEMICAL WEAPONS CONVENTION IMPLEMENTATION ORDINANCE, 2000")

    def test_the_kind_is_read_from_the_title_when_no_number_line_exists(self):
        head = "THE PAKISTAN AERONAUTICAL COMPLEX BOARD\nORDINANCE, 2000\nCONTENTS\n"
        self.assertEqual(extract_statute_metadata(head, [])["statute_kind"], "Ordinance")

    def test_the_enactment_number_is_found_even_when_set_mid_line(self):
        # 8 of the 24 statutes reported as having no number did print one; it
        # was simply not on a line of its own.
        head = "THE BANKERS BOOKS EVIDENCE ACT, 1891\nA Regulation No. I of 1891 to provide\n"
        self.assertEqual(extract_statute_metadata(head, [])["act_number"], "I")

    def test_a_number_belonging_to_another_act_is_not_taken(self):
        # Once the body starts, the same words introduce other statutes, so the
        # unanchored search stops at the end of the opening block.
        head = "THE W ACT, 1990\n" + "x" * 2100 + "\nunder the Companies Act No. VII of 1913\n"
        self.assertIsNone(extract_statute_metadata(head, [])["act_number"])

    def test_a_footnote_marker_glued_to_the_word_does_not_hide_the_number(self):
        # "1ACT No. LXIV OF 1975" is the ordinary way this line is printed --
        # the 1 is a footnote marker.  A word boundary does not exist between
        # "1" and "A", so the earlier pattern skipped 7 of 60 statutes here.
        head = "THE MALARIA ERADICATION BOARD (REPEAL) ACT, 1975\n1ACT No. LXIV OF 1975\n"
        self.assertEqual(extract_statute_metadata(head, [])["act_number"], "LXIV")

    def test_a_number_inside_a_longer_word_is_still_not_taken(self):
        # The lookbehind only permits digits before the keyword; letters, which
        # would mean the match sits inside another word, still block it.
        head = "THE X ACT, 1990\nENACT No. IV of 1990\n"
        self.assertIsNone(extract_statute_metadata(head, [])["act_number"])

    def test_non_breaking_spaces_do_not_hide_the_number(self):
        head = "THE Z ACT, 1974\nordinance\u00a0No\u00a0LVII\u00a0of\u00a01974\n"
        self.assertEqual(extract_statute_metadata(head, [])["act_number"], "LVII")

    def test_a_repealed_law_is_reported_as_repealed(self):
        # The corpus publishes these as a stub with no content.  That is a
        # state of the law, not a parse failure, and is more useful said than
        # left as every field missing.
        got = extract_statute_metadata("THIS LAW HAS BEEN REPEALED\n", [])
        self.assertTrue(got["repealed"])
        self.assertIsNone(got["title"])

    def test_an_ordinary_statute_is_not_marked_repealed(self):
        self.assertFalse(extract_statute_metadata(self.HEAD, [])["repealed"])

    def test_a_document_stating_none_of_it_invents_none_of_it(self):
        got = extract_statute_metadata("Some unrelated text.", [])
        self.assertIsNone(got["title"])
        self.assertIsNone(got["act_number"])
        self.assertIsNone(got["commencement_date"])
        self.assertFalse(got["has_preamble"])


if __name__ == "__main__":
    unittest.main()
