"""A judgment, understood: confirmed metadata, a checked summary, and every paragraph tagged for chunking.

A language model does the reading; it never writes the record.

* Metadata: the model gives each field with the words that show it.  A field is ``confirmed`` only if
  those words are on a page and contain the value.  Otherwise it is ``rejected`` (and says why) or
  ``not_found``.  Confirmed fields carry the page and box of their evidence.
* Tags: each paragraph gets one tag from a closed list.  The paragraph's text is never touched.
* Summary: every sentence must cite paragraph ids, and every figure and name in it must appear in the
  paragraphs it cites.  A sentence that does not pass is dropped and listed.
* Chunks: sections are runs of paragraphs with one tag, each paragraph a child of its section, so a
  retriever can match a small paragraph and return the section around it.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docintel.agent.ground import ground
from docintel.understand.agent import Brain

TAGS = (
    "heading", "facts", "procedural_history", "issue", "petitioner_argument", "respondent_argument",
    "law_cited", "reasoning", "holding", "order", "other",
)
SUMMARY_TAGS = {"facts", "procedural_history", "issue", "reasoning", "holding", "order"}
SCALAR_FIELDS = ("court", "case_number", "decision_date", "petitioner", "respondent", "outcome")
LIST_FIELDS = ("judges",)
BODY_TYPES = {"text", "list", "heading"}
HEAD_PARAGRAPHS, TAIL_PARAGRAPHS = 30, 8  # where the identity and the outcome of a judgment are stated
SHOWN_CHARS = 500
WINDOW = 30  # paragraphs tagged per model call
TAG_CHARS = 300
SUMMARY_SOURCES, SUMMARY_CHARS = 40, 700

METADATA_PROMPT = """From this judgment extract, as JSON only, these fields: court, case_number, decision_date, petitioner,
respondent, outcome (each {{"value": ..., "quote": ...}} or null) and judges (a list of such objects).
"quote" is words copied exactly from the text below that show the value, and the value must appear inside the quote.
Use null for anything not stated. Never infer.

{text}
"""
TAG_PROMPT = """Label each paragraph of a judgment with exactly one of: {tags}.
Answer JSON only: {{"tags": [{{"id": "...", "tag": "..."}}]}}. Use only the ids given.

{text}
"""
SUMMARY_PROMPT = """Summarise this judgment in 3 to 6 sentences, as JSON only:
{{"sentences": [{{"text": "...", "cites": ["paragraph ids"]}}]}}.
Use only facts stated in the paragraphs a sentence cites; every sentence must cite at least one. Do not add anything.

