"""The two engines Specter already had, behind the ``Engine`` protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docintel.engines.base import Policy
from docintel.engines.native import SpecterParser
from docintel.engines.scanned import ScannedParser


class NativeEngine:
    """Reads the PDF's own text layer.  No model, no cost."""

    name = "digital"
    policy: Policy = "local"

    def __init__(self, rtl_mode: str = "image") -> None:
        self._parser = SpecterParser(rtl_mode=rtl_mode)

    def parse(
        self, pdf: Path, output_dir: Path | None, *, stem: str | None = None, pages: list[int] | None = None
    ) -> dict[str, Any]:
        # The text layer is read for the whole document; `pages` is not a saving here.
        return self._parser.parse(pdf, output_dir, stem=stem)


class OcrEngine:
    """Renders pages and reads them with the local OCR models."""

    name = "scanned"
    policy: Policy = "local"

    def __init__(self, render_scale: float = 2.0, preprocess_variant: str = "grayscale",
                 retry_threshold: float = 0.45, save_page_images: bool = False) -> None:
        self._parser = ScannedParser(render_scale=render_scale, preprocess_variant=preprocess_variant,
                                     retry_threshold=retry_threshold, save_page_images=save_page_images)

    def parse(
        self, pdf: Path, output_dir: Path | None, *, stem: str | None = None, pages: list[int] | None = None
    ) -> dict[str, Any]:
        if output_dir is None:
            raise ValueError("the OCR engine writes page assets and needs an output directory")
        # `pages` is the OCR page list; the others are emitted with geometry only.
        return self._parser.parse(pdf, output_dir, ocr_pages=pages, stem=stem)
