import re
import unittest
from pathlib import Path

import fitz

from specter.specter_parser import (
    SpecterParser,
    _counsel_role,
    _court_id,
    _iso_date,
    _normalise_court,
    _cover_metadata,
    _extract_metadata,
    _locate_value,
    _metadata_provenance,
    _narrow_to_spans,
    _party_side,
    _signed_judges,
    CASE_RE,
)


ROOT = Path(__file__).resolve().parents[1]


def block(text, bbox, spans=None, score=0.9):
    return {"text": text, "bbox": bbox, "spans": spans or [], "confidence": {"score": score}}


def span(text, bbox):
    return {"text": text, "bbox": bbox}


def page(number, blocks):
    return {"page_number": number, "blocks": blocks}


class SpanNarrowingTests(unittest.TestCase):
    def test_bbox_tightens_to_the_span_holding_the_value(self):
        # A block is a whole paragraph; its box cannot usefully highlight a
        # date inside it.
        target = block(
            "Date of hearing: 24.10.2024",
            [100, 200, 500, 260],
            [span("Date of hearing: ", [100, 200, 220, 216]), span("24.10.2024", [230, 200, 300, 216])],
        )
        self.assertEqual(_narrow_to_spans(target, "24.10.2024"), [230, 200, 300, 216])

    def test_value_split_across_spans_unions_only_those_spans(self):
        target = block(
            "No.964/J/2023 filed",
            [100, 200, 500, 260],
            [span("No.964", [100, 200, 150, 216]), span("/J/2023", [150, 200, 200, 216]), span(" filed", [200, 200, 240, 216])],
        )
        self.assertEqual(_narrow_to_spans(target, "no.964/j/2023"), [100, 200, 200, 216])

    def test_falls_back_to_the_block_when_no_span_matches(self):
        target = block("some text", [10, 20, 30, 40], [span("other", [10, 20, 15, 30])])
        self.assertEqual(_narrow_to_spans(target, "absent"), [10, 20, 30, 40])

    def test_block_without_spans_returns_its_own_box(self):
        self.assertEqual(_narrow_to_spans(block("t", [1, 2, 3, 4]), "t"), [1, 2, 3, 4])


class LocateValueTests(unittest.TestCase):
    def test_value_inside_one_block_is_located_and_narrowed(self):
        pages = [page(1, [block("decided on 24.10.2024", [0, 0, 400, 20], [span("24.10.2024", [90, 0, 160, 16])])])]
        found = _locate_value("24.10.2024", pages)
        self.assertEqual(found["page"], 1)
        self.assertEqual(found["bbox"], [90, 0, 160, 16])
        self.assertEqual(found["source"], "native_text")

    def test_a_value_spanning_two_blocks_unions_them(self):
        # "X Versus Y" is assembled from separate blocks around a standalone
        # separator line, so the honest box is the union of both sides.
        pages = [page(1, [
            block("Ghulam Muhammad", [100, 100, 300, 116]),
            block("Vs.", [180, 120, 220, 136]),
            block("The State etc.", [100, 140, 300, 156]),
        ])]
        found = _locate_value("Ghulam Muhammad Vs. The State etc.", pages)
        self.assertEqual(found["source"], "native_text_parts")
        self.assertEqual(found["bbox"], [100, 100, 300, 156])

    def test_an_unlocatable_value_returns_nothing_rather_than_a_guess(self):
        # A box that does not contain what it points at is worse than no box.
        pages = [page(1, [block("unrelated content", [0, 0, 100, 20])])]
        self.assertIsNone(_locate_value("Nowhere To Be Found", pages))

    def test_empty_target_is_not_located(self):
        self.assertIsNone(_locate_value("   ", [page(1, [block("x", [0, 0, 1, 1])])]))


class ProvenanceTests(unittest.TestCase):
    def test_located_field_reports_page_box_and_source(self):
        pages = [page(2, [block("Crl. Appeal No.964/J/2023", [10, 10, 200, 26])])]
        prov = _metadata_provenance({"case_number": "Crl. Appeal No.964/J/2023"}, pages)
        self.assertEqual(prov["case_number"]["page"], 2)
        self.assertEqual(prov["case_number"]["source"], "native_text")

    def test_unlocated_field_says_so_instead_of_claiming_a_box(self):
        prov = _metadata_provenance({"court": "SOME COURT"}, [page(1, [block("x", [0, 0, 1, 1])])])
        self.assertEqual(prov["court"]["bbox"], [])
        self.assertEqual(prov["court"]["source"], "no_source_span")

    def test_counsel_is_skipped_because_each_row_carries_its_own_box(self):
        prov = _metadata_provenance({"counsel": [{"role": "state", "names": "Mr. X"}]}, [])
        self.assertNotIn("counsel", prov)


