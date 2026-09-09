"""Section-addressable statutes: the text a lawyer cites, and nothing else.

Every contamination pinned here was found in a real section of the Penal Code.
"""

import unittest

from specter.statute_sections import _short_form, build, section_bodies


def block(block_id, text, kind="text", page=1):
    return {"id": block_id, "type": kind, "text": text,
            "bbox": [72.0, 100.0, 500.0, 120.0], "contains_rtl": False}


def marker(kind, number, title, block_id, page=1):
    return {"kind": kind, "number": number, "title": title,
            "block_id": block_id, "page": page}


class SectionTextTests(unittest.TestCase):
    def document(self, blocks):
        return {"pages": [{"page_number": 1, "blocks": blocks}]}

    def test_a_section_runs_to_the_next_section(self):
        doc = self.document([
            block("b0", "34. Acts done by several persons."),
            block("b1", "When a criminal act is done by several persons."),
            block("b2", "35. Whenever an act is criminal."),
        ])
        structure = [marker("section", "34", "Acts done by several persons", "b0"),
                     marker("section", "35", "Whenever an act is criminal", "b2")]
        sections = section_bodies(doc, structure)
        self.assertEqual(len(sections), 2)
        self.assertNotIn("35.", sections[0]["text"])
        self.assertEqual(sections[0]["block_ids"], ["b0", "b1"])

    def test_a_page_footer_is_not_part_of_the_law(self):
        # "Page 36 of 179" sat inside section 34.
        doc = self.document([
            block("b0", "34. Acts done by several persons."),
            block("b1", "Page 36 of 179", kind="footer"),
            block("b2", "each is liable for that act."),
        ])
        text = section_bodies(doc, [marker("section", "34", "Acts", "b0")])[0]["text"]
        self.assertNotIn("Page 36", text)
        self.assertIn("each is liable", text)

    def test_an_amendment_footnote_is_not_part_of_the_law(self):
        # "1Subs. by the Law Reforms Ordinance, 1972" sat inside section 511.
        doc = self.document([
            block("b0", "511. Punishment for attempting to commit offences."),
            block("b1", "1Subs. by the Law Reforms Ordinance, 1972, s.2.", kind="footnote"),
        ])
        text = section_bodies(doc, [marker("section", "511", "Punishment", "b0")])[0]["text"]
        self.assertNotIn("Subs. by", text)

    def test_a_chapter_heading_ends_the_section_before_it(self):
        # "Of Fraudulent Deeds and Dispositions of Property" ran on into s.420.
        doc = self.document([
            block("b0", "420. Cheating and dishonestly inducing delivery."),
            block("b1", "Of Fraudulent Deeds and Dispositions of Property"),
            block("b2", "421. Dishonest removal of property."),
        ])
        structure = [marker("section", "420", "Cheating", "b0"),
                     marker("part", "II", "Of Fraudulent Deeds", "b1"),
                     marker("section", "421", "Dishonest removal", "b2")]
        sections = section_bodies(doc, structure)
        self.assertNotIn("Fraudulent Deeds", sections[0]["text"])

    def test_the_section_carries_the_chapter_it_sits_under(self):
        doc = self.document([
            block("b0", "CHAPTER V"),
            block("b1", "109. Punishment of abetment."),
        ])
        structure = [marker("chapter", "V", "", "b0"),
                     marker("section", "109", "Punishment of abetment", "b1")]
        self.assertEqual(section_bodies(doc, structure)[0]["chapter"], "V")

    def test_the_heading_is_recovered_whole_from_the_text(self):
        # The marker's own title stops at the line the number sits on; the
        # heading wraps onto the next block.
        doc = self.document([
            block("b0", "149. Every member of unlawful assembly guilty of offence committed in prosecution of"),
            block("b1", "common object. If an offence is committed by any member."),
        ])
        structure = [marker("section", "149", "Every member of unlawful assembly guilty of "
                            "offence committed in prosecution of", "b0")]
        section = section_bodies(doc, structure)[0]
        self.assertTrue(section["heading"].endswith("common object"), section["heading"])
        self.assertFalse(section["text"].startswith("149."))


class SeparatorBeforeHeadingTests(unittest.TestCase):
    def test_a_separator_printed_before_the_heading_does_not_hide_it(self):
        # The Penal Code prints "371A.__ Selling person for purposes of
        # prostitution, etc." -- separator first -- and the heading reader
        # stopped on it and returned nothing at all.
        doc = {"pages": [{"page_number": 1, "blocks": [
            block("b0", "371A.__ Selling person for purposes of prostitution, etc. "
                        "Whoever sells, lets to hire, or otherwise disposes of any person"),
        ]}]}
        section = section_bodies(doc, [marker("section", "371A", "", "b0")])[0]
        self.assertEqual(section["heading"], "Selling person for purposes of prostitution, etc")


