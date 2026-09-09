"""Chunking runs on text; provenance comes from the offset map."""

import re
import unittest
from pathlib import Path

from rag.chunk import (PARAGRAPH_NUMBER, approximate_tokens, boundaries,
                       chunk_document, pack)
from rag.normalise import flatten


def block(block_id, text, page=1, kind="text", x0=72.0, top=100.0):
    return {"id": block_id, "type": kind, "text": text,
            "bbox": [x0, top, 540.0, top + 14.0]}


def payload(blocks_by_page, kind="judgment", metadata=None, structure=None):
    return {"pages": [{"page_number": n, "blocks": bs} for n, bs in blocks_by_page],
            "document": {"document_kind": kind, "metadata": metadata or {},
                         "structure": structure or []}}


class OffsetMapTests(unittest.TestCase):
    def test_a_justified_line_reads_as_prose_and_still_points_at_its_blocks(self):
        # PyMuPDF returns this judgment line as four blocks, one word each.
        doc = flatten(payload([(2, [block("b0", "and"), block("b1", "recovery"),
                                    block("b2", "of"), block("b3", "certain")])]),
                      "doc", "LHC")
        self.assertEqual(doc.text, "and recovery of certain")
        where = doc.provenance(0, len(doc.text))
        self.assertEqual(where["block_ids"], ["b0", "b1", "b2", "b3"])
        self.assertEqual(len(where["bboxes"]), 4)

    def test_a_span_maps_to_only_the_blocks_it_touches(self):
        doc = flatten(payload([(1, [block("b0", "first"), block("b1", "second"),
                                    block("b2", "third")])]), "doc", "LHC")
        start = doc.text.index("second")
        self.assertEqual(doc.provenance(start, start + 6)["block_ids"], ["b1"])

    def test_a_chunk_crossing_a_page_break_carries_both_pages(self):
        doc = flatten(payload([(2, [block("b0", "the counsel argued that the")]),
                               (3, [block("b1", "order lacked jurisdiction.")])]),
                      "doc", "LHC")
        where = doc.provenance(0, len(doc.text))
        self.assertEqual((where["page_start"], where["page_end"]), (2, 3))

    def test_a_page_footer_is_not_content(self):
        doc = flatten(payload([(2, [block("b0", "the appeal is allowed."),
                                    block("b1", "Page 4 of 7", kind="footer")])]),
                      "doc", "LHC")
        self.assertNotIn("Page 4", doc.text)


class RtlFlagTests(unittest.TestCase):
    def test_a_chunk_containing_urdu_says_so(self):
        # This read `block_type == "table"` and called the result "rtl", which
        # flagged tables and no Urdu at all.
        blocks = [block("b0", "The petitioner relies on the following passage."),
                  {**block("b1", "اردو متن"), "contains_rtl": True}]
        chunks = chunk_document(payload([(2, blocks)]), "doc", "LHC")
        self.assertTrue(chunks[0].flags["rtl"])

    def test_a_chunk_with_no_urdu_does_not_claim_it(self):
        chunks = chunk_document(payload([(2, [block("b0", "The appeal is allowed."),
                                              block("b1", "a | b", kind="table")])]),
                                "doc", "LHC")
        self.assertFalse(chunks[0].flags["rtl"])


class PackTests(unittest.TestCase):
    def test_a_short_passage_is_one_chunk(self):
        text = "A short judgment paragraph."
        self.assertEqual(pack(text, 512, 64, [], approximate_tokens), [(0, len(text))])

    def test_a_long_passage_breaks_at_an_allowed_stop(self):
        text = ("First sentence here. " * 60).strip()
        stops = boundaries(text, __import__("rag.chunk", fromlist=["SENTENCE_END"]).SENTENCE_END)
        pieces = pack(text, 64, 0, stops, approximate_tokens)
        self.assertGreater(len(pieces), 1)
        for start, end in pieces:
            self.assertLessEqual(approximate_tokens(text[start:end]), 80)
        # No character is lost between consecutive pieces.
        self.assertEqual(pieces[0][1], pieces[1][0])

    def test_a_passage_with_no_stop_is_still_cut_to_budget(self):
        # An over-long chunk is silently truncated by the model, losing text
        # without saying so; cutting at the budget is visible and reversible.
        text = "x" * 4000
        pieces = pack(text, 64, 0, [], approximate_tokens)
        self.assertTrue(all(approximate_tokens(text[a:b]) <= 80 for a, b in pieces))


