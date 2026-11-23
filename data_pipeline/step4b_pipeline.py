#!/usr/bin/env python3
"""Step 4B: reference validation support and focused formula-OCR improvement.

This script only touches the same four Step 4A pages. Provisional full-page
references are read from the Git-ignored data/evaluation/step4a_reference/
directory; the tracked outputs are schemas, aggregate metrics, validation
summaries, and math-verification results. No reference is treated as visually
verified.

Subcommands:
  formula    Rerun RapidLaTeXOCR on refined, formula-only crops.
  math       Run SymPy symbolic comparisons and write aggregate verification.
  evaluate   Recompute metrics and compare to Step 4A (indicative only).
  verify     Check pipeline-facing JSON schema consistency.
  all        Run all four.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import step4a_pipeline as s4

ROOT = s4.ROOT
PROVISIONAL_REFERENCE_DIR = ROOT / "data" / "evaluation" / "step4a_reference"
REFINED_FORMULA_DIR = s4.FORMULA_DIR / "step4b"
PREVIOUS_METRICS = ROOT / "docs" / "step4a_metrics.json"
SCHEMA_OUT = ROOT / "docs" / "step4b_reference_schema.json"
SCHEMA_VALIDATION_OUT = ROOT / "docs" / "step4b_schema_validation.json"
METRICS_OUT = ROOT / "docs" / "step4b_metrics.json"
COMPARISON_OUT = ROOT / "docs" / "step4b_formula_ocr_comparison.json"
MATH_OUT = ROOT / "docs" / "step4b_math_verification.json"


def load_provisional_reference(doc_id: str, page_no: int) -> dict[str, Any]:
    """Load a Git-ignored, provisional reference; never implies visual approval."""
    path = PROVISIONAL_REFERENCE_DIR / f"{doc_id}_p{page_no:04d}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing Step 4B provisional reference: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def formula_crop_bboxes(block: dict[str, Any]) -> list[list[int]]:
    boxes = block.get("formula_crop_bboxes") or [block.get("bbox")]
    return [box for box in boxes if box]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_schema_subset(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    """Validate the JSON-Schema subset used by step4b_reference_schema.json.

    The `jsonschema` package is not installed in the free environment, so this
    checker covers type, enum, required, min/maxItems and minimum only.
    """
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in enum {schema['enum']!r}")

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required field '{required}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                _validate_schema_subset(value[key], subschema, f"{path}.{key}", errors)
    elif expected == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array")
            return
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems={schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems={schema['maxItems']}")
        for i, item in enumerate(value):
            if "items" in schema:
                _validate_schema_subset(item, schema["items"], f"{path}[{i}]", errors)
    elif expected == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path}: expected integer")
        elif "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum={schema['minimum']}")
    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{path}: expected number")
    elif expected == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean")


def verify_schema_consistency() -> dict[str, Any]:
    """Validate local reference structure without exposing transcriptions."""
    schema = json.loads(SCHEMA_OUT.read_text(encoding="utf-8"))
    files: list[dict[str, Any]] = []
    for item in s4.PILOT:
        doc_id, page_no = item["doc_id"], item["pdf_page"]
        path = PROVISIONAL_REFERENCE_DIR / f"{doc_id}_p{page_no:04d}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        errors: list[str] = []
        _validate_schema_subset(data, schema, "$", errors)

        review = data.get("review", {})
        if review.get("visual_confirmation") is not False:
            errors.append("$.review.visual_confirmation must remain false")
        if review.get("approved_for_training_or_rag") is not False:
            errors.append("$.review.approved_for_training_or_rag must remain false")
        if review.get("verification_status") != "provisional_programmatic_visual_pending":
            errors.append("$.review.verification_status must remain provisional_programmatic_visual_pending")

        files.append({
            "file": path.name,
            "sha256": _sha256(path),
            "valid": not errors,
            "errors": errors,
            "review_status": review.get("verification_status"),
            "visual_confirmation": review.get("visual_confirmation"),
            "approved_for_training_or_rag": review.get("approved_for_training_or_rag"),
        })

    result = {
        "schema_version": "1.0",
        "schema_file": SCHEMA_OUT.name,
        "schema_sha256": _sha256(SCHEMA_OUT),
        "validator": "built-in subset checker (type, enum, required, min/maxItems, minimum)",
        "all_valid": all(f["valid"] for f in files),
        "files": files,
        "note": "Structural consistency only; it does not imply visual verification.",
    }
    SCHEMA_VALIDATION_OUT.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[step4b-verify] wrote {SCHEMA_VALIDATION_OUT}")
    return result


def ocr_formulas_refined() -> None:
    """Rerun RapidLaTeXOCR on tightened formula-only crops for four pages."""
    from PIL import Image
    from rapid_latex_ocr import LaTeXOCR

    REFINED_FORMULA_DIR.mkdir(parents=True, exist_ok=True)
    engine = LaTeXOCR()
    for item in s4.PILOT:
        doc_id, page_no = item["doc_id"], item["pdf_page"]
        image = Image.open(s4.page_image_path(doc_id, page_no))
        ref = load_provisional_reference(doc_id, page_no)
        results: list[dict[str, Any]] = []
        for idx, block in enumerate(ref["reference"]["blocks"]):
            if block.get("type") != "formula":
                continue
            crop_outputs: list[dict[str, Any]] = []
            for crop_no, bbox in enumerate(formula_crop_bboxes(block)):
                crop_path = REFINED_FORMULA_DIR / (
                    f"{doc_id}_p{page_no:04d}_b{idx:02d}_c{crop_no:02d}.png"
                )
                image.crop(tuple(bbox)).save(crop_path)
                started = time.perf_counter()
                latex, confidence = engine(crop_path)
                elapsed = time.perf_counter() - started
                crop_outputs.append({
                    "crop_index": crop_no,
                    "bbox": bbox,
                    "crop_path": str(crop_path),
                    "latex": latex,
                    "confidence": float(confidence),
                    "runtime_seconds": round(elapsed, 3),
                })
            combined = r",\quad ".join(c["latex"] for c in crop_outputs)
            results.append({
                "block_index": idx,
                "order": block.get("order"),
                "bbox": block.get("bbox"),
                "formula_crop_bboxes": formula_crop_bboxes(block),
                "latex": combined,
                "crops": crop_outputs,
            })
            print(f"[step4b-formula] {doc_id} p{page_no} block {idx}: {combined[:90]}")
        out = REFINED_FORMULA_DIR / f"{doc_id}_p{page_no:04d}.json"
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[step4b-formula] wrote {out}")


def math_verification() -> dict[str, Any]:
    """Derive statuses from real SymPy comparisons; separate calculations from checks."""
    import sympy as sp
    from sympy.parsing.latex import parse_latex

    x, t = sp.symbols("x t", real=True)
    a = sp.symbols("a", real=True, positive=True)
    ip = sp.symbols("Ip")
    checks: list[dict[str, Any]] = []
    calculated_values: dict[str, str] = {}

    def normalize_parsed_symbols(expr: Any) -> Any:
        """Map parse_latex names to the pipeline's assumed symbols and constants."""
        return expr.subs({
            sp.Symbol("e"): sp.E,
            sp.Symbol("pi"): sp.pi,
            sp.Symbol("a"): a,
            sp.Symbol("t"): t,
            sp.Symbol("x"): x,
            sp.Symbol("Ip"): ip,
        })

    def add_comparison(
        check_id: str,
        calculated: Any,
        independent: Any,
        basis: str,
        assumptions: list[str],
        note: str = "",
        reference_latex: str | None = None,
    ) -> None:
        difference = sp.simplify(sp.together(calculated - independent))
        status = "PASS" if difference == 0 else "FAIL"
        calculated_values[check_id] = str(calculated)
        checks.append({
            "check_id": check_id,
            "status": status,
            "verification_type": "symbolic_comparison",
            "verification_basis": basis,
            "calculated_by_sympy": str(calculated),
            "independent_expected": str(independent),
            "difference_simplified": str(difference),
            "assumptions": assumptions,
            "reference_latex": reference_latex,
            "note": note,
        })

    real_x = ["x is real"]
    positive_a = ["a is real", "a > 0", "a != 0 where division by a occurs"]
    common_a = positive_a + ["a^2 + 1 != 0 (always true for real a)"]

    # Advanced course book: definite integrals vs independently written antiderivatives.
    v1 = sp.integrate(sp.pi * x, (x, 0, 1))
    v1_antideriv = sp.pi * x**2 / 2
    add_comparison(
        "advanced_course_book.V1", v1,
        v1_antideriv.subs(x, 1) - v1_antideriv.subs(x, 0),
        "SymPy definite integral vs independently specified antiderivative pi*x^2/2",
        real_x,
    )

    v2 = sp.integrate(sp.pi * x**4, (x, 0, 1))
    v2_antideriv = sp.pi * x**5 / 5
    add_comparison(
        "advanced_course_book.V2", v2,
        v2_antideriv.subs(x, 1) - v2_antideriv.subs(x, 0),
        "SymPy definite integral vs independently specified antiderivative pi*x^5/5",
        real_x,
    )

    add_comparison(
        "advanced_course_book.V1_minus_V2", sp.simplify(v1 - v2),
        sp.Rational(3, 10) * sp.pi,
        "Calculated V1-V2 vs independently stated closed form 3*pi/10",
        real_x,
    )

    final_volume = sp.integrate(
        sp.pi * ((3 - x) ** 2 - (x**2 + 1) ** 2), (x, -2, 1)
    )
    expanded_poly = sp.expand((3 - x) ** 2 - (x**2 + 1) ** 2)
    final_antideriv = sp.pi * (
        -x**5 / sp.Integer(5) - x**3 / sp.Integer(3) - 3 * x**2 + 8 * x
    )
    add_comparison(
        "advanced_course_book.final_interval_integral", final_volume,
        final_antideriv.subs(x, 1) - final_antideriv.subs(x, -2),
        "SymPy definite integral vs independently written antiderivative of "
        f"{expanded_poly}; reference stores the source integral only",
        real_x,
        "The numeric value is recorded for verification but is not substituted into the reference transcription.",
    )

    # Basic answer key: derivative of each reference primitive vs reference f.
    derivative_pairs = [
        ("basic_answer_key.q1a", -7 * x + 4, -7,
         r"F(x)=-7x+4,\ f(x)=-7", real_x),
        ("basic_answer_key.q1b", 3 * x**3 - 7 * x, 9 * x**2 - 7,
         r"F(x)=3x^3-7x,\ f(x)=9x^2-7", real_x),
        ("basic_answer_key.q1c", 3 * sp.exp(2 * x - 1) - 7, 6 * sp.exp(2 * x - 1),
         r"F(x)=3e^{2x-1}-7,\ f(x)=6e^{2x-1}", real_x),
        ("basic_answer_key.q1d", sp.log(sp.exp(3 * x) - x) + sp.sqrt(11),
         (3 * sp.exp(3 * x) - 1) / (sp.exp(3 * x) - x),
         r"F(x)=\ln(e^{3x}-x)+\sqrt{11},\ f(x)=(3e^{3x}-1)/(e^{3x}-x)",
         real_x + ["exp(3*x)-x != 0 on the stated domain"]),
    ]
    for check_id, primitive, derivative, latex, assumptions in derivative_pairs:
        add_comparison(
            check_id, sp.diff(primitive, x), derivative,
            "Derivative of reference primitive vs independently stated f(x)",
            assumptions, reference_latex=latex,
        )

    # Advanced answer key: integration by parts.
    j = sp.integrate(sp.exp(-a * t) * sp.sin(t), (t, 0, sp.pi / 2))
    j_closed = (1 - a * sp.exp(-a * sp.pi / 2)) / (a**2 + 1)
    add_comparison(
        "advanced_answer_key.J_integral", j, j_closed,
        "SymPy definite integral vs integration-by-parts closed form",
        common_a,
        reference_latex=r"J=\frac{1-ae^{-a\pi/2}}{a^2+1}",
    )

    j_antideriv = -(a * sp.sin(t) + sp.cos(t)) * sp.exp(-a * t) / (a**2 + 1)
    add_comparison(
        "advanced_answer_key.J_antiderivative", sp.diff(j_antideriv, t),
        sp.exp(-a * t) * sp.sin(t),
        "Derivative of independently written antiderivative vs original integrand",
        common_a,
        reference_latex=r"\int e^{-at}\sin t\,dt=-\frac{e^{-at}(a\sin t+\cos t)}{a^2+1}",
    )

    ip_relation = sp.Eq(ip, 1 / a - j / a)
    ip_solution = sp.solve(ip_relation, ip)[0]

    corrected_latex = r"I'=\frac{a+e^{-a\pi/2}}{a^2+1}"
    corrected_rhs = normalize_parsed_symbols(parse_latex(corrected_latex).rhs)
    add_comparison(
        "advanced_answer_key.Iprime_corrected", ip_solution, corrected_rhs,
        "Solve I' = 1/a - J/a using SymPy J vs corrected reference LaTeX parsed symbolically",
        common_a,
        "Corrected expression is preserved verbatim in corrected_Iprime.latex; parsed names were normalized to pipeline symbols/constants.",
        reference_latex=corrected_latex,
    )

    # Preserve the original Step 4A block 9 expression and compare it symbolically.
    original_latex = (
        r"I'=\frac{e^{-a\pi}}{a^2+1}\left(e^{-a\pi}\cdot 0-\cos 0\right)"
        r"-\frac{1}{a^2}\left[ -\frac{e^{-at}(a\sin t+\cos t)}{a^2+1}\right]_0^{\pi/2}"
    )
    original_rhs = normalize_parsed_symbols(parse_latex(original_latex).rhs)
    add_comparison(
        "advanced_answer_key.Iprime_step4a_block9", original_rhs, corrected_rhs,
        "Parsed original Step 4A RHS vs parsed corrected RHS",
        common_a + ["parse_latex does not interpret the evaluation bar, leaving free t in the original RHS"],
        "The original expression is retained only as an audit field; the corrected expression is the reference.",
        reference_latex=original_latex,
    )

    result = {
        "schema_version": "2.0",
        "tool": "sympy",
        "sympy_version": sp.__version__,
        "scope": (
            "Supported expressions only. Statuses are derived from simplified symbolic "
            "differences; proofs, layout, and unsupported LaTeX require human visual review."
        ),
        "assumptions": {
            "symbols": {"x": "real", "t": "real", "a": "real and positive"},
            "constraints": [
                "a > 0; divisions by a also require a != 0",
                "a^2 + 1 != 0 (automatic for real a)",
                "exp(3*x) - x != 0 on the q1d domain",
                "parse_latex emits unassumed named symbols; e/pi are normalized to constants and a/t/x/Ip to the pipeline symbols before comparison",
                "Step 4A block 9 parses with a free t because the evaluation bar is not interpreted by parse_latex",
            ],
        },
        "calculated_values": calculated_values,
        "corrected_Iprime": {
            "latex": corrected_latex,
            "sympy_rhs": str(corrected_rhs),
            "sympy_verified": (
                next(c["status"] for c in checks
                     if c["check_id"] == "advanced_answer_key.Iprime_corrected") == "PASS"
            ),
            "visual_confirmation": False,
        },
        "original_step4a_block9": {
            "latex": original_latex,
            "sympy_rhs": str(original_rhs),
            "status": next(c["status"] for c in checks
                           if c["check_id"] == "advanced_answer_key.Iprime_step4a_block9"),
            "preserved_only_as_audit_record": True,
        },
        "checks": checks,
        "summary": {
            "symbolic_comparisons": len(checks),
            "pass": sum(c["status"] == "PASS" for c in checks),
            "fail": sum(c["status"] == "FAIL" for c in checks),
            "calculated_values_recorded": len(calculated_values),
        },
    }
    MATH_OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[step4b-math] wrote {MATH_OUT}")
    return result


