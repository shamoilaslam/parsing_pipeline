# Working on Specter

## Quick verification

```powershell
python -m unittest discover -s tests -v
python -m specter --help
```

For a focused change, parse one fixture into a temporary artifact directory and validate it:

```powershell
python -m specter parse data/pdfs/2024LHC6559.pdf --out artifacts/_smoke
python -m specter validate artifacts/_smoke/2024LHC6559.json
```

Remove temporary directories after verification. Do not write generated output beside source files.

## Repository rules

- Keep parser code in `specter/`, regression tests in `tests/`, and operator scripts in `scripts/`.
- Keep input PDFs under `data/pdfs/` and reference outputs under `data/references/`.
- Write generated JSON, Markdown, overlays, reports, and benchmarks under `artifacts/`.
- Treat `data/references/llamaparse/` as comparison material, not ground truth.
- Do not add API keys, `.env`, model caches, or generated artifacts to commits.
- Preserve provenance when changing canonical JSON fields; validators should diagnose rather than silently rewrite authoritative text.

## Change checklist

1. Add or update a regression test for behavior changes.
2. Run the full unittest command above.
3. Run `python -m specter validate` on an affected canonical document.
4. Update `docs/architecture.md` or `docs/evaluation.md` when contracts or metrics change.