class JudgmentChunkTests(unittest.TestCase):
    def test_a_numbered_paragraph_is_preferred_as_a_boundary(self):
        body = ("2. " + "The suit was contested by the respondent. " * 20
                + "3. " + "Learned counsel for the appellant argued at length. " * 20)
        chunks = chunk_document(payload([(2, [block("b0", body)])], kind="judgment",
                                        metadata={"case_number": "R.F.A.848/2011",
                                                  "court": "LHC"}),
                                "2011LHC4385", "LHC", budget=128, overlap=0)
        self.assertGreater(len(chunks), 1)
        # The split lands on a printed paragraph number, which is what a lawyer
        # cites, rather than mid-argument.
        self.assertTrue(any(c.structure.get("number") in {"2", "3"} for c in chunks))
        self.assertTrue(any(c.text.startswith("3.") for c in chunks))

    def test_the_prefix_is_for_the_embedder_and_not_part_of_the_text(self):
        chunks = chunk_document(payload([(2, [block("b0", "The appeal is allowed.")])],
                                        metadata={"case_number": "R.F.A.848/2011",
                                                  "court": "LHC"}),
                                "2011LHC4385", "LHC")
        self.assertNotIn("R.F.A.848/2011", chunks[0].text)
        self.assertIn("R.F.A.848/2011", chunks[0].embedding_text())


class StatuteChunkTests(unittest.TestCase):
    def test_a_section_is_one_chunk_and_cites_as_a_lawyer_would(self):
        blocks = [block("b0", "302. Punishment of qatl-i-amd. Whoever commits qatl-e-amd shall be punished."),
                  block("b1", "303. Qatl committed under ikrah-i-tam. Whoever commits qatl-e-amd under duress.")]
        doc = payload([(1, blocks)], kind="statute",
                      metadata={"title": "THE PAKISTAN PENAL CODE", "act_short": "PPC",
                                "sections_complete": True},
                      structure=[{"kind": "section", "number": "302", "title": "Punishment of qatl-i-amd", "block_id": "b0"},
                                 {"kind": "section", "number": "303", "title": "Qatl under ikrah-i-tam", "block_id": "b1"}])
        chunks = chunk_document(doc, "ppc", "pakistancode")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].citation, "302 PPC")
        self.assertIn("qatl-i-amd", chunks[0].prefix)
        self.assertTrue(chunks[0].flags["sections_complete"])
        self.assertEqual(chunks[0].block_ids, ["b0"])


class ProvenanceRoundTripTests(unittest.TestCase):
    """The boxes a chunk records must contain the words the chunk claims.

    This is the promise the whole design rests on: an answer can point at the
    page region it came from. Asserting that every chunk *has* boxes says
    nothing about whether they point anywhere useful, so the text is re-read out
    of the PDF at those coordinates and compared against the chunk.
    """

    @classmethod
    def setUpClass(cls):
        import fitz

        from specter.specter_parser import SpecterParser

        cls.fitz = fitz
        cls.path = Path(__file__).resolve().parents[1] / "data" / "pdfs" / "2024LHC6559.pdf"
        cls.payload = SpecterParser(rtl_mode="raw").parse(cls.path)
        cls.chunks = chunk_document(cls.payload, cls.path.stem, "LHC")

    @staticmethod
    def _words(text):
        return [word for word in re.sub(r"\s+", " ", text).strip().lower().split()
                if len(word) > 3]

    def test_every_chunk_records_a_page_and_at_least_one_box(self):
        self.assertTrue(self.chunks)
        for chunk in self.chunks:
            self.assertTrue(chunk.bboxes, chunk.id)
            self.assertIsNotNone(chunk.page_start, chunk.id)

    def test_the_recorded_boxes_contain_the_chunk_text(self):
        with self.fitz.open(self.path) as pdf:
            for chunk in self.chunks:
                inside = " ".join(
                    pdf[int(block_id.split("_")[0][1:]) - 1].get_textbox(self.fitz.Rect(*box))
                    for box, block_id in zip(chunk.bboxes, chunk.block_ids))
                inside = re.sub(r"\s+", " ", inside).lower()
                words = self._words(chunk.text)
                if not words:
                    continue
                covered = sum(1 for word in words if word in inside) / len(words)
                self.assertGreater(covered, 0.9, f"{chunk.id}: {covered:.2f}")


if __name__ == "__main__":
    unittest.main()
