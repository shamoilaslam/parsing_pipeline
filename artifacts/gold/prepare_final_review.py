#!/usr/bin/env python3
"""One combined review pass over the whole merged benchmark - not a fresh
transcription like batch1-4, just a consistency check. Every page is
pre-filled with the text you already corrected. You're looking for:

  - leftover meta-block fragments (Court:/Case No:/Parties: lines that
    didn't get cleanly removed)
  - tables that aren't using the template below yet
  - anything that looks wrong on a second look

Pages that should be EXCLUDED from the benchmark entirely (like the
intentionally-blank 2020LHC3741 page 27) - tick "Discard this page"
instead of leaving the text blank. Any page that's already empty gets
this box pre-ticked for you to confirm, on the assumption an empty
gold-text page was probably meant to be discarded, not transcribed as
"nothing here."

TABLE TEMPLATE - use this whenever you touch a page with a table:

    [TABLE]
    Col A | Col B | Col C
    Row1 A | Row1 B | Row1 C
    Row2 A | Row2 B | Row2 C
    [/TABLE]

    - one row per line, cells separated by " | " (space-pipe-space), exactly
    - wrap the whole table in [TABLE] / [/TABLE] on their own lines
    - empty cell -> a single "-", never leave it blank (keeps column count
      consistent so it can be split by " | " later without ambiguity)
    - a merged/spanning cell -> repeat the value in each position it spans

This is also shown in the sidebar of the HTML itself, so you don't have to
remember it while reviewing.

Usage:
    python prepare_final_review.py gold_lhc_benchmark_enriched.json --output final_review.html

Open final_review.html, work through all 192 pages, click "Export
corrections" when done - downloads final_review_corrections.json.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import fitz  # PyMuPDF


def _render_b64(page: "fitz.Page", zoom: float = 1.8) -> str:
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("jpg", jpg_quality=80)
    return base64.b64encode(data).decode("ascii")


def build_items(records: list[dict]) -> list[dict]:
    items = []
    records = sorted(records, key=lambda r: (r["doc_id"], r["page_number"]))
    opened: dict[str, "fitz.Document"] = {}
    try:
        for r in records:
            path = r.get("path")
            item = {
                "doc_id": r["doc_id"],
                "source_name": r["source_name"],
                "page_number": r["page_number"],
                "text": r.get("text", ""),
                "status": r.get("status", ""),
                "court": r.get("court"),
                "mode": r.get("mode"),
                "table_flag": bool((r.get("page_features") or {}).get("tables", 0) > 0),
                "rtl_pua_flag": bool(
                    (r.get("page_features") or {}).get("native_rtl_characters", 0) > 0
                    or (r.get("page_features") or {}).get("native_pua_characters", 0) > 0
                ),
                "suggest_discard": len((r.get("text") or "").strip()) == 0,
                "image_b64": None,
                "error": None,
            }
            if not path:
                item["error"] = "no path on record - can't render image"
                items.append(item)
                continue
            pdf_path = Path(path)
            if not pdf_path.exists():
                item["error"] = f"file not found: {pdf_path}"
                items.append(item)
                continue
            try:
                if str(pdf_path) not in opened:
                    opened[str(pdf_path)] = fitz.open(pdf_path)
                doc = opened[str(pdf_path)]
                page = doc[item["page_number"] - 1]
                item["image_b64"] = _render_b64(page)
            except Exception as exc:  # noqa: BLE001 - want this on-screen, not a crash
                item["error"] = f"could not render page: {exc}"
            items.append(item)
    finally:
        for doc in opened.values():
            doc.close()
    return items


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Final consistency review</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; display: flex; height: 100vh; }
  #imgpane { flex: 1.3; background:#1e1e1e; display:flex; align-items:center; justify-content:center; overflow:auto; padding:10px; box-sizing:border-box; }
  #imgpane img { max-width: 100%; max-height: 100%; box-shadow: 0 0 12px rgba(0,0,0,.5); }
  #textpane { flex: 1; display:flex; flex-direction:column; padding:16px; box-sizing:border-box; min-width: 380px; }
  #meta { font-size: 13px; color:#555; margin-bottom:8px; }
  #meta .badge { display:inline-block; padding:1px 7px; border-radius:10px; font-size:11px; margin-right:5px; background:#eee; }
  #meta .badge.table { background:#fde3b0; }
  #meta .badge.rtl { background:#c9e3ff; }
  textarea { flex:1; font-size:14px; line-height:1.55; padding:10px; font-family: ui-monospace, monospace; }
  #controls { display:flex; gap:10px; margin-top:10px; align-items:center; flex-wrap: wrap; }
  button { padding:9px 18px; font-size:14px; cursor:pointer; }
  #counter { margin-left:auto; color:#555; font-size: 13px; }
  label { font-size: 13px; }
  #discardRow { background:#fff3f3; padding:6px 10px; border-radius:6px; }
  #template { font-size:12px; background:#f5f5f5; padding:10px; border-radius:6px; margin-top:10px; white-space:pre-wrap; font-family: ui-monospace, monospace; max-height: 160px; overflow:auto; }
  details summary { cursor:pointer; font-size:13px; color:#555; margin-top:8px; }
</style>
</head>
<body>
<div id="imgpane"><img id="pageimg" src=""></div>
<div id="textpane">
  <div id="meta"></div>
  <textarea id="text" spellcheck="false" dir="auto"></textarea>
  <div id="controls">
    <button id="prev">&larr; Prev</button>
    <button id="next">Next &rarr;</button>
    <span id="counter"></span>
  </div>
  <div id="controls">
    <label id="discardRow"><input type="checkbox" id="discard"> Discard this page (exclude from benchmark)</label>
    <button id="export">Export corrections</button>
  </div>
  <details>
    <summary>Table template (click to expand)</summary>
    <div id="template">[TABLE]
Col A | Col B | Col C
Row1 A | Row1 B | Row1 C
Row2 A | Row2 B | Row2 C
[/TABLE]

- one row per line, cells separated by " | " exactly
- wrap the table in [TABLE] / [/TABLE] on their own lines
- empty cell -> "-", never leave blank
- merged/spanning cell -> repeat the value in each position</div>
  </details>
</div>
<script>
const items = __DATA__;
const outName = "__OUTNAME__";
const corrections = {};
let i = 0;

function key(it) { return it.doc_id + "#" + it.page_number; }

function save() {
  const it = items[i];
  corrections[key(it)] = {
    doc_id: it.doc_id, source_name: it.source_name, page_number: it.page_number,
    text: document.getElementById('text').value,
    discard: document.getElementById('discard').checked,
    original_text: it.text,
  };
}

function show() {
  const it = items[i];
  document.getElementById('counter').textContent = (i + 1) + " / " + items.length;
  let badges = "";
  if (it.table_flag) badges += '<span class="badge table">table</span>';
  if (it.rtl_pua_flag) badges += '<span class="badge rtl">rtl/pua</span>';
  document.getElementById('meta').innerHTML =
    it.source_name + "   |   page " + it.page_number + "   |   " + (it.court || "?") + " / " + (it.mode || "?") + "   " + badges +
    (it.error ? '<br><span style="color:#c00">' + it.error + '</span>' : '');
  document.getElementById('pageimg').src = it.image_b64 ? ("data:image/jpeg;base64," + it.image_b64) : "";
  const existing = corrections[key(it)];
  document.getElementById('text').value = existing ? existing.text : it.text;
  document.getElementById('discard').checked = existing ? existing.discard : it.suggest_discard;
}

document.getElementById('next').onclick = () => { save(); if (i < items.length - 1) { i++; show(); } };
document.getElementById('prev').onclick = () => { save(); if (i > 0) { i--; show(); } };
document.addEventListener('keydown', (e) => {
  if (e.altKey && e.key === 'ArrowRight') { document.getElementById('next').click(); }
  if (e.altKey && e.key === 'ArrowLeft') { document.getElementById('prev').click(); }
});
document.getElementById('export').onclick = () => {
  save();
  const blob = new Blob([JSON.stringify(Object.values(corrections), null, 2)], { type: "application/json" });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = outName;
  a.click();
};
show();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one combined final-review HTML from the merged/enriched gold benchmark.")
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("--output", type=Path, default=Path("final_review.html"))
    args = parser.parse_args()

    records = json.loads(args.benchmark.read_text(encoding="utf-8"))
    items = build_items(records)
    out_name = f"{args.output.stem}_corrections.json"
    payload = json.dumps(items, ensure_ascii=False)
    html_out = HTML_TEMPLATE.replace("__DATA__", payload).replace("__OUTNAME__", out_name)
    args.output.write_text(html_out, encoding="utf-8")

    n_errors = sum(1 for it in items if it["error"])
    n_suggest_discard = sum(1 for it in items if it["suggest_discard"])
    print(f"{len(items)} pages -> open {args.output} in any browser")
    if n_errors:
        print(f"  {n_errors} pages couldn't be rendered (missing file or bad path) - you'll see the error inline instead of an image, text is still editable")
    print(f"  {n_suggest_discard} page(s) pre-ticked 'discard' because their text is currently empty - confirm or uncheck each one")
    print(f"  Exporting from the browser downloads: {out_name}")


if __name__ == "__main__":
    main()
