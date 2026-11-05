"""Provision mentions: statute resolution, section keys, roles."""

from intel import provisions

PPC, CPC, CRPC, CNSA = "5_ppc", "6_code_of_civil_procedure_cpc", "4_code_of_criminal_procedure_crpc", "893_cnsa"
CONST, CONTRACT = "1_constitution_of_the_islamic_republic_of_pakistan", "72_contract_act_1872"
INDEX = {"short": {"PPC": PPC, "CPC": CPC, "CRPC": CRPC, "CNSA": CNSA}, "constitution": CONST,
         "sections": {PPC: {"302", "324", "34", "337F"}, CPC: {"12"}, CRPC: {"497"}, CNSA: {"9"},
                      CONST: {"199"}, CONTRACT: {"54", "3"}},
         "kinds": {PPC: "code", CPC: "code", CRPC: "code", CNSA: "act", CONST: "constitution", CONTRACT: "act"}}


class _Statute:
    """Stands in for statute_links.Statute: link() only calls .contains and reads a few fields."""

    def __init__(self, statute_id, words, year):
        self.statute_id, self.title, self.year = statute_id, " ".join(words), year
        self.haystacks, self.category = (tuple(words),), "civil_laws"

    def contains(self, needle):
        words = self.haystacks[0]
        return any(words[i:i + len(needle)] == needle for i in range(len(words) - len(needle) + 1))


TITLES = [_Statute(CONTRACT, ("contract", "act", "1872"), 1872)]


def _one(text):
    return provisions.mentions(text, INDEX, TITLES)


def test_an_abbreviated_list_resolves_every_number_and_reads_the_fir_as_a_charge():
    found = _one("The FIR was registered under sections 302, 324 and 34 PPC at Police Station Saddar.")
    assert [(m["statute_id"], m["section"], m["role"]) for m in found] == [
        (PPC, "302", "charged_under"), (PPC, "324", "charged_under"), (PPC, "34", "charged_under")]


def test_a_letter_suffix_and_a_clause_are_split_from_the_section():
    [m] = _one("He was convicted under section 337-F(i) PPC.")
    assert (m["section"], m["pinpoint"], m["section_found"]) == ("337F", "(i)", True)


def test_the_constitution_and_a_bare_article():
    first, second = _one("Article 199(1)(a) of the Constitution applies. The petition under Article 199 was filed.")
    assert (first["statute_id"], first["method"]) == (CONST, "constitution")
    assert (second["statute_id"], second["method"], second["role"]) == (CONST, "article_default", "invoked")


def test_a_title_resolves_and_the_act_refers_back_to_it():
    first, second = _one("Section 54 of the Contract Act, 1872 governs. Section 3 of the Act is relevant.")
    assert (first["statute_id"], first["method"]) == (CONTRACT, "title")
    assert (second["statute_id"], second["method"]) == (CONTRACT, "reference_back")


def test_reference_back_to_an_unheld_provincial_act_is_not_sent_to_an_older_held_one():
    found = _one("Section 54 of the Contract Act, 1872 governs. Section 15 of the Punjab Pre-emption Act, 1913 "
                 "applies. Section 10 of the Act is declared unconstitutional.")
    assert found[1]["statute_id"] is None and found[1]["not_held"]["reason"] == "not_held_for_this_jurisdiction"
    assert found[2]["statute_id"] is None and found[2]["role"] == "vires_challenged"


def test_order_and_rules_are_one_mention_not_three():
    [m] = _one("Order XXXIX Rules 1 and 2 CPC deals with temporary injunctions.")
    assert (m["kind"], m["section"], m["pinpoint"]) == ("order", "O.XXXIX", "R.1,2")


def test_scope_of_a_section_is_interpretation():
    [m] = _one("The question is the scope of section 497(2) Cr.P.C. in such cases.")
    assert m["role"] == "interpreted" and m["pinpoint"] == "(2)"


INDEX["short"]["QSO"] = "945_qso"
INDEX["sections"]["945_qso"] = {"2", "3", "17"}
INDEX["kinds"]["945_qso"] = "order"


def test_a_sub_inspector_and_a_year_are_not_provisions():
    assert _one("Muhammad Sohara S.I recorded the statement under the Police Rules 1934.") == []


def test_a_list_closed_by_comma_and_keeps_its_statute():
    found = _one("The crime report named Sections 409, 467, 477-A, and 109 of the PPC.")
    assert [(m["statute_id"], m["section"]) for m in found] == [(PPC, "409"), (PPC, "467"), (PPC, "477A"), (PPC, "109")]


def test_a_statute_named_before_the_sections():
    found = _one("The offences under the Pakistan Penal Code, namely:- Sections 302, 324 were tried.")
    assert {m["method"] for m in found} == {"named_before"}
    assert _one("He relied on PPC section 302 for the purpose.")[0]["statute_id"] == PPC


def test_qanun_e_shahadat_articles_are_not_constitution_articles():
    assert _one("Article 3 of Q.S. vests in the court the discretion.")[0]["statute_id"] == "945_qso"
    assert _one("Article 17 read with Article 3 of the Qanun-e-Shahadat Order applies.")[1]["statute_id"] == "945_qso"
    bare = _one("Under Article 2(4), the Qanun-e-Shahadat does not require more.")[0]
    assert bare["statute_id"] == "945_qso" and bare["method"] == "article_default_qso"