class CounselRoleTests(unittest.TestCase):
    def test_by_and_for_the_forms_are_recognised(self):
        self.assertEqual(_counsel_role("Petitioner by:"), "petitioner")
        self.assertEqual(_counsel_role("Respondents by"), "respondents")
        self.assertEqual(_counsel_role("For the Appellant:"), "appellant")
        self.assertEqual(_counsel_role("Revision-Petitioner by"), "revision petitioner")

    def test_rows_that_are_not_counsel_are_rejected(self):
        # cover_fields reports every two-column table it finds, which on some
        # judgments includes deposition tables and schedules.  Those are real
        # structure but not case metadata.
        for label in ("suit", "PW-1 examination-in-chief", "Sr. No.", ""):
            self.assertIsNone(_counsel_role(label), label)

    def test_unreadable_urdu_labels_are_rejected(self):
        # Broken Nastaleeq tables leak in as labels of transposed glyphs.
        self.assertIsNone(_counsel_role("ہ یپ ےلہس ےقوت قرم"))


class CoverMetadataTests(unittest.TestCase):
    def test_hearing_date_is_read_from_the_labelled_row(self):
        fields = [{"label": "Date of hearing", "value": "15.11.2023.", "bbox": [1, 2, 3, 4], "page": 1}]
        result = _cover_metadata(fields, [])
        self.assertEqual(result["hearing_date"], "15.11.2023")
        self.assertEqual(result["hearing_date_bbox"], [1, 2, 3, 4])

    def test_plural_label_spelling_is_accepted(self):
        fields = [{"label": "Date(s) of hearings", "value": "01.01.2020", "bbox": [], "page": 1}]
        self.assertEqual(_cover_metadata(fields, [])["hearing_date"], "01.01.2020")

    def test_counsel_rows_become_roles_with_geometry(self):
        fields = [
            {"label": "For the State:", "value": "Mr. Shahid Aleem, DPP.", "bbox": [5, 6, 7, 8], "page": 1},
            {"label": "suit", "value": "not counsel", "bbox": [], "page": 1},
        ]
        counsel = _cover_metadata(fields, [])["counsel"]
        self.assertEqual(len(counsel), 1)
        self.assertEqual(counsel[0]["role"], "state")
        self.assertEqual(counsel[0]["bbox"], [5, 6, 7, 8])

    def test_earliest_order_date_is_used(self):
        result = _cover_metadata([], [{"value": "28.01.2013", "page": 6}, {"value": "02.12.2013", "page": 1}])
        self.assertEqual(result["order_date"], "02.12.2013")


class PartySideTests(unittest.TestCase):
    def test_trailing_punctuation_only_counts_as_absent(self):
        # Otherwise a stray "." becomes a party and the caption never falls
        # back to the following line.
        self.assertEqual(_party_side(" . "), "")
        self.assertEqual(_party_side(" :- "), "")

    def test_meaningful_text_is_kept_with_its_periods(self):
        # "etc." and initials must survive so the value stays quotable.
        self.assertEqual(_party_side(" The State etc. "), "The State etc.")


class PartyCaptionTests(unittest.TestCase):
    def test_source_separator_is_preserved(self):
        meta = _extract_metadata("IN THE LAHORE HIGH COURT\nGhulam Muhammad Vs. The State etc.\n", {})
        self.assertEqual(meta["parties"], "Ghulam Muhammad Vs. The State etc.")

    def test_a_cited_case_in_the_body_is_not_read_as_the_parties(self):
        # A bare "v." deep in the prose is a citation, not this case's caption.
        text = (
            "IN THE LAHORE HIGH COURT\n"
            "J U D G M E N T\n"
            "5. Reference may be made to the case Mohtarma Benazir Bhutto v. President\n"
        )
        self.assertIsNone(_extract_metadata(text, {})["parties"])

    def test_the_judgment_sheet_form_label_does_not_stop_the_scan(self):
        # These pages carry "JUDGMENT SHEET" above the caption; breaking on it
        # would abandon the scan before the parties are reached.
        text = "JUDGMENT SHEET\nIN THE LAHORE HIGH COURT\nMuhammad Khawar Ilyas\nVersus\nFederation of Pakistan\n"
        self.assertEqual(_extract_metadata(text, {})["parties"], "Muhammad Khawar Ilyas Versus Federation of Pakistan")