def _page_detection_metrics(lines: list[dict[str, Any]], ref_formulas: list[dict[str, Any]]) -> dict[str, Any]:
    """Line-level math detection plus formula-region coverage.

    Precision/recall are computed over Tesseract TSV lines, not formula regions,
    because a single displayed formula often spans several OCR lines. A line is
    relevant when >=50% of its box lies inside a refined formula-only crop.
    Formula-region coverage is reported separately so the two scopes are clear.
    """
    math_re = re.compile(r"[0-9=+\-*/(){}\[\]<>√∫∑∏∂∞≈≠≤≥πθλμΔΩ]")
    crop_boxes: list[list[int]] = []
    for block in ref_formulas:
        crop_boxes.extend(formula_crop_bboxes(block))

    def is_relevant(line: dict[str, Any]) -> bool:
        return any(s4.box_containment(line["bbox"], box) >= 0.50 for box in crop_boxes)

    detected = [ln for ln in lines if math_re.search(ln["text"])]
    relevant = [ln for ln in lines if is_relevant(ln)]
    tp = sum(is_relevant(ln) for ln in detected)
    fp = len(detected) - tp
    fn = sum(not math_re.search(ln["text"]) for ln in relevant)
    covered_regions = sum(
        any(s4.box_containment(ln["bbox"], box) >= 0.50 for ln in detected)
        for box in crop_boxes
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "detection_scope": "tesseract_tsv_line_level_math_regex_vs_refined_formula_crops",
        "detected_math_lines": len(detected),
        "math_reference_lines": len(relevant),
        "detection_true_positives": tp,
        "detection_false_positives": fp,
        "detection_false_negatives": fn,
        "detection_precision": round(precision, 4) if precision is not None else None,
        "detection_recall": round(recall, 4) if recall is not None else None,
        "formula_region_coverage": (
            round(covered_regions / len(crop_boxes), 4) if crop_boxes else None
        ),
    }


