"""A quality score for text whose engine reports none (Nemotron): high for prose, low for broken output."""

from docintel.quality import text_quality

PROSE = "The minutes of the meeting held at BDA's offices in Quetta on 28 April 1993 indicate that BDA has approached the Directorate."


def test_ordinary_prose_scores_high():
    score, reasons = text_quality(PROSE)
    assert score >= 0.85 and reasons == []


def test_a_generation_loop_is_penalised():
    score, reasons = text_quality("the the the the the the the the " + PROSE)
    assert score < 0.6 and "repeated_tokens" in reasons
    assert text_quality("of the Government of the Government of the Government of the Government of the Government")[0] < 0.6


def test_symbol_soup_and_replacement_characters_are_penalised():
    assert "mostly_symbols" in text_quality("@#$ %^& *(){} ~~~ ||| ;;; ... ---")[1]
    assert "replacement_characters" in text_quality("Dear �� Sir")[1]


def test_empty_text_scores_zero():
    assert text_quality("   ")[0] == 0.0


def test_the_score_stays_between_zero_and_one():
    for text in (PROSE, "x", "�" * 50, "a " * 500):
        assert 0.0 <= text_quality(text)[0] <= 1.0
