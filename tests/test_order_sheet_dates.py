"""An order sheet states its date in the form body; the ISO form must follow the date."""

from pathlib import Path

import pytest

from docintel.engines.native import SpecterParser

PDFS = Path(__file__).resolve().parents[1] / "data" / "pdfs"


@pytest.mark.parametrize(
    ("name", "stated", "iso"),
    [
        ("2025LHC5084.pdf", "28.04.2025", "2025-04-28"),
        ("2021LHC9943.pdf", "30.09.2021", "2021-09-30"),
    ],
)
def test_a_decision_date_taken_from_the_order_sheet_has_its_iso_form(name, stated, iso):
    metadata = SpecterParser(rtl_mode="raw").parse(PDFS / name)["document"]["metadata"]
    assert metadata["decision_date"] == stated
    assert metadata["decision_date_iso"] == iso
