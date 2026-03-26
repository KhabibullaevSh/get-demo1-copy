"""
excel_writer.py — Write BOQ to Excel with companion sheets.

Sheets produced:
  1. BOQ          — main bill of quantities (column layout matches final approved BOQ)
  2. Traceability — evidence + derivation rule for every item
  3. Manual Review — items flagged for review
  4. QA Summary   — package completeness + provenance stats
  5. Source Summary — which files contributed which data

STRUCTURE ALIGNMENT NOTE (2026-03-26):
  The BOQ sheet column order now matches the approved final BOQ format:
    STOCK CODE | MATERIALS DESCRIPTION | QTY | RATE (PGK) | AMOUNT (PGK)
    | CONFIDENCE | SOURCE | NOTES / DRAWING REF

  Section header rows use the final BOQ numeric package codes (50106–50129).
  Rows are sorted by package order (50106 → 50107 → ... → 50124).
  Item display names use "Category | Specification" convention.

  No quantities were changed by this alignment step.
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


# ── Colour palette — matched to final BOQ package code sections ───────────────
_SECTION_COLOURS_FINAL: dict[str, str] = {
    "50106": "FFD6E4F0",   # WPC Timber — light blue
    "50107": "FFD5E8D4",   # Structural — light green
    "50111": "FFDAE8FC",   # Fixings — sky
    "50112": "FFD5F5E3",   # Roof — mint
    "50113": "FFFFE6CC",   # Ext Cladding — peach
    "50114": "FFFCF3CF",   # Openings — yellow
    "50115": "FFFDEBD0",   # Int Linings — apricot
    "50117": "FFE8DAEF",   # Services — lavender
    "50118": "FFEAF4FB",   # Insulation — pale sky
    "50119": "FFFDEDEC",   # Electrical — pale pink
    "50124": "FFFEF9E7",   # Stairs — cream
    "50129": "FFF2F3F4",   # FFE — light grey
    "50199": "FFEEEEEE",   # Unclassified
}

# Backward-compat colours for legacy A–K section names
_SECTION_COLOURS_LEGACY: dict[str, str] = {
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

_HEADER_FILL       = "FF2C3E50"   # dark slate
_SECTION_HDR_FILL  = "FF3D5A80"   # medium blue — matches final BOQ section header style
_MR_FILL           = "FFFFF3CD"   # light amber for manual review rows
_PLACEHOLDER_FILL  = "FFFFE0E0"   # light red for placeholder rows

_THIN   = Side(style="thin",   color="FFCCCCCC")
_MEDIUM = Side(style="medium", color="FF999999")
_BORDER = Border(left=_THIN, right=_THIN, bottom=_THIN)
_SECTION_BORDER = Border(
    left=Side(style="medium", color="FF3D5A80"),
    right=Side(style="medium", color="FF3D5A80"),
    bottom=Side(style="medium", color="FF3D5A80"),
)


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

    # ── Sheet 1: BOQ (final BOQ column format) ────────────────────────────────
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
    """
    Write main BOQ sheet.

    Column layout matches approved final BOQ:
      A: STOCK CODE
      B: MATERIALS DESCRIPTION
      C: QTY
      D: (blank — quantity basis note)
      E: RATE (PGK)
      F: AMOUNT (PGK)
      G: CONFIDENCE
      H: SOURCE
      I: NOTES / DRAWING REF

    Section header rows:
      Use final BOQ numeric package codes (50106–50129) as section headers,
      matching the style of the approved BOQ.
    """
    # ── Title row ─────────────────────────────────────────────────────────────
    title = f"BILL OF QUANTITIES — {project_name}" if project_name else "BILL OF QUANTITIES"
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=14, color="FF2C3E50")
    ws.merge_cells("A1:I1")
    ws.row_dimensions[1].height = 28

    # ── Column headers (row 2, matching final BOQ) ────────────────────────────
    headers = [
        "STOCK CODE",
        "MATERIALS DESCRIPTION",
        "QTY",
        "",                   # quantity basis / blank
        "RATE (PGK)",
        "AMOUNT (PGK)",
        "CONFIDENCE",
        "SOURCE",
        "NOTES / DRAWING REF",
    ]
    _header_row(ws, headers, row=2)
    ws.row_dimensions[2].height = 30

    current_pkg_code = ""
    row_idx = 3

    for item in boq_items:
        pkg_code     = item.get("package_code", "50199")
        section_lbl  = item.get("boq_section_final", item.get("boq_section", ""))
        fill_hex     = _SECTION_COLOURS_FINAL.get(pkg_code, "FFFFFFFF")
        # strip leading "FF" for openpyxl PatternFill
        fill_rgb     = fill_hex[2:] if fill_hex.startswith("FF") else fill_hex

        # ── Section header row (when package changes) ─────────────────────────
        if pkg_code != current_pkg_code:
            hdr_cell = ws.cell(row=row_idx, column=1, value=section_lbl)
            hdr_cell.font  = Font(bold=True, color="FFFFFFFF", size=10)
            hdr_cell.fill  = PatternFill(fill_type="solid", fgColor=_SECTION_HDR_FILL[2:])
            hdr_cell.alignment = Alignment(vertical="center")
            for col in range(1, len(headers) + 1):
                c = ws.cell(row=row_idx, column=col)
                c.fill   = PatternFill(fill_type="solid", fgColor=_SECTION_HDR_FILL[2:])
                c.border = _SECTION_BORDER
            ws.row_dimensions[row_idx].height = 18
            row_idx += 1
            current_pkg_code = pkg_code

        # ── Data row ──────────────────────────────────────────────────────────
        mr  = item.get("manual_review", False)
        ph  = item.get("quantity_status") == "placeholder"
        qty = item.get("quantity")

        if mr:
            row_fill = _MR_FILL[2:]
        elif ph:
            row_fill = _PLACEHOLDER_FILL[2:]
        else:
            row_fill = fill_rgb

        # Use normalized display name for column B; stock code for column A
        display_name = item.get("item_display_name") or item.get("item_name", "")
        stock_code   = item.get("item_code", "")
        qty_basis    = item.get("quantity_status", "")   # brief provenance in blank col
        source_tag   = item.get("source_evidence", "").split(":")[0].strip()[:30] if item.get("source_evidence") else ""

        values = [
            stock_code,       # A: STOCK CODE
            display_name,     # B: MATERIALS DESCRIPTION (normalized)
            qty if qty is not None else "",   # C: QTY
            qty_basis,        # D: blank / qty_basis note
            None,             # E: RATE (PGK) — not populated (requires rate library)
            None,             # F: AMOUNT (PGK) — not populated
            item.get("confidence", ""),       # G: CONFIDENCE
            source_tag,       # H: SOURCE
            item.get("notes", ""),            # I: NOTES / DRAWING REF
        ]

        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.fill   = PatternFill(fill_type="solid", fgColor=row_fill)
            cell.border = _BORDER
            if col_idx == 2:   # description — wrap text
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 9:   # notes
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_idx == 3:   # qty — right-align
                cell.alignment = Alignment(horizontal="right", vertical="center")

        row_idx += 1

    # ── Column widths matching final BOQ proportions ──────────────────────────
    _set_col_widths(ws, [16, 48, 8, 14, 10, 12, 12, 20, 45])
    ws.freeze_panes = "A3"

    # ── Auto-filter on header row ─────────────────────────────────────────────
    ws.auto_filter.ref = f"A2:I{row_idx - 1}"


def _write_traceability_sheet(ws, boq_items: list[dict]) -> None:
    headers = [
        "Item No", "Package Code", "Section (Final BOQ)", "Item Name (Original)",
        "Item Display Name", "Unit", "Qty",
        "Status", "Quantity Basis", "Source Evidence", "Derivation Rule", "Confidence"
    ]
    _header_row(ws, headers)
    for row_idx, item in enumerate(boq_items, 2):
        vals = [
            item.get("item_no", ""),
            item.get("package_code", ""),
            item.get("boq_section_final", item.get("boq_section", "")),
            item.get("item_name", ""),
            item.get("item_display_name", ""),
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
    _set_col_widths(ws, [8, 8, 38, 40, 40, 6, 10, 12, 30, 50, 50, 10])
    ws.freeze_panes = "A2"


def _write_manual_review_sheet(ws, boq_items: list[dict]) -> None:
    headers = [
        "Item No", "Package Code", "Item Display Name", "Unit", "Qty",
        "Confidence", "Status", "Notes / Action Required"
    ]
    _header_row(ws, headers)
    mr_items = [i for i in boq_items if i.get("manual_review")]
    for row_idx, item in enumerate(mr_items, 2):
        vals = [
            item.get("item_no", ""),
            item.get("package_code", ""),
            item.get("item_display_name") or item.get("item_name", ""),
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
    _set_col_widths(ws, [8, 10, 48, 6, 10, 10, 12, 60])
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
    ws.cell(row, 1, "PACKAGE COMPLETENESS (V3 sections)").font = Font(bold=True)
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