class RealDocumentMetadataTests(unittest.TestCase):
    """Every located field must point at a region that really contains it."""

    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "data" / "pdfs" / "2024LHC6559.pdf"
        cls.result = SpecterParser(rtl_mode="raw").parse(cls.path)

    def _region(self, prov):
        with fitz.open(self.path) as pdf:
            return pdf[prov["page"] - 1].get_textbox(fitz.Rect(*prov["bbox"]))

    def test_every_located_field_is_actually_inside_its_bbox(self):
        prov = self.result["document"]["metadata_provenance"]
        checked = 0
        for field, entry in prov.items():
            if not entry.get("bbox") or not entry.get("page") or field.startswith("pdf_"):
                continue
            value = entry["value"]
            value = value[0] if isinstance(value, list) else value
            words = [w for w in re.findall(r"[a-z0-9]+", str(value).casefold()) if len(w) > 1]
            if not words:
                # A bare section number such as "1" carries no token worth
                # locating; containment would be satisfied by any digit.
                continue
            found = set(re.findall(r"[a-z0-9]+", self._region(entry).casefold()))
            self.assertTrue(all(w in found for w in words), f"{field}: {value!r}")
            checked += 1
        self.assertGreater(checked, 3)

    def test_atomic_field_boxes_are_a_single_line_tall(self):
        # A paragraph-sized box cannot usefully highlight a date.
        prov = self.result["document"]["metadata_provenance"]
        for field in ("case_number", "decision_date"):
            entry = prov.get(field, {})
            if entry.get("bbox"):
                self.assertLess(entry["bbox"][3] - entry["bbox"][1], 40, field)

    def test_counsel_is_extracted_with_roles_and_geometry(self):
        counsel = self.result["document"]["metadata"]["counsel"]
        roles = {c["role"] for c in counsel}
        self.assertIn("appellant", roles)
        self.assertIn("state", roles)
        for row in counsel:
            self.assertEqual(len(row["bbox"]), 4)
            self.assertTrue(row["names"].strip())

    def test_hearing_date_comes_from_the_cover_row(self):
        self.assertEqual(self.result["document"]["metadata"]["hearing_date"], "24.10.2024")



class CourtNameTests(unittest.TestCase):
    """OCR runs centred headings together; downstream matching needs them apart."""

    def test_a_joined_court_is_respaced(self):
        self.assertEqual(_normalise_court("INTHESUPREMECOURTOFPAKISTAN"), "SUPREME COURT OF PAKISTAN")
        self.assertEqual(_normalise_court("IN THESUPREMECOURTOFPAKISTAN"), "SUPREME COURT OF PAKISTAN")

    def test_an_already_spaced_court_is_returned_untouched(self):
        # A real variant must never be flattened into the canonical form.
        for value in ("IN THE SUPREME COURT OF PAKISTAN", "IN THE LAHORE HIGH COURT, LAHORE"):
            self.assertEqual(_normalise_court(value), value)

    def test_an_unknown_joined_value_is_left_alone_rather_than_guessed(self):
        self.assertEqual(_normalise_court("SOMETHINGENTIRELYUNKNOWN"), "SOMETHINGENTIRELYUNKNOWN")

    def test_nothing_is_invented_from_nothing(self):
        self.assertIsNone(_normalise_court(None))


class CanonicalFieldTests(unittest.TestCase):
    """Verbatim values are citable; canonical ones are filterable.

    Both are kept.  Storing only what the page says leaves the same court under
    several spellings and dates in several formats, and a filter then misses
    rows without raising anything -- the failure mode is silence, not an error.
    """

    def test_every_spelling_of_a_court_maps_to_one_identifier(self):
        for spelling in ("IN THE SUPREME COURT OF PAKISTAN", "SUPREME COURT OF PAKISTAN",
                         "IN THE. SUPREME COURT OF PAKISTAN", "supreme court of pakistan"):
            self.assertEqual(_court_id(spelling), "supreme_court_pakistan", spelling)

    def test_different_courts_stay_distinct(self):
        self.assertEqual(_court_id("IN THE LAHORE HIGH COURT, LAHORE"), "lahore_high_court")
        self.assertEqual(_court_id("Islamabad High Court"), "islamabad_high_court")

    def test_an_unrecognised_court_is_not_guessed(self):
        self.assertIsNone(_court_id("Something Else Entirely"))
        self.assertIsNone(_court_id(None))

    def test_every_date_style_in_the_corpus_becomes_iso(self):
        self.assertEqual(_iso_date("17.02.2016"), "2016-02-17")
        self.assertEqual(_iso_date("2.6.2005"), "2005-06-02")
        self.assertEqual(_iso_date("11-09-2025"), "2025-09-11")
        self.assertEqual(_iso_date("11/09/2025"), "2025-09-11")

    def test_a_two_digit_year_is_refused_rather_than_guessed(self):
        self.assertIsNone(_iso_date("1.9.25"))

    def test_an_impossible_date_yields_nothing(self):
        self.assertIsNone(_iso_date("31.02.2020"))
        self.assertIsNone(_iso_date("2016"))
        self.assertIsNone(_iso_date(None))

    def test_the_verbatim_value_is_still_carried(self):
        metadata = _extract_metadata("IN THE SUPREME COURT OF PAKISTAN\nDate of Hearing: 17.02.2016\n", {})
        self.assertEqual(metadata["decision_date"], "17.02.2016")
        self.assertEqual(metadata["decision_date_iso"], "2016-02-17")
        self.assertEqual(metadata["court_id"], "supreme_court_pakistan")

