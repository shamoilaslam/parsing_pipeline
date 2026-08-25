import json

notebook_path = "Specter_Production_Parser_Lab.ipynb"
with open(notebook_path, "r", encoding="utf-8") as f:
    nb = json.load(f)

def create_markdown(source):
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [s + "\n" for s in source.split("\n")[:-1]] + ([source.split("\n")[-1]] if source.split("\n")[-1] else [])
    }

def create_code(source):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [s + "\n" for s in source.split("\n")[:-1]] + ([source.split("\n")[-1]] if source.split("\n")[-1] else [])
    }

c1 = create_markdown("# 3. RTL Text Analysis and Bidirectional Correction")

c2 = create_code('''import json
from bidi.algorithm import get_display
from rich.table import Table
from rich import print

def get_script_type(char):
    if not char or not char.strip():
        return "SPACE"
    cp = ord(char)
    # Arabic, Arabic Supplement, Arabic Extended-A, Arabic Presentation Forms-A, Arabic Presentation Forms-B
    if (0x0600 <= cp <= 0x06FF or 
        0x0750 <= cp <= 0x077F or 
        0x08A0 <= cp <= 0x08FF or 
        0xFB50 <= cp <= 0xFDFF or 
        0xFE70 <= cp <= 0xFEFF):
        return "RTL"
    if 0x0000 <= cp <= 0x024F:
        if char.isdigit():
            return "NUM"
        return "LTR"
    return "OTHER"''')

c3 = create_code('''def extract_and_correct_runs(line_chars):
    runs = []
    current_run_chars = []
    current_script = None
    
    for char_info in line_chars:
        char = char_info.get("char", "")
        script = get_script_type(char)
        
        if script == "SPACE":
            if current_script is not None:
                current_run_chars.append(char_info)
            continue
            
        if script != current_script:
            if current_run_chars:
                runs.append({"script": current_script, "chars": current_run_chars})
            current_script = script
            current_run_chars = [char_info]
        else:
            current_run_chars.append(char_info)
            
    if current_run_chars:
        runs.append({"script": current_script, "chars": current_run_chars})
        
    corrected_runs = []
    contains_rtl = False
    needs_bidi = False
    
    char_index_offset = 0
    
    for run in runs:
        script = run["script"]
        chars = run["chars"]
        text = "".join(c.get("char", "") for c in chars)
        
        run_length = len(text)
        char_start = char_index_offset
        char_end = char_index_offset + run_length
        char_index_offset = char_end
        
        bboxes = [c.get("bbox", []) for c in chars if c.get("bbox")]
        if bboxes:
            min_x = min(b[0] for b in bboxes)
            min_y = min(b[1] for b in bboxes)
            max_x = max(b[2] for b in bboxes)
            max_y = max(b[3] for b in bboxes)
            run_bbox = [min_x, min_y, max_x, max_y]
        else:
            run_bbox = []
        
        is_visual = False
        corrected_text = text
        
        if script == "RTL":
            contains_rtl = True
            non_space = [c for c in chars if c.get("char", "").strip()]
            if len(non_space) > 1:
                first_x = non_space[0].get("bbox", [0])[0]
                last_x = non_space[-1].get("bbox", [0])[0]
                is_visual = first_x < last_x
            elif len(non_space) == 1:
                is_visual = True
                
            if is_visual:
                needs_bidi = True
                corrected_text = get_display(text)
                
        corrected_runs.append({
            "script": script,
            "original_text": text,
            "corrected_text": corrected_text,
            "is_visual_order": is_visual if script == "RTL" else None,
            "char_start": char_start,
            "char_end": char_end,
            "bbox": run_bbox
        })
        
    return corrected_runs, contains_rtl, needs_bidi''')

