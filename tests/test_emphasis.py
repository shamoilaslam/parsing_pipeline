import unittest
from pathlib import Path

from specter.specter_parser import (
    CHAR_UNDERLINE,
    FONT_BOLD,
    FONT_ITALIC,
    FONT_SUPERSCRIPT,
    SpecterParser,
    _emphasis,
    _emphasis_markdown,
    _heading_level,
    _join_wrapped_words,
    _plain_words,
    _table_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def span(text, **emphasis):
    return {"text": text, "emphasis": {k: v for k, v in emphasis.items() if v}}


class EmphasisFlagTests(unittest.TestCase):
    def test_font_traits_come_from_flags(self):
        # 4 is the serif bit, which is styling noise, not emphasis.
        self.assertEqual(
            _emphasis({"flags": FONT_BOLD | FONT_ITALIC | 4}),
            {"bold": True, "italic": True, "underline": False, "superscript": False},
        )

    def test_underline_comes_from_char_flags_not_flags(self):
        # PyMuPDF splits these across two bitfields and exports no named
        # constant for underline; it is the strongest citation signal in LHC
        # judgments, so this mapping is worth pinning down.
        self.assertTrue(_emphasis({"char_flags": CHAR_UNDERLINE})["underline"])
        self.assertFalse(_emphasis({"flags": CHAR_UNDERLINE})["underline"])

    def test_superscript_bit(self):
        self.assertTrue(_emphasis({"flags": FONT_SUPERSCRIPT})["superscript"])

    def test_missing_fields_are_not_emphasis(self):
        self.assertEqual(set(_emphasis({}).values()), {False})


class EmphasisMarkdownTests(unittest.TestCase):
    def test_plain_spans_render_unchanged(self):
        spans = [span("ordinary text")]
        self.assertEqual(_emphasis_markdown(spans, "ordinary text"), "ordinary text")

    def test_bold_italic_underline_nest(self):
        spans = [span("Zulfiqaruddin v. State", bold=True, italic=True, underline=True)]
        self.assertEqual(
            _emphasis_markdown(spans, "Zulfiqaruddin v. State"),
            "<u>***Zulfiqaruddin v. State***</u>",
        )

    def test_markers_hug_the_words_not_the_padding(self):
        # "** bold **" does not render as bold in any markdown engine.
        spans = [span("  cited  ", bold=True)]
        self.assertEqual(_emphasis_markdown(spans, "cited"), "  **cited**  ")

    def test_adjacent_spans_with_same_emphasis_merge_into_one_run(self):
        spans = [span("Muhammad ", bold=True), span("Rawab", bold=True)]
        self.assertEqual(_emphasis_markdown(spans, "Muhammad Rawab"), "**Muhammad Rawab**")

    def test_inline_emphasis_inside_a_sentence(self):
        spans = [span("relied upon "), span("Nazir Ahmed", underline=True), span(" for this.")]
        self.assertEqual(
            _emphasis_markdown(spans, "relied upon Nazir Ahmed for this."),
            "relied upon <u>Nazir Ahmed</u> for this.",
        )

    def test_falls_back_to_text_when_spans_do_not_reassemble(self):
        # Spans carry no line separators, so they cannot always rebuild the
        # block text.  Emphasis is presentation and must never cost content.
        spans = [span("only part", bold=True)]
        self.assertEqual(_emphasis_markdown(spans, "only part of the real text"), "only part of the real text")

    def test_whitespace_only_span_gets_no_markers(self):
        spans = [span("  ", bold=True), span("word")]
        self.assertEqual(_emphasis_markdown(spans, "word"), "  word")


class WrappedWordTests(unittest.TestCase):
    def test_line_broken_word_is_rejoined(self):
        self.assertEqual(_join_wrapped_words("sub- section (2)"), "sub-section (2)")

    def test_emphasis_marker_between_letter_and_hyphen_does_not_block_the_join(self):
        # Regression: the source italicises "quasi", so the closing marker
        # sits between the letter and the hyphen.
        self.assertEqual(_join_wrapped_words("*quasi*- judicial"), "*quasi*-judicial")

    def test_hyphen_before_a_capital_is_left_alone(self):
        # "Qatl-i-Amd", "PW- 9": a following capital or digit means this is a
        # real compound, not a wrapped word.
        self.assertEqual(_join_wrapped_words("Ranjha- Advocate"), "Ranjha- Advocate")
        self.assertEqual(_join_wrapped_words("section 304- B"), "section 304- B")

    def test_ordinary_dash_usage_is_untouched(self):
        self.assertEqual(_join_wrapped_words("the appellant - who was present"), "the appellant - who was present")


class PlainWordsTests(unittest.TestCase):
    def test_markers_are_stripped_for_comparison(self):
        self.assertEqual(_plain_words("<u>***a b***</u> c"), ["a", "b", "c"])


class TableMarkdownTests(unittest.TestCase):
    def test_cell_newlines_are_flattened(self):
        # A raw newline inside a pipe cell terminates the row and splits one
        # table into several in every markdown renderer.
        markdown = _table_markdown([["For the State:", "Mr. Shahid Aleem,\nDistrict Public\nProsecutor."]])
        self.assertNotIn("\n|", markdown.replace("\n| ---", ""))
        self.assertIn("Mr. Shahid Aleem, District Public Prosecutor.", markdown)
        for line in markdown.split("\n"):
            self.assertTrue(line.startswith("|"), line)

    def test_empty_rows_yield_empty_markdown(self):
        self.assertEqual(_table_markdown([]), "")


class HeadingLevelTests(unittest.TestCase):
    def test_large_type_is_level_one(self):
        self.assertEqual(_heading_level({"font_sizes": [18.0]}, 12.0), 1)

    def test_slightly_raised_type_is_level_two(self):
        self.assertEqual(_heading_level({"font_sizes": [12.5]}, 12.0), 2)

    def test_missing_sizes_do_not_crash(self):
        self.assertEqual(_heading_level({}, 12.0), 2)


class RealDocumentEmphasisTests(unittest.TestCase):
    """Pinned against a real judgment: emphasis is the citation signal."""

    @classmethod
    def setUpClass(cls):
        cls.result = SpecterParser(rtl_mode="raw").parse(ROOT / "data" / "pdfs" / "2024LHC6559.pdf")

    def test_underlined_case_citation_is_marked_and_locatable(self):
        matches = [
            s
            for page in self.result["pages"]
            for block in page["blocks"]
            for s in block.get("spans", [])
            if "Zulfiqaruddin" in s.get("text", "")
        ]
        self.assertTrue(matches)
        emphasis = matches[0].get("emphasis", {})
        self.assertTrue(emphasis.get("underline"))
        self.assertTrue(emphasis.get("bold"))
        # A citation is only useful for grounding if it carries geometry.
        self.assertEqual(len(matches[0]["bbox"]), 4)

    def test_citation_renders_with_markers_in_markdown(self):
        self.assertIn("<u>***Zulfiqaruddin v. State etc.***</u>", self.result["markdown"])

    def test_plain_spans_carry_no_emphasis_key(self):
        # Most spans are plain; storing an all-false dict on each would bloat
        # every document for no information.
        plain = [
            s
            for page in self.result["pages"]
            for block in page["blocks"]
            for s in block.get("spans", [])
            if not s.get("emphasis")
        ]
        self.assertTrue(plain)

    def test_text_field_stays_free_of_markup(self):
        # ``text`` is the citation source of truth and the benchmark target;
        # all enrichment belongs in ``markdown`` and span attributes.
        for page in self.result["pages"]:
            for block in page["blocks"]:
                self.assertNotIn("<u>", block["text"])
                self.assertNotIn("**", block["text"])

    def test_both_heading_levels_are_emitted(self):
        levels = {
            block.get("heading_level")
            for page in self.result["pages"]
            for block in page["blocks"]
            if block["type"] == "heading"
        }
        self.assertEqual(levels, {1, 2})

    def test_table_markdown_rows_are_valid(self):
        for page in self.result["pages"]:
            for block in page["blocks"]:
                if block["type"] == "table":
                    for line in block["markdown"].split("\n"):
                        self.assertTrue(line.startswith("|"), line)


if __name__ == "__main__":
    unittest.main()