def _block_order_metrics(blocks: list[dict[str, Any]], lines: list[dict[str, Any]]) -> dict[str, Any]:
    matched: list[int] = []
    used: set[int] = set()
    for block in blocks:
        target = s4.normalize_text(block.get("text") or block.get("latex", ""))
        best_i, best_score = -1, 0.0
        bbox = block.get("bbox")
        for i, ln in enumerate(lines):
            if i in used:
                continue
            sim = SequenceMatcher(None, target[:200], s4.normalize_text(ln["text"])[:200]).ratio()
            if bbox:
                sim = max(sim, s4.box_containment(ln["bbox"], bbox))
            if sim > best_score:
                best_i, best_score = i, sim
        if best_i >= 0 and best_score >= 0.35:
            used.add(best_i)
            matched.append(best_i)
    pairs = total_ok = 0
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            pairs += 1
            total_ok += int(matched[i] < matched[j])
    return {
        "block_coverage": round(len(matched) / len(blocks), 4) if blocks else None,
        "reading_order_accuracy": round(total_ok / pairs, 4) if pairs else None,
    }


def evaluate() -> dict[str, Any]:
    previous = json.loads(PREVIOUS_METRICS.read_text(encoding="utf-8")) if PREVIOUS_METRICS.exists() else {}
    previous_pages = {f"{p['doc_id']}_p{p['pdf_page']:04d}": p for p in previous.get("pages", [])}

    metrics: dict[str, Any] = {
        "schema_version": "1.0",
        "reference_status": "provisional_programmatic_visual_pending",
        "approval": {"training": False, "rag": False},
        "pages": [],
        "comparison": {},
    }
    comparison_pages: list[dict[str, Any]] = []
    formula_total = formula_exact_total = formula_semantic_total = 0
    for item in s4.PILOT:
        doc_id, page_no = item["doc_id"], item["pdf_page"]
        key = f"{doc_id}_p{page_no:04d}"
        ref = load_provisional_reference(doc_id, page_no)
        blocks = ref["reference"]["blocks"]
        ref_formulas = [b for b in blocks if b.get("type") == "formula"]

        ocr_txt_path = s4.OCR_DIR / f"{key}.txt"
        tsv_path = s4.OCR_DIR / f"{key}.tsv"
        ocr_text = ocr_txt_path.read_text(encoding="utf-8") if ocr_txt_path.exists() else ""
        lines = s4.parse_tsv(tsv_path) if tsv_path.exists() else []

        fp = REFINED_FORMULA_DIR / f"{key}.json"
        formula_results = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        by_index = {r["block_index"]: r for r in formula_results}

        exact = semantic = 0
        similarities: list[float] = []
        for idx, block in enumerate(blocks):
            if block.get("type") != "formula":
                continue
            output = by_index.get(idx)
            if not output:
                continue
            ref_latex = s4.normalize_latex(block["latex"])
            hyp_latex = s4.normalize_latex(output["latex"])
            similarities.append(1.0 - s4.cer(ref_latex, hyp_latex))
            exact += int(hyp_latex == ref_latex)
            semantic += int(s4.sympy_match(block["latex"], output["latex"]) is True)

        formula_total += len(ref_formulas)
        formula_exact_total += exact
        formula_semantic_total += semantic

        detection = _page_detection_metrics(lines, ref_formulas)
        order = _block_order_metrics(blocks, lines)
        timing = ref.get("quality", {}).get("manual_correction_seconds")
        timing_source = ref.get("quality", {}).get(
            "manual_correction_timing_source", "self_estimate_unmeasured"
        )
        page_metrics = {
            "doc_id": doc_id,
            "pdf_page": page_no,
            "printed_page": ref["source"].get("printed_page"),
            "reference_status": ref.get("review", {}).get("verification_status"),
            "visual_confirmation": ref.get("review", {}).get("visual_confirmation"),
            "cer_all": round(s4.cer(ref["reference"]["text"], ocr_text), 4),
            "cer_khmer": round(s4.cer(ref["reference"]["text"], ocr_text, khmer_only=True), 4),
            "reference_blocks": len(blocks),
            "reference_formulas": len(ref_formulas),
            "formula_ocr_attempted": len(formula_results),
            "formula_normalized_exact": round(exact / len(ref_formulas), 4) if ref_formulas else None,
            "formula_semantic_match": round(semantic / len(ref_formulas), 4) if ref_formulas else None,
            "formula_latex_similarity": (
                round(sum(similarities) / len(similarities), 4) if similarities else None
            ),
            **detection,
            **order,
            "manual_correction_seconds": timing,
            "manual_correction_timing_source": timing_source,
        }
        metrics["pages"].append(page_metrics)

        prev = previous_pages.get(key, {})
        comparison_pages.append({
            "doc_id": doc_id,
            "pdf_page": page_no,
            "step4a_formula_latex_similarity": prev.get("formula_latex_similarity"),
            "step4b_formula_latex_similarity": page_metrics["formula_latex_similarity"],
            "step4a_formula_detection_false_positives": prev.get("formula_detection_false_positives"),
            "step4b_detection_false_positives": page_metrics["detection_false_positives"],
            "step4a_reference_formulas": prev.get("reference_formulas"),
            "step4b_reference_formulas": page_metrics["reference_formulas"],
            "comparable": prev.get("reference_formulas") == page_metrics["reference_formulas"],
        })

    numeric_keys = [
        "cer_all", "cer_khmer", "formula_normalized_exact", "formula_semantic_match",
        "formula_latex_similarity", "detection_precision", "detection_recall",
        "formula_region_coverage", "block_coverage", "reading_order_accuracy",
    ]
    agg: dict[str, float | None] = {}
    for key in numeric_keys:
        vals = [p[key] for p in metrics["pages"] if p.get(key) is not None]
        agg[key] = round(sum(vals) / len(vals), 4) if vals else None
    metrics["macro_average"] = agg
    metrics["micro_average"] = {
        "basis": "all formula blocks across the four pilot pages",
        "formula_total": formula_total,
        "formula_exact_matches": formula_exact_total,
        "formula_exact_accuracy": (
            round(formula_exact_total / formula_total, 4) if formula_total else None
        ),
        "formula_semantic_matches": formula_semantic_total,
        "formula_semantic_accuracy": (
            round(formula_semantic_total / formula_total, 4) if formula_total else None
        ),
        "note": "Pooled block-level counts; not averaged over pages.",
    }
    metrics["manual_correction_total"] = {
        "seconds": sum(p["manual_correction_seconds"] or 0 for p in metrics["pages"]),
        "minutes": round(sum(p["manual_correction_seconds"] or 0 for p in metrics["pages"]) / 60, 1),
        "source": "self_estimate_unmeasured",
    }
    prev_agg = previous.get("macro_average", {})
    deltas: dict[str, float | None] = {}
    for key in ["cer_all", "cer_khmer", "formula_latex_similarity", "block_coverage", "reading_order_accuracy"]:
        if prev_agg.get(key) is not None and agg.get(key) is not None:
            deltas[key] = round(agg[key] - prev_agg[key], 4)
    metrics["comparison"] = {
        "label": "indicative_only_not_a_controlled_accuracy_comparison",
        "step4a_macro_average": prev_agg,
        "step4b_macro_average": agg,
        "step4b_micro_average": metrics["micro_average"],
        "deltas_step4b_minus_step4a": deltas,
        "notes": [
            "Step 4B references are corrected but not visually confirmed; CER changes reflect reference corrections as well as OCR performance.",
            "Formula-block counts changed after splitting merged expressions, so exact/similarity comparisons are indicative only.",
            "Step 4A reported macro CER 0.648 in prose; its metrics JSON contained 0.647. Step 4B recomputes from source values.",
            "Step 4A false positives used broad reference bboxes; Step 4B uses tight formula-only crops and line-level precision/recall. The FP counts are not directly comparable.",
            "Formula crops were manually refined; formula_region_coverage = 1.0 does not demonstrate automatic formula detection.",
            "This Step 4A/4B comparison is indicative, not a controlled accuracy comparison.",
        ],
    }
    METRICS_OUT.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    COMPARISON_OUT.write_text(
        json.dumps({
            "schema_version": "1.0",
            "scope": "four Step 4A pages only",
            "pages": comparison_pages,
            "aggregate": metrics["comparison"],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[step4b-evaluate] wrote {METRICS_OUT}")
    print(f"[step4b-evaluate] wrote {COMPARISON_OUT}")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["formula", "math", "evaluate", "verify", "all"])
    args = parser.parse_args()
    if args.command in ("formula", "all"):
        ocr_formulas_refined()
    if args.command in ("math", "all"):
        math_verification()
    if args.command in ("evaluate", "all"):
        evaluate()
    if args.command in ("verify", "all"):
        verify_schema_consistency()


if __name__ == "__main__":
    main()