class SameSizeFootnoteTests(unittest.TestCase):
    """A statute set at 7pt prints its footnotes at 7pt too.

    The House Building Finance Corporation Act gives the size rule nothing to
    work with, so 14 of its sections ended with the amendment note that follows
    them.  The note still opens with its marker and its verb, which the law
    never does.
    """

    def test_an_amendment_note_the_size_rule_cannot_see_is_still_not_the_law(self):
        doc = {"pages": [{"page_number": 1, "blocks": [
            block("b0", "2. Definitions. In this Act, “Corporation” means the House Building Finance Corporation."),
            block("b1", "1 Ins. and subs. by the House Building Finance Corporation "
                        "(Amdt.) Ordinance, 1979 (40 of 1979), s. 17."),
        ]}]}
        text = section_bodies(doc, [marker("section", "2", "Definitions", "b0")])[0]["text"]
        self.assertNotIn("Ins. and subs.", text)
        self.assertIn("means the House Building Finance Corporation", text)

    def test_a_repealed_section_that_reads_like_a_note_is_still_a_section(self):
        # "10 & 11. [Amendment of the Sea Customs Act, 1878.] Rep. by the
        # Repealing Act, 1938" opens a section and carries an amendment verb.
        doc = {"pages": [{"page_number": 1, "blocks": [
            block("b0", "10 & 11. [Amendment of the Sea Customs Act, 1878.] "
                        "Rep. by the Repealing Act, 1938 (I of 1938), s. 2."),
        ]}]}
        sections = section_bodies(doc, [marker("section", "10", "", "b0")])
        self.assertEqual(len(sections), 1)
        self.assertIn("Sea Customs Act", sections[0]["text"])

    def test_the_law_may_still_say_that_something_was_substituted(self):
        doc = {"pages": [{"page_number": 1, "blocks": [
            block("b0", "5. Effect. Any reference substituted by this Act shall have effect accordingly."),
        ]}]}
        text = section_bodies(doc, [marker("section", "5", "Effect", "b0")])[0]["text"]
        self.assertIn("substituted by this Act", text)


class ShortFormTests(unittest.TestCase):
    """Judgments cite "302 PPC", so the abbreviation is worth recovering."""

    def test_the_filename_abbreviation_is_read(self):
        self.assertEqual(_short_form("pakistan_penal_code_ppc1860_under_review", "", False), "PPC")
        self.assertEqual(_short_form("code_of_criminal_procedure_crpc_1898", "", False), "CRPC")
        self.assertEqual(_short_form("qanun_e_shahadat_order_1984_qso", "", False), "QSO")

    def test_a_word_of_the_printed_title_is_not_an_abbreviation(self):
        self.assertIsNone(
            _short_form("societies_registration_act_1860", "THE SOCIETIES REGISTRATION ACT, 1860", True))


class ActTests(unittest.TestCase):
    def test_the_completeness_check_travels_with_the_corpus(self):
        payload = {
            "schema_version": "specter.v1",
            "pages": [{"page_number": 1, "blocks": [block("b0", "1. Short title.")]}],
            "document": {
                "source_file": "5_pakistan_penal_code_ppc1860_under_review.pdf",
                "document_kind": "statute",
                "metadata": {"sections_complete": True, "missing_sections": []},
                "structure": [marker("section", "1", "Short title", "b0")],
            },
        }
        act = build(payload)
        self.assertTrue(act["sections_complete"])
        self.assertEqual(act["act_short"], "PPC")
        self.assertEqual(act["sections"][0]["citation"], "1 PPC")


if __name__ == "__main__":
    unittest.main()


class MarginNumberTests(unittest.TestCase):
    """The number is set in the margin, and reading order puts it either side.

    The Penal Code prints section 1 as "Title and extent of operation of the
    Code..." at x=144 with "1." at x=108 on the same line, and PyMuPDF returns
    the two in that order -- so the section began after its own heading and lost
    it.  Section 2 on the same page comes back the other way round.  Geometry
    settles it: the heading is the block that shares the number's line and sits
    to its right, whichever side of it reading order puts it on.
    """

    def line(self, block_id, text, x0, top):
        return {"id": block_id, "type": "text", "text": text,
                "bbox": [x0, top, 540.0, top + 14.0], "contains_rtl": False}

    def test_a_heading_ordered_before_its_number_is_kept(self):
        doc = {"pages": [{"page_number": 1, "blocks": [
            self.line("b0", "Title and extent of operation of the Code. This Act shall be called the", 144.0, 246.5),
            self.line("b1", "1.", 108.0, 247.6),
            self.line("b2", "Penal Code, and shall take effect throughout Pakistan.", 72.0, 260.3),
        ]}]}
        section = section_bodies(doc, [marker("section", "1", "", "b1")])[0]
        self.assertTrue(section["text"].startswith("Title and extent"), section["text"])
        self.assertEqual(section["block_ids"], ["b0", "b1", "b2"])
        # The number must not be left sitting inside the sentence it interrupts.
        self.assertNotIn("1.", section["text"])
        self.assertIn("called the Penal Code", section["text"])

    def test_a_heading_ordered_after_its_number_is_unchanged(self):
        doc = {"pages": [{"page_number": 1, "blocks": [
            self.line("b0", "Penal Code, and shall take effect throughout Pakistan.", 72.0, 260.3),
            self.line("b1", "2.", 108.0, 287.2),
            self.line("b2", "Punishment of offences committed within Pakistan. Every person", 144.0, 287.2),
        ]}]}
        section = section_bodies(doc, [marker("section", "2", "", "b1")])[0]
        self.assertEqual(section["block_ids"], ["b1", "b2"])

    def test_the_previous_section_does_not_also_claim_that_line(self):
        doc = {"pages": [{"page_number": 1, "blocks": [
            self.line("b0", "1.", 108.0, 100.0),
            self.line("b1", "Short title. This Act may be called the Act.", 144.0, 100.0),
            self.line("b2", "Title and extent of operation. This Act shall be called the", 144.0, 246.5),
            self.line("b3", "2.", 108.0, 247.6),
            self.line("b4", "Code, and shall take effect throughout Pakistan.", 72.0, 260.3),
        ]}]}
        first, second = section_bodies(doc, [marker("section", "1", "Short title", "b0"),
                                             marker("section", "2", "", "b3")])
        self.assertEqual(first["block_ids"], ["b0", "b1"])
        self.assertEqual(second["block_ids"], ["b2", "b3", "b4"])


