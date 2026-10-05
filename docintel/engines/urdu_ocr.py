"""Optional OCR backends for Urdu text regions.

The parser keeps this module optional because OCR model runtimes are large.
Both backends return the same small result shape, so a measured backend can be
selected without changing the canonical document schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class UrduOCR:
    name = "urdu-ocr"

    def recognize(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        raise NotImplementedError


class RapidArabicOCR(UrduOCR):
    """CPU ONNX recognizer using PP-OCRv3 Arabic/Urdu weights."""

    name = "rapidocr-arabic-urdu"

    def __init__(self, model_dir: str | Path = "models/rapidocr_arabic"):
        from rapidocr_onnxruntime.ch_ppocr_v3_rec import TextRecognizer
        from rapidocr_onnxruntime.utils import LoadImage

        model_dir = Path(model_dir)
        required = [model_dir / "rec.onnx", model_dir / "dict.txt"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(f"RapidOCR Urdu model files missing: {missing}")
        self._load_image = LoadImage()
        self._recognizer = TextRecognizer({
            "model_path": str(model_dir / "rec.onnx"),
            "keys_path": str(model_dir / "dict.txt"),
            "use_cuda": False,
            "rec_img_shape": [3, 48, 320],
            "rec_batch_num": 6,
        })

    def recognize(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        images = [self._load_image(str(path)) for path in image_paths]
        return self.recognize_images(images)

    def recognize_images(self, images: list[Any]) -> list[dict[str, Any]]:
        """Recognize already-loaded line images without a disk round-trip."""
        results, _ = self._recognizer(images)
        return [{"text": text.strip(), "score": float(score)} for text, score in results]


class TrOCRUrdu(UrduOCR):
    """Dedicated Urdu TrOCR model; weights are downloaded by Transformers on first use."""

    name = "trocr-urdu"

    def __init__(self, model_id: str = "mohammadalihumayun/trocr-ur", device: str = "cpu"):
        from transformers import AutoProcessor, VisionEncoderDecoderModel
        import torch

        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(model_id)
        self._model = VisionEncoderDecoderModel.from_pretrained(model_id)
        self._device = torch.device(device)
        self._model.to(self._device)
        self._model.eval()

    def recognize(self, image_paths: list[Path]) -> list[dict[str, Any]]:
        from PIL import Image

        images = [Image.open(path).convert("RGB") for path in image_paths]
        inputs = self._processor(images=images, return_tensors="pt")
        pixel_values = inputs.pixel_values.to(self._device)
        with self._torch.inference_mode():
            generated = self._model.generate(
                pixel_values,
                num_beams=4,
                max_new_tokens=256,
                early_stopping=True,
            )
        texts = self._processor.batch_decode(generated, skip_special_tokens=True)
        return [{"text": text.strip(), "score": None} for text in texts]


def build_urdu_ocr(engine: str, model_dir: str = "models/rapidocr_arabic", model_id: str = "mohammadalihumayun/trocr-ur") -> UrduOCR | None:
    if engine == "none":
        return None
    if engine == "rapidocr":
        return RapidArabicOCR(model_dir)
    if engine == "trocr":
        return TrOCRUrdu(model_id)
    raise ValueError(f"Unknown Urdu OCR engine: {engine}")
