# Step 4A — Free/Open-Source OCR Pilot Preparation

Status: **complete** for the four approved pages. No paid API, no billing, no Google Vision/Mathpix, no full-chapter processing, and no RAG/fine-tuning/model training was performed.

## 1. Pages processed

| Source | Document ID | PDF page | Printed page | Render size (300 DPI) | Rendered image |
|---|---|---:|---:|---|---|
| Basic textbook | `basic_course_book` | 114 | 109 | 2481 × 3509 | `C:\Users\Meng\AppData\Local\Temp\opencode\step4a\pages\basic_course_book_p0114.png` |
| Advanced textbook | `advanced_course_book` | 85 | 80 | 2481 × 3509 | `...\pages\advanced_course_book_p0085.png` |
| Basic answer key | `basic_answer_key` | 1 | 1 | 2550 × 3301 | `...\pages\basic_answer_key_p0001.png` |
| Advanced answer key | `advanced_answer_key` | 20 | 20 | 2550 × 3301 | `...\pages\advanced_answer_key_p0020.png` |

All images, Tesseract text/TSV files, formula crops, and model files are outside the Git repository. SHA-256 hashes before and after rendering are identical for all four source PDFs (see `%TEMP%\opencode\step4a\render_manifest.json`).

## 2. Toolchain (100% free/open-source)

| Role | Tool used | Note |
|---|---|---|
| PDF render | `pypdfium2` 4.30/5.15 | Free, BSD-2-Clause. 300 DPI. |
| PDF text layer | `pypdf` 7.1.1 | Diagnostic only; Khmer text layer is unusable. |
| Khmer OCR | Tesseract 5.4.0 (`khm` + `eng`, tessdata_best) | Installed via winget. Initial winget error still left a working install. |
| Formula OCR | **RapidLaTeXOCR 0.0.9** (ONNX port of pix2tex, Apache-2.0) | Used because PyTorch/Texify DLLs are blocked by Windows Application Control. Model files: ~171 MB, downloaded from the project's public GitHub release. |
| Math verification | SymPy 1.14 + `antlr4-python3-runtime` | LaTeX parser enabled. |
| Environment | Python 3.12 venv with `--system-site-packages` | Avoided incompatible/blocked wheels. |

No Google Cloud Vision, Mathpix, paid OCR, billing activation, or Colab usage occurred.

### Windows dependency issues (no security policy bypassed)

