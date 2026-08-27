#!/usr/bin/env python3
"""Turn a gold_manifest.json into one self-contained HTML file for review.

For every selected page: renders the page as an image and pulls whatever
text can already be read from the PDF as a starting draft. You open the
HTML file in any browser, look at the image, fix the draft text where it's
wrong (for most digital English pages this is nothing or a few words), and
click Next. No typing from a blank page, no server, no install beyond
PyMuPDF - the output is one HTML file you just double-click.

Do this in small batches, not all 250 documents at once:

    python prepare_review.py gold_manifest.json --start 0   --limit 20 --output batch1.html
    python prepare_review.py gold_manifest.json --start 20  --limit 20 --output batch2.html

Open batch1.html in your browser, work through it, click "Export
corrections" when done - that downloads gold_corrections_batch1.json.
Do the next batch whenever you have another 20 minutes.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import fitz  # PyMuPDF
import re

_COURT_RE = re.compile(
    r"(LAHORE|SINDH|ISLAMABAD|PESHAWAR|BALOCHISTAN)\s+HIGH\s+COURT|SUPREME\s+COURT\s+OF\s+PAKISTAN|FEDERAL\s+SHARIAT\s+COURT",
    re.I,
)
_CASE_NO_RE = re.compile(r"\b([A-Z][a-zA-Z .]{2,40}?No\.?\s*[\w][\w\-/]*(?:\s+of\s+\d{4})?)\b")
_VERSUS_RE = re.compile(r"\n\s*(.+?)\s*\n\s*(?:versus|vs\.?|v\.)\s*\n\s*(.+?)\s*\n", re.I)

# A page only needs a human to look at it if it has one of these risk
# signals. Pages with none of these were checked across your first 20
# documents and came back correct every time - don't spend time on them
# again, just accept the draft as-is.
RISK_KEYS = ("tables", "native_rtl_characters", "native_pua_characters", "dominant_image")


def needs_review(features: dict) -> bool:
    # An empty page needs a human look too - a blank draft could mean a
    # genuinely blank page (fine) or an extraction that silently found
    # nothing on a page that isn't actually blank (not fine). No flag in the
    # feature set distinguishes those, so don't auto-accept either case.
    if features.get("native_characters", 1) == 0:
        return True
    return bool(
        features.get("tables", 0) > 0
        or features.get("native_rtl_characters", 0) > 0
        or features.get("native_pua_characters", 0) > 0
        or features.get("dominant_image", False)
    )


def _draft_text(page: "fitz.Page", features: dict) -> str:
    raw = page.get_text("text") or ""

    court = _COURT_RE.search(raw)
    case_no = _CASE_NO_RE.search(raw)
    parties = _VERSUS_RE.search(raw)
    meta_lines = []
    if court:
        meta_lines.append(f"Court: {re.sub(r'\\s+', ' ', court.group(0)).strip()}")
    if case_no:
        meta_lines.append(f"Case No: {case_no.group(1).strip()}")
    if parties:
        meta_lines.append(f"Parties: {parties.group(1).strip()} vs {parties.group(2).strip()}")
    meta_block = "\n".join(meta_lines)

    # No table reconstruction. It guessed wrong on real pages, sometimes
    # badly, and fixing a bad guess costs more time than typing the table
    # by hand costs. Just the exact page text - fix tables yourself when
    # you see one, same as any other correction.
    return "\n".join(filter(None, [meta_block, "", raw.strip()]))


def _render_b64(page: "fitz.Page", zoom: float = 1.8) -> str:
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes("jpg", jpg_quality=80)
    return base64.b64encode(data).decode("ascii")


def build_items(manifest_path: Path, start: int, limit: int | None) -> tuple[list[dict], list[dict]]:
    """Returns (needs_review, auto_accepted). auto_accepted pages have no
    table/RTL/PUA/scan risk signal - based on your own check of the first
    20 documents, those come back correct on their own, so they're written
    straight to gold without spending your time on them again."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = manifest["documents"]
    docs = docs[start : start + limit] if limit else docs[start:]
    needs_review_items: list[dict] = []
    auto_accepted: list[dict] = []
    for d in docs:
        pdf_path = Path(d["path"])
        if not pdf_path.exists():
            needs_review_items.append({
                "doc_id": d["doc_id"], "source_name": d["source_name"],
                "page_number": None, "error": f"file not found: {pdf_path}",
            })
            continue
        doc = fitz.open(pdf_path)
        try:
            for sp in d["selected_pages"]:
                pno = sp["page_number"]
                page = doc[pno - 1]
                features = sp.get("features", {})
                draft = _draft_text(page, features)
                if needs_review(features):
                    needs_review_items.append({
                        "doc_id": d["doc_id"],
                        "source_name": d["source_name"],
                        "page_number": pno,
                        "reasons": sp.get("reasons", []),
                        "draft": draft,
                        "image_b64": _render_b64(page),
                    })
                else:
                    auto_accepted.append({
                        "doc_id": d["doc_id"], "source_name": d["source_name"],
                        "page_number": pno, "text": draft, "reviewed": False,
                        "note": "auto-accepted: no table/rtl/pua/scan signal on this page",
                    })
        finally:
            doc.close()
    return needs_review_items, auto_accepted


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Gold review</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0; display: flex; height: 100vh; }
  #imgpane { flex: 1.3; background:#1e1e1e; display:flex; align-items:center; justify-content:center; overflow:auto; padding:10px; box-sizing:border-box; }
  #imgpane img { max-width: 100%; max-height: 100%; box-shadow: 0 0 12px rgba(0,0,0,.5); }
  #textpane { flex: 1; display:flex; flex-direction:column; padding:16px; box-sizing:border-box; }
  #meta { font-size: 13px; color:#555; margin-bottom:8px; }
  textarea { flex:1; font-size:15px; line-height:1.6; padding:10px; unicode-bidi: plaintext; }
  #controls { display:flex; gap:10px; margin-top:10px; align-items:center; flex-wrap: wrap; }
  button { padding:9px 18px; font-size:14px; cursor:pointer; }
  #counter { margin-left:auto; color:#555; font-size: 13px; }
  label { font-size: 13px; }
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
    <label><input type="checkbox" id="flag"> Not sure about this one</label>
    <button id="export">Export corrections</button>
    <span id="counter"></span>
  </div>