class CompendiumCitationTests(unittest.TestCase):
    def test_a_compendium_is_not_given_a_citation_that_resolves_to_sixty_texts(self):
        payload = {
            "schema_version": "specter.v1",
            "pages": [{"page_number": 1, "blocks": [block("b0", "1. Short title."),
                                                    block("b1", "1. Short title.")]}],
            "document": {
                "source_file": "3_estacode.pdf",
                "document_kind": "statute",
                "metadata": {"numbering_restarts": 62, "sections_complete": None},
                "structure": [marker("section", "1", "Short title", "b0"),
                              marker("section", "1", "Short title", "b1")],
            },
        }
        act = build(payload)
        self.assertEqual(act["numbering_restarts"], 62)
        self.assertEqual(act["ambiguous_sections"], 2)
        # The text is still law and still retrievable; only the citation, which
        # would resolve to two texts, is withheld.
        self.assertTrue(all(s["citation"] is None for s in act["sections"]))
        self.assertTrue(all(s["text"] for s in act["sections"]))

    def test_an_unambiguous_section_beside_an_ambiguous_one_keeps_its_citation(self):
        payload = {
            "schema_version": "specter.v1",
            "pages": [{"page_number": 1, "blocks": [block("b0", "1. Short title."),
                                                    block("b1", "1. Acknowledgment of a debt."),
                                                    block("b2", "2. Definitions.")]}],
            "document": {
                "source_file": "151_stamp_act_1899.pdf", "document_kind": "statute",
                "metadata": {"numbering_restarts": 2, "title": "THE STAMP ACT, 1899"},
                "structure": [marker("section", "1", "Short title", "b0"),
                              marker("section", "1", "Acknowledgment", "b1"),
                              marker("section", "2", "Definitions", "b2")],
            },
        }
        act = build(payload)
        by_id = {s["block_ids"][0]: s for s in act["sections"]}
        self.assertIsNone(by_id["b0"]["citation"])
        self.assertIsNone(by_id["b1"]["citation"])
        self.assertEqual(by_id["b2"]["citation"], "section 2 of THE STAMP ACT, 1899")


class CategoryTests(unittest.TestCase):
    """The corpus files a statute by area of law and states it nowhere else."""

    def payload(self, source_file):
        return {
            "schema_version": "specter.v1",
            "pages": [{"page_number": 1, "blocks": [block("b0", "1. Short title.")]}],
            "document": {"source_file": source_file, "document_kind": "statute",
                         "metadata": {}, "structure": [marker("section", "1", "Short title", "b0")]},
        }

    def test_the_folder_names_the_area_of_law(self):
        act = build(self.payload(r"D:\corpus\pakistancode\criminal_laws\44_act.pdf"))
        self.assertEqual(act["category"], "Criminal")

    def test_a_statute_not_filed_under_a_category_claims_none(self):
        act = build(self.payload(r"D:\corpus\pakistancode\44_act.pdf"))
        self.assertIsNone(act["category"])


class MissingSectionsAreVisibleTests(unittest.TestCase):
    def test_a_statute_with_no_sections_still_reports_whether_it_was_repealed(self):
        payload = {
            "schema_version": "specter.v1",
            "pages": [{"page_number": 1, "blocks": [block("b0", "THIS LAW HAS BEEN REPEALED")]}],
            "document": {"source_file": "1046_general_statistics_act_1975.pdf",
                         "document_kind": "statute",
                         "metadata": {"repealed": True}, "structure": []},
        }
        act = build(payload)
        self.assertEqual(act["sections"], [])
        self.assertTrue(act["repealed"])
