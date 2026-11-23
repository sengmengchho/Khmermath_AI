#!/usr/bin/env python3
"""Audit Chapter 4 PDF text extraction with pypdf.

The script reads document paths and Chapter 4 page ranges from
``docs/chapter4_inventory.csv``, validates each PDF, computes SHA-256
checksums, and analyzes text only inside each document's Chapter 4 range.

It does not modify the PDFs or the inventory. It does not perform OCR,
formula recognition, or mathematical verification.

Exit codes:
    0 - report generated; all documents passed structural validation
    1 - report generated; at least one document failed validation/readability
    2 - inventory or command-line error prevented the audit
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader

REQUIRED_INVENTORY_COLUMNS = [
    "curriculum_level",
    "document_type",
    "relative_file_path",
    "total_pdf_pages",
    "chapter_start_pdf_page",
    "chapter_end_pdf_page",
    "printed_page_range",
    "extractable_khmer",
    "formula_quality",
    "notes",
]

KHMER_RANGES = ((0x1780, 0x17FF),)
PRIVATE_USE_RANGES = (
    (0xE000, 0xF8FF),       # Basic Multilingual Plane private-use area
    (0xF0000, 0xFFFFD),    # Supplementary Private Use Area-A
    (0x100000, 0x10FFFD),  # Supplementary Private Use Area-B
)

# Heuristic thresholds. These are deliberately conservative and are reported
# in the JSON so that another audit run can reproduce the same flags.
PRIVATE_USE_FLAG_THRESHOLD = 0.05  # 5% of non-whitespace characters
NO_KHMER_MIN_NONSPACE = 20         # ignore very short/math-only pages

WARNINGS = [
    "Character ratios and heuristic flags cannot guarantee mathematical formula accuracy or semantic correctness.",
    "A page can pass every heuristic and still contain wrong or malformed formulas.",
    "This audit performs text-layer extraction only; it does not perform OCR, formula recognition, or symbolic verification.",
    "Pages with no Unicode Khmer are flagged for review; this heuristic may also flag legitimate math-only or non-Khmer pages.",
    f"Private-use characters are flagged when they make up at least {PRIVATE_USE_FLAG_THRESHOLD:.0%} of non-whitespace characters.",
]


class AuditError(Exception):
    """Raised when the audit cannot start or an inventory is unusable."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Chapter 4 PDF text extraction using inventory metadata."
    )
    parser.add_argument(
        "--inventory",
        default="docs/chapter4_inventory.csv",
        help="Inventory CSV path (default: docs/chapter4_inventory.csv)",
    )
    parser.add_argument(
        "--report",
        default="docs/chapter4_audit_report.json",
        help="JSON report output path (default: docs/chapter4_audit_report.json)",
    )
    return parser.parse_args(argv)