</div>
<script>
const items = __DATA__;
const outName = "__OUTNAME__";
const corrections = {};
let i = 0;

function key(it) { return it.doc_id + "#" + it.page_number; }

function save() {
  const it = items[i];
  if (it.page_number === null) return;
  corrections[key(it)] = {
    doc_id: it.doc_id, source_name: it.source_name, page_number: it.page_number,
    text: document.getElementById('text').value,
    flagged: document.getElementById('flag').checked
  };
}

function show() {
  const it = items[i];
  document.getElementById('counter').textContent = (i + 1) + " / " + items.length;
  if (it.page_number === null) {
    document.getElementById('meta').textContent = it.source_name + "  -  " + it.error;
    document.getElementById('pageimg').src = "";
    document.getElementById('text').value = "";
    document.getElementById('flag').checked = false;
    return;
  }
  document.getElementById('meta').textContent =
    it.source_name + "   |   page " + it.page_number + "   |   why picked: " + (it.reasons || []).join(", ");
  document.getElementById('pageimg').src = "data:image/jpeg;base64," + it.image_b64;
  const existing = corrections[key(it)];
  document.getElementById('text').value = existing ? existing.text : it.draft;
  document.getElementById('flag').checked = existing ? existing.flagged : false;
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
    parser = argparse.ArgumentParser(description="Build a self-contained HTML review batch from a gold manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--start", type=int, default=0, help="index of first document in documents[] to include")
    parser.add_argument("--limit", type=int, default=20, help="how many documents in this batch")
    parser.add_argument("--output", type=Path, default=Path("review_batch.html"))
    args = parser.parse_args()

    items, auto_accepted = build_items(args.manifest, args.start, args.limit)
    out_name = f"gold_corrections_{args.output.stem}.json"
    payload = json.dumps(items, ensure_ascii=False)
    html_out = HTML_TEMPLATE.replace("__DATA__", payload).replace("__OUTNAME__", out_name)
    args.output.write_text(html_out, encoding="utf-8")

    auto_path = args.output.with_name(f"auto_accepted_{args.output.stem}.json")
    auto_path.write_text(json.dumps(auto_accepted, ensure_ascii=False, indent=2), encoding="utf-8")

    n_pages = sum(1 for it in items if it.get("page_number") is not None)
    total = n_pages + len(auto_accepted)
    print(f"{total} pages total this batch.")
    print(f"  {len(auto_accepted)} auto-accepted, no review needed -> {auto_path}")
    print(f"  {n_pages} need your eyes -> open {args.output} in any browser")
    print(f"  Exporting from the browser downloads: {out_name}")
    print(f"  Combine {auto_path} + {out_name} afterward - together they're your full gold set for this batch.")


if __name__ == "__main__":
    main()
