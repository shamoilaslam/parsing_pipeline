"""Run Nemotron 3.5 Parse over PDF pages, on a GPU (bfloat16) or, slowly, on the CPU (float32).

    python -m docintel.engines.nemotron_gpu case.pdf --pages 1 2 3 --work work/

Written from the model card.  The CPU path has run once, on one page and 150
tokens (about 9 minutes), and its output parsed correctly; the GPU path has never
run.  The first page is checked before the rest, so a wrong prompt or decode shows
up at once instead of after a long session.

Output goes to ``<work>/raw.jsonl``, one line per page, written as each page
finishes.  Re-running skips pages already in it.  Back on the machine without a
GPU, ``render_jobs`` + ``load_raw`` + ``ingest`` (``docintel.engines.nemotron_batch``)
turn that file into ``specter.v1`` pages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docintel.engines.nemotron_batch import append_raw, load_raw, render_jobs
from docintel.engines.nemotron_parse import parse_output

MODEL = "nvidia/Nemotron-3.5-Parse"  # a hub id or a local folder
# The model card's default prompt: boxes, classes, markdown text, text inside pictures.
PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
MAX_NEW_TOKENS = 8744  # the card's default


def _load_model(model_id: str):
    import torch
    from transformers import AutoModel, AutoProcessor

    # As in the card's own script: bfloat16 on a GPU, float32 on the CPU.
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model = AutoModel.from_pretrained(model_id, trust_remote_code=True, dtype=dtype).to(device).eval()
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    return model, processor, device


def _read_page(model, processor, device: str, model_id: str, image_path: Path) -> str:
    import torch
    from PIL import Image
    from transformers import GenerationConfig

    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=[image], text=PROMPT, return_tensors="pt", add_special_tokens=False).to(device)
    config = GenerationConfig.from_pretrained(model_id, trust_remote_code=True)
    config.max_new_tokens, config.do_sample, config.num_beams, config.repetition_penalty = MAX_NEW_TOKENS, False, 1, 1.1
    with torch.inference_mode():
        output = model.generate(**inputs, generation_config=config)
    # The coordinate and class markers must survive decoding.
    return processor.batch_decode(output, skip_special_tokens=False)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, nargs="+", required=True, help="1-based page numbers")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--model", default=MODEL, help="hub id or local folder (default: %(default)s)")
    args = parser.parse_args()

    jobs = render_jobs(args.pdf, args.pages, args.work / "images")
    log = args.work / "raw.jsonl"
    done = load_raw(log)
    todo = [job for job in jobs if job["page_number"] not in done]
    if not todo:
        print("nothing to do: every page is already in raw.jsonl")
        return 0

    model, processor, device = _load_model(args.model)
    for index, job in enumerate(todo):
        raw = _read_page(model, processor, device, args.model, args.work / "images" / job["image"])
        if index == 0 and not parse_output(raw, page_size=job["page_size"]):
            print(f"first page produced no regions; check the prompt and decoding. Raw output:\n{raw}", file=sys.stderr)
            return 2
        append_raw(log, job["page_number"], raw)
        print(f"page {job['page_number']}: {len(raw)} chars", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