def resolve_from_repo(value: str, repo_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def in_ranges(codepoint: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= codepoint <= end for start, end in ranges)


def ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def char_metrics(text: str) -> dict[str, int]:
    """Count characters needed for the audit.

    Ratios are calculated from these counts by the caller. Every non-whitespace
    character is placed in exactly one category.
    """
    metrics = {
        "char_count": len(text),
        "whitespace_char_count": 0,
        "nonspace_char_count": 0,
        "khmer_char_count": 0,
        "private_use_char_count": 0,
        "replacement_char_count": 0,
        "ascii_letter_count": 0,
        "digit_count": 0,
        "other_char_count": 0,
    }
    for char in text:
        codepoint = ord(char)
        if char.isspace():
            metrics["whitespace_char_count"] += 1
        elif in_ranges(codepoint, KHMER_RANGES):
            metrics["khmer_char_count"] += 1
        elif in_ranges(codepoint, PRIVATE_USE_RANGES):
            metrics["private_use_char_count"] += 1
        elif codepoint == 0xFFFD:
            metrics["replacement_char_count"] += 1
        elif char.isascii() and char.isalpha():
            metrics["ascii_letter_count"] += 1
        elif char.isdigit():
            metrics["digit_count"] += 1
        else:
            metrics["other_char_count"] += 1
    metrics["nonspace_char_count"] = (
        metrics["char_count"] - metrics["whitespace_char_count"]
    )
    return metrics


def page_flags(metrics: dict[str, int]) -> list[str]:
    """Return heuristic flags for one page.

    Empty pages are reported separately from suspected corruption. The
    ``no_unicode_khmer`` heuristic is only applied when there is enough text
    to be meaningful.
    """
    flags: list[str] = []
    nonspace = metrics["nonspace_char_count"]
    if nonspace == 0:
        flags.append("no_extractable_text")
        return flags
    if metrics["replacement_char_count"] > 0:
        flags.append("replacement_character_present")
    pua_ratio = ratio(metrics["private_use_char_count"], nonspace)
    if pua_ratio >= PRIVATE_USE_FLAG_THRESHOLD:
        flags.append("high_private_use_ratio")
    if metrics["khmer_char_count"] == 0 and nonspace >= NO_KHMER_MIN_NONSPACE:
        flags.append("no_unicode_khmer")
    return flags


def page_status(flags: list[str]) -> str:
    if "no_extractable_text" in flags:
        return "no_text"
    if flags:
        return "suspected_corruption"
    return "ok"


def compress_pages(pages: list[int]) -> str:
    """Compress sorted 1-based page numbers into readable ranges."""
    values = sorted(set(pages))
    if not values:
        return ""
    spans: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        spans.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    spans.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(spans)


def load_inventory(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise AuditError(f"Inventory not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise AuditError(f"Inventory has no header row: {path}")
        missing = [name for name in REQUIRED_INVENTORY_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise AuditError(
                f"Inventory is missing required columns: {', '.join(missing)}"
            )
        rows = list(reader)
    if not rows:
        raise AuditError(f"Inventory contains no records: {path}")
    return rows, list(reader.fieldnames)


def validate_inventory_row(row: dict[str, str]) -> list[str]:
    errors: list[str] = []
    missing = [
        field for field in REQUIRED_INVENTORY_COLUMNS
        if not (row.get(field) or "").strip()
    ]
    if missing:
        errors.append(f"Missing required inventory fields: {', '.join(missing)}")
    return errors


def parse_int_field(row: dict[str, str], field: str, errors: list[str]) -> int | None:
    value = (row.get(field) or "").strip()
    try:
        return int(value)
    except ValueError:
        errors.append(f"Invalid integer in '{field}': {value!r}")
        return None


def empty_validation() -> dict[str, Any]:
    return {
        "file_exists": False,
        "file_size_bytes": None,
        "sha256_computed": False,
        "sha256_expected": None,
        "sha256_matches_expected": None,
        "page_count_matches": None,
        "chapter_range_valid": None,
        "errors": [],
    }


def analyze_document(
    index: int,
    row: dict[str, str],
    repo_root: Path,
) -> dict[str, Any]:
    """Validate and analyze one inventory record without raising on bad PDFs."""
    relative_value = (row.get("relative_file_path") or "").strip()
    doc: dict[str, Any] = {
        "index": index,
        "curriculum_level": (row.get("curriculum_level") or "").strip(),
        "document_type": (row.get("document_type") or "").strip(),
        "relative_file_path": relative_value,
        "printed_page_range": (row.get("printed_page_range") or "").strip(),
        "declared_total_pdf_pages": None,
        "chapter_start_pdf_page": None,
        "chapter_end_pdf_page": None,
        "chapter_page_count": None,
        "pdf_page_count": None,
        "file_size_bytes": None,
        "sha256": None,
        "status": "error",
        "validation": empty_validation(),
        "text_analysis": None,
    }
    errors = validate_inventory_row(row)

    if not relative_value:
        errors.append("No PDF path provided in inventory.")
        doc["validation"]["errors"] = errors
        return doc

    pdf_path = resolve_from_repo(relative_value, repo_root)
    validation = doc["validation"]
    validation["file_exists"] = pdf_path.is_file()

    expected_sha = (row.get("sha256") or "").strip().lower() or None
    validation["sha256_expected"] = expected_sha

    if not validation["file_exists"]:
        errors.append(f"PDF file not found: {pdf_path}")
        validation["errors"] = errors
        return doc

    try:
        doc["file_size_bytes"] = pdf_path.stat().st_size
        doc["sha256"] = sha256_file(pdf_path)
        validation["file_size_bytes"] = doc["file_size_bytes"]
        validation["sha256_computed"] = True
        if expected_sha is not None:
            validation["sha256_matches_expected"] = doc["sha256"] == expected_sha
            if doc["sha256"] != expected_sha:
                errors.append(
                    f"SHA-256 mismatch for {pdf_path}: "
                    f"expected {expected_sha}; got {doc['sha256']}"
                )
    except OSError as exc:
        errors.append(f"Could not read PDF metadata/checksum for {pdf_path}: {exc}")
        validation["errors"] = errors
        return doc

    total_pages = parse_int_field(row, "total_pdf_pages", errors)
    start_page = parse_int_field(row, "chapter_start_pdf_page", errors)
    end_page = parse_int_field(row, "chapter_end_pdf_page", errors)
    doc["declared_total_pdf_pages"] = total_pages
    doc["chapter_start_pdf_page"] = start_page
    doc["chapter_end_pdf_page"] = end_page

    if total_pages is None or start_page is None or end_page is None:
        validation["errors"] = errors
        return doc

    try:
        with pdf_path.open("rb") as stream:
            reader = PdfReader(stream)
            actual_pages = len(reader.pages)
            doc["pdf_page_count"] = actual_pages
            page_count_matches = actual_pages == total_pages
            validation["page_count_matches"] = page_count_matches
            if not page_count_matches:
                errors.append(
                    f"Page count mismatch for {pdf_path}: "
                    f"inventory says {total_pages}; actual PDF has {actual_pages}"
                )

            chapter_range_valid = 1 <= start_page <= end_page <= actual_pages
            validation["chapter_range_valid"] = chapter_range_valid
            if not chapter_range_valid:
                errors.append(
                    f"Invalid Chapter 4 range for {pdf_path}: "
                    f"{start_page}-{end_page} with {actual_pages} PDF pages"
                )
                validation["errors"] = errors
                return doc

            chapter_page_count = end_page - start_page + 1
            doc["chapter_page_count"] = chapter_page_count
            pages: list[dict[str, Any]] = []
            page_errors = 0

            for pdf_page in range(start_page, end_page + 1):
                page = reader.pages[pdf_page - 1]
                page_record: dict[str, Any] = {
                    "pdf_page": pdf_page,
                    "status": "ok",
                    "has_extractable_text": False,
                    "flags": [],
                    "char_count": 0,
                    "nonspace_char_count": 0,
                    "khmer_char_count": 0,
                    "khmer_ratio_of_all_chars": 0.0,
                    "khmer_ratio_of_nonspace_chars": 0.0,
                    "private_use_char_count": 0,
                    "private_use_ratio_of_all_chars": 0.0,
                    "private_use_ratio_of_nonspace_chars": 0.0,
                    "replacement_char_count": 0,
                    "ascii_letter_count": 0,
                    "digit_count": 0,
                    "other_char_count": 0,
                    "extraction_error": None,
                }
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # damaged page or extractor failure
                    page_errors += 1
                    page_record["status"] = "extraction_error"
                    page_record["flags"] = ["page_extraction_error"]
                    page_record["extraction_error"] = f"{type(exc).__name__}: {exc}"
                    errors.append(
                        f"Text extraction failed for {pdf_path} PDF page "
                        f"{pdf_page}: {page_record['extraction_error']}"
                    )
                    pages.append(page_record)
                    continue

                metrics = char_metrics(text)
                flags = page_flags(metrics)
                page_record.update(metrics)
                page_record["flags"] = flags
                page_record["status"] = page_status(flags)
                page_record["has_extractable_text"] = metrics["nonspace_char_count"] > 0
                page_record["khmer_ratio_of_all_chars"] = ratio(
                    metrics["khmer_char_count"], metrics["char_count"]
                )
                page_record["khmer_ratio_of_nonspace_chars"] = ratio(
                    metrics["khmer_char_count"], metrics["nonspace_char_count"]
                )
                page_record["private_use_ratio_of_all_chars"] = ratio(
                    metrics["private_use_char_count"], metrics["char_count"]
                )
                page_record["private_use_ratio_of_nonspace_chars"] = ratio(
                    metrics["private_use_char_count"], metrics["nonspace_char_count"]
                )
                pages.append(page_record)

            totals = {
                key: sum(page[key] for page in pages)
                for key in (
                    "char_count",
                    "nonspace_char_count",
                    "khmer_char_count",
                    "private_use_char_count",
                    "replacement_char_count",
                    "ascii_letter_count",
                    "digit_count",
                    "other_char_count",
                )
            }
            doc["text_analysis"] = {
                "pages_analyzed": len(pages),
                "pages_with_no_text": sum(
                    1 for page in pages if page["status"] == "no_text"
                ),
                "pages_suspected_corruption": sum(
                    1 for page in pages if page["status"] == "suspected_corruption"
                ),
                "pages_with_extraction_errors": page_errors,
                "extracted_char_count": totals["char_count"],
                "extracted_nonspace_char_count": totals["nonspace_char_count"],
                "khmer_char_count": totals["khmer_char_count"],
                "khmer_ratio_of_all_chars": ratio(
                    totals["khmer_char_count"], totals["char_count"]
                ),
                "khmer_ratio_of_nonspace_chars": ratio(
                    totals["khmer_char_count"], totals["nonspace_char_count"]
                ),
                "private_use_char_count": totals["private_use_char_count"],
                "private_use_ratio_of_all_chars": ratio(
                    totals["private_use_char_count"], totals["char_count"]
                ),
                "private_use_ratio_of_nonspace_chars": ratio(
                    totals["private_use_char_count"], totals["nonspace_char_count"]
                ),
                "replacement_char_count": totals["replacement_char_count"],
                "pages": pages,
            }
    except Exception as exc:  # damaged, encrypted, or otherwise unreadable PDF
        errors.append(
            f"Could not read PDF {pdf_path}: {type(exc).__name__}: {exc}"
        )
        validation["errors"] = errors
        return doc

    doc["status"] = "ok" if not errors else "error"
    validation["errors"] = errors
    return doc


def build_summary(documents: list[dict[str, Any]]) -> dict[str, Any]:
    analyzed = [doc["text_analysis"] for doc in documents if doc["text_analysis"]]
    totals = {
        key: sum(item[key] for item in analyzed)
        for key in (
            "pages_analyzed",
            "pages_with_no_text",
            "pages_suspected_corruption",
            "pages_with_extraction_errors",
            "extracted_char_count",
            "extracted_nonspace_char_count",
            "khmer_char_count",
            "private_use_char_count",
            "replacement_char_count",
        )
    }
    documents_with_errors = sum(1 for doc in documents if doc["status"] != "ok")
    return {
        "documents_total": len(documents),
        "documents_valid": len(documents) - documents_with_errors,
        "documents_with_validation_errors": documents_with_errors,
        "validation_passed": documents_with_errors == 0,
        "pages_analyzed": totals["pages_analyzed"],
        "pages_with_no_text": totals["pages_with_no_text"],
        "pages_suspected_corruption": totals["pages_suspected_corruption"],
        "pages_with_extraction_errors": totals["pages_with_extraction_errors"],
        "extracted_char_count": totals["extracted_char_count"],
        "extracted_nonspace_char_count": totals["extracted_nonspace_char_count"],
        "khmer_char_count": totals["khmer_char_count"],
        "overall_khmer_ratio_of_all_chars": ratio(
            totals["khmer_char_count"], totals["extracted_char_count"]
        ),
        "overall_khmer_ratio_of_nonspace_chars": ratio(
            totals["khmer_char_count"], totals["extracted_nonspace_char_count"]
        ),
        "private_use_char_count": totals["private_use_char_count"],
        "overall_private_use_ratio_of_all_chars": ratio(
            totals["private_use_char_count"], totals["extracted_char_count"]
        ),
        "overall_private_use_ratio_of_nonspace_chars": ratio(
            totals["private_use_char_count"], totals["extracted_nonspace_char_count"]
        ),
        "replacement_char_count": totals["replacement_char_count"],
    }


def print_report(report: dict[str, Any], report_path: Path) -> None:
    summary = report["summary"]
    print("Chapter 4 PDF text-layer audit")
    print(f"Inventory: {report['inventory']['path']}")
    print(f"Inventory SHA-256: {report['inventory']['sha256']}")
    print(f"Report: {report['report_path']}")
    print("Scope: text extraction only; 1-based inclusive PDF pages")
    print()
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")

    for doc in report["documents"]:
        print()
        print(
            f"[{doc['index']}/{summary['documents_total']}] "
            f"{doc['curriculum_level']} / {doc['document_type']}"
        )
        print(f"  path: {doc['relative_file_path']}")
        print(f"  status: {doc['status'].upper()}")
        if doc["sha256"]:
            print(f"  sha256: {doc['sha256']}")
        if doc["pdf_page_count"] is not None:
            print(
                f"  pages: {doc['pdf_page_count']} "
                f"(declared {doc['declared_total_pdf_pages']})"
            )
        if (
            doc["chapter_start_pdf_page"] is not None
            and doc["chapter_page_count"] is not None
        ):
            print(
                f"  chapter: PDF pages {doc['chapter_start_pdf_page']}-"
                f"{doc['chapter_end_pdf_page']} "
                f"({doc['chapter_page_count']} pages); "
                f"printed {doc['printed_page_range']}"
            )
        analysis = doc["text_analysis"]
        if analysis:
            print(
                f"  text: {analysis['extracted_char_count']:,} chars "
                f"({analysis['extracted_nonspace_char_count']:,} non-space)"
            )
            print(
                f"  khmer: {analysis['khmer_char_count']:,} chars "
                f"({analysis['khmer_ratio_of_all_chars']:.2%} of all; "
                f"{analysis['khmer_ratio_of_nonspace_chars']:.2%} of non-space)"
            )
            print(
                f"  private-use: {analysis['private_use_char_count']:,} chars "
                f"({analysis['private_use_ratio_of_all_chars']:.2%} of all; "
                f"{analysis['private_use_ratio_of_nonspace_chars']:.2%} of non-space)"
            )
            print(f"  replacement chars: {analysis['replacement_char_count']:,}")
            print(f"  pages with no text: {analysis['pages_with_no_text']}")
            print(
                f"  pages suspected corrupted: "
                f"{analysis['pages_suspected_corruption']}"
            )
            print(
                f"  page extraction errors: "
                f"{analysis['pages_with_extraction_errors']}"
            )
            reason_pages: dict[str, list[int]] = defaultdict(list)
            for page in analysis["pages"]:
                for flag in page["flags"]:
                    reason_pages[flag].append(page["pdf_page"])
            if reason_pages:
                for reason, pages in sorted(reason_pages.items()):
                    print(f"    {reason}: {compress_pages(pages)}")
        if doc["validation"]["errors"]:
            for error in doc["validation"]["errors"]:
                print(f"  ERROR: {error}")

    print()
    print("Summary")
    print(
        f"  documents: {summary['documents_total']} "
        f"({summary['documents_valid']} valid, "
        f"{summary['documents_with_validation_errors']} errors)"
    )
    print(f"  pages analyzed: {summary['pages_analyzed']}")
    print(f"  pages with no text: {summary['pages_with_no_text']}")
    print(f"  pages suspected corrupted: {summary['pages_suspected_corruption']}")
    print(f"  page extraction errors: {summary['pages_with_extraction_errors']}")
    print(
        f"  khmer ratio (non-space): "
        f"{summary['overall_khmer_ratio_of_nonspace_chars']:.2%}"
    )
    print(
        f"  private-use ratio (non-space): "
        f"{summary['overall_private_use_ratio_of_nonspace_chars']:.2%}"
    )
    print(
        f"  structural validation: "
        f"{'PASS' if summary['validation_passed'] else 'FAIL'}"
    )
    print(f"  report written: {report_path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    inventory_path = resolve_from_repo(args.inventory, repo_root)
    report_path = resolve_from_repo(args.report, repo_root)

    try:
        inventory_sha = sha256_file(inventory_path)
        rows, fieldnames = load_inventory(inventory_path)
    except AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: Could not read inventory: {exc}", file=sys.stderr)
        return 2

    documents = [
        analyze_document(index, row, repo_root)
        for index, row in enumerate(rows, start=1)
    ]
    summary = build_summary(documents)
    fonttools_available = importlib.util.find_spec("fontTools") is not None
    report_warnings = list(WARNINGS)
    if not fonttools_available:
        report_warnings.append(
            "The optional fontTools package is not installed; pypdf may not "
            "fully parse some CFF/Type1 fonts and may emit encoding warnings."
        )
    report = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tool": {
            "name": "audit_pdfs.py",
            "python_version": platform.python_version(),
            "pypdf_version": pypdf.__version__,
            "platform": platform.platform(),
            "fonttools_available": fonttools_available,
        },
        "inventory": {
            "path": display_path(inventory_path, repo_root),
            "sha256": inventory_sha,
            "record_count": len(rows),
            "fields": fieldnames,
        },
        "report_path": display_path(report_path, repo_root),
        "warnings": report_warnings,
        "methodology": {
            "scope": "Chapter 4 text extraction only; no OCR or formula verification",
            "page_numbering": "1-based inclusive PDF page numbers",
            "khmer_codepoint_range": "U+1780-U+17FF",
            "private_use_codepoint_ranges": [
                "U+E000-U+F8FF",
                "U+F0000-U+FFFFD",
                "U+100000-U+10FFFD",
            ],
            "ratios": (
                "Reported over all extracted characters and over "
                "non-whitespace characters"
            ),
            "corruption_heuristics": {
                "no_extractable_text": "no non-whitespace characters extracted",
                "replacement_character_present": "U+FFFD appears in extracted text",
                "high_private_use_ratio": (
                    f"private-use characters >= {PRIVATE_USE_FLAG_THRESHOLD:.0%} "
                    "of non-whitespace characters"
                ),
                "no_unicode_khmer": (
                    "zero Unicode Khmer characters and at least "
                    f"{NO_KHMER_MIN_NONSPACE} non-whitespace characters"
                ),
            },
        },
        "summary": summary,
        "documents": documents,
    }

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except OSError as exc:
        print(f"ERROR: Could not write report {report_path}: {exc}", file=sys.stderr)
        return 2

    print_report(report, report_path)
    return 0 if summary["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
