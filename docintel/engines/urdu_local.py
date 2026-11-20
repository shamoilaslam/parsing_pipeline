"""A local vision model for Urdu crops, run on our own machine and never trusted on its own word.

A vision model asked to read a crop it cannot read will often write fluent Urdu anyway.  So nothing it
says is kept unless it passes the same two checks every time:

* it is Urdu of a plausible length for the crop and does not loop (``plausible_urdu``);
* two readings of the same crop, at two sizes, agree.  A model that reads the pixels gives the same
  words twice; one that is making them up does not.

A crop that fails is left unread: the page keeps an ``illegible`` block rather than a guess.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
from rapidfuzz import fuzz

from docintel.engines.native import _is_rtl
from docintel.quality import text_quality

MODEL = "Qwen/Qwen3-VL-8B-Instruct"  # a hub id; nothing is downloaded until the first read
AGREEMENT = 85  # similarity (0-100) two readings of one crop must reach
SECOND_LOOK_SCALE = 1.5
MIN_PIXELS_PER_CHAR = 200  # a character cannot be drawn in less area than this: bounds the length a crop can hold
MIN_URDU_SHARE = 0.8  # of the letters, in Arabic script
PROMPT = (
    "Transcribe the Urdu text in this image exactly as written. Do not translate, summarise, correct or "
    "add anything. Keep spelling, digits, punctuation and line breaks. If you cannot read it, answer only: ILLEGIBLE."
)

Reader = Callable[[np.ndarray], str]


def plausible_urdu(text: str, size: tuple[int, int]) -> bool:
    """Is ``text`` Urdu, of a length ``size`` (width, height in pixels) could hold, and not a loop?"""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    if sum(_is_rtl(char) for char in letters) / len(letters) < MIN_URDU_SHARE:
        return False
    if len(text.strip()) > size[0] * size[1] / MIN_PIXELS_PER_CHAR:
        return False
    return "repeated_tokens" not in text_quality(text)[1]


class LocalVision:
    name = "local"

    def __init__(self, reader: Reader) -> None:
        self._read = reader

    def _two_readings(self, image: np.ndarray) -> tuple[str, str]:
        larger = cv2.resize(image, None, fx=SECOND_LOOK_SCALE, fy=SECOND_LOOK_SCALE, interpolation=cv2.INTER_CUBIC)
        return self._read(image).strip(), self._read(larger).strip()

    def complete(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The crops both readings agree on.  The others are left out, to stay unread."""
        results = []
        for task in tasks:
            image = cv2.imread(task["image"])
            if image is None:
                continue
            first, second = self._two_readings(image)
            size = (image.shape[1], image.shape[0])
            agreement = fuzz.ratio(first, second)
            if plausible_urdu(first, size) and plausible_urdu(second, size) and agreement >= AGREEMENT:
                results.append({
                    "id": task["id"], "text": first, "confidence": round(agreement / 100, 3),
                    "provider": self.name, "model": MODEL, "status": "accepted",
                })
        return results


def hf_reader(model_id: str = MODEL) -> Reader:
    """A reader backed by a Hugging Face vision-language model, loaded on its first use.

    Not run or tested here: it needs a GPU and a large download, both left for the Colab session.
    """
    loaded: list[Any] = []

    def read(image: np.ndarray) -> str:
        import torch
        from PIL import Image
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if not loaded:
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            model = AutoModelForImageTextToText.from_pretrained(model_id, dtype=dtype, device_map="auto" if torch.cuda.is_available() else None)
            loaded.extend([model.eval(), AutoProcessor.from_pretrained(model_id)])
        model, processor = loaded
        picture = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        messages = [{"role": "user", "content": [{"type": "image", "image": picture}, {"type": "text", "text": PROMPT}]}]
        inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=512, do_sample=False)
        return processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]

    return read
