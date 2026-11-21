"""The language model behind document understanding, run on our own machine.

``HFBrain`` loads an instruction-tuned model from the Hugging Face hub or a local folder
and answers a prompt with the first JSON object in its reply.  Decoding is greedy so the
same page gives the same answer.  Nothing leaves the machine.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "Qwen/Qwen2.5-3B-Instruct"
MAX_NEW_TOKENS = 900


def first_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                in_string, escaped = not (char == '"' and not escaped), char == chr(92) and not escaped
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1], strict=False)
                    except json.JSONDecodeError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


class HFBrain:
    def __init__(self, model_id: str = MODEL, dtype: str | None = None) -> None:
        self._model_id = model_id
        self._dtype = dtype  # "float16", "bfloat16" or "float32"; default: what the GPU supports
        self._loaded: tuple[Any, Any] | None = None
        self.last_reply: str | None = None  # the raw reply to the last prompt, kept so a failed answer can be read

    def _load(self):
        if self._loaded is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            from docintel.engines.nemotron_gpu import choose_dtype

            dtype = getattr(torch, self._dtype or choose_dtype(torch.cuda.get_device_capability() if torch.cuda.is_available() else None))
            model = AutoModelForCausalLM.from_pretrained(self._model_id, dtype=dtype, device_map="auto" if torch.cuda.is_available() else None)
            self._loaded = (model.eval(), AutoTokenizer.from_pretrained(self._model_id))
        return self._loaded

    def json(self, prompt: str) -> dict[str, Any] | None:
        import torch

        model, tokenizer = self._load()
        ids = tokenizer.apply_chat_template([{"role": "user", "content": prompt}], add_generation_prompt=True,
                                            return_tensors="pt", return_dict=True).to(model.device)
        with torch.inference_mode():
            out = model.generate(**ids, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        self.last_reply = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        return first_json_object(self.last_reply)
