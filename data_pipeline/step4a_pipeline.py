#!/usr/bin/env python3
"""Step 4A: free/open-source OCR pilot for four Chapter 4 PDF pages.

Subcommands:
  render   Render the four pilot pages at 300 DPI with pypdfium2.
  ocr      Run Tesseract 5 (khm+eng) and RapidLaTeXOCR on the rendered pages.
  evaluate Score the OCR output against the manually verified references.

All generated images and intermediate OCR files are written outside the
Git-tracked repository (default: %LOCALAPPDATA%\\Temp\\opencode\\step4a).
Source PDFs are opened read-only and never modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get(
    "STEP4A_WORK",
    r"C:\Users\Meng\AppData\Local\Temp\opencode\step4a",
))
PAGES_DIR = WORK / "pages"
OCR_DIR = WORK / "ocr"
FORMULA_DIR = WORK / "formulas"
TESSDATA_DIR = WORK / "tessdata"
REFERENCE_DIR = ROOT / "docs" / "step4a_reference"

DEFAULT_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DOCS: dict[str, dict[str, Any]] = {
    "basic_course_book": {
        "document_type": "course_book",
        "curriculum_level": "basic",
        "relative_file_path": "data/raw/Course_book/Course_book_Basic_Level.pdf",
    },
    "advanced_course_book": {
        "document_type": "course_book",
        "curriculum_level": "advanced",
        "relative_file_path": "data/raw/Course_book/Course_book_High_Level.pdf",
    },
    "basic_answer_key": {
        "document_type": "answer_key",
        "curriculum_level": "basic",
        "relative_file_path": "data/raw/Answer_key_book/Basic_Level/Chapter4.pdf",
    },
    "advanced_answer_key": {
        "document_type": "answer_key",
        "curriculum_level": "advanced",
        "relative_file_path": "data/raw/Answer_key_book/High_Level/Chapter4.pdf",
    },
}

PILOT: list[dict[str, Any]] = [
    {"doc_id": "basic_course_book", "pdf_page": 114, "printed_page": "109"},
    {"doc_id": "advanced_course_book", "pdf_page": 85, "printed_page": "80"},
    {"doc_id": "basic_answer_key", "pdf_page": 1, "printed_page": "1"},
    {"doc_id": "advanced_answer_key", "pdf_page": 20, "printed_page": "20"},
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dirs() -> None:
    for d in (WORK, PAGES_DIR, OCR_DIR, FORMULA_DIR, TESSDATA_DIR, REFERENCE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def page_image_path(doc_id: str, pdf_page: int) -> Path:
    return PAGES_DIR / f"{doc_id}_p{pdf_page:04d}.png"


def render() -> None:
    import pypdfium2 as pdfium

    ensure_dirs()
    manifest: dict[str, Any] = {
        "pilot": "Step 4A",
        "dpi": 300,
        "renderer": "pypdfium2",
        "pages": [],
    }
    for item in PILOT:
        doc_id = item["doc_id"]
        page_no = item["pdf_page"]
        rel = DOCS[doc_id]["relative_file_path"]
        pdf_path = ROOT / rel
        if not pdf_path.exists():
            raise FileNotFoundError(f"Missing source PDF: {pdf_path}")
        before = sha256(pdf_path)
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[page_no - 1]
        bitmap = page.render(scale=300 / 72)
        image = bitmap.to_pil()
        out = page_image_path(doc_id, page_no)
        image.save(out)
        pdf.close()
        after = sha256(pdf_path)
        if before != after:
            raise RuntimeError(f"Source PDF changed during render: {rel}")
        record = {
            **item,
            **DOCS[doc_id],
            "image_path": str(out),
            "image_width": image.width,
            "image_height": image.height,
            "pdf_sha256_before": before,
            "pdf_sha256_after": after,
        }
        manifest["pages"].append(record)
        print(f"[render] {doc_id} p{page_no}: {image.width}x{image.height} -> {out}")
    (WORK / "render_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def extract_text_layer() -> None:
    """Save pypdf text layers for the four pilot pages (diagnostic only)."""
    from pypdf import PdfReader

    ensure_dirs()
    for item in PILOT:
        doc_id = item["doc_id"]
        page_no = item["pdf_page"]
        pdf_path = ROOT / DOCS[doc_id]["relative_file_path"]
        reader = PdfReader(str(pdf_path))
        text = reader.pages[page_no - 1].extract_text()
        out = OCR_DIR / f"{doc_id}_p{page_no:04d}_pypdf.txt"
        out.write_text(text, encoding="utf-8")
        print(f"[extract] {doc_id} p{page_no}: {len(text)} chars -> {out}")


def tesseract_exe() -> str:
    return os.environ.get("TESSERACT_EXE", DEFAULT_TESSERACT)


def run_tesseract(image: Path, config: str) -> str:
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = str(TESSDATA_DIR)
    cmd = [tesseract_exe(), str(image), "stdout", "-l", "khm+eng", "--psm", "3"]
    cmd.extend(config.split())
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env, check=True
    )
    return result.stdout


def ocr_text() -> None:
    ensure_dirs()
    for item in PILOT:
        doc_id = item["doc_id"]
        page_no = item["pdf_page"]
        image = page_image_path(doc_id, page_no)
        if not image.exists():
            raise FileNotFoundError(f"Run render first: {image}")
        txt = run_tesseract(image, "")
        tsv = run_tesseract(image, "tsv")
        (OCR_DIR / f"{doc_id}_p{page_no:04d}.txt").write_text(txt, encoding="utf-8")
        (OCR_DIR / f"{doc_id}_p{page_no:04d}.tsv").write_text(tsv, encoding="utf-8")
        print(f"[ocr-text] {doc_id} p{page_no}: {len(txt)} chars")


def load_reference(doc_id: str, page_no: int) -> dict[str, Any]:
    path = REFERENCE_DIR / f"{doc_id}_p{page_no:04d}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing manual reference: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ocr_formulas() -> None:
    """Run RapidLaTeXOCR (ONNX pix2tex port) on reference formula crops."""
    from PIL import Image
    from rapid_latex_ocr import LaTeXOCR

    ensure_dirs()
    engine = LaTeXOCR()
    for item in PILOT:
        doc_id = item["doc_id"]
        page_no = item["pdf_page"]
        image = Image.open(page_image_path(doc_id, page_no))
        ref = load_reference(doc_id, page_no)
        results = []
        for idx, block in enumerate(ref["reference"]["blocks"]):
            if block.get("type") != "formula":
                continue
            bbox = block.get("bbox")
            if not bbox:
                raise ValueError(f"Formula block without bbox: {doc_id} p{page_no} #{idx}")
            crop = image.crop(tuple(bbox))
            crop_path = FORMULA_DIR / f"{doc_id}_p{page_no}_f{idx:02d}.png"
            crop.save(crop_path)
            started = time.perf_counter()
            latex, confidence = engine(crop_path)
            elapsed = time.perf_counter() - started
            results.append({
                "block_index": idx,
                "bbox": bbox,
                "crop_path": str(crop_path),
                "latex": latex,
                "confidence": float(confidence),
                "runtime_seconds": round(elapsed, 3),
            })
            print(f"[formula] {doc_id} p{page_no} #{idx}: {latex[:80]}")
        (FORMULA_DIR / f"{doc_id}_p{page_no:04d}.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def normalize_text(text: str, khmer_only: bool = False) -> str:
    text = unicodedata.normalize("NFC", text)
    if khmer_only:
        text = "".join(
            ch for ch in text
            if 0x1780 <= ord(ch) <= 0x17FF or 0x19E0 <= ord(ch) <= 0x19FF
        )
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text


def cer(reference: str, hypothesis: str, khmer_only: bool = False) -> float:
    ref = normalize_text(reference, khmer_only=khmer_only)
    hyp = normalize_text(hypothesis, khmer_only=khmer_only)
    if not ref:
        return 0.0 if not hyp else 1.0
    # Levenshtein distance using a rolling row.
    previous = list(range(len(hyp) + 1))
    for i, rch in enumerate(ref, start=1):
        current = [i]
        for j, hch in enumerate(hyp, start=1):
            cost = 0 if rch == hch else 1
            current.append(min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + cost,
            ))
        previous = current
    return min(1.0, previous[-1] / len(ref))


def parse_tsv(path: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for raw in f:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            if row.get("level") == "5" and row.get("text", "").strip():
                lines.append({
                    "text": row["text"],
                    "bbox": [
                        int(row["left"]), int(row["top"]),
                        int(row["left"]) + int(row["width"]),
                        int(row["top"]) + int(row["height"]),
                    ],
                    "block_num": int(row["block_num"]),
                    "par_num": int(row["par_num"]),
                    "line_num": int(row["line_num"]),
                    "conf": float(row["conf"]),
                })
    return lines


def box_iou(a: list[int], b: list[int]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def box_containment(inner: list[int], outer: list[int]) -> float:
    """Fraction of the inner box that lies inside the outer box."""
    if not inner or not outer:
        return 0.0
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = max(1, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return ((x1 - x0) * (y1 - y0)) / area


def normalize_latex(latex: str) -> str:
    s = latex.strip()
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = re.sub(r"\\(?:,|;|!|:|quad|qquad|displaystyle|text\{[^}]*\})", "", s)
    s = s.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
    s = re.sub(r"\s+", "", s)
    s = s.strip("$")
    return s


def sympy_match(ref: str, hyp: str) -> bool | None:
    try:
        from sympy.parsing.latex import parse_latex
        from sympy import simplify, Equality, Symbol
        a, b = parse_latex(ref), parse_latex(hyp)
        if isinstance(a, Equality) or isinstance(b, Equality):
            return simplify(a.lhs - a.rhs - (b.lhs - b.rhs)) == 0
        return simplify(a - b) == 0
    except Exception:
        return None


def evaluate() -> None:
    metrics: dict[str, Any] = {
        "schema_version": "1.0",
        "pages": [],
    }
    for item in PILOT:
        doc_id = item["doc_id"]
        page_no = item["pdf_page"]
        ref = load_reference(doc_id, page_no)
        ocr_txt_path = OCR_DIR / f"{doc_id}_p{page_no:04d}.txt"
        tsv_path = OCR_DIR / f"{doc_id}_p{page_no:04d}.tsv"
        ocr_text = ocr_txt_path.read_text(encoding="utf-8") if ocr_txt_path.exists() else ""
        lines = parse_tsv(tsv_path) if tsv_path.exists() else []
        ref_text = ref["reference"]["text"]
        blocks = ref["reference"]["blocks"]
        formula_results: list[dict[str, Any]] = []
        fp = FORMULA_DIR / f"{doc_id}_p{page_no:04d}.json"
        if fp.exists():
            formula_results = json.loads(fp.read_text(encoding="utf-8"))

        ref_formulas = [b for b in blocks if b.get("type") == "formula"]
        matched_formulas = 0
        exact_formulas = 0
        semantic_formulas = 0
        latex_similarities: list[float] = []
        by_index = {r["block_index"]: r for r in formula_results}
        for idx, block in enumerate(blocks):
            if block.get("type") != "formula":
                continue
            output = by_index.get(idx)
            if not output:
                continue
            matched_formulas += 1
            ref_latex = normalize_latex(block["latex"])
            hyp_latex = normalize_latex(output["latex"])
            latex_similarities.append(1.0 - cer(ref_latex, hyp_latex))
            if hyp_latex == ref_latex:
                exact_formulas += 1
            sm = sympy_match(block["latex"], output["latex"])
            if sm is True:
                semantic_formulas += 1

        # Formula detection coverage: formula-line heuristic over Tesseract TSV.
        math_re = re.compile(r"[0-9=+\-*/(){}\[\]<>√∫∑∏∂∞≈≠≤≥πθλμΔΩ]")
        detected = [ln for ln in lines if math_re.search(ln["text"])]
        covered = 0
        for block in ref_formulas:
            bbox = block.get("bbox")
            if bbox and any(box_containment(ln["bbox"], bbox) >= 0.50 for ln in detected):
                covered += 1
        false_positives = sum(
            1 for ln in detected
            if not any(box_containment(ln["bbox"], b.get("bbox", [])) >= 0.50
                       for b in ref_formulas if b.get("bbox"))
        )

        # Block coverage and reading order via fuzzy text/bbox matching.
        matched_blocks: list[int] = []
        ocr_used: set[int] = set()
        for order, block in enumerate(blocks):
            target = block.get("text") or block.get("latex", "")
            target = normalize_text(target)
            best_i, best_score = -1, 0.0
            bbox = block.get("bbox")
            for i, ln in enumerate(lines):
                if i in ocr_used:
                    continue
                sim = SequenceMatcher(None, target[:200], normalize_text(ln["text"])[:200]).ratio()
                if bbox:
                    sim = max(sim, box_containment(ln["bbox"], bbox))
                if sim > best_score:
                    best_i, best_score = i, sim
            if best_i >= 0 and best_score >= 0.35:
                ocr_used.add(best_i)
                matched_blocks.append(best_i)
        order_pairs = 0
        order_ok = 0
        for a in range(len(matched_blocks)):
            for b in range(a + 1, len(matched_blocks)):
                order_pairs += 1
                if matched_blocks[a] < matched_blocks[b]:
                    order_ok += 1

        page_metrics = {
            "doc_id": doc_id,
            "pdf_page": page_no,
            "printed_page": ref["source"].get("printed_page"),
            "cer_all": round(cer(ref_text, ocr_text), 4),
            "cer_khmer": round(cer(ref_text, ocr_text, khmer_only=True), 4),
            "reference_blocks": len(blocks),
            "reference_formulas": len(ref_formulas),
            "ocr_lines": len(lines),
            "formula_ocr_attempted": len(formula_results),
            "formula_normalized_exact": (
                round(exact_formulas / len(ref_formulas), 4) if ref_formulas else None
            ),
            "formula_semantic_match": (
                round(semantic_formulas / len(ref_formulas), 4) if ref_formulas else None
            ),
            "formula_latex_similarity": (
                round(sum(latex_similarities) / len(latex_similarities), 4)
                if latex_similarities else None
            ),
            "formula_detection_coverage": (
                round(covered / len(ref_formulas), 4) if ref_formulas else None
            ),
            "formula_detection_false_positives": false_positives,
            "block_coverage": round(len(matched_blocks) / len(blocks), 4) if blocks else None,
            "reading_order_accuracy": (
                round(order_ok / order_pairs, 4) if order_pairs else None
            ),
            "manual_correction_seconds": ref.get("quality", {}).get("manual_correction_seconds"),
            "notes": ref.get("notes", ""),
        }
        metrics["pages"].append(page_metrics)
        print(json.dumps(page_metrics, ensure_ascii=False))

    # Aggregate macro averages over pages with defined values.
    numeric_keys = [
        "cer_all", "cer_khmer", "formula_normalized_exact", "formula_semantic_match",
        "formula_latex_similarity", "formula_detection_coverage", "block_coverage",
        "reading_order_accuracy",
    ]
    agg: dict[str, float | None] = {}
    for key in numeric_keys:
        vals = [p[key] for p in metrics["pages"] if p.get(key) is not None]
        agg[key] = round(sum(vals) / len(vals), 4) if vals else None
    metrics["macro_average"] = agg
    out = ROOT / "docs" / "step4a_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[evaluate] wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["render", "extract", "ocr", "evaluate"])
    parser.add_argument(
        "--stage",
        choices=["text", "formula", "all"],
        default="all",
        help="For ocr: which engines to run (default: all).",
    )
    args = parser.parse_args()
    if args.command == "render":
        render()
    elif args.command == "extract":
        extract_text_layer()
    elif args.command == "ocr":
        if args.stage in ("text", "all"):
            ocr_text()
        if args.stage in ("formula", "all"):
            ocr_formulas()
    elif args.command == "evaluate":
        evaluate()


if __name__ == "__main__":
    main()
