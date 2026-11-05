"""Treatment and disposition cues: precise where it matters, ``referred`` when unsure."""

from intel import treatment
from intel.context import citation_spans


def _label(text: str, alias: str) -> str:
    spans = citation_spans(text)
    [(start, end, _)] = [(s, e, c) for s, e, c in spans if c["id"] == alias]
    others = [(s, e) for s, e, c in spans if c["id"] != alias]
    return treatment.classify(text, start, end, others)[0]


def test_a_single_cited_case_said_to_be_overruled_is_overruled():
    assert _label("The view taken in Ahmad v. Bashir (PLD 1990 SC 1) has since been overruled.",
                  "PLD 1990 SC 1") == "overruled"


def test_the_case_that_overrules_is_not_the_overruled_one():
    assert _label("That view was overruled in Province of Punjab v. Ali (PLD 2010 SC 11).",
                  "PLD 2010 SC 11") != "overruled"


def test_an_overruled_objection_is_not_an_overruled_precedent():
    assert _label("Relying on PLD 2001 SC 5, the preliminary objection is overruled.", "PLD 2001 SC 5") != "overruled"


def test_a_negative_cue_in_a_string_cite_binds_only_to_the_case_it_follows():
    text = "Counsel cited PLD 1990 SC 1 (overruled) and 2005 SCMR 911 in support."
    assert _label(text, "PLD 1990 SC 1") == "overruled"
    assert _label(text, "2005 SCMR 911") != "overruled"


def test_a_negated_cue_is_not_read():
    assert _label("The judgment in 2005 SCMR 911 has not been overruled so far.", "2005 SCMR 911") != "overruled"


def test_counsels_reliance_is_not_the_courts():
    assert _label("Learned counsel for the petitioner has relied upon 2005 SCMR 911 to argue otherwise.",
                  "2005 SCMR 911") == "cited_by_counsel"


def test_the_courts_reliance_is_followed():
    assert _label("Reliance is placed on Muhammad Ali v. State (2005 SCMR 911).", "2005 SCMR 911") == "followed"


def test_distinguished_applies_to_a_whole_string_cite():
    text = "The cases 2005 SCMR 911 and PLD 1990 SC 1 are distinguishable on facts."
    assert _label(text, "2005 SCMR 911") == _label(text, "PLD 1990 SC 1") == "distinguished"


def test_disposition_reads_the_order_at_the_end():
    text = "x " * 2000 + "For the foregoing reasons, this appeal is allowed and the impugned judgment is set aside."
    assert treatment.disposition(text)["outcome"] == "allowed"
    assert treatment.disposition("x " * 2000 + "The petition is dismissed.")["outcome"] == "dismissed"
    assert treatment.disposition("x " * 2000 + "The appeal is allowed and the case is remanded to the "
                                 "trial court.")["outcome"] == "remanded"


def test_a_cue_inside_the_quoted_passage_belongs_to_the_cited_case():
    assert _label("Reference is made to Chief Settlement Commissioner v. Raja Fazil Khan (PLD 1975 SC 331):- "
                  "\u201cIt seems to us that while there are cases which have been doubted\u201d.",
                  "PLD 1975 SC 331") != "doubted"


def test_the_case_that_declares_another_not_good_law_is_not_itself_bad():
    assert _label("They had been declared by the five member bench of this court in the case of Gulshan Bibi vs. "
                  "Muhammad Sadiq (PLD 2016 SC 769) as \u201cnot a good law.\u201d", "PLD 2016 SC 769") != "not_good_law"


def test_an_active_overruling_case_is_not_overruled():
    assert _label("Ekram Khan v. Commissioner (2004 SCMR 183) overruled both the judgments rendered earlier.",
                  "2004 SCMR 183") != "overruled"


def test_the_object_of_an_active_overruling_is_overruled():
    assert _label("Their Lordships overruled Subedar Abdul Rehman v. The State (1981 PCrLJ 61) which held otherwise.",
                  "1981 PCrLJ 61") == "overruled"


def test_a_case_that_could_be_overruled_is_not_overruled():
    assert _label("A Bench decision in PLD 1979 SC 741 can be overruled by a larger Bench.", "PLD 1979 SC 741") \
        != "overruled"


def test_a_judgment_set_aside_by_the_supreme_court_is_reversed():
    assert _label("The judgment in Pervez Musharraf v. Federation (PLD 2020 Lahore 285), which has subsequently been "
                  "set aside by the Supreme Court, cannot be relied on.", "PLD 2020 Lahore 285") == "reversed"


def test_reliance_reported_in_the_past_is_counsels_not_the_courts():
    assert _label("He placed reliance on Malik Gul Hassan v. Federation (2016 SCMR 1030).", "2016 SCMR 1030") != "followed"


def test_a_sentence_ends_after_a_year():
    text = ("Silk Bank v. Al-Khan (2017 CLD 496) and judgment dated 21.11.2011 in C.O.S. No. 210 of 2010. "
            "The case law relied upon by learned counsel for the defendants is distinguishable.")
    assert _label(text, "2017 CLD 496") != "distinguished"


def test_followed_in_another_sense_is_not_following_a_precedent():
    assert _label("A provision is mandatory when its directions are followed by an express provision for default, "
                  "see PLD 1974 SC 134.", "PLD 1974 SC 134") != "followed"
    assert _label("The dictum has been further followed by this Court in Falcon Ventures v. Banking Court "
                  "(2004 CLD 726).", "2004 CLD 726") == "followed"
