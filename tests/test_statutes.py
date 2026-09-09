import unittest
from pathlib import Path

from specter.specter_parser import _carry_footnote_run, _classify
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

    def test_the_number_itself_is_not_stripped_as_a_marker(self):
        # Public Private Partnership Authority Act 2017: the number is set
        # plain and only the title emphasised, so the number matched the marker
        # shape.  Stripping it left ". Chief Executive Officer", which is not a
        # heading at all, and section 7 vanished from the run.
        target = block("7. Chief Executive Officer. (1) The Federal Government", [
            sized("7", 12.0),
            sized(".  Chief Executive Officer", 12.0, bold=True, underline=True),
            sized(". (1) The Federal Government", 12.0),
        ])
        self.assertEqual(_heading_line(target),
                         "7. Chief Executive Officer. (1) The Federal Government")

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

    def test_a_chapter_inside_a_schedule_does_not_end_the_schedule(self):
        """A schedule may carry structure of its own.

        The Carriage by Air Act reproduces the Montreal Convention as its
        Fourth Schedule, chapters and all, and ending the schedule at
        "CHAPTER I" put the Convention's 106 numbered Articles back in
        collision with the Act's own sections.

        This replaces a test asserting the opposite. Measured over the 841
        parsed statutes, Carriage by Air is the *only* document where a chapter
        follows a schedule at all, and there the numbering restarts -- no
        statute in the corpus returns to its own sections that way.
        """
        pages = [page(1, [
            block("1. Short title.", [span("1. Short title.", bold=True, underline=True)]),
            block("THE SCHEDULE", [span("THE SCHEDULE", bold=True)]),
            block("CHAPTER II", [span("CHAPTER II", bold=True)]),
            block("2. Extent.", [span("2. Extent.", bold=True, underline=True)]),
        ])]
        found = detect_structure(pages)
        self.assertEqual([f["kind"] for f in found],
                         ["section", "schedule", "chapter", "schedule_item"])


class BareNumberRecoveryTests(unittest.TestCase):
    """A section number set alone, with no full stop, and typed as a footer.

    The National Rahmatul-lil-Aalameen Authority Act prints "3" on its own and
    the heading on the next line, so the number reads as a page number and the
    section vanished from the run.
    """

    def pages(self, number_text):
        return [{"page_number": 4, "blocks": [
            {"id": "b0", "type": "text", "text": "1. Short title.", "bbox": [0, 0, 10, 10],
             "spans": [span("1. Short title.", bold=True)]},
            {"id": "b1", "type": "text", "text": "2. Definitions.", "bbox": [0, 10, 10, 20],
             "spans": [span("2. Definitions.", bold=True)]},
            {"id": "b2", "type": "footer", "text": number_text, "bbox": [0, 20, 10, 30],
             "spans": [span(number_text)]},
            {"id": "b3", "type": "text", "text": "Establishment of the Authority. (1) There shall be",
             "bbox": [0, 30, 10, 40],
             "spans": [span("Establishment of the Authority", bold=True, underline=True),
                       span(". (1) There shall be")]},
            {"id": "b4", "type": "text", "text": "4. Advisory Board.", "bbox": [0, 40, 10, 50],
             "spans": [span("4. Advisory Board.", bold=True)]},
        ]}]

    def test_the_hole_is_filled_from_the_bare_number(self):
        structure = detect_structure(self.pages("3"))
        self.assertEqual(section_sequence(structure)["missing"], [])
        recovered = [s for s in structure if s["number"] == "3"]
        self.assertEqual(recovered[0]["title"], "Establishment of the Authority")

    def test_a_page_number_is_not_read_as_a_section(self):
        # 9 is outside the detected run, so it is never looked for.
        structure = detect_structure(self.pages("9"))
        self.assertNotIn("9", [s.get("number") for s in structure])

    def test_a_number_followed_by_plain_prose_is_left_alone(self):
        pages = self.pages("3")
        pages[0]["blocks"][3]["spans"] = [span("Establishment of the Authority. (1) There shall be")]
        structure = detect_structure(pages)
        self.assertEqual(section_sequence(structure)["missing"], [3])


