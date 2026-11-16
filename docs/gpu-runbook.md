# Running Nemotron 3.5 Parse (GPU, or CPU for tests)

Only the model step needs the model. Everything before and after runs anywhere.
Model: `nvidia/Nemotron-3.5-Parse` (0.63B parameters, OpenMDW-1.1 licence, needs `trust_remote_code`).

## What has been tested

- **CPU, float32, one page** (2024LHC1690 p1 at 2x, 150 new tokens): about 9 minutes. The output followed
  the grammar `<x_a><y_b>TEXT<x_c><y_d><class_NAME>`, the English text was right, and the boxes were within
  4 pt of the PDF's own text boxes. Coordinates are plain fractions of the page image (x * width, y * height).
- **Speed on CPU:** most of the time is reading the image. Roughly 10 to 30 minutes per full page, so
  CPU is for tests only; small crops (one paragraph) take about a minute.
- **GPU:** never run. Expect it to be far faster, but that is not measured.
- The model card does not list Urdu. See the Urdu result in the plan.

## 0. Machine

- GPU: Linux or WSL2 with an NVIDIA GPU (Turing or newer) and a working `nvidia-smi`. The model card's
  reference hardware is H100/H200; no VRAM figure is given.
- CPU: any machine with about 4 GB of free RAM (float32 weights are 2.5 GB).
- Python 3.11+.

## 1. Install (once)

```bash
git clone <repo> && cd <repo> && git checkout docintel-adapters   # or main, once merged
python -m venv .venv && source .venv/bin/activate
pip install torch                      # a CUDA build on a GPU machine
pip install -e ".[urdu]"               # transformers 5.6.1, accelerate, timm, open-clip-torch, albumentations
```

The first run downloads about 2.5 GB from Hugging Face, plus a small code file for the vision encoder from
`nvidia/C-RADIOv4-SO400M` (pinned to one revision).

## 2. Run the model on a small case first

```bash
python -m docintel.engines.nemotron_gpu case.pdf --pages 1 2 3 --work work/
```

- The script uses the GPU in bfloat16 if there is one, otherwise the CPU in float32 (as the card's own script does).
  `--model` takes a hub id or a local folder.
- Page 1 is checked before the rest. If it prints `first page produced no regions`, stop and send the raw
  output it prints.
- Output is `work/raw.jsonl`, one line per page, written as each page finishes. If the session ends, run the
  same command again: finished pages are skipped.

## 3. Use the output

```bash
# Box accuracy on a DIGITAL pdf, scored against the pdf's own text boxes (no annotation)
python -m eval.box_check case.pdf --work work/

# Parse a scanned pdf, taking its scan pages from the model output
python -m docintel parse case.pdf --out artifacts/run --engine nemotron --nemotron-work work/
```

## 4. Urdu text

Urdu pages are sent to the vision stage, which sends crops to a third-party API. It sends nothing unless you opt in:

```bash
python -m docintel urdu case.pdf artifacts/run/case.json --output-dir artifacts/run --policy third_party
```

Blocks whose own text looks sensitive (CNIC, phone, medico-legal, victim wording) are withheld even then; the
list is in `<output-dir>/vision_queue/<stem>.json` under `withheld`.
