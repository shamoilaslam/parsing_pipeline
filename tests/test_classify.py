"""The document-type classifier: text in, a kind with its confidence and evidence out."""

import pytest

from docintel.understand.classify import classify

FIR = """FIRST INFORMATION REPORT
(Under Section 154 Cr.P.C)
Police Station: Gulberg, Lahore    FIR No. 123/2024    Date: 12-03-2024
Name of complainant: Ali Khan. Offence: 302, 34 PPC."""

NDA = """NON-DISCLOSURE AGREEMENT
This Agreement is made on 1 March 2024 between Alpha Ltd (the "Disclosing Party") and Beta Ltd,
hereinafter referred to as the "Receiving Party". WHEREAS the parties wish to exchange Confidential Information..."""


def test_an_fir_is_recognised_from_its_title_and_form_fields():
    result = classify(FIR)
    assert result.kind == "fir"
    assert 0.5 < result.confidence <= 1.0
    assert "title:first_information_report" in result.reasons


def test_an_nda_is_a_contract():
    assert classify(NDA).kind == "contract"


def test_text_with_no_legal_cues_is_unknown_with_zero_confidence():
    result = classify("Dear all, lunch is at one o'clock on Friday.")
    assert (result.kind, result.confidence) == ("unknown", 0.0)


JUDGMENT = """IN THE LAHORE HIGH COURT, LAHORE
JUDGMENT SHEET
W.P. No. 12345 of 2023
Ali versus Province of Punjab
JUDGMENT
For the reasons recorded above, this petition is dismissed."""

ORDER = """ORDER SHEET
IN THE COURT OF ADDITIONAL SESSIONS JUDGE, LAHORE
Date of hearing    Order or other proceedings
12-03-2024    Case adjourned to 20-03-2024 for arguments."""

PLEADING = """IN THE LAHORE HIGH COURT, LAHORE
WRIT PETITION UNDER ARTICLE 199 OF THE CONSTITUTION
Respectfully Sheweth:
1. That the petitioner is a citizen of Pakistan.
PRAYER: It is prayed that the impugned order be set aside."""

AFFIDAVIT = """AFFIDAVIT
I, Ali Khan, son of Bashir Khan, do hereby solemnly affirm and declare on oath as under:
DEPONENT"""

WITNESS = """STATEMENT OF WITNESS
Statement under Section 161 Cr.P.C. of PW-3 Ali Khan, recorded by the Investigating Officer."""

MEDICAL = """MEDICO-LEGAL CERTIFICATE
MLC No. 44/2024
Injuries noted: bruise on left arm.
Medical Officer, Mayo Hospital, Lahore"""

STATUTE = """THE PAKISTAN PENAL CODE, 1860
An Act to provide a general penal code for Pakistan.
Be it enacted as follows: Short title and commencement. This Act shall come into force at once."""

URDU_FIR = """ابتدائی اطلاعی رپورٹ
تھانہ: گلبرگ
مقدمہ نمبر 123/2024"""


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (JUDGMENT, "judgment"),
        (ORDER, "order"),
        (PLEADING, "pleading"),
        (AFFIDAVIT, "affidavit"),
        (WITNESS, "witness_statement"),
        (MEDICAL, "medical_report"),
        (STATUTE, "statute"),
        (URDU_FIR, "fir"),
    ],
)
def test_each_kind_of_case_file_document_is_recognised(text, kind):
    assert classify(text).kind == kind


def test_a_judgment_that_only_mentions_an_fir_in_its_body_stays_a_judgment():
    body = (
        JUDGMENT
        + "\n"
        + "x " * 400
        + "\nThe First Information Report, FIR No. 9/2020, was registered at Police Station Gulberg."
    )
    assert classify(body).kind == "judgment"


# The opening of a real Lahore High Court order sheet (2021LHC9943, from the gold set), plus a disposal
# sentence from its body: "Versus" and "is allowed" must not outvote the form's own title and columns.
REAL_ORDER_SHEET = """Form No:HCJD/C-121
ORDER SHEET
LAHORE HIGH COURT, LAHORE
JUDICIAL DEPARTMENT
Case No.
Crl. Misc. No.49770-B of 2021
Bilal Moeen Butt alias Bilal Hussain Butt
Versus
The State etc.
S.No. of order/ Proceedings | Date of order/ Proceedings | Order with signature of Judge and that of Parties of counsel, where necessary.
30.09.2021 Mr. Muhammad Tallal Dar, Advocate for the petitioner.
For the reasons stated above, this petition is allowed and bail is granted."""


def test_a_real_order_sheet_is_an_order_not_a_judgment():
    assert classify(REAL_ORDER_SHEET).kind == "order"


LETTER = """BHP MINERALS Commercial Projects Division  Reference: BM/PAR/93/041
16 September 1993
To: The Managing Director, Balochistan Development Authority
Dear Sir,
We refer to the discussions between the parties concerning the proposed joint venture.
Yours faithfully, Director"""

NOTICE = """GOVERNMENT OF BALOCHISTAN
NOTIFICATION
No. SO(M)/12/93. In exercise of the powers conferred by rule 5, the Government is pleased to relax rule 12."""

APPLICATION = """APPLICATION FOR EXPLORATION LICENCE EL-5
The applicant hereby applies for the grant of an exploration licence over the area described in Schedule A."""

LICENCE_ORDER = """GRANT OF EXPLORATION LICENCE EL-5
The Government is pleased to grant an exploration licence to the licensee for a period of three years."""

MINUTES = """MINUTES OF THE BOARD MEETING
Present: Mr A (Chairman), Mr B. In attendance: Company Secretary.
RESOLVED that the novation of the agreement be approved."""

LEDGER = """EXPLORATION EXPENDITURE LEDGER
Date  Particulars  Debit  Credit  Balance
12.03.2004  Licence fee  50,000  -  50,000
Total  1,250,000"""

REPORT = """FEASIBILITY STUDY EXTRACT
Executive summary. The valuation of the project applies a discounted cash flow methodology."""

EMAIL = """From: A <a@x.com>
To: B <b@y.com>
Sent: 12 March 2024 10:14
Subject: Quantum hearing coordination"""

CONTRACT = """JOINT VENTURE AGREEMENT
This Agreement is made between the Parties hereinafter referred to as the Operator and the Partner."""

ADDENDUM = """ADDENDUM NO. 1 TO THE AGREEMENT
The parties hereby amend clause 4 of the Agreement hereinafter defined."""

WITNESS_STATEMENT = """NINTH WITNESS STATEMENT OF TIMOTHY CLARKE
I, Timothy Clarke, make this statement and confirm that it is true. Statement of truth."""

MEMORIAL = """CLAIMANT'S MEMORIAL ON DAMAGES
The Claimant submits the following Memorial. Request for relief: the Respondent is to pay damages."""


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (LETTER, "letter"),
        (NOTICE, "notice"),
        (APPLICATION, "application"),
        (LICENCE_ORDER, "order"),
        (MINUTES, "minutes"),
        (LEDGER, "ledger"),
        (REPORT, "report"),
        (EMAIL, "email"),
        (CONTRACT, "contract"),
        (ADDENDUM, "contract"),
        (WITNESS_STATEMENT, "witness_statement"),
        (MEMORIAL, "pleading"),
    ],
)
def test_other_documents_in_a_case_file_are_recognised(text, kind):
    assert classify(text).kind == kind