class FootnoteTypingTests(unittest.TestCase):
    """An amendment note is not the law, and must not read as body text.

    Header and footer detection works by repetition across pages; a footnote is
    different on every page, so 111 of the Penal Code's amendment notes were
    typed as body text -- 29,942 characters that a retrieval system would have
    quoted as statute.
    """

    def block(self, text, size=8.0, y0=700.0, kind="text"):
        return {"text": text, "bbox": [72.0, y0, 500.0, y0 + 10.0],
                "font_sizes": [size], "type": kind, "language": "en",
                "contains_rtl": False}

    def classify(self, text, size=8.0, y0=700.0):
        return _classify(self.block(text, size, y0), set(), set(),
                         842.0, 595.0, 12.0, [])[0]

    def test_an_amendment_note_is_a_footnote(self):
        for text in ("1Subs. by the Law Reforms Ordinance, 1972 (12 of 1972), s. 2",
                     "2S.489E.ins. by the Indian Penal Code(Amdt.) Act, 1943",
                     "10Subs. by the Indian Penal Code Amdt. Act, 1898 (4 of 1898), s. 2",
                     "3Added by the West Pakistan Ordinance No. XIII of 1959, s. 3."):
            self.assertEqual(self.classify(text), "footnote", text)

    def test_body_text_of_the_same_shape_is_not(self):
        # Same wording, body size, high on the page: this is the statute.
        self.assertNotEqual(
            self.classify("1Subs. by the Law Reforms Ordinance, 1972", size=12.0), "footnote")
        self.assertNotEqual(
            self.classify("1Subs. by the Law Reforms Ordinance, 1972", y0=120.0), "footnote")

    def test_a_contents_entry_is_not_a_footnote(self):
        # Small type low on the page, but no marker and no amendment wording --
        # this is the contents list, and typing it as a footnote would drop it.
        for text in ("Penalty and procedure, etc.", "Public servant.", "Any other toe"):
            self.assertNotEqual(self.classify(text), "footnote", text)

    def test_a_note_carries_on_into_the_block_it_wraps_into(self):
        blocks = [self.block("1Subs. by A. O., 1949, Sch., for", y0=700.0, kind="footnote"),
                  self.block("the Province of Baluchistan.", y0=712.0),
                  self.block("302. Punishment of qatl-i-amd.", size=12.0, y0=300.0)]
        _carry_footnote_run(blocks, 842.0, 12.0)
        self.assertEqual([b["type"] for b in blocks], ["footnote", "footnote", "text"])

    def test_a_footnote_is_never_read_as_a_section(self):
        pages = [{"page_number": 1, "blocks": [
            {"id": "b0", "type": "text", "text": "1. Short title.", "bbox": [0, 0, 10, 10],
             "spans": [span("1. Short title.", bold=True)]},
            {"id": "b1", "type": "footnote", "text": "2Subs. by Act XIV of 2011, s.63.",
             "bbox": [0, 700, 10, 710], "spans": [span("2Subs. by Act XIV of 2011, s.63.")]},
        ]}]
        self.assertEqual([s["number"] for s in detect_structure(pages)], ["1"])


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
        # The contents list is cut before this is reached, so a run that still
        # begins twice is either a compendium or an index nothing resolved.
        # Neither has one run to be complete, and saying so beats asserting it.
        self.assertIsNone(got["complete"])

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


class FrontMatterTests(unittest.TestCase):
    """A contents listing numbers from 1 exactly as the body does.

    Read as sections, its entries duplicate every real one and carry a dotted
    leader instead of the law -- 91 of them in the Sales Tax Act.
    """

    def page(self, blocks):
        return [{"page_number": 1, "blocks": blocks}]

    def block(self, block_id, text, kind="heading", bold=True):
        return {"id": block_id, "type": kind, "text": text,
                "bbox": [72.0, 100.0, 500.0, 120.0],
                "spans": [{"text": text, "size": 11.0, "bbox": [72.0, 100.0, 500.0, 120.0],
                           "emphasis": {"bold": bold, "underline": False}}]}

    def test_the_contents_list_is_not_the_statute(self):
        pages = self.page([
            self.block("b0", "Contents"),
            self.block("b1", "1. Short title, extent and commencement. ....... 7"),
            self.block("b2", "2. Definitions. ....... 8"),
            self.block("b3", "An Act to consolidate the law relating to the levy of a tax"),
            self.block("b4", "1. Short title, extent and commencement. This Act may be called"),
            self.block("b5", "2. Definitions. In this Act, unless there is anything repugnant"),
        ])
        found = detect_structure(pages)
        sections = [entry for entry in found if entry["kind"] in {"section", "definitions"}]
        self.assertEqual([entry["block_id"] for entry in sections], ["b4", "b5"])

    def test_the_cut_never_removes_a_section_printed_only_once(self):
        """Section 1 recites the statute's own name.

        "This Ordinance may be called the Islamabad Rent Restriction Ordinance,
        2001." reads as a title, so the body was anchored *after* section 1 and
        the section was cut away with the front matter -- in 77 statutes. A
        contents list is a duplicate of the body, so a cut that loses a number
        outright is not a contents list and is not taken.
        """
        pages = self.page([
            self.block("b0", "WHEREAS it is expedient to restrict the increase of rent;"),
            self.block("b1", "1. Short title, extent and commencement.— (1) This Ordinance may be called"),
            self.block("b2", "the Islamabad Rent Restriction Ordinance, 2001."),
            self.block("b3", "2. Definitions. In this Ordinance, unless the context otherwise requires"),
        ])
        numbers = [e["number"] for e in detect_structure(pages)
                   if e["kind"] in {"section", "definitions"}]
        self.assertEqual(numbers, ["1", "2"])

    def test_a_statute_with_no_enacting_formula_keeps_every_section(self):
        pages = self.page([
            self.block("b0", "1. Short title. This Order may be called the Order of 1979."),
            self.block("b1", "2. Definitions. In this Order, unless the context otherwise requires"),
        ])
        sections = [e for e in detect_structure(pages) if e["kind"] in {"section", "definitions"}]
        self.assertEqual(len(sections), 2)


