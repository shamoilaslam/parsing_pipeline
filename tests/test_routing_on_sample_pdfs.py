"""Triage + router end to end: the sample PDFs route exactly as they did before.

The expected values were recorded from ``classify_pdf`` as shipped before it
delegated to ``docintel.triage`` and ``docintel.router``, so a change that moves
any page between native and OCR shows up here.
"""

from pathlib import Path

import pytest

from docintel.pipeline import classify_pdf

PDFS = Path(__file__).resolve().parents[1] / "data" / "pdfs"

SCANNED = {
    "2013LHC4394.pdf": ("scanned", list(range(1, 19))),
    "2014LHC4829.pdf": ("scanned", list(range(1, 14))),
    "2014LHC4864 (1).pdf": ("scanned", list(range(1, 11))),
    "cp_159_2021.pdf": ("scanned", [1, 2, 3, 4]),
    "CR-378-2018.pdf": ("scanned", list(range(1, 15))),
    "LHC-CASE2.pdf": ("scanned", [1, 2, 3, 6]),
}

DIGITAL = [
    "2013LHC3273.pdf",
    "2015LHC625 (1).pdf",
    "2018LHC1986 (1).pdf",
    "2021LHC9943.pdf",
    "2023LHC5924.pdf",
    "2023LHC6744.pdf",
    "2024LHC1690.pdf",
    "2024LHC2628.pdf",
    "2024LHC4594.pdf",
    "2024LHC5682.pdf",
    "2024LHC6559.pdf",
    "2025LHC495.pdf",
    "2025LHC5084.pdf",
    "2025PCrlj57.pdf",
    "c.p._954_2022 (1).pdf",
    "cp_1017_2022.pdf",
]


def routed(name):
    path = PDFS / name
    if not path.exists():
        pytest.skip(f"{name} is not in data/pdfs")
    result = classify_pdf(path)
    return result["mode"], result["scan_pages"]


@pytest.mark.parametrize("name", sorted(SCANNED))
def test_scanned_samples_send_the_same_pages_to_ocr(name):
    assert routed(name) == SCANNED[name]


@pytest.mark.parametrize("name", DIGITAL)
def test_digital_samples_stay_native(name):
    assert routed(name) == ("digital", [])