if __name__ == "__main__":
    unittest.main()


class SignedJudgeTests(unittest.TestCase):
    """The name signed above "JUDGE" at the foot of the document."""

    def test_the_signature_names_the_judge(self):
        # On an IHC order sheet this is the only statement of who decided:
        # there is no cover, no "Present:" list and no "NAME, J.-" attribution.
        self.assertEqual(_signed_judges(["Disposed of in the above terms.",
                                         "(MIANGUL HASSAN AURANGZEB)", "JUDGE"]),
                         ["MIANGUL HASSAN AURANGZEB"])

    def test_a_two_judge_bench_signs_twice(self):
        lines = ["(MUHAMMAD AZAM KHAN)", "(INAAM AMEEN MINHAS)", "JUDGE", "JUDGE"]
        self.assertEqual(_signed_judges(lines), ["MUHAMMAD AZAM KHAN", "INAAM AMEEN MINHAS"])

    def test_a_bracketed_name_with_no_office_under_it_is_not_a_judge(self):
        # The typist's initials and the uploader's name sit in the same place.
        self.assertEqual(_signed_judges(["(SOME PARTY LIMITED)", "Ahtesham*"]), [])

    def test_ordinary_bracketed_prose_is_not_a_signature(self):
        self.assertEqual(_signed_judges(["(the petitioner herein)", "JUDGE"]), [])


class AuthorAttributionTests(unittest.TestCase):
    def test_the_author_is_read_whether_the_court_writes_a_period_or_a_colon(self):
        # LHC and SC write "NAME, J.-"; IHC writes "NAME, J:-".  Requiring the
        # period lost the author on every IHC judgment.
        for written in ("MOHSIN AKHTAR KAYANI, J.- Through this appeal",
                        "MOHSIN AKHTAR KAYANI, J:- Through this appeal"):
            got = _extract_metadata(written, {})["judges"]
            self.assertEqual(got, ["MOHSIN AKHTAR KAYANI"], written)


class CaseNumberFormTests(unittest.TestCase):
    """Every court abbreviates its own case types."""

    def test_a_dotted_initialism_is_a_case_designator(self):
        for written in ("R.F.A No.81-2018", "C.R.No.338 /2017", "I.C.A. No. 12/2020",
                        "F.A.O. No.5 of 2019", "W.P. No.507 /2021", "C.P.L.A. No.1 of 2019"):
            self.assertEqual(CASE_RE.search(written).group(1), written, written)

    def test_the_forms_the_supreme_court_uses_are_untouched(self):
        # These were named literally before the initialism shape was added and
        # are named literally still: "C.P" and "W.P" carry one dot, not two.
        for written in ("C.P No.512 of 2020", "W.P No.99/2020", "WP No.12/2021"):
            self.assertEqual(CASE_RE.search(written).group(1), written, written)

    def test_the_year_survives_the_spaces_the_court_leaves_around_its_slash(self):
        # "No.2475 / 2018" stopped at the first space, dropping the year -- and
        # a case number without its year is ambiguous across years.
        for written in ("Writ Petition No.2475 / 2018", "Crl. Misc. No.1565 -B/ 2023"):
            self.assertEqual(CASE_RE.search(written).group(1), written, written)

    def test_a_consolidated_run_still_keeps_every_number(self):
        written = "Civil Appeals No.101 & 102-P of 2011"
        self.assertEqual(CASE_RE.search(written).group(1), written)
