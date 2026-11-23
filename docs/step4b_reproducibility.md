# Step 4B reproducibility

## Scope

Step 4B uses only the same four Step 4A pilot pages. It does **not** start full-chapter extraction, RAG ingestion, fine-tuning, or training. Full-page transcriptions stay in the Git-ignored directory `data/evaluation/step4a_reference/` and remain provisional pending human image review.

All tools are free/open-source. No paid service, billing activation, Google Cloud Vision, or Mathpix was used.

## Environment

Recorded on Windows 11 (`10.0.26300`), Python 3.12.9.

| Component | Version / detail |
|---|---|
| Tesseract | `5.4.0.20240606` (Leptonica `1.84.1`) |
| Tesseract languages | system `eng`/`osd`; `khm` from `tessdata_best` copied into the temporary `TESSDATA_PREFIX` used by `step4a_pipeline.py` |
| pypdfium2 | `5.15.2` |
| pypdf | `7.1.1` |
| RapidLaTeXOCR | `0.0.9` (ONNX pix2tex port) |
| onnxruntime | `1.32.0` |
| opencv-python | `5.1.0.95` |
| Pillow | `12.2.0` |
| NumPy | `2.5.4` |
| SymPy | `1.14.0` |
| antlr4-python3-runtime | `4.11.1` (required by `sympy.parsing.latex.parse_latex`) |
| tokenizers | `0.23.3` |
| chardet | `5.2.0` |
| `jsonschema` | not installed; Step 4B uses a small built-in subset checker |

The Python environment is disposable and outside the repository. Source PDF hashes and rendered page metadata are recorded in the temporary `render_manifest.json`, not committed.

## Free OCR pipeline

1. `pypdfium2` rendered the four approved PDF pages at 300 DPI.
2. Tesseract ran with `-l khm+eng --psm 3` using the temporary `TESSDATA_PREFIX`.
3. `pypdf` supplied the Unicode text layer for non-visual reference cross-checks.
4. Refined formula-only crops were passed to RapidLaTeXOCR's ONNX model.
5. SymPy compared supported expressions; proofs and layout remain human-review items.

## Windows Application Control limitations

Windows Application Control blocked unsigned PyTorch/Texify DLLs (`OSError: [WinError 4551]`, including `torch_python.dll`). It also blocked the NumPy 1.26 and chardet 7 mypyc DLL builds. The pipeline therefore uses RapidLaTeXOCR's ONNX runtime rather than PyTorch/Texify.

No system policy, signing configuration, or security control was modified or bypassed.

## Temporary RapidLaTeXOCR compatibility patch

In the disposable virtual environment only, this line in
`.../Lib/site-packages/rapid_latex_ocr/main.py` was changed to explicitly coerce the NumPy 2.x result to a Python integer:

```python
argmax_idx = int(np.argmax(resizer_res, axis=-1).item())
```

This is a local dependency compatibility shim for NumPy 2.x. It does not alter Windows Application Control, signed binaries, or system settings. A fresh environment pinned to NumPy 1.x may not need it.

## Commands

From the repository root, using the disposable Python interpreter:

```powershell
$py = 'C:\Users\Meng\AppData\Local\Temp\opencode\khmermath-ocr2\Scripts\python.exe'

& $py -m py_compile data_pipeline\step4a_pipeline.py data_pipeline\step4b_pipeline.py
& $py data_pipeline\step4b_pipeline.py all
& $py data_pipeline\step4b_pipeline.py verify
```

`all` reruns the formula retest, math verification, metric recomputation, and structural schema check. The formula retest reads the four rendered page images from the temporary workspace and writes formula crops there; only aggregate JSON and documentation are staged.

## Validation status

- Pipeline syntax: `py_compile` passed for both pipeline modules.
- JSON structure: `docs/step4b_schema_validation.json` reports `all_valid: true` for all four provisional references.
- Review flags: all four references have `visual_confirmation: false`, `approved_for_training_or_rag: false`, and `verification_status: provisional_programmatic_visual_pending`.
- Math: 12 symbolic comparisons, 11 pass, 1 fail. The single failure is the preserved original Step 4A block 9 expression; the corrected `I'` expression passes.
- Formula metrics: macro averages are retained; micro-average formula accuracy is `1/26` exact and `2/26` semantic.
- Step 4A/4B comparison remains indicative only, not a controlled accuracy comparison.
