"""Citations: the one part of a judgment that points outside it.

A wrong edge in a citation graph asserts an authority the judge never relied
on. That is worse than a missing one, so precision is what these tests defend:
the corpus is full of ``<year> <word> <number>`` shapes that are not citations
at all, and a reporter whitelist is the only thing separating them.
"""
import unittest

from specter.citations import (
    build_graph,
    cited_authorities,
    find_citations,
    self_citation,
)


def ids(text):
    return [citation["id"] for citation in find_citations(text)]


class YearFirstTests(unittest.TestCase):
    """``2008 SCMR 598`` -- the common Pakistani form, 879 of 1,142 sampled."""

    def test_the_ordinary_form_is_read(self):
        self.assertEqual(ids("relied upon (2008 SCMR 598), wherein"), ["2008 SCMR 598"])

    def test_the_reporter_may_be_spelt_any_of_the_ways_the_corpus_spells_it(self):
        # One series, four typographies, all observed in the corpus.
        for written in ("1968 P.Cr.L.J. 1077", "1968 PCrLJ 1077",
                        "1968 PCr.LJ 1077", "1968 P Cr. L J 1077"):
            self.assertEqual(ids(written), ["1968 PCrLJ 1077"], written)

    def test_a_bracketed_sub_series_is_a_different_series(self):
        # "2024 PLC (C.S.) 402" and "2024 PLC 402" are different cases; folding
        # the sub-series away would merge two authorities into one node.
        self.assertEqual(ids("2024 PLC (C.S.) 402"), ["2024 PLC(CS) 402"])
        self.assertEqual(ids("2010 PLC 259"), ["2010 PLC 259"])

    def test_the_surrounding_parenthesis_is_not_part_of_the_citation(self):
        # "...others (2008 SCMR 598)" -- the bracket belongs to the sentence.
        found = find_citations("Ali v. State (2008 SCMR 598) held")
        self.assertEqual(found[0]["text"], "2008 SCMR 598")


class ReporterFirstTests(unittest.TestCase):
    """``PLD 2015 SC 123`` -- one series across every court, so it names one."""

    def test_the_court_is_read_and_canonicalised(self):
        for written in ("PLD 2015 SC 123", "PLD 2015 Supreme Court 123",
                        "PLD 2015 S.C. 123", "PLD 2015 SC (Pak.) 123"):
            self.assertEqual(ids(written), ["PLD 2015 SC 123"], written)

    def test_a_high_court_keeps_its_own_name(self):
        self.assertEqual(ids("PLD 1998 Lahore 90"), ["PLD 1998 Lahore 90"])
        self.assertEqual(ids("PLD 1998 Lah. 90"), ["PLD 1998 Lahore 90"])

    def test_a_missing_space_before_the_page_is_still_read(self):
        # "PLD 2013 SC1; PLD 2013 SC 501" appears exactly like that.
        self.assertEqual(ids("PLD 2013 SC1"), ["PLD 2013 SC 1"])

    def test_the_longer_reading_wins_over_the_shorter_one_inside_it(self):
        # "PLD 2015 SC 123" also contains the shape "2015 ... 123"; reporting
        # both would double every PLD citation in the corpus.
        self.assertEqual(len(find_citations("PLD 2015 SC 123")), 1)


class JurisdictionTests(unittest.TestCase):
    """Foreign authority is real, and must be filterable."""

    def test_an_indian_citation_is_read_and_marked(self):
        found = find_citations("(2015) 11 Supreme Court Cases 493")
        self.assertEqual(found[0]["id"], "2015 11 SCC 493")
        self.assertEqual(found[0]["jurisdiction"], "IN")

    def test_the_volume_is_part_of_the_identity(self):
        # The Indian series runs several volumes a year, so "(2015) 11 SCC 493"
        # and "(2015) 2 SCC 493" are different cases.
        self.assertNotEqual(ids("(2015) 11 SCC 493"), ids("(2015) 2 SCC 493"))

    def test_a_pakistani_citation_is_marked_pakistani(self):
        self.assertEqual(find_citations("2008 SCMR 598")[0]["jurisdiction"], "PK")


class PrecisionTests(unittest.TestCase):
    """What the corpus contains that looks like a citation and is not."""

    def test_a_year_beside_a_number_is_not_a_citation(self):
        # These shapes are common in the corpus; a general pattern matched them
        # in quantity, which is why the reporter list exists.
        for written in ("appeals of 2018 AND 3 others", "Crl. Appeal No. 9 of 2014",
                        "Dated 2019 at 5 p.m.", "Section 10 of the Ordinance, 2002",
                        "order dated 22.11.2021 passed by 3 members"):
            self.assertEqual(ids(written), [], written)

    def test_an_unknown_reporter_is_not_invented(self):
        self.assertEqual(ids("2008 ZZZZ 598"), [])

    def test_a_number_that_is_part_of_a_longer_token_is_not_a_page(self):
        self.assertEqual(ids("2008 SCMR 598/2009"), [])

    def test_a_year_no_law_report_has_seen_is_refused(self):
        # The year pattern spans 1800-2099; nothing outside it is a citation.
        self.assertEqual(ids("1200 SCMR 598"), [])
        self.assertEqual(ids("2150 SCMR 598"), [])


