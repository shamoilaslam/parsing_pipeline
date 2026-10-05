"""Component-level diagnostics for canonical Specter documents.

Validators only observe and produce retry recommendations. They never rewrite
text, boxes, metadata, or reading order.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1]) if len(box) == 4 else 0.0


def _iou(left: list[float], right: list[float]) -> float:
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    total = _area(left) + _area(right) - overlap
    return overlap / total if total else 0.0


def _score(checks: list[bool]) -> float:
    return sum(checks) / len(checks) if checks else 1.0


def validate_bboxes(document: dict[str, Any]) -> dict[str, Any]:
    checks = []
    issues = []
    block_count = 0
    for page in document.get("pages", []):
        width, height = float(page.get("width", 0)), float(page.get("height", 0))
        blocks = page.get("blocks", [])
        block_count += len(blocks)
        for block in blocks:
            box = block.get("bbox", [])
            valid = len(box) == 4 and _area(box) > 0 and box[0] >= -2 and box[1] >= -2 and box[2] <= width + 2 and box[3] <= height + 2
            checks.append(valid)
            if not valid:
                issues.append({"page": page.get("page_number"), "block": block.get("id"), "reason": "bbox_out_of_page_or_empty", "bbox": box})
            for span in block.get("spans", []):
                for char_box in span.get("char_bboxes", []):
                    # Combining marks can legitimately have zero horizontal
                    # width; require valid extents and page containment, not
                    # a positive area for every glyph.
                    checks.append(len(char_box) == 4 and char_box[2] >= char_box[0] and char_box[3] >= char_box[1] and box[0] - 2 <= char_box[0] <= box[2] + 2 and box[1] - 2 <= char_box[1] <= box[3] + 2)
    return {"score": round(_score(checks), 4), "blocks": block_count, "issues": issues[:100]}


def validate_reading_order(document: dict[str, Any]) -> dict[str, Any]:
    issues = []
    page_scores = []
    global_orders = []
    for page in document.get("pages", []):
        orders = [block.get("reading_order") for block in page.get("blocks", [])]
        valid = orders == list(range(len(orders))) and len(set(orders)) == len(orders)
        page_scores.append(valid)
        global_orders.extend(block.get("document_reading_order") for block in page.get("blocks", []))
        if not valid:
            issues.append({"page": page.get("page_number"), "orders": orders})
    global_valid = global_orders == list(range(len(global_orders))) and len(set(global_orders)) == len(global_orders)
    if not global_valid:
        issues.append({"reason": "non_contiguous_document_reading_order", "orders": global_orders[:100]})
    checks = page_scores + [global_valid]
    return {"score": round(_score(checks), 4), "pages": len(page_scores), "global_valid": global_valid, "issues": issues}


def validate_headers_footers(document: dict[str, Any]) -> dict[str, Any]:
    bands: dict[str, dict[str, set[int]]] = {"header": defaultdict(set), "footer": defaultdict(set)}
    repeated = {"header": set(), "footer": set()}
    for page in document.get("pages", []):
        page_no = int(page.get("page_number", 0))
        for block in page.get("blocks", []):
            kind = block.get("type")
            if kind not in bands:
                continue
            key = re.sub(r"\d+", "#", re.sub(r"\s+", " ", str(block.get("text", "")).casefold())).strip()
            if key:
                bands[kind][key].add(page_no)
    for kind in bands:
        repeated[kind] = {key for key, pages in bands[kind].items() if len(pages) >= 2}
    issues = []
    for kind in bands:
        for key, pages in bands[kind].items():
            if len(pages) == 1 and len(document.get("pages", [])) >= 4:
                issues.append({"kind": kind, "signature": key, "reason": "single_page_band_candidate", "page": sorted(pages)[0]})
    observed = sum(len(pages) for kind in bands for pages in bands[kind].values())
    repeated_count = sum(len(pages) for kind in repeated for key, pages in bands[kind].items() if key in repeated[kind])
    return {"score": round(repeated_count / observed, 4) if observed else 1.0, "repeated_signatures": {kind: sorted(values) for kind, values in repeated.items()}, "issues": issues[:100]}


def validate_tables(document: dict[str, Any]) -> dict[str, Any]:
    tables = [block for page in document.get("pages", []) for block in page.get("blocks", []) if block.get("type") == "table"]
    table_results = []
    for table in tables:
        rows = table.get("rows", [])
        widths = [len(row) for row in rows]
        cells = table.get("cells", [])
        nonempty = sum(bool(str(cell.get("text", "")).strip()) for cell in cells) if cells else sum(bool(str(value).strip()) for row in rows for value in row)
        expected = sum(widths)
        result = {
            "id": table.get("id"),
            "rows": len(rows),
            "columns": max(widths or [0]),
            "rectangular": bool(widths) and len(set(widths)) == 1,
            "cell_coverage": nonempty / expected if expected else 0.0,
            "has_cell_bboxes": bool(cells) and all(len(cell.get("bbox", [])) == 4 for cell in cells),
            "issues": [],
        }
        if not result["rectangular"]:
            result["issues"].append("ragged_rows")
        # Blank cells are valid in legal schedules (for example a merged
        # respondent cell spanning several rows).  Candidate rejection in the
        # parser handles low-fill prose false positives; validation only flags
        # genuinely sparse structures.
        if result["cell_coverage"] < 0.65:
            result["issues"].append("empty_cells")
        if not result["has_cell_bboxes"]:
            result["issues"].append("missing_cell_bboxes")
        table_results.append(result)
    checks = [not result["issues"] for result in table_results]
    return {"score": round(_score(checks), 4), "tables": len(table_results), "results": table_results}


def validate_text_continuity(document: dict[str, Any]) -> dict[str, Any]:
    issues = []
    checks = []
    for page in document.get("pages", []):
        seen = Counter()
        previous_end = 0
        for block in page.get("blocks", []):
            text = re.sub(r"\s+", " ", str(block.get("text", "")).casefold()).strip()
            if not text:
                continue
            seen[text] += 1
            start, end = block.get("start_index"), block.get("end_index")
            valid = start is None or end is None or (int(start) >= previous_end and int(end) >= int(start))
            checks.append(valid)
            if not valid:
                issues.append({"page": page.get("page_number"), "block": block.get("id"), "reason": "non_monotonic_text_indices"})
            previous_end = max(previous_end, int(end or previous_end))
        for text, count in seen.items():
            if count > 1 and len(text) > 20:
                issues.append({"page": page.get("page_number"), "reason": "duplicate_block_text", "count": count, "text": text[:120]})
    return {"score": round(_score(checks), 4), "issues": issues[:100]}


def validate_paragraph_geometry(document: dict[str, Any]) -> dict[str, Any]:
    """Check that semantic paragraphs have one enclosing bbox and are not fragments."""
    checks = []
    issues = []
    paragraph_count = 0
    for page in document.get("pages", []):
        blocks = page.get("blocks", [])
        for index, block in enumerate(blocks):
            if block.get("type") not in {"text", "list", "heading"}:
                continue
            paragraph_count += 1
            box = block.get("bbox", [])
            line_boxes = block.get("line_bboxes", []) or [box]
            encloses_lines = len(box) == 4 and all(
                len(line_box) == 4
                and box[0] - 1 <= line_box[0] <= line_box[2] <= box[2] + 1
                and box[1] - 1 <= line_box[1] <= line_box[3] <= box[3] + 1
                for line_box in line_boxes
            )
            checks.append(encloses_lines)
            if not encloses_lines:
                issues.append({"page": page.get("page_number"), "block": block.get("id"), "reason": "bbox_does_not_enclose_all_lines"})
            if index + 1 < len(blocks):
                nxt = blocks[index + 1]
                if block.get("type") == nxt.get("type") == "text":
                    left_gap = abs(float(block.get("bbox", [0])[0]) - float(nxt.get("bbox", [0])[0]))
                    vertical_gap = float(nxt.get("bbox", [0, 0])[1]) - float(block.get("bbox", [0, 0, 0, 0])[3])
                    line_height = statistics.median([max(1.0, line[3] - line[1]) for line in line_boxes if len(line) == 4] or [10.0])
                    if len(str(block.get("text", ""))) >= 40 and len(str(nxt.get("text", ""))) >= 40 and left_gap <= 8 and 0 <= vertical_gap <= line_height * 0.65:
                        issues.append({"page": page.get("page_number"), "block": block.get("id"), "next_block": nxt.get("id"), "reason": "likely_unmerged_paragraph_fragments"})
    return {"score": round(_score(checks), 4), "paragraphs": paragraph_count, "issues": issues[:100]}


def validate_numbering(document: dict[str, Any]) -> dict[str, Any]:
    """Check numbered judgment paragraphs for gaps, regressions, and duplicates."""
    number_re = re.compile(r"^\s*(\d{1,4})[.)]\s+(?=[A-Z\"“‘(])")
    numbered = []
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") in {"header", "footer", "table", "form_header"}:
                continue
            match = number_re.match(str(block.get("text", "")))
            # A legal section such as ``311. Ta'zir ...`` is not a judgment
            # paragraph.  Keep the validator focused on the usual paragraph
            # numbering range; statutes remain available in metadata.
            if match and int(match.group(1)) < 100:
                sizes = block.get("font_sizes", []) or [0.0]
                numbered.append({
                    "page": page.get("page_number"),
                    "block": block.get("id"),
                    "number": int(match.group(1)),
                    # Indentation and font size separate the judgment's main
                    # paragraphs from quoted judgments, statutory lists, and
                    # nested enumerations that legitimately restart numbering.
                    "sequence_key": (round(float(block.get("bbox", [0])[0]) / 10), round(statistics.median(sizes), 1)),
                })
    transitions = []
    issues = []
    groups: dict[tuple[int, float], list[dict[str, Any]]] = defaultdict(list)
    for item in numbered:
        groups[item["sequence_key"]].append(item)
    for sequence_key, sequence in groups.items():
        previous = None
        for item in sequence:
            current = item["number"]
            if previous is None or current == 1:
                previous = current
                continue
            expected = previous + 1
            valid = current == expected
            transitions.append(valid)
            if current > expected:
                issues.append({**item, "reason": "numbering_gap", "expected": expected, "sequence_key": sequence_key})
            elif current <= previous:
                issues.append({**item, "reason": "numbering_regression_or_duplicate", "previous": previous, "sequence_key": sequence_key})
            previous = current
    return {
        "score": round(_score(transitions), 4),
        "numbered_blocks": len(numbered),
        "transitions": len(transitions),
        "sequence_groups": len(groups),
        "issues": issues[:100],
    }


def validate_metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata", {})
    provenance = document.get("metadata_provenance", {})
    fields = ["court", "case_number", "decision_date", "parties"]
    present = [field for field in fields if metadata.get(field)]
    missing = [field for field in fields if field not in present]
    provenance_missing = [field for field in present if not provenance.get(field, {}).get("source")]
    score = (len(present) / len(fields)) * (1.0 if not provenance_missing else 0.75)
    return {
        "score": round(score, 4),
        "present": present,
        "missing": missing,
        "provenance_missing": provenance_missing,
    }


def validate(document: dict[str, Any]) -> dict[str, Any]:
    components = {
        "reading_order": validate_reading_order(document),
        "headers_footers": validate_headers_footers(document),
        "tables": validate_tables(document),
        "bboxes": validate_bboxes(document),
        "text_continuity": validate_text_continuity(document),
        "paragraph_geometry": validate_paragraph_geometry(document),
        "numbering": validate_numbering(document),
        "metadata": validate_metadata(document.get("document", {})),
    }
    retry = [name for name, result in components.items() if float(result.get("score", 0.0)) < 0.90]
    return {
        "quality_profile": {name: result.get("score", 0.0) for name, result in components.items()},
        "components": components,
        "retry_components": retry,
        "policy": "diagnostic only; validators never rewrite parser output",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Specter JSON document without rewriting it.")
    parser.add_argument("prediction", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(json.loads(args.prediction.read_text(encoding="utf-8")))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