c4 = create_code('''def process_glyph_document(glyph_doc):
    enriched_pages = []
    total_lines = 0
    rtl_lines = 0
    corrected_lines = 0
    
    for page in glyph_doc.get("pages", []):
        enriched_page = dict(page)
        enriched_page["blocks"] = []
        for block in page.get("blocks", []):
            enriched_block = dict(block)
            enriched_lines = []
            
            for line in block.get("lines", []):
                total_lines += 1
                line_chars = []
                for span in line.get("spans", []):
                    line_chars.extend(span.get("chars", []))
                    
                original_text = "".join(c.get("char", "") for c in line_chars)
                runs, contains_rtl, needs_bidi = extract_and_correct_runs(line_chars)
                
                corrected_text = "".join(r["corrected_text"] for r in runs)
                
                if contains_rtl: rtl_lines += 1
                if needs_bidi: corrected_lines += 1
                
                rtl_run_count = sum(1 for r in runs if r["script"] == "RTL")
                mixed_script = len(set(r["script"] for r in runs if r["script"] not in ("SPACE", "OTHER"))) > 1
                
                simple_runs = [{
                    "script": r["script"], 
                    "text": r["corrected_text"],
                    "char_start": r["char_start"],
                    "char_end": r["char_end"],
                    "bbox": r["bbox"]
                } for r in runs]
                
                enriched_line = dict(line)
                enriched_line["original_text"] = original_text
                enriched_line["corrected_text"] = corrected_text
                enriched_line["script_runs"] = simple_runs
                
                confidence_score = 0.97 if contains_rtl else 1.0
                enriched_line["confidence"] = {
                    "contains_rtl": contains_rtl,
                    "needs_bidi": needs_bidi,
                    "rtl_run_count": rtl_run_count,
                    "mixed_script": mixed_script,
                    "confidence": confidence_score
                }
                
                enriched_lines.append(enriched_line)
            enriched_block["lines"] = enriched_lines
            enriched_page["blocks"].append(enriched_block)
        enriched_pages.append(enriched_page)
        
    stats = {
        "total_lines": total_lines,
        "rtl_lines": rtl_lines,
        "corrected_lines": corrected_lines
    }
    
    enriched_doc = dict(glyph_doc)
    enriched_doc["pages"] = enriched_pages
    return enriched_doc, stats

if 'glyph_document' not in locals() and 'glyph_document' not in globals():
    try:
        with open("artifacts/legacy/loose/glyph_document.json", "r", encoding="utf-8") as f:
            glyph_document = json.load(f)
    except FileNotFoundError:
        print("glyph_document not found in artifacts/legacy/loose/")

if 'glyph_document' in locals() or 'glyph_document' in globals():
    enriched_document, processing_stats = process_glyph_document(glyph_document)''')

c5 = create_code('''if 'processing_stats' in locals():
    print(f"Total Lines Processed: {processing_stats['total_lines']}")
    print(f"Lines containing RTL: {processing_stats['rtl_lines']}")
    print(f"Lines corrected: {processing_stats['corrected_lines']}")

    table = Table(title="RTL Text Analysis & Correction (Sample)")
    table.add_column("Original (Visual)")
    table.add_column("Corrected (Logical)")
    table.add_column("Needs Bidi")
    table.add_column("Script Runs")

    displayed = 0
    for page in enriched_document.get("pages", []):
        for block in page.get("blocks", []):
            for line in block.get("lines", []):
                if line.get("contains_rtl") and displayed < 10:
                    run_summary = ", ".join(r["script"] for r in line["script_runs"] if r["script"] != "SPACE")
                    table.add_row(
                        line["original_text"],
                        line["corrected_text"],
                        str(line.get("needs_bidi")),
                        run_summary
                    )
                    displayed += 1

    print(table)''')

c6 = create_code('''if 'enriched_document' in locals():
    with open("artifacts/legacy/loose/bidi_corrected_document.json", "w", encoding="utf-8") as f:
        json.dump(enriched_document, f, indent=4, ensure_ascii=False)
    print("Enriched document exported to artifacts/legacy/loose/bidi_corrected_document.json")''')

c7 = create_markdown("### Mixed Script Validation")

c8 = create_code('''# Validate Mixed Scripts
dummy_mixed = "Section 302 PPC کے تحت"
dummy_chars = []
x = 0
for char in dummy_mixed:
    dummy_chars.append({"char": char, "bbox": [x, 0, x+10, 10]})
    x += 10 # simulated LTR visual order (but in reality the Urdu might be RTL)

runs, cr, nb = extract_and_correct_runs(dummy_chars)
print("Mixed String Original:", dummy_mixed)
for r in runs:
    print(f"[{r['script']}] '{r['original_text']}' -> '{r['corrected_text']}' | BBox: {r.get('bbox')} | chars: {r.get('char_start')}-{r.get('char_end')}")

assert "".join(r['corrected_text'] for r in runs if r['script'] in ('LTR', 'NUM')).strip() == "Section 302 PPC", "English intact check failed"
assert any(r['script'] == 'RTL' for r in runs), "Urdu run check failed"
print("Mixed line validation passed!")''')