- PyTorch/Texify: `torch_python.dll` is unsigned and blocked by Application Control. Not bypassed.
- NumPy 1.26 (required by RapidLaTeXOCR's metadata) and chardet 7 mypyc DLL were also blocked. Used NumPy 2.5.4 (already allowlisted) with a one-line compatibility patch in the temporary venv, and pure-Python chardet 5.2.0.
- Tesseract `tsv` config initially failed because the temp TESSDATA directory lacked `configs/`; copied the official configs from the Tesseract install.

## 3. Reproducible commands

```powershell
$py = "$env:LOCALAPPDATA\Temp\opencode\khmermath-ocr2\Scripts\python.exe"
& $py data_pipeline\step4a_pipeline.py render
& $py data_pipeline\step4a_pipeline.py extract
& $py data_pipeline\step4a_pipeline.py ocr --stage text
& $py data_pipeline\step4a_pipeline.py ocr --stage formula
& $py data_pipeline\step4a_pipeline.py evaluate
```

Outputs: `docs/step4a_metrics.json`; intermediates in `%TEMP%\opencode\step4a`.

## 4. Manual reference schema

Each `docs/step4a_reference/<doc_id>_p<page>.json` contains:

```json
{
  "schema_version": "1.0",
  "source": {
    "doc_id": "basic_answer_key",
    "document_type": "answer_key",
    "curriculum_level": "basic",
    "relative_file_path": "data/raw/Answer_key_book/Basic_Level/Chapter4.pdf",
    "pdf_page": 1,
    "printed_page": "1",
    "render_dpi": 300,
    "image_width": 2550,
    "image_height": 3301
  },
  "reference": {
    "text": "...",
    "blocks": [
      {"order": 4, "type": "formula", "bbox": [342,791,1484,835],
       "latex": "F(x)=-7x+4,\\quad f(x)=-7"},
      {"order": 3, "type": "text", "bbox": [305,663,1281,716],
       "text": "១. បង្ហាញថា F(x) ជាព្រីមីទីវនៃ f(x)..."}
    ]
  },
  "quality": {"manual_correction_seconds": 360,
              "verification_method": "..."},
  "notes": "..."
}
```

`bbox` values are `[x0, y0, x1, y1]` in 300-DPI pixels. Headers/footers are excluded from scoring.

## 5. OCR text examples

Basic textbook p114, Tesseract draft (line 4–8):

```text
អាដកេក្រាល
ក្នងការគណនាអាំងតេក្រាលដែលមានដើមកំណើតមកពីការគណនាផ្ទៃក្រឡានៃផ្ទែ
```

Manual reference correction:

```text
អាំងតេក្រាល
ក្នុងការគណនាអាំងតេក្រាលដែលមានដើមកំណើតមកពីការគណនាផ្ទៃក្រឡានៃផ្ទៃ ផ្នែកបង់...
```

Basic answer key p1, Tesseract draft vs reference:

```text
OCR:  អាំងតេក្រាល ... ព្រីមីទីវនៃ/(@) .VeR
REF:  អាំងតេក្រាល ... ព្រីមីទីវនៃ f(x), ∀x∈ℝ
```

## 6. Formula OCR examples (RapidLaTeXOCR)

| Source | Reference LaTeX | OCR LaTeX (excerpt) |
|---|---|---|
| Basic textbook p114 | `\int_a^b f(x)\,dx` | `\left.\stackrel{b}{f}~f(x)d x~~\stackrel{\rightharpoonup}{\downarrow}\right.\ 8` |
| Advanced textbook p85 | `V_1=\pi\int_0^1[f(x)]^2dx=...` | `V_{1}\;=\;\int_{0}^{1}\pi[f(x)]^{2}dx\;=\;\int_{0}^{1}\pi(\sqrt{x})^{2}dx` |
| Basic answer key p1 | `F(x)=3e^{2x-1}-7, f(x)=6e^{2x-1}` | `F(x)=3e^{x^{2}-1}-7 ... f(x)=6x e^{...}` |
| Advanced answer key p20 | `I=\int_0^{\pi/2}(e^{-2at}+e^{-at}\cos t)dt` | `=\frac{1}{2}\int_{0}^{\pi}(e^{-at}+e^{-at}\cos t)dt` |

Tight, formula-only crops work reasonably; crops that include Khmer labels or item numbers produce severe artifacts.

## 7. Evaluation results

Full data: `docs/step4a_metrics.json`.

| Page | CER (all) | CER (Khmer) | Formula exact | Formula similarity | Formula coverage | Detection FP | Block coverage | Reading order |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Basic textbook p114 | 0.110 | 0.088 | 0.00 | 0.00 | 1.00 | 33 | 1.00 | 1.00 |
| Advanced textbook p85 | 1.000* | 0.575 | 0.00 | 0.137 | 1.00 | 69 | 1.00 | 0.944 |
| Basic answer key p1 | 0.478 | 0.240 | 0.00 | 0.079 | 1.00 | 24 | 1.00 | 1.00 |
| Advanced answer key p20 | 1.000* | 0.503 | 0.00 | 0.167 | 1.00 | 34 | 0.889 | 1.00 |
| **Macro average** | **0.647** | **0.352** | **0.00** | **0.096** | **1.00** | **40** | **0.972** | **0.986** |

\* CER is capped at 1.0; raw values exceeded 1 on text-dense pages because OCR inserted formula/figure noise.

Formula normalized-exact match and SymPy semantic match are both 0.00. The character-level LaTeX similarity shows partial structure recovery (~10%). Manual correction estimates recorded in the references total **1680 seconds (~28 min)** for four pages, i.e. ~7 min/page. These are provisional self-estimates, not human stopwatch timings.

## 8. Errors, limitations, and recommendations

1. **Reference verification limitation.** This model cannot receive image input, so the "manual" references were produced by correcting Tesseract drafts and reconstructing formulas from the pypdf text layer. They were **not visually confirmed against the rendered page**. A human reviewer must validate the JSON references before they are treated as gold.
2. **Tesseract line fragmentation.** Khmer words are split into many TSV lines; block coverage and reading-order scores are optimistic because reference blocks are coarse. Future work should group lines into paragraphs before scoring.
3. **Formula OCR is not pilot-ready.** RapidLaTeXOCR/pix2tex works only on clean, isolated formula crops; item labels and Khmer text destroy output. Need a better cropper (layout model or manually refined bboxes) before scaling.
4. **Formula detector heuristic is weak.** Math-symbol regex over Tesseract lines has high false positives (24–69 per page). Coverage is 1.00 only because reference bboxes are broad. Use stricter geometric/layout detection.
5. **No semantic matches.** SymPy parsing succeeds only on clean LaTeX; OCR output contains unsupported constructs (`\stackrel`, `\widetilde`, etc.). Use normalized token similarity as the primary formula metric in this pilot; keep SymPy as a secondary check after cleanup.
6. **Windows Application Control.** PyTorch/Texify and some wheel DLLs are blocked. RapidLaTeXOCR ONNX is a working free substitute. Do not attempt to disable or bypass the policy.
7. **Recommendation for the seven-week deadline.** Keep the free pipeline (pypdfium2 + Tesseract khm/eng + ONNX pix2tex) but budget explicit human QA. Do **not** assume OCR alone can produce training data. First have a human verify these four references; then improve formula cropping before expanding to eight pages.

## 9. Step 4B: reference validation and focused formula-OCR improvement

Scope was limited to the same four Step 4A pages. Full-page reference transcriptions were moved from `docs/step4a_reference/` to `data/evaluation/step4a_reference/`, which is Git-ignored. The repository keeps only the schema (`docs/step4b_reference_schema.json`), pipeline code, aggregate metrics, and verification summaries.

### 9.1 Reference corrections and review status

Each corrected reference now records `reviewer`, `review_date`, `verification_status = provisional_programmatic_visual_pending`, `visual_confirmation = false`, `approved_for_training_or_rag = false`, and a `checks` object. The schema title/ID and the pipeline loader now say `provisional` rather than `verified`; the Git-ignored directory remains `data/evaluation/step4a_reference/` as previously specified. Key corrections:

- `basic_course_book_p0114.json`: corrected the Khmer term `គិមិត្តសញ្ញា` → `និមិត្តសញ្ញា`; replaced the broad formula box with a formula-only crop.
- `advanced_course_book_p0085.json`: corrected `ស្មូលីត` → `សូលីត`; moved the general-volume crop to the correct expression region; tightened all formula crops.
- `basic_answer_key_p0001.json`: added separate crops per expression so Khmer item labels, `និង`, and exercise numbers are excluded.
- `advanced_answer_key_p0020.json`: split merged derivation steps into separate blocks, reassigned formula-only crops, and preserved the original Step 4A block 9 expression in `original_step4a_latex`. The corrected expression is `I'=(a+e^{-aπ/2})/(a²+1)` with `correction.sympy_verified = true`.

**Limitation:** this runtime cannot receive image input. Verification was non-visual: rendered-page OCR text, pypdf text spans, TSV geometry, and SymPy algebra. Human/image-capable visual confirmation of the four references is still required before they can be treated as gold.

### 9.2 Mathematical verification

Full results: `docs/step4b_math_verification.json` (SymPy 1.14.0). Summary: **12 symbolic comparisons, 11 pass, 1 fail**. Statuses are no longer hardcoded: each is derived from a simplified symbolic difference. Raw SymPy values are recorded separately in `calculated_values`, so calculations are not confused with verification outcomes.

- Volumes: `V1 = π/2`, `V2 = π/5`, `V1 − V2 = 3π/10`; the final interval integral is `117π/5`. Each was compared against an independently written antiderivative.
- All four basic answer-key primitive/derivative pairs simplify to a zero difference.
- Advanced answer key: `J = ∫₀^{π/2} e^{-at} sin t dt = (1 − a e^{-aπ/2})/(a²+1)`, verified both as a definite integral and by differentiating an independently written antiderivative.
- Solving `I' = 1/a − J/a` symbolically gives the corrected `I' = (a + e^{-aπ/2})/(a²+1)`; the parsed corrected LaTeX also passes.
- The single failure is the preserved original Step 4A block 9 expression, which uses `e^{-aπ}` and an incomplete evaluation bar. `parse_latex` leaves a free `t` there, so the symbolic comparison fails as expected.
- Assumptions are explicit: `x, t` real; `a` real and positive; `a ≠ 0` where division by `a` occurs; `a²+1 ≠ 0`; `exp(3x)−x ≠ 0` on the q1d domain. Parsed names `e`, `π`, `a`, `t`, `x`, and `Ip` are normalized to pipeline symbols/constants before comparison.
- Proofs, layout, and unsupported LaTeX still require human visual inspection.

### 9.3 Focused formula-OCR retest

RapidLaTeXOCR was rerun only on tightened formula-only crops for the four approved pages. Crops are in the Git-ignored references; aggregate comparison: `docs/step4b_formula_ocr_comparison.json`.

| Page | Step 4A similarity | Step 4B similarity | Step 4B exact | Step 4B semantic | Main remaining problem |
|---|---:|---:|---:|---:|---|
| Basic textbook p114 | 0.000 | 0.643 | 0.00 | 0.00 | Output `\int_a^b f(x) dx` is close but not normalized-exact. |
| Advanced textbook p85 | 0.137 | 0.164 | 0.00 | 0.00 | Long formulas truncated; `\mathcal{T}` and array artifacts. |
| Basic answer key p1 | 0.079 | 0.411 | 0.10 | 0.20 | Exponent errors such as `e^{x²−1}` vs `e^{2x−1}` and `9x³` vs `9x²`. |
| Advanced answer key p20 | 0.167 | 0.340 | 0.00 | 0.00 | Limits and factors misread (for example `π` for `π/2`); block count changed 6 → 10 after splitting, so this page is not directly comparable. |

### 9.4 Updated metrics

Full data: `docs/step4b_metrics.json`.

| Metric | Step 4A | Step 4B |
|---|---:|---:|
| CER (all, macro) | 0.647 | 0.6472 |
| CER (Khmer, macro) | 0.3517 | 0.3536 |
| Formula normalized exact (macro) | 0.000 | 0.025 |
| Formula semantic match (macro) | 0.000 | 0.050 |
| Formula exact (micro, pooled) | — | 0.0385 (1/26) |
| Formula semantic (micro, pooled) | — | 0.0769 (2/26) |
| Formula LaTeX similarity (macro) | 0.0958 | 0.3891 |
| Detection precision | not reported | 0.4348 |
| Detection recall | not reported | 0.5866 |
| Formula-region coverage | 1.000 | 1.000 |
| Block coverage | 0.9722 | 1.000 |
| Reading order | 0.9861 | 1.000 |
| Manual correction time | 1680 s (self-estimate) | 1680 s (self-estimate) |

Macro averages are retained for continuity. The new micro-average pools all 26 formula blocks across the four pages: **1/26 exact** (0.0385) and **2/26 semantic** (0.0769). Detection precision/recall are line-level values: a Tesseract TSV line is counted as a detection when it contains a math-symbol regex match and as relevant when at least 50% of its box lies inside a refined formula-only crop. The formula crops were **manually refined**, so `formula_region_coverage = 1.00` only means every curated crop was sampled; it does **not** demonstrate automatic formula detection. Step 4A false-positive counts used broad boxes and are not directly comparable. The Step 4A/4B comparison is explicitly labeled **indicative only, not a controlled accuracy comparison**.

### 9.5 Remaining errors and decision

1. Final visual confirmation of the four references is still blocked in this runtime.
2. Formula OCR remains inaccurate for limits, exponents, fractions, and multi-line derivations; micro-average exact/semantic accuracy is only `1/26` and `2/26`.
3. The line-level math detector has low precision (0.4348) and incomplete recall (0.5866); it is not suitable as a production formula detector.
4. CER is capped at 1.0 on text-dense pages and is not a useful progress signal there.
5. Step 4A/4B comparisons are indicative only: references were corrected, crops were manually refined, and the advanced answer-key formula blocks were split.
6. Correction time is a self-estimate, not stopwatch-measured.
7. One math comparison fails by design: the preserved original Step 4A block 9 expression. The corrected `I'` expression passes symbolically.

Given that the 100% free formula OCR is still inaccurate after crop improvement, the recommended dataset path is **human-corrected LaTeX for the initial 20–30 exercises**, not further OCR over-optimization. No training, RAG ingestion, fine-tuning, or full-chapter extraction has been started.

## 10. Reproducibility

Free dependencies, exact versions, Windows Application Control limitations, the temporary RapidLaTeXOCR NumPy 2.x compatibility patch, and verification commands are documented in `docs/step4b_reproducibility.md`. Pipeline syntax was checked with `py_compile`, and `docs/step4b_schema_validation.json` reports all four provisional references as structurally consistent (`all_valid: true`) while retaining `visual_confirmation: false` and `approved_for_training_or_rag: false`.

## 11. Stop condition

Step 4B is complete for the four pilot pages. The approved files are staged but intentionally not committed or pushed. Awaiting human visual review and explicit approval before any commit, eight-page expansion, full 171-page extraction, RAG ingestion, or training.