class ProvenanceTests(unittest.TestCase):
    """A cited authority that cannot be pointed at is not evidence."""

    def page(self, number, blocks):
        return {"page_number": number, "blocks": blocks}

    def block(self, text, bbox=(10, 20, 300, 40), block_id="p1_b0", kind="text"):
        return {"text": text, "bbox": list(bbox), "id": block_id, "type": kind}

    def test_each_citation_carries_the_page_and_box_it_was_printed_in(self):
        pages = [self.page(3, [self.block("relied on (2008 SCMR 598) at length")])]
        found = cited_authorities(pages)
        self.assertEqual(found[0]["mentions"][0]["page"], 3)
        self.assertEqual(found[0]["mentions"][0]["bbox"], [10, 20, 300, 40])

    def test_one_authority_relied_on_twice_is_one_citation_with_two_mentions(self):
        pages = [self.page(1, [self.block("see 2008 SCMR 598", block_id="a"),
                               self.block("again 2008 SCMR 598", block_id="b")])]
        found = cited_authorities(pages)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]["mentions"]), 2)

    def test_a_running_header_is_not_scanned(self):
        pages = [self.page(1, [self.block("2008 SCMR 598", kind="header")])]
        self.assertEqual(cited_authorities(pages), [])


class AuthorityParagraphTests(unittest.TestCase):
    """A real "relied upon" paragraph -- the shape that carries most citations.

    Modelled on the authorities listed in *Dr. Ghulam Sarwar v. Province of
    Punjab* (2024 PLC (C.S.) 402), which cites seventeen cases in one sentence
    across four jurisdictions.  A published index of that judgment lists eight
    of them; all seventeen are printed on the page.
    """

    PARAGRAPH = (
        "In support, they relied upon Mian Tariq Javed v. Province of Punjab (2008 SCMR 598), "
        "Mst. Basharat Jehan v. Director-General (2015 SCMR 1418), "
        "Pakistan Medical and Dental Council v. Muhammad Fahad Malik (2018 SCMR 1956), "
        "Uzma Manzoor v. Vice-Chancellor (2022 SCMR 694), "
        "Muhammad Yasin Saqib v. Chairman, PTC (2003 PLC (C.S.) 1105), "
        "Abdul Wahab v. KPPSC (2014 PLC (C.S.) 926), "
        "Dr. Khalil-ur-Rehman v. Government of Punjab (2015 PLC (C.S.) 793), "
        "Ayaz Ahmed Khan v. Federation of Pakistan (2021 PLC (C.S.) 1394), "
        "Altaf Hussain v. FPSC (2022 PLC (C.S.) 92), "
        "Danish Usman v. Government of KP (2022 PLC (C.S.) 418), "
        "Pradeep Kumar Rai v. Dinesh Kumar Pandey ((2015) 11 Supreme Court Cases 493), "
        "Madras Institute v. K. Sivasubramaniyan ((2016) 1 Supreme Court Cases 454), "
        "Ashok Kumar v. State of Bihar [(2017) 4 Supreme Court Cases 357], "
        "Dr. W. B. Vasantha v. IIT Madras (2013 SCC Online Mad 2171), "
        "Smt. Kamlesh Devi v. State of Haryana (2016 SCC Online P&H 9912) and "
        "Mahesh M.R. v. State of Kerala (2019 SCC Online Ker 19645). "
        "Reference can be made to Province of Punjab v. Zulfiqar Ali (2006 SCMR 678)."
    )

    def test_every_authority_in_the_paragraph_is_found(self):
        self.assertEqual(ids(self.PARAGRAPH), [
            "2008 SCMR 598", "2015 SCMR 1418", "2018 SCMR 1956", "2022 SCMR 694",
            "2003 PLC(CS) 1105", "2014 PLC(CS) 926", "2015 PLC(CS) 793",
            "2021 PLC(CS) 1394", "2022 PLC(CS) 92", "2022 PLC(CS) 418",
            "2015 11 SCC 493", "2016 1 SCC 454", "2017 4 SCC 357",
            "SCCONLINE 2013 Mad 2171", "SCCONLINE 2016 P&H 9912",
            "SCCONLINE 2019 Ker 19645", "2006 SCMR 678",
        ])

    def test_the_foreign_authorities_are_separable_from_the_domestic_ones(self):
        found = find_citations(self.PARAGRAPH)
        self.assertEqual(sum(c["jurisdiction"] == "PK" for c in found), 11)
        self.assertEqual(sum(c["jurisdiction"] == "IN" for c in found), 6)

    def test_nothing_but_the_authorities_is_matched(self):
        # The paragraph is dense with names, initials and brackets; a looser
        # pattern reads several of them as citations.
        self.assertEqual(len(find_citations(self.PARAGRAPH)), 17)