c9 = create_markdown("### Character Coordinates Visualization")

c10 = create_code('''import matplotlib.pyplot as plt

def visualize_rtl_line(glyph_doc):
    target_line = None
    for page in glyph_doc.get("pages", []):
        for block in page.get("blocks", []):
            for line in block.get("lines", []):
                chars = []
                for span in line.get("spans", []):
                    chars.extend(span.get("chars", []))
                
                text = "".join(c.get("char", "") for c in chars)
                if any(get_script_type(c) == "RTL" for c in text):
                    target_line = chars
                    break
            if target_line: break
        if target_line: break

    if not target_line:
        print("No RTL line found for visualization.")
        return

    x_coords = []
    y_coords = []
    indices = []
    labels = []

    for i, char_info in enumerate(target_line):
        char = char_info.get("char", "").strip()
        bbox = char_info.get("bbox")
        if char and bbox:
            x_coords.append(bbox[0]) # min x
            y_coords.append(bbox[1]) # min y
            indices.append(i)
            labels.append(char)

    plt.figure(figsize=(12, 4))
    plt.scatter(indices, x_coords, color='blue', label='X Coordinate')
    
    for i, txt in enumerate(labels):
        plt.annotate(txt, (indices[i], x_coords[i]), textcoords="offset points", xytext=(0,10), ha='center')
        
    plt.xlabel("Character Index (Reading Order from PDF)")
    plt.ylabel("X Coordinate")
    plt.title("Visual vs Logical Order (X-Coord by Index)")
    plt.legend()
    plt.grid(True)
    plt.show()

if 'glyph_document' in locals() or 'glyph_document' in globals():
    visualize_rtl_line(glyph_document)''')

c11 = create_markdown("### Ground Truth Comparison (CER / WER)")

