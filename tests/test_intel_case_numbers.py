"""Case numbers: every spelling of one case gives one key."""

import pytest

from intel.case_numbers import case_type, from_sc_label, parse


@pytest.mark.parametrize("printed,keys", [
    ("Writ Petition No.2385/2006", ["WP/2385/2006"]),
    ("W.P.No.2385 of 2006", ["WP/2385/2006"]),
    ("WRIT PETITION NO. 2385/06", ["WP/2385/2006"]),
    ("Crl.Misc.No.5072-Bof2013", ["CRL.MISC/5072-B/2013"]),
    ("Crl. Appeal No.374-J of 2009", ["CRL.A/374-J/2009"]),
    ("ICA No.224-2013", ["ICA/224/2013"]),
    ("FAO No.195 of\n2012", ["FAO/195/2012"]),
    ("C.M. No.494-03", ["C.M./494/2003"]),
    ("Criminal Appeal No. 909", ["CRL.A/909/?"]),
    ("Criminal Appeals No. 599, 600 & 602 of 2020", ["CRL.A/599/2020", "CRL.A/600/2020", "CRL.A/602/2020"]),
    ("Civil Petitions No. 1054-K & 1055-K of 2023", ["C.P./1054-K/2023", "C.P./1055-K/2023"]),
    ("WPs Nos. 10398/08, 17812/09 & 20670/2010 respectively", ["WP/10398/2008", "WP/17812/2009", "WP/20670/2010"]),
    ("Crl.A.No.130/2005 and M.R.No.213/2005", ["CRL.A/130/2005", "M.R./213/2005"]),
    ("WP No. 246503, 254424- 25, 254408 of 2018", ["WP/246503/2018", "WP/254424/2018", "WP/254425/2018", "WP/254408/2018"]),
    ("C. P. No. D-1306/2012", ["C.P./D-1306/2012"]),
    ("Crl.A.Nos.304, 305 & M.R.No.39 of 2014/Bwp.", ["CRL.A/304/2014", "CRL.A/305/2014", "M.R./39/2014"]),
    ("Cr. As. No. 281, 303/2012", ["CRL.A/281/2012", "CRL.A/303/2012"]),
    ("Customs Reference Application Nos. 28724 to 28725/25", ["CUSTOMS.REF/28724/2025", "CUSTOMS.REF/28725/2025"]),
    ("whereby Writ Petition No.17047/2015 of the", ["WP/17047/2015"]),
    ("case FIR No.1145/2013", []),
    ("CIVIL APPEALS NO.1113 TO 1116 OF 2017", ["C.A./1113/2017", "C.A./1114/2017", "C.A./1115/2017", "C.A./1116/2017"]),
])
def test_spellings(printed, keys):
    assert [case.key for case in parse(printed)] == keys


def test_a_civil_revision_is_not_a_civil_review_petition():
    assert case_type("C.R.") == "C.R."
    assert case_type("C.R.P.") == "C.R.P."


def test_an_unknown_type_is_kept_not_dropped():
    assert parse("Tax Reference No. 5 of 2010")[0].type == "TAXREFERENCE"


def test_nothing_parsed_from_nothing():
    assert parse(None) == [] and parse("no case here") == []


def test_sc_filename_label():
    assert from_sc_label("Crl.P.L.A", "467", "2023").key == "CRL.P/467/2023"
    assert from_sc_label("C.R.P", "296", "2020").key == "C.R.P./296/2020"
