"""Run Nemotron Parse over PDF pages on a machine with an NVIDIA GPU.

    python -m docintel.engines.nemotron_gpu case.pdf --pages 1 2 3 --work work/

UNTESTED: written from the model card for nvidia/NVIDIA-Nemotron-Parse-v1.2 and
never run, because it needs Linux and an NVIDIA GPU (Turing or newer).  The
first page is checked before the rest, so a wrong prompt or decode shows up in
seconds instead of after a long session.

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

MODEL = "nvidia/NVIDIA-Nemotron-Parse-v1.2"
# Boxes, classes and markdown text; do not read text inside pictures.
PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
MAX_NEW_TOKENS = 4096


def _load_model():
    import torch
    from transformers import AutoModel, AutoProcessor

    model = AutoModel.from_pretrained(MODEL, trust_remote_code=True, torch_dtype=torch.bfloat16).to("cuda").eval()
    processor = AutoProcessor.from_pretrained(MODEL, trust_remote_code=True)
    return model, processor


def _read_page(model, processor, image_path: Path) -> str:
    import torch
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=[image], text=PROMPT, return_tensors="pt", add_special_tokens=False).to("cuda")
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, repetition_penalty=1.1)
    # The coordinate and class markers must survive decoding.
    return processor.batch_decode(output, skip_special_tokens=False)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, nargs="+", required=True, help="1-based page numbers")
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()

    jobs = render_jobs(args.pdf, args.pages, args.work / "images")
    log = args.work / "raw.jsonl"
    done = load_raw(log)
    todo = [job for job in jobs if job["page_number"] not in done]
    if not todo:
        print("nothing to do: every page is already in raw.jsonl")
        return 0

    model, processor = _load_model()
    for index, job in enumerate(todo):
        raw = _read_page(model, processor, args.work / "images" / job["image"])
        if index == 0 and not parse_output(raw, image_size=job["image_size"], page_size=job["page_size"]):
            print(f"first page produced no regions; check the prompt and decoding. Raw output:\n{raw}", file=sys.stderr)
            return 2
        append_raw(log, job["page_number"], raw)
        print(f"page {job['page_number']}: {len(raw)} chars", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