class SelfCitationTests(unittest.TestCase):
    def test_the_lahore_neutral_citation_names_the_document_itself(self):
        self.assertEqual(self_citation({"neutral_citation": "2023 LHC 5924"}), "2023 LHC 5924")

    def test_a_court_that_states_no_citation_claims_none(self):
        # SC and IHC judgments carry no reported citation: the reporter assigns
        # one only on publication, so nothing citing them can be linked back.
        self.assertIsNone(self_citation({"neutral_citation": None}))
        self.assertIsNone(self_citation({}))


def document(name, citations, neutral=None, date_iso=None, kind="judgment", stem=None):
    return {
        "source_name": name,
        "source_file": f"D:/corpus/{name}",
        "output_stem": stem,
        "document_kind": kind,
        "metadata": {"neutral_citation": neutral, "decision_date_iso": date_iso},
        "citations": [{"id": cid, "year": int(cid.split()[0]) if cid.split()[0].isdigit() else None,
                       "reporter": "SCMR", "jurisdiction": "PK", "text": cid,
                       "mentions": [{"page": 1, "bbox": [0, 0, 1, 1]}]}
                      for cid in citations],
    }


class GraphTests(unittest.TestCase):
    def test_an_edge_is_drawn_for_every_authority_relied_on(self):
        graph = build_graph([document("a.pdf", ["2008 SCMR 598", "2015 SCMR 1418"])])
        self.assertEqual(graph["edges"], 2)
        self.assertEqual(graph["distinct_authorities"], 2)

    def test_two_judgments_relying_on_one_authority_share_its_node(self):
        # This is the value of the graph even when the authority itself is not
        # in the corpus: "what else relied on 2008 SCMR 598" is answerable.
        graph = build_graph([document("a.pdf", ["2008 SCMR 598"]),
                             document("b.pdf", ["2008 SCMR 598"])])
        self.assertEqual(graph["most_cited"][0], {"id": "2008 SCMR 598", "cited_by": 2,
                                                  "in_corpus": False})

    def test_an_authority_we_hold_is_marked_as_held(self):
        graph = build_graph([document("cited.pdf", [], neutral="2023 LHC 5924"),
                             document("citing.pdf", ["2023 LHC 5924"])])
        self.assertEqual(graph["edges_to_a_document_we_hold"], 1)
        self.assertTrue(next(n for n in graph["node_list"] if n["id"] == "2023 LHC 5924")["in_corpus"])

    def test_a_judgment_printing_its_own_citation_does_not_cite_itself(self):
        # The header of a reported judgment states its own citation.
        graph = build_graph([document("a.pdf", ["2023 LHC 5924"], neutral="2023 LHC 5924")])
        self.assertEqual(graph["edges"], 0)

    def test_a_citation_dated_after_the_judgment_is_reported_not_dropped(self):
        # Observed cause is an OCR misread on the page -- "2077 SCMR 7354" for
        # "2017 SCMR 1354".  The page really does say that, so the edge stands
        # and the impossibility is reported beside it.
        graph = build_graph([document("a.pdf", ["2077 SCMR 7354"], date_iso="2014-06-01")])
        self.assertEqual(graph["edges"], 1)
        self.assertEqual(len(graph["anachronisms"]), 1)
        self.assertEqual(graph["anachronisms"][0]["cited_year"], 2077)

    def test_an_ordinary_citation_raises_no_finding(self):
        graph = build_graph([document("a.pdf", ["2008 SCMR 598"], date_iso="2014-06-01")])
        self.assertEqual(graph["anachronisms"], [])

    def test_two_documents_with_the_same_filename_are_two_nodes(self):
        # 21,712 IHC files are called judgment.pdf.  Keying a node on the
        # filename stem would collapse the whole court into one node.
        graph = build_graph([
            document("judgment.pdf", ["2008 SCMR 598"], stem="Criminal_Appeal-31-2020_judgment_a1"),
            document("judgment.pdf", ["2015 SCMR 1418"], stem="Writ_Petition-4-2019_judgment_b2"),
        ])
        self.assertEqual(graph["documents"], 2)
        self.assertEqual(graph["edges"], 2)

    def test_an_edge_carries_the_page_it_was_read_from(self):
        graph = build_graph([document("a.pdf", ["2008 SCMR 598"])])
        self.assertEqual(graph["edge_list"][0]["page"], 1)
        self.assertEqual(graph["edge_list"][0]["bbox"], [0, 0, 1, 1])


if __name__ == "__main__":
    unittest.main()
