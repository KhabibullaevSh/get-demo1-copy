"""
source_inventory.py — Track all input files and their parse/usage status.

Produces a source inventory list consumed by QA reporting and the summary writer.
"""

from __future__ import annotations
import logging
from pathlib import Path

log = logging.getLogger("boq.source_inventory")

# Maps file category (from detect_files) to source_type
_CATEGORY_TO_TYPE: dict[str, str] = {
    "dwg":   "dwg",
    "dxf":   "dxf",
    "pdf":   "pdf",
    "ifc":   "ifc",
    "bom":   "bom",
}

# Heuristic: derive discipline from filename keywords
_DISCIPLINE_KEYWORDS: dict[str, list[str]] = {
    "architectural": [
        "arch", "floor plan", "floor_plan", "floorplan", "elevation",
        "section", "detail", "finish", "room",
    ],
    "structural": [
        "struct", "steel", "concrete", "frame", "truss", "foundation",
        "footing", "beam", "column", "slab", "ifc",
    ],
    "mechanical": [
        "mech", "hvac", "plumb", "electrical", "elec", "services",
        "drainage", "reticulation",
    ],
}


def _infer_discipline(filename: str) -> str:
    name_lower = filename.lower()
    for discipline, keywords in _DISCIPLINE_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return discipline
    return "unknown"


def _infer_source_type(category: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".dwg",):
        return "dwg"
    if ext in (".dxf",):
        return "dxf"
    if ext in (".pdf",):
        return "pdf"
    if ext in (".ifc",):
        return "ifc"
    if ext in (".xlsx", ".xls", ".csv"):
        return "bom"
    return _CATEGORY_TO_TYPE.get(category, "other")


def build_inventory(files: dict, parse_results: dict) -> list[dict]:
    """
    Build a source inventory from detected files and parse results.

    Args:
        files:         dict from detect_files() with keys dwg/dxf/pdf/ifc/bom/warnings
        parse_results: dict with keys "dwg", "pdf", "bom" containing extraction results

    Returns a list of source records:
    [
      {
        "filename": str,
        "source_type": "pdf"|"dwg"|"dxf"|"ifc"|"bom"|"other",
        "discipline": "architectural"|"structural"|"mechanical"|"unknown",
        "parsed_ok": bool,
        "used_in_reasoning": bool,
        "parse_warnings": [str]
      }
    ]
    """
    inventory: list[dict] = []
    seen_paths: set[str] = set()

    # ── Collect warnings from each parse result ───────────────────────────────
    dwg_data = parse_results.get("dwg") or {}
    pdf_data = parse_results.get("pdf") or {}
    bom_data = parse_results.get("bom") or {}

    dwg_warnings = list(dwg_data.get("warnings", []))
    pdf_warnings = list(pdf_data.get("warnings", []))
    bom_warnings = list(bom_data.get("warnings", []))

    # ── DWG has data if summary is non-empty ─────────────────────────────────
    dwg_has_data = bool(dwg_data.get("summary", {}).get("total_floor_area_m2", 0))
    pdf_has_data = bool(pdf_data.get("rooms") or pdf_data.get("doors") or pdf_data.get("windows"))
    bom_has_data = bool(bom_data.get("raw_items") or bom_data.get("normalized", {}).get("wall_frame_lm"))

    for category, entries in files.items():
        if category == "warnings":
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            path = str(entry.get("path", ""))
            if path in seen_paths:
                continue
            seen_paths.add(path)

            fname       = Path(path).name
            source_type = _infer_source_type(category, fname)
            discipline  = _infer_discipline(fname)

            # Determine parse status
            if category in ("dwg", "dxf"):
                parsed_ok          = dwg_has_data
                used_in_reasoning  = dwg_has_data
                parse_warnings     = dwg_warnings
            elif category == "pdf":
                parsed_ok          = pdf_has_data
                used_in_reasoning  = pdf_has_data
                parse_warnings     = pdf_warnings
            elif category in ("bom", "ifc"):
                parsed_ok          = bom_has_data
                used_in_reasoning  = bom_has_data
                parse_warnings     = bom_warnings
            else:
                parsed_ok          = False
                used_in_reasoning  = False
                parse_warnings     = []

            record: dict = {
                "filename":          fname,
                "source_type":       source_type,
                "discipline":        discipline,
                "parsed_ok":         parsed_ok,
                "used_in_reasoning": used_in_reasoning,
                "parse_warnings":    list(parse_warnings),
            }
            inventory.append(record)

    log.info(
        "Source inventory: %d files  (%d parsed ok)",
        len(inventory),
        sum(1 for r in inventory if r["parsed_ok"]),
    )
    return inventory