c12 = create_code('''import jiwer

def compute_iou(boxA, boxB):
    # box is [x_min, y_min, x_max, y_max] or similar
    # if box format is different, adapt. 
    # Let's assume standard [x0, y0, x1, y1] for pipeline.
    # For GT, we need to convert dict to list.
    
    gt_x0 = boxA.get('x', 0)
    gt_y0 = boxA.get('y', 0)
    gt_x1 = gt_x0 + boxA.get('w', 0)
    gt_y1 = gt_y0 + boxA.get('h', 0)
    
    pipe_x0, pipe_y0, pipe_x1, pipe_y1 = boxB
    
    inter_x0 = max(gt_x0, pipe_x0)
    inter_y0 = max(gt_y0, pipe_y0)
    inter_x1 = min(gt_x1, pipe_x1)
    inter_y1 = min(gt_y1, pipe_y1)
    
    inter_area = max(0, inter_x1 - inter_x0) * max(0, inter_y1 - inter_y0)
    
    boxA_area = max(0, gt_x1 - gt_x0) * max(0, gt_y1 - gt_y0)
    boxB_area = max(0, pipe_x1 - pipe_x0) * max(0, pipe_y1 - pipe_y0)
    
    iou = inter_area / float(boxA_area + boxB_area - inter_area) if (boxA_area + boxB_area - inter_area) > 0 else 0
    return iou

def evaluate_rtl_accuracy(extracted_doc, gt_path):
    try:
        with open(gt_path, "r", encoding="utf-8") as f:
            gt_data = json.load(f)
    except FileNotFoundError:
        print(f"Ground truth file {gt_path} not found.")
        return

    def extract_gt_texts(items):
        lines = []
        for item in items:
            if item.get("type") == "text":
                text = item.get("value", "")
                if any(get_script_type(c) == "RTL" for c in text):
                    bboxes = item.get("bbox", [])
                    if bboxes and isinstance(bboxes, list):
                        bbox = bboxes[0] if isinstance(bboxes[0], dict) else None
                        if bbox and 'y' in bbox:
                            lines.append({"y": bbox['y'], "bbox": bbox, "text": text})
            if "items" in item:
                lines.extend(extract_gt_texts(item["items"]))
        return lines

    gt_lines = []
    for page in gt_data.get("pages", []):
        page_num = page.get("page_number")
        for line in extract_gt_texts(page.get("items", [])):
            line["page"] = page_num
            gt_lines.append(line)

    # Extract pipeline lines
    pipe_lines = []
    for page in extracted_doc.get("pages", []):
        page_num = page.get("page")
        for block in page.get("blocks", []):
            for line in block.get("lines", []):
                if line.get("contains_rtl"):
                    bboxes = [c.get("bbox") for span in line.get("spans", []) for c in span.get("chars", []) if c.get("bbox")]
                    if bboxes:
                        min_x = min(b[0] for b in bboxes)
                        min_y = min(b[1] for b in bboxes)
                        max_x = max(b[2] for b in bboxes)
                        max_y = max(b[3] for b in bboxes)
                        pipe_lines.append({
                            "page": page_num, 
                            "y": min_y, 
                            "bbox": [min_x, min_y, max_x, max_y],
                            "original": line.get("original_text"),
                            "corrected": line.get("corrected_text")
                        })

    original_cer = []
    corrected_cer = []
    original_wer = []
    corrected_wer = []
    
    for gt in gt_lines:
        page_matches = [p for p in pipe_lines if p["page"] == gt["page"]]
        if not page_matches: continue
        
        # Find all lines that intersect with GT bbox
        intersecting = []
        for p in page_matches:
            iou = compute_iou(gt["bbox"], p["bbox"])
            if iou > 0:
                intersecting.append((p, p["y"]))
                
        if not intersecting:
            # fallback distance check
            for p in page_matches:
                if abs(p["y"] - gt["y"]) < 20:
                    intersecting.append((p, p["y"]))
                    
        if intersecting:
            # sort by Y coordinate
            intersecting.sort(key=lambda x: x[1])
            orig_text = " ".join(p["original"] for p, _ in intersecting)
            corr_text = " ".join(p["corrected"] for p, _ in intersecting)
            
            try:
                orig_err = jiwer.cer(gt["text"], orig_text)
                corr_err = jiwer.cer(gt["text"], corr_text)
                
                orig_wer = jiwer.wer(gt["text"], orig_text)
                corr_wer = jiwer.wer(gt["text"], corr_text)
                
                original_cer.append(orig_err)
                corrected_cer.append(corr_err)
                original_wer.append(orig_wer)
                corrected_wer.append(corr_wer)
            except Exception as e:
                print(f"Exception during evaluation: {e}")
                continue

    if original_cer:
        avg_orig_cer = sum(original_cer) / len(original_cer)
        avg_corr_cer = sum(corrected_cer) / len(corrected_cer)
        avg_orig_wer = sum(original_wer) / len(original_wer)
        avg_corr_wer = sum(corrected_wer) / len(corrected_wer)
        
        print(f"Evaluated {len(original_cer)} RTL lines.")
        print(f"Original CER:  {avg_orig_cer:.4f} | Corrected CER: {avg_corr_cer:.4f}")
        print(f"Original WER:  {avg_orig_wer:.4f} | Corrected WER: {avg_corr_wer:.4f}")
        if avg_corr_cer <= avg_orig_cer:
            print("BiDi correction improved or maintained CER.")
        else:
            print("BiDi correction degraded CER.")
    else:
        print("No matching lines found for evaluation.")

if 'enriched_document' in locals() or 'enriched_document' in globals():
    evaluate_rtl_accuracy(enriched_document, "2024LHC6559.json")''')

new_cells = []
replaced_idx = -1
for i, c in enumerate(nb["cells"]):
    if c["cell_type"] == "code" and any("def get_script_type" in s for s in c.get("source", [])):
        replaced_idx = i - 1 # target the markdown cell just before it
        break

if replaced_idx == -1:
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] == "code" and any("TODO: Implement 3" in s for s in c.get("source", [])):
            replaced_idx = i
            break

if replaced_idx != -1:
    nb["cells"] = nb["cells"][:replaced_idx] + [c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12] + nb["cells"][replaced_idx:]
    # Remove duplicates if previous injected code was below
    final_cells = []
    in_rtl_section = False
    for c in nb["cells"]:
        if c == c1:
            if not in_rtl_section:
                final_cells.extend([c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12])
                in_rtl_section = True
        elif c["cell_type"] == "markdown" and any("4. Layout Analysis" in s for s in c.get("source", [])):
            in_rtl_section = False
            final_cells.append(c)
        elif not in_rtl_section:
            final_cells.append(c)
    nb["cells"] = final_cells
else:
    nb["cells"].extend([c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12])

with open(notebook_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1)