class BackReferenceTests(unittest.TestCase):
    """A cross-reference that opens a block reads as a section heading.

    The Penal Code prints "127. Receiving property taken by war or depredation
    mentioned in sections 125 and" / "126. Whoever receives any property...".
    The "126." completes the reference; read as a heading it both invented a
    second section 126 and took section 127's text away from it.
    """

    def page(self, blocks):
        return [{"page_number": 1, "blocks": blocks}]

    def block(self, block_id, text):
        return {"id": block_id, "type": "list", "text": text,
                "bbox": [72.0, 100.0, 500.0, 120.0],
                "spans": [{"text": text, "size": 11.0, "bbox": [72.0, 100.0, 500.0, 120.0],
                           "emphasis": {"bold": True, "underline": False}}]}

    def test_a_number_that_goes_backwards_is_not_a_new_section(self):
        pages = self.page([
            self.block("b0", "126. Committing depredation on territories of Power at peace with Pakistan."),
            self.block("b1", "127. Receiving property taken by war or depredation mentioned in sections 125 and"),
            self.block("b2", "126. Whoever receives any property knowing the same to have been taken"),
        ])
        sections = [e for e in detect_structure(pages) if e["kind"] in {"section", "definitions"}]
        self.assertEqual([e["block_id"] for e in sections], ["b0", "b1"])

    def test_a_number_not_seen_before_is_kept_even_out_of_order(self):
        # Only a repeat is a cross-reference; a genuine section printed out of
        # order must not be thrown away.
        pages = self.page([
            self.block("b0", "126. Committing depredation on territories."),
            self.block("b1", "128. Public servant voluntarily allowing prisoner to escape."),
            self.block("b2", "127. Receiving property taken by war."),
        ])
        sections = [e for e in detect_structure(pages) if e["kind"] in {"section", "definitions"}]
        self.assertEqual(len(sections), 3)


class CompendiumTests(unittest.TestCase):
    """Estacode is 1,044 pages of separate rule-sets, each numbered from 1.

    Read as one Act it reported 1,179 sections in which "section 1" resolved to
    62 different texts.  A repeated section 1 is what says the document is a
    compendium rather than a statute, and a compendium has no section run to
    check or to cite.
    """

    def page(self, blocks):
        return [{"page_number": 1, "blocks": blocks}]

    def block(self, block_id, text):
        return {"id": block_id, "type": "list", "text": text,
                "bbox": [72.0, 100.0, 500.0, 120.0],
                "spans": [{"text": text, "size": 11.0, "bbox": [72.0, 100.0, 500.0, 120.0],
                           "emphasis": {"bold": True, "underline": False}}]}

    def test_a_repeated_section_one_is_not_a_single_act(self):
        blocks = []
        for run in range(3):
            for number in (1, 2, 3):
                blocks.append(self.block(f"b{run}_{number}",
                                         f"{number}. Short title and commencement of rules {run}."))
        structure = detect_structure(self.page(blocks))
        self.assertEqual(section_sequence(structure)["restarts"], 3)
        self.assertIsNone(section_sequence(structure)["complete"])

    def test_a_statute_numbered_once_has_a_checkable_run(self):
        blocks = [self.block(f"b{n}", f"{n}. A section of the Act.") for n in (1, 2, 3)]
        sequence = section_sequence(detect_structure(self.page(blocks)))
        self.assertEqual(sequence["restarts"], 1)
        self.assertTrue(sequence["complete"])