def test_of_the_act_refers_back_to_the_last_act_not_the_last_statute():
    found = _one("Section 54 of the Contract Act, 1872 governs. Article 199 of the Constitution applies. "
                 "Section 3 of the Act bars it. Rule 9(5) of the Rules applies.")
    assert (found[2]["statute_id"], found[2]["method"]) == (CONTRACT, "reference_back")
    assert found[3]["statute_id"] is None


def test_an_article_of_some_other_constitution_is_not_ours():
    assert _one("Article 2(ii) of the PCB Constitution defines a club.")[0]["statute_id"] is None
    assert _one("Article 102 of the Constitution of UAE provides otherwise.")[0]["statute_id"] is None


def test_a_letter_suffixed_section_is_kept_as_printed():
    [m] = _one("Recovery was effected u/s 9-C CNSA.")
    assert (m["section"], m["section_found"], m["clause_reading"]) == ("9C", False, "9(c)")


STAMP = "151_stamp_act_1899"
INDEX["sections"][STAMP] = {"12"}
INDEX["kinds"][STAMP] = "act"
TITLES.append(_Statute(STAMP, ("stamp", "act", "1899"), 1899))


def test_of_the_act_follows_an_act_named_in_prose_even_when_it_is_not_held():
    found = _one("Section 12 of the Stamp Act, 1899 was cited. The dispute is governed by the Punjab Rented "
                 "Premises Act, 2009 (the Act). Section 12(a) of the Act requires a certified copy.")
    assert found[0]["statute_id"] == STAMP
    assert found[1]["statute_id"] is None and found[1]["not_held"]["reason"] == "refers_back_to_unheld_statute"


def test_a_title_with_no_distinctive_word_names_nothing():
    [m] = _one("The perusal of section 6 of Ordinance 1984 shows that registration is required.")
    assert m["statute_id"] is None and m["not_held"]["reason"] == "title_names_no_statute"


def test_a_bare_article_in_a_qanun_e_shahadat_passage_is_not_the_constitutions():
    text = ("The Qanun-e-Shahadat Order governs proof. " + "The witness was examined at length. " * 5
            + "Subject to Article 124 when the question is whether a man is alive.")
    assert _one(text)[0]["statute_id"] == "945_qso"


def test_of_the_ordinance_with_only_a_prose_ordinance_named_does_not_fail():
    found = _one("This petition concerns the Banking Companies Ordinance, 1962. Section 5 of the Ordinance applies.")
    assert found[0]["method"] == "reference_back"


def test_an_article_a_law_is_measured_against_is_a_benchmark_not_struck_down():
    found = _one("Section 5 of the Contract Act, 1872 is declared ultra vires Article 199 of the Constitution.")
    assert [(m["statute_id"], m["role"]) for m in found] == [(CONTRACT, "vires_challenged"), (CONST, "benchmark")]


def test_procedural_provisions_are_not_charged_under():
    found = _one("In FIR No.12 under section 302 PPC the accused was examined under section 342 Cr.P.C.")
    assert [(m["statute_id"], m["role"]) for m in found] == [(PPC, "charged_under"), (CRPC, "mentioned")]


def test_a_bare_article_beside_an_indian_report_is_not_ours():
    assert _one("Article 21 protects him, see (2014) 3 SCC 1.")[0]["statute_id"] is None


def test_an_article_of_a_quoted_short_name_is_not_the_constitutions():
    assert _one('The suit was brought in terms of Article 124 of "The Order" which reads.')[0]["method"] != "article_default"


RENTED = "pb_punjab_rented_premises_act_2009"
ADMIN = "pb_administrator_generals_act_1913"
INDEX["sections"][RENTED] = {"12", "15"}
INDEX["kinds"][RENTED] = "act"
PUNJAB = [_Statute(RENTED, ("punjab", "rented", "premises", "act", "2009"), 2009),
          _Statute(ADMIN, ("punjab", "administrator", "generals", "act", "1913"), 1913)]


def _pb(text):
    return provisions.mentions(text, INDEX, TITLES, PUNJAB)


def test_a_punjab_act_named_as_punjab_resolves_to_the_punjab_code():
    [m] = _pb("Section 15 of the Punjab Rented Premises Act, 2009 applies.")
    assert (m["statute_id"], m["section_found"]) == (RENTED, True)


def test_a_plain_title_prefers_the_federal_code_and_falls_back_to_punjab():
    assert _pb("Section 54 of the Contract Act, 1872 governs.")[0]["statute_id"] == CONTRACT
    [m] = _pb("Section 12 of the Rented Premises Act, 2009 requires it.")
    assert m["statute_id"] == RENTED


def test_another_provinces_act_is_not_sent_to_punjab():
    [m] = _pb("Section 15 of the Sindh Rented Premises Ordinance, 1979 applies.")
    assert m["statute_id"] is None


def test_without_the_punjab_code_a_punjab_act_stays_unheld():
    [m] = provisions.mentions("Section 15 of the Punjab Rented Premises Act, 2009 applies.", INDEX, TITLES)
    assert m["statute_id"] is None


def test_a_bare_finance_act_does_not_fall_back_to_punjab():
    from intel.provisions import _Titles, punjab_titles
    punjab = punjab_titles([{"statute_id": "pb_punjab_finance_act_1999", "title": "PUNJAB FINANCE ACT, 1999",
                             "year": 1999, "pdf": "x.pdf"}])
    resolver = _Titles([], {"kinds": {}}, punjab)
    assert not resolver._link("Finance Act, 1999").get("matched")
