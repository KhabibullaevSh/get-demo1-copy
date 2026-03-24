"""
excel_writer.py — Write BOQ to Excel with companion sheets.

Sheets produced:
  1. BOQ          — main bill of quantities
  2. Traceability — evidence + derivation rule for every item
  3. Manual Review — items flagged for review
  4. QA Summary   — package completeness + provenance stats
  5. Source Summary — which files contributed which data
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

log = logging.getLogger("boq.v3.excel_writer")

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False


# ── Colour palette (ARGB without #) ─────────────────────────────────────────
_SECTION_COLOURS: dict[str, str] = {
    "A - Structural Frame":   "FFD6E4F0",
    "B - Roof":               "FFD5F5E3",
    "C - Insulation":         "FFE8DAEF",
    "D - Openings":           "FFFCF3CF",
    "E - Linings & Ceilings": "FFFDEBD0",
    "F - Finishes":           "FFFDEDEC",
    "G - Floor System":       "FFEBF5FB",
    "H - Substructure":       "FFF2F3F4",
    "I - Services":           "FFEBF5FB",
    "J - Stairs":             "FFFEF9E7",
    "K - External Works":     "FFEAFAF1",
}
_HEADER_FILL = "FF2C3E50"
_MR_FILL     = "FFFFF3CD"   # light amber for manual review rows
_PLACEHOLDER_FILL = "FFFFE0E0"

_THIN = Side(style="thin", color="FFCCCCCC")
_BORDER = Border(left=_THIN, right=_THIN, bottom=_THIN)


def _apply_fill(cell, hex_argb: str) -> None:
    cell.fill = PatternFill(fill_type="solid", fgColor=hex_argb)


def _header_row(ws, headers: list[str], row: int = 1) -> None:
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = Font(bold=True, color="FFFFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor=_HEADER_FILL)
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def _set_col_widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def write_boq_excel(
    boq_items:    list[dict],
    qa_report:    dict,
    output_path:  str,
    project_name: str = "",
    source_files: list[str] | None = None,
) -> str:
    """
    Write all output sheets to an Excel workbook.

    Returns the output file path.
    """
    if not _HAS_OPENPYXL:
        log.error("openpyxl not installed — Excel output skipped")
        return ""

    wb = openpyxl.Workbook()

    # ── Sheet 1: BOQ ──────────────────────────────────────────────────────────
    ws_boq = wb.active
    ws_boq.title = "BOQ"
    _write_boq_sheet(ws_boq, boq_items, project_name)

    # ── Sheet 2: Traceability ─────────────────────────────────────────────────
    ws_trace = wb.create_sheet("Traceability")
    _write_traceability_sheet(ws_trace, boq_items)

    # ── Sheet 3: Manual Review ────────────────────────────────────────────────
    ws_mr = wb.create_sheet("Manual Review")
    _write_manual_review_sheet(ws_mr, boq_items)

    # ── Sheet 4: QA Summary ───────────────────────────────────────────────────
    ws_qa = wb.create_sheet("QA Summary")
    _write_qa_sheet(ws_qa, qa_report)

    # ── Sheet 5: Source Summary ───────────────────────────────────────────────
    ws_src = wb.create_sheet("Source Summary")
    _write_source_sheet(ws_src, boq_items, source_files or [])

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wb.save(output_path)
    log.info("Excel written: %s", output_path)
    return output_path


def _write_boq_sheet(ws, boq_items: list[dict], project_name: str) -> None:
    headers = [
        "Item No", "Section", "Item Name", "Stock Code",
        "Unit", "Quantity", "Rate (PGK)", "Amount (PGK)",
        "Status", "Confidence", "Notes"
    ]
    _header_row(ws, headers)
    ws.row_dimensions[1].height = 30

    current_section = ""
    row_idx = 2
    for item in boq_items:
        section = item.get("boq_section", "")
        fill_hex = _SECTION_COLOURS.get(section, "FFFFFFFF")

        # Section separator row
        if section != current_section:
            ws.cell(row=row_idx, column=1, value=section)
            ws.cell(row=row_idx, column=1).font = Font(bold=True)
            for col in range(1, len(headers) + 1):
                c = ws.cell(row=row_idx, column=col)
                c.fill = PatternFill(fill_type="solid", fgColor=fill_hex[2:] if fill_hex.startswith("FF") else fill_hex)
            row_idx += 1
            current_section = section

        mr    = item.get("manual_review", False)
        ph    = item.get("quantity_status") == "placeholder"
        qty   = item.get("quantity")
        row_fill = _MR_FILL if mr else (_PLACEHOLDER_FILL if ph else "FFFFFFFF")

        values = [
            item.get("item_no", ""),
            section,
            item.get("item_name", ""),
            item.get("item_code", ""),
            item.get("unit", ""),
            qty if qty is not None else "",
            None,
            None,
            item.get("quantity_status", ""),
            item.get("confidence", ""),
            item.get("notes", ""),
        ]
        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor=row_fill[2:] if row_fill.startswith("FF") else row_fill)
            cell.border = _BORDER
            if col_idx == 3:   # item name — wrap text
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 11:  # notes
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        row_idx += 1

    _set_col_widths(ws, [8, 24, 45, 14, 6, 10, 10, 12, 12, 10, 40])
    ws.freeze_panes = "A2"


def _write_traceability_sheet(ws, boq_items: list[dict]) -> None:
    headers = [
        "Item No", "Section", "Item Name", "Unit", "Qty",
        "Status", "Quantity Basis", "Source Evidence", "Derivation Rule", "Confidence"
    ]
    _header_row(ws, headers)
    for row_idx, item in enumerate(boq_items, 2):
        vals = [
            item.get("item_no", ""),
            item.get("boq_section", ""),
            item.get("item_name", ""),
            item.get("unit", ""),
            item.get("quantity"),
            item.get("quantity_status", ""),
            item.get("quantity_basis", ""),
            item.get("source_evidence", ""),
            item.get("derivation_rule", ""),
            item.get("confidence", ""),
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = _BORDER
    _set_col_widths(ws, [8, 24, 40, 6, 10, 12, 30, 50, 50, 10])
    ws.freeze_panes = "A2"


def _write_manual_review_sheet(ws, boq_items: list[dict]) -> None:
    headers = [
        "Item No", "Section", "Item Name", "Unit", "Qty",
        "Confidence", "Status", "Notes / Action Required"
    ]
    _header_row(ws, headers)
    mr_items = [i for i in boq_items if i.get("manual_review")]
    for row_idx, item in enumerate(mr_items, 2):
        vals = [
            item.get("item_no", ""),
            item.get("boq_section", ""),
            item.get("item_name", ""),
            item.get("unit", ""),
            item.get("quantity"),
            item.get("confidence", ""),
            item.get("quantity_status", ""),
            item.get("notes", ""),
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.fill = PatternFill(fill_type="solid", fgColor="FFFFF3CD")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = _BORDER
    _set_col_widths(ws, [8, 24, 40, 6, 10, 10, 12, 60])
    ws.freeze_panes = "A2"


def _write_qa_sheet(ws, qa_report: dict) -> None:
    ws.cell(1, 1, "QA SUMMARY REPORT").font = Font(bold=True, size=14)

    row = 3
    prov = qa_report.get("provenance_summary", {})
    ws.cell(row, 1, "QUANTITY PROVENANCE").font = Font(bold=True)
    row += 1
    for label, key in [
        ("Total Items", "total"), ("Measured", "measured"),
        ("Calculated", "calculated"), ("Inferred", "inferred"),
        ("Placeholder", "placeholder"), ("Manual Review", "manual_review_count"),
    ]:
        ws.cell(row, 1, label)
        ws.cell(row, 2, prov.get(key, 0))
        row += 1

    row += 1
    conf = qa_report.get("confidence_summary", {})
    ws.cell(row, 1, "CONFIDENCE BREAKDOWN").font = Font(bold=True)
    row += 1
    for label in ["HIGH", "MEDIUM", "LOW"]:
        ws.cell(row, 1, label)
        ws.cell(row, 2, conf.get(label, 0))
        ws.cell(row, 3, f"{conf.get(f'pct_{label.lower()}', 0):.1f}%")
        row += 1

    row += 1
    ws.cell(row, 1, "PACKAGE COMPLETENESS").font = Font(bold=True)
    row += 1
    ws.cell(row, 1, "Package").font = Font(bold=True)
    ws.cell(row, 2, "Status").font = Font(bold=True)
    ws.cell(row, 3, "Items").font = Font(bold=True)
    ws.cell(row, 4, "Measured").font = Font(bold=True)
    ws.cell(row, 5, "Calculated").font = Font(bold=True)
    ws.cell(row, 6, "Inferred").font = Font(bold=True)
    ws.cell(row, 7, "Placeholder").font = Font(bold=True)
    row += 1
    for pkg, data in qa_report.get("package_completeness", {}).items():
        ws.cell(row, 1, pkg)
        ws.cell(row, 2, data.get("status", ""))
        ws.cell(row, 3, data.get("item_count", 0))
        ws.cell(row, 4, data.get("measured", 0))
        ws.cell(row, 5, data.get("calculated", 0))
        ws.cell(row, 6, data.get("inferred", 0))
        ws.cell(row, 7, data.get("placeholder", 0))
        if data.get("status") == "MISSING":
            for col in range(1, 8):
                ws.cell(row, col).fill = PatternFill(fill_type="solid", fgColor="FFFFE0E0")
        row += 1

    row += 1
    ws.cell(row, 1, "WARNINGS / ISSUES").font = Font(bold=True)
    row += 1
    for w in qa_report.get("warnings", []):
        ws.cell(row, 1, w)
        row += 1

    _set_col_widths(ws, [40, 12, 8, 10, 10, 10, 12])


def _write_source_sheet(ws, boq_items: list[dict], source_files: list[str]) -> None:
    ws.cell(1, 1, "SOURCE SUMMARY").font = Font(bold=True, size=14)
    row = 3

    # Source file list
    ws.cell(row, 1, "Input Files").font = Font(bold=True)
    row += 1
    for f in source_files:
        ws.cell(row, 1, os.path.basename(f))
        row += 1

    row += 1
    ws.cell(row, 1, "Quantity Status Breakdown by Source").font = Font(bold=True)
    row += 1
    src_counts: dict[str, dict] = {}
    for item in boq_items:
        ev = item.get("source_evidence", "unknown")
        src_label = ev.split(":")[0].strip() if ":" in ev else ev.split(" ")[0]
        src_counts.setdefault(src_label, {"count": 0})
        src_counts[src_label]["count"] += 1

    for src, data in sorted(src_counts.items()):
        ws.cell(row, 1, src)
        ws.cell(row, 2, data["count"])
        row += 1

    _set_col_widths(ws, [50, 12])
