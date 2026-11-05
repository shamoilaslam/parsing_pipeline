# GPU runbook: Nemotron Parse

Only the model step needs the GPU. Everything before and after runs anywhere.
`docintel/engines/nemotron_gpu.py` has **never been run**; this is its first test.

## 0. Machine

- Linux or WSL2 (Nemotron Parse does not run on plain Windows).
- NVIDIA GPU, Turing or newer, with a current driver. `nvidia-smi` must work.
- Python 3.11+.

## 1. Install (once)

```bash
git clone <repo> && cd <repo> && git checkout docintel-pipeline
python -m venv .venv && source .venv/bin/activate
pip install torch                      # a CUDA build; check torch.cuda.is_available()
pip install -e ".[urdu]"               # pins transformers 5.6.1, accelerate, timm, open-clip-torch, albumentations
python -c "import torch; print(torch.cuda.is_available())"   # must print True
```

The first model run downloads about 2 GB from Hugging Face.

## 2. Run the model on a small case first

```bash
python -m docintel.engines.nemotron_gpu case.pdf --pages 1 2 3 --work work/
```

- Page 1 is checked before the rest. If it prints `first page produced no regions`,
  stop and send the raw output it prints: the prompt or decoding is wrong.
- Output is `work/raw.jsonl`, one line per page, written as each page finishes.
  If the session ends, run the same command again: finished pages are skipped.

## 3. Back on the CPU machine

Copy `work/raw.jsonl` next to the PDF, then:

```bash
# Box accuracy on a DIGITAL pdf, scored against the pdf's own text boxes (no annotation)
python -m eval.box_check case.pdf --work work/

# Parse a scanned pdf, taking its scan pages from the model output
python -m docintel parse case.pdf --out artifacts/run --engine nemotron --nemotron-work work/
```

## 4. Urdu text

Nemotron supplies the boxes; Urdu text comes from the vision stage, which sends
crops to a third-party API. It sends nothing unless you opt in:

```bash
python -m docintel urdu case.pdf artifacts/run/case.json --output-dir artifacts/run --policy third_party
```

Blocks whose own text looks sensitive (CNIC, phone, medico-legal, victim wording)
are withheld even then; the list is in `<output-dir>/vision_queue/<stem>.json` under `withheld`.

## What to send back if something is wrong

- `nvidia-smi` and the first lines of the traceback.
- The raw output printed by `first page produced no regions`.
- `work/raw.jsonl` and the PDF, if scoring looks off.
