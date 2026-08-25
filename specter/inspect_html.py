"""Build a self-contained page to eyeball parsed output against the source.

Every claim the pipeline makes is spatial -- this block is a heading, that value
sits in this box -- and the only honest way to check a bounding box is to draw
it on the page it points at.  This renders each page beside its blocks, with
every box drawn where the parser says it is.

    python -m specter inspect artifacts/run --out artifacts/inspect/index.html

Pages are rendered as JPEG and embedded, so the result is one file that opens
anywhere with no server and no assets folder.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path
from typing import Any

import fitz


TYPE_COLOURS = {
    "heading": "#d946ef",
    "table": "#0ea5e9",
    "form_header": "#64748b",
    "header": "#94a3b8",
    "footer": "#94a3b8",
    "list": "#f59e0b",
    "text": "#22c55e",
}


def page_image(page: fitz.Page, scale: float, quality: int) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return base64.b64encode(pixmap.tobytes("jpeg", jpg_quality=quality)).decode("ascii")


def block_rows(page: dict[str, Any]) -> str:
    rows = []
    for block in page["blocks"]:
        colour = TYPE_COLOURS.get(block["type"], "#22c55e")
        confidence = block.get("confidence") or {}
        score = confidence.get("score") if isinstance(confidence, dict) else confidence
        flag = ""
        if block.get("text_status"):
            flag = f' <span class="warn">{html.escape(", ".join(block["text_status"].get("reasons", [])))}</span>'
        rows.append(
            f'<div class="row" data-block="{block["reading_order"]}">'
            f'<div class="meta"><span class="chip" style="background:{colour}">{html.escape(block["type"])}</span>'
            f'<span class="ord">#{block["reading_order"]}</span>'
            f'<span class="score">{"" if score is None else f"{float(score):.2f}"}</span>{flag}</div>'
            f'<div class="txt">{html.escape(block["text"][:4000]) or "<em>(empty)</em>"}</div>'
            f"</div>"
        )
    return "".join(rows) or '<p class="muted">No blocks on this page.</p>'


def page_section(document: dict[str, Any], page: dict[str, Any], image: str, scale: float, index: int) -> str:
    boxes = []
    for block in page["blocks"]:
        x0, y0, x1, y1 = block["bbox"]
        colour = TYPE_COLOURS.get(block["type"], "#22c55e")
        boxes.append(
            f'<div class="box" data-block="{block["reading_order"]}" title="{html.escape(block["type"])} #{block["reading_order"]}" '
            f'style="left:{x0 * scale:.1f}px;top:{y0 * scale:.1f}px;'
            f'width:{max(1.0, (x1 - x0) * scale):.1f}px;height:{max(1.0, (y1 - y0) * scale):.1f}px;'
            f'border-color:{colour}"><span style="background:{colour}">{block["reading_order"]}</span></div>'
        )
    excluded = ""
    if page.get("excluded"):
        excluded = f'<span class="warn">excluded: {html.escape(str(page.get("exclusion_reason")))}</span>'
    return (
        f'<section class="page" id="p{index}">'
        f'<h3>{html.escape(document["source_name"])} &mdash; page {page["page_number"]} '
        f'<small>{html.escape(page.get("extraction", ""))} &middot; {len(page["blocks"])} blocks</small> {excluded}</h3>'
        f'<div class="split">'
        f'<div class="canvas"><img src="data:image/jpeg;base64,{image}" alt="page {page["page_number"]}">{"".join(boxes)}</div>'
        f'<div class="blocks">{block_rows(page)}</div>'
        f"</div></section>"
    )


def document_header(document: dict[str, Any]) -> str:
    metadata = document.get("metadata") or {}
    provenance = document.get("metadata_provenance") or {}
    fields = []
    for key in ("case_number", "court", "decision_date", "hearing_date", "judges", "parties"):
        value = metadata.get(key)
        if not value:
            continue
        source = (provenance.get(key) or {}).get("source", "")
        located = "located" if (provenance.get(key) or {}).get("bbox") else source or "no source span"
        fields.append(
            f'<tr><th>{html.escape(key)}</th><td>{html.escape(str(value)[:200])}</td>'
            f'<td class="muted">{html.escape(located)}</td></tr>'
        )
    findings = (document.get("label_check") or {}).get("findings") or []
    if findings:
        items = "".join(
            f'<li><b>{html.escape(f["field"])}</b>: {html.escape(f["state"])}'
            + (f' &mdash; filename says {html.escape(str(f.get("label")))}, page says {html.escape(str(f.get("extracted"))[:80])}'
               if f["state"] == "disagrees" else f' &mdash; {html.escape(str(f.get("label")))}')
            + "</li>"
            for f in findings
        )
        fields.append(f'<tr><th>label check</th><td colspan="2"><ul class="findings">{items}</ul></td></tr>')
    router = document.get("router") or {}
    diagnostics = document.get("diagnostics") or {}
    return (
        f'<div class="doc"><h2>{html.escape(document["source_name"])}</h2>'
        f'<p class="muted">{html.escape(str(router.get("mode", "")))} &middot; '
        f'{document.get("page_count", 0)} pages &middot; confidence {document.get("confidence", 0)} &middot; '
        f'{html.escape(str(diagnostics.get("status", "")))}</p>'
        f'<table class="meta-table">{"".join(fields)}</table></div>'
    )


STYLE = """
:root { color-scheme: light dark; }
body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #0b0f19; color: #e5e7eb; }
header.top { position: sticky; top: 0; background: #111827; padding: 12px 18px; border-bottom: 1px solid #374151; z-index: 5; }
h1 { font-size: 17px; margin: 0; }
.doc { padding: 18px; border-top: 3px solid #374151; }
h2 { font-size: 15px; margin: 0 0 4px; }
h3 { font-size: 13px; font-weight: 600; margin: 22px 0 8px; }
h3 small { font-weight: 400; color: #9ca3af; }
.muted { color: #9ca3af; font-size: 12px; }
.warn { color: #fca5a5; font-size: 11px; border: 1px solid #7f1d1d; padding: 1px 5px; border-radius: 4px; }
.meta-table { border-collapse: collapse; font-size: 12px; }
.meta-table th { text-align: left; padding: 2px 12px 2px 0; color: #9ca3af; font-weight: 500; vertical-align: top; }
.meta-table td { padding: 2px 12px 2px 0; vertical-align: top; }
.findings { margin: 2px 0; padding-left: 16px; }
.split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.canvas { position: relative; display: inline-block; line-height: 0; border: 1px solid #374151; }
.canvas img { max-width: 100%; height: auto; display: block; }
.box { position: absolute; border: 1.5px solid; background: rgba(255,255,255,0.03); cursor: pointer; }
.box span { position: absolute; top: -1px; left: -1px; font: 9px/1.4 monospace; color: #0b0f19; padding: 0 3px; }
.box.on { background: rgba(250, 204, 21, 0.28); border-width: 2.5px; }
.blocks { max-height: 88vh; overflow: auto; font-size: 12px; }
.row { border-bottom: 1px solid #1f2937; padding: 6px 4px; }
.row.on { background: #1f2937; }
.meta { display: flex; gap: 8px; align-items: center; margin-bottom: 3px; }
.chip { color: #0b0f19; border-radius: 4px; padding: 0 6px; font-size: 10px; font-weight: 600; }
.ord, .score { color: #6b7280; font: 10px monospace; }
.txt { white-space: pre-wrap; word-break: break-word; }
@media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
"""

SCRIPT = """
document.addEventListener('mouseover', function (event) {
  var node = event.target.closest('.box, .row');
  if (!node) return;
  var section = node.closest('.page');
  var id = node.getAttribute('data-block');
  section.querySelectorAll('.box, .row').forEach(function (el) {
    el.classList.toggle('on', el.getAttribute('data-block') === id);
  });
});
document.addEventListener('click', function (event) {
  var box = event.target.closest('.box');
  if (!box) return;
  var section = box.closest('.page');
  var row = section.querySelector('.row[data-block="' + box.getAttribute('data-block') + '"]');
  if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
});
"""


def build(results: list[dict[str, Any]], scale: float, quality: int, max_pages: int) -> str:
    sections = []
    index = 0
    for result in results:
        document = result["document"]
        source = Path(document["source_file"])
        if not source.exists():
            sections.append(f'<div class="doc"><h2>{html.escape(document["source_name"])}</h2>'
                            f'<p class="warn">source PDF not found at {html.escape(str(source))}</p></div>')
            continue
        sections.append(document_header(document))
        with fitz.open(source) as pdf:
            for page in result["pages"][:max_pages]:
                number = page["page_number"]
                if number > pdf.page_count:
                    continue
                index += 1
                sections.append(page_section(document, page, page_image(pdf[number - 1], scale, quality), scale, index))
    return (
        "<!doctype html><meta charset='utf-8'><title>Specter output inspector</title>"
        f"<style>{STYLE}</style>"
        "<header class='top'><h1>Specter output inspector</h1>"
        "<p class='muted'>Boxes are drawn where the parser says each block is. "
        "Hover a box or a block to link the two; click a box to scroll to its text.</p></header>"
        f"{''.join(sections)}<script>{SCRIPT}</script>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render parsed documents beside their source pages, with bounding boxes drawn.")
    parser.add_argument("json", nargs="+", type=Path, help="parsed .json files, or a folder of them")
    parser.add_argument("--out", type=Path, default=Path("artifacts/inspect/index.html"))
    parser.add_argument("--scale", type=float, default=1.4, help="page render scale")
    parser.add_argument("--quality", type=int, default=55, help="embedded JPEG quality")
    parser.add_argument("--max-pages", type=int, default=6, help="pages per document (keeps the file openable)")
    args = parser.parse_args()

    paths: list[Path] = []
    for item in args.json:
        if item.is_dir():
            paths.extend(sorted(p for p in item.glob("*.json") if p.name != "manifest.json"))
        else:
            paths.append(item)
    results = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "pages" in payload:
            results.append(payload)
    if not results:
        parser.error("No parsed documents found")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(results, args.scale, args.quality, args.max_pages), encoding="utf-8")
    size = args.out.stat().st_size / 1e6
    print(json.dumps({"documents": len(results), "output": str(args.out), "megabytes": round(size, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