{text}
"""


@dataclass(frozen=True)
class Para:
    id: str
    page: int
    text: str
    bbox: list[float]


def paragraphs(document: dict[str, Any]) -> list[Para]:
    found = []
    for page in document["pages"]:
        body = [b for b in page["blocks"] if b.get("type") in BODY_TYPES and not b.get("decorative") and b["text"].strip()]
        for block in sorted(body, key=lambda b: b.get("reading_order", 0)):
            found.append(Para(block["id"], page["page_number"], block["text"].strip(), list(block["bbox"])))
    return found


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w%$']+", " ", text.lower())).strip()


def _pages(paras: list[Para]) -> list[dict[str, Any]]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for para in paras:
        by_page.setdefault(para.page, []).append({"id": para.id, "text": para.text, "bbox": para.bbox})
    return [{"page_number": number, "blocks": blocks} for number, blocks in sorted(by_page.items())]


UNUSABLE = {"status": "not_found", "why": "model_answer_unusable"}


def _confirm(item: Any, pages: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(item, dict) or not item.get("value") or not item.get("quote"):
        return {"status": "not_found"}
    value, quote = str(item["value"]), str(item["quote"])
    evidence = ground(quote, pages)
    if evidence is None:
        return {"status": "rejected", "claimed": value, "why": "quote_not_found"}
    if _norm(value) not in _norm(quote):
        return {"status": "rejected", "claimed": value, "why": "value_not_in_quote"}
    return {"status": "confirmed", "value": value, "quote": quote, "page": evidence.page,
            "block_id": evidence.block_id, "bbox": evidence.bbox}


def extract_metadata(brain: Brain, paras: list[Para], rule_case_number: str | None = None) -> dict[str, Any]:
    shown = paras[:HEAD_PARAGRAPHS] + [p for p in paras[HEAD_PARAGRAPHS:][-TAIL_PARAGRAPHS:]]
    text = "\n".join(f"- {p.text[:SHOWN_CHARS]}" for p in shown)
    answer = brain.json(METADATA_PROMPT.format(text=text))
    if not isinstance(answer, dict):
        return {**{field: dict(UNUSABLE) for field in SCALAR_FIELDS}, **{field: [] for field in LIST_FIELDS}}
    pages = _pages(paras)
    meta: dict[str, Any] = {field: _confirm(answer.get(field), pages) for field in SCALAR_FIELDS}
    for field in LIST_FIELDS:
        items = answer.get(field)
        meta[field] = [_confirm(item, pages) for item in items] if isinstance(items, list) else []
    case = meta["case_number"]
    if rule_case_number and case["status"] == "confirmed":
        case["agrees_with_rules"] = _norm(rule_case_number) in _norm(case["value"]) or _norm(case["value"]) in _norm(rule_case_number)
    return meta


def tag_paragraphs(brain: Brain, paras: list[Para]) -> dict[str, str]:
    tags = {para.id: "other" for para in paras}
    for start in range(0, len(paras), WINDOW):
        window = paras[start : start + WINDOW]
        answer = brain.json(TAG_PROMPT.format(tags=", ".join(TAGS), text="\n".join(f"[{p.id}] {p.text[:TAG_CHARS]}" for p in window)))
        ids = {p.id for p in window}
        for item in (answer.get("tags") if isinstance(answer, dict) else None) or []:
            if isinstance(item, dict) and item.get("id") in ids and item.get("tag") in TAGS:
                tags[item["id"]] = item["tag"]
    return tags


NUMBER = re.compile(r"\d[\d,./-]*")
NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")


def _supported(sentence: str, cited: str) -> bool:
    """Every figure and every capitalised name (not the first word) in the sentence is in the cited text."""
    haystack, compact = _norm(cited), re.sub(r"\s", "", cited)
    figures = [number.rstrip(".,-/") for number in NUMBER.findall(sentence)]
    rest = sentence.strip().split(" ", 1)[1] if " " in sentence.strip() else ""  # the first word is capitalised anyway
    names = NAME.findall(rest)
    return all(figure in compact for figure in figures) and all(_norm(name) in haystack for name in names)


def summarise(brain: Brain, paras: list[Para], tags: dict[str, str]) -> dict[str, Any]:
    source = [p for p in paras if tags.get(p.id) in SUMMARY_TAGS][:SUMMARY_SOURCES] or paras[:HEAD_PARAGRAPHS]
    answer = brain.json(SUMMARY_PROMPT.format(text="\n".join(f"[{p.id}] {p.text[:SUMMARY_CHARS]}" for p in source)))
    if not isinstance(answer, dict):
        return {"sentences": [], "rejected": [], "note": "model_answer_unusable"}
    by_id = {p.id: p for p in paras}
    kept, rejected = [], []
    for item in (answer.get("sentences") if isinstance(answer, dict) else None) or []:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        text, cites = str(item["text"]).strip(), item.get("cites") if isinstance(item.get("cites"), list) else []
        if not cites:
            rejected.append({"text": text, "why": "no_citation"})
        elif any(cite not in by_id for cite in cites):
            rejected.append({"text": text, "why": "unknown_paragraph"})
        elif not _supported(text, " ".join(by_id[cite].text for cite in cites)):
            rejected.append({"text": text, "why": "unsupported_figure_or_name"})
        else:
            kept.append({"text": text, "cites": cites})
    return {"sentences": kept, "rejected": rejected}


def chunk_tree(paras: list[Para], tags: dict[str, str]) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    entries = []
    for para in paras:
        tag = tags.get(para.id, "other")
        if not sections or sections[-1]["tag"] != tag:
            sections.append({"id": f"s{len(sections) + 1}", "tag": tag, "title": para.text if tag == "heading" else None,
                             "paragraph_ids": [], "pages": [para.page, para.page], "texts": []})
        section = sections[-1]
        section["paragraph_ids"].append(para.id)
        section["texts"].append(para.text)
        section["pages"][1] = para.page
        entries.append({"id": para.id, "section": section["id"], "tag": tag, "page": para.page, "bbox": para.bbox, "text": para.text})
    for section in sections:
        section["text"] = "\n\n".join(section.pop("texts"))
    return {"sections": sections, "paragraphs": entries}


class _Counted:
    """The brain, counting how many of its answers were unusable: a failure must not look like an absent field."""

    def __init__(self, brain: Brain) -> None:
        self._brain, self.total, self.unusable = brain, 0, 0

    def json(self, prompt: str) -> dict[str, Any] | None:
        answer = self._brain.json(prompt)
        self.total += 1
        self.unusable += not isinstance(answer, dict)
        return answer


def analyse_judgment(document: dict[str, Any], model: Brain) -> dict[str, Any]:
    brain = _Counted(model)
    paras = paragraphs(document)
    meta = extract_metadata(brain, paras, (document.get("document", {}).get("metadata") or {}).get("case_number"))
    tags = tag_paragraphs(brain, paras)
    summary = summarise(brain, paras, tags)
    tree = chunk_tree(paras, tags)
    flat = [meta[f] for f in SCALAR_FIELDS] + [item for f in LIST_FIELDS for item in meta[f]]
    return {
        "metadata": meta, "summary": summary, **tree,
        "model_calls": {"total": brain.total, "unusable": brain.unusable},
        "needs_review": bool(summary["rejected"]) or brain.unusable > 0 or any(item["status"] == "rejected" for item in flat),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Metadata, summary and paragraph tags for one parsed judgment.")
    parser.add_argument("parsed", type=Path, help="a parsed <stem>.json")
    parser.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct", help="text model: a hub id or folder (downloaded on first use)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dtype", choices=["float16", "bfloat16", "float32"], help="default: what the GPU supports")
    args = parser.parse_args()
    from docintel.agent.brain import HFBrain

    brain = HFBrain(args.model, args.dtype)
    replies: list[str | None] = []
    original = brain.json
    brain.json = lambda prompt: (lambda answer: (replies.append(brain.last_reply), answer)[1])(original(prompt))  # type: ignore[method-assign]
    result = analyse_judgment(json.loads(args.parsed.read_text(encoding="utf-8")), brain)
    # What the model actually said, so an unusable answer can be read, not guessed at.
    args.out.with_suffix(".replies.json").write_text(json.dumps(replies, ensure_ascii=False, indent=2), encoding="utf-8")
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