class TitleWithoutAYearTests(unittest.TestCase):
    """Not every statute prints a year after its name.

    Requiring one left the Penal Code with no title at all, so every section of
    it was labelled with the filename slug -- "pakistan penal code ppc1860
    under review".
    """

    def test_a_code_that_prints_no_year_still_has_a_title(self):
        got = extract_statute_metadata("THE PAKISTAN PENAL CODE\n1Act No. XLV OF 1860\n")
        self.assertEqual(got["title"], "THE PAKISTAN PENAL CODE")

    def test_a_title_that_ends_in_a_full_stop_is_read(self):
        got = extract_statute_metadata(
            "THE FEDERAL PUBLIC SERVICE COMMISSION\nORDINANCE, 1977.\nCONTENTS\n")
        self.assertEqual(got["title"], "THE FEDERAL PUBLIC SERVICE COMMISSION ORDINANCE, 1977")

    def test_the_year_is_still_kept_where_it_is_printed(self):
        got = extract_statute_metadata("THE CANTONMENTS ACT, 1924\n")
        self.assertEqual(got["title"], "THE CANTONMENTS ACT, 1924")


class ScheduleHeadingTests(unittest.TestCase):
    """A schedule numbers its own entries from 1, so missing its heading puts
    every one of them in collision with a real section.

    The Stamp Act heads its duty table "1[SCHEDULE 1" -- the amendment marker
    glued to the front, and the number printed after the word rather than
    before it. Neither was allowed, so 123 of its 144 "sections" were schedule
    entries, and 15 other statutes failed the same way.
    """

    def page(self, blocks):
        return [{"page_number": 1, "blocks": blocks}]

    def block(self, block_id, text):
        return {"id": block_id, "type": "list", "text": text,
                "bbox": [72.0, 100.0, 500.0, 120.0],
                "spans": [{"text": text, "size": 11.0, "bbox": [72.0, 100.0, 500.0, 120.0],
                           "emphasis": {"bold": True, "underline": False}}]}

    def kinds(self, pages):
        return [(entry["kind"], entry.get("number")) for entry in detect_structure(pages)]

    def test_a_schedule_numbered_after_the_word_is_found(self):
        got = self.kinds(self.page([
            self.block("b0", "1. Short title. This Act may be called the Stamp Act, 1899."),
            self.block("b1", "1[SCHEDULE 1"),
            self.block("b2", "1. ACKNOWLEDGMENT of a debt exceeding twenty rupees in amount."),
        ]))
        self.assertIn(("schedule", None), got)
        self.assertEqual([k for k, _ in got].count("section"), 1)
        self.assertIn(("schedule_item", "1"), got)

    def test_a_schedule_heading_naming_its_calling_section_is_found(self):
        # "_____________ THE SCHEDULE (See section 41)" -- the rule printed above
        # the heading comes back inside the same block, and the cross-reference
        # follows the word.  The Quaid-e-Azam University Act's First Statutes,
        # which number from 1, sat under it.
        got = self.kinds(self.page([
            self.block("b0", "1. Short title. This Act may be called the Act."),
            self.block("b1", "_____________ THE SCHEDULE (See section 41)"),
            self.block("b2", "1. The Faculties. The University shall include the following Faculties."),
        ]))
        self.assertIn(("schedule", None), got)
        self.assertIn(("schedule_item", "1"), got)
        self.assertEqual([k for k, _ in got].count("section"), 1)

    def test_a_bracketed_cross_reference_is_allowed_too(self):
        got = self.kinds(self.page([
            self.block("b0", "1. Short title. This Act may be called the Act."),
            self.block("b1", "SCHEDULE [see SECTION 5]"),
            self.block("b2", "1. An entry of the schedule."),
        ]))
        self.assertIn(("schedule", None), got)
        self.assertIn(("schedule_item", "1"), got)

    def test_the_ordinal_before_the_word_still_works(self):
        got = self.kinds(self.page([
            self.block("b0", "1. Short title. This Act may be called the Act."),
            self.block("b1", "THE FIRST SCHEDULE"),
            self.block("b2", "1. An entry of the schedule."),
        ]))
        self.assertIn(("schedule", None), got)
        self.assertIn(("schedule_item", "1"), got)

    def test_a_sentence_mentioning_a_schedule_is_not_one(self):
        got = self.kinds(self.page([
            self.block("b0", "1. Short title. Nothing in the Schedule shall affect this Act."),
        ]))
        self.assertEqual(got, [("section", "1")])
