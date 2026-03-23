"""
quantity_builder.py — Build the neutral quantity model from the project model.

CRITICAL: All quantities come from the project_model (which was built from
DXF / IFC / PDF / BOM).  No quantities are looked up from any BOQ file.

Every record explicitly names which extractor provided it and which derivation
rule was applied (if any).
"""
from __future__ import annotations

import logging
from typing import Any

from src.quantity.derivation_rules import (
    ceiling_batten_lm,
    downpipe_count,
    ext_wall_area_m2,
    fascia_lm,
    fc_ceiling_sheets,
    fc_wall_sheets,
    gutter_lm,
    ridgecap_lm,
    roof_batten_lm,
    roof_fixings_boxes,
    sisalation_rolls,
)

log = logging.getLogger("boq.v2.quantity_builder")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _qrow(
    item_group: str,
    element_type: str,
    subtype: str,
    quantity: Any,
    unit: str,
    quantity_basis: str,       # "measured" | "derived" | "provisional" | "manual_review"
    quantity_rule_used: str,
    source_evidence: str,
    confidence: str,           # "HIGH" | "MEDIUM" | "LOW"
    assumption: str = "",
    manual_review: bool = False,
    v2_extractor_source: str = "",
) -> dict:
    return {
        "item_group":          item_group,
        "element_type":        element_type,
        "subtype":             subtype,
        "quantity":            quantity,
        "unit":                unit,
        "quantity_basis":      quantity_basis,
        "quantity_rule_used":  quantity_rule_used,
        "source_evidence":     source_evidence,
        "confidence":          confidence,
        "assumption":          assumption,
        "manual_review":       manual_review,
        "v2_extractor_source": v2_extractor_source or quantity_basis,
    }


def _g(project_model: dict, *keys) -> Any:
    """Navigate geometry field, returning value scalar."""
    node = project_model.get("geometry", {})
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return None
    if isinstance(node, dict):
        return node.get("value")
    return node


def _s(project_model: dict, *keys) -> Any:
    """Navigate structural field, returning value scalar."""
    node = project_model.get("structural", {})
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return None
    if isinstance(node, dict):
        return node.get("value")
    return node


def _src(project_model: dict, section: str, key: str) -> tuple[str, str]:
    """Return (source, confidence) for a project_model field."""
    node = project_model.get(section, {}).get(key, {})
    if isinstance(node, dict):
        return node.get("source", "unknown"), node.get("confidence", "LOW")
    return "unknown", "LOW"


def _o(project_model: dict, key: str) -> Any:
    """Navigate openings field."""
    node = project_model.get("openings", {}).get(key, {})
    if isinstance(node, dict):
        return node.get("value")
    return node


# ─── package builders ─────────────────────────────────────────────────────────

def _build_structure(pm: dict) -> list[dict]:
    rows: list[dict] = []
    struct_priority = pm.get("structural", {}).get("source_priority_used", "unknown")
    src_label       = struct_priority  # "framecad_bom" | "ifc_model" | "dxf_derived"

    def _row(element_type, subtype, lm_value, conf, rule="direct from source"):
        if lm_value and lm_value > 0:
            rows.append(_qrow(
                "Structure", element_type, subtype,
                round(lm_value, 2), "lm",
                "measured" if src_label in ("framecad_bom", "ifc_model") else "provisional",
                rule,
                f"{src_label}: {element_type} {subtype}",
                conf,
                v2_extractor_source=src_label,
            ))

    ifc_conf = "HIGH" if src_label != "dxf_derived" else "LOW"
    _row("Wall Frame", "Stud",       _s(pm, "wall_stud_lm"),     ifc_conf)
    _row("Wall Frame", "Top Plate",  _s(pm, "wall_plate_lm"),    ifc_conf)
    _row("Wall Frame", "Noggin",     _s(pm, "wall_noggin_lm"),   ifc_conf)
    _row("Roof Frame", "Rafter",     _s(pm, "roof_rafter_lm"),   ifc_conf)
    _row("Roof Frame", "Plate",      _s(pm, "roof_plate_lm"),    ifc_conf)
    _row("Roof Frame", "Noggin",     _s(pm, "roof_noggin_lm"),   ifc_conf)
    _row("Floor",      "Joist",      _s(pm, "floor_joist_lm"),   "MEDIUM")
    _row("Verandah",   "Frame",      _s(pm, "verandah_frame_lm"), ifc_conf)
    _row("Structure",  "Girt",       _s(pm, "girt_lm"),          "MEDIUM")

    # Bottom plate — separate row, marked manual_review due to anomalous IFC lengths
    btm_plate = _s(pm, "wall_btm_plate_lm")
    if btm_plate and btm_plate > 0:
        rows.append(_qrow(
            "Structure", "Wall Frame", "Bottom Plate",
            round(btm_plate, 2), "lm",
            "measured", "direct from source",
            f"{src_label}: Wall Frame Bottom Plate",
            "LOW",  # LOW — all B members are 15.0 m (anomalous) — needs BOM confirm
            assumption="IFC B-type members all 15.0 m — likely cumulative length artifact, not cut lengths. Confirm with FrameCAD BOM.",
            manual_review=True,
            v2_extractor_source=src_label,
        ))

    # Structural steel SHS posts — always separate from LGS wall frame
    shs_lm = _s(pm, "steel_shs_lm")
    if shs_lm and shs_lm > 0:
        rows.append(_qrow(
            "Structure", "Structural Steel Post", "75×75×4 SHS",
            round(shs_lm, 2), "lm",
            "measured", "direct from source",
            f"{src_label}: SHS steel (desc=XX/00, name=75x75x4 SHS)",
            "HIGH",
            assumption="Structural steel hollow section (not LGS). Confirm post type and size from structural drawings.",
            v2_extractor_source=src_label,
        ))

    # Unclassified LGS — separate provisional row
    lgs_unc = _s(pm, "lgs_unclassified_lm")
    if lgs_unc and lgs_unc > 0:
        rows.append(_qrow(
            "Structure", "LGS Members", "unclassified (desc=numeric artifact)",
            round(lgs_unc, 2), "lm",
            "provisional", "FrameCAD export artifact — type unknown",
            f"{src_label}: 128 × ~3.7 m IfcBeam, desc='2440.000050'",
            "LOW",
            assumption="128 LGS beams (~3.7 m each, 89S41-075-500) with numeric description. Likely floor-joist cassette or ceiling purlin. Confirm with FrameCAD BOM.",
            manual_review=True,
            v2_extractor_source=src_label,
        ))

    # Post count from DXF STRUCTURE CIRCLE
    post_count = _g(pm, "post_count")
    if post_count and post_count > 0:
        rows.append(_qrow(
            "Structure", "Post/Column", "circular",
            post_count, "nr",
            "measured", "DXF STRUCTURE CIRCLE count",
            "dxf_geometry: STRUCTURE layer CIRCLE entities",
            "HIGH", v2_extractor_source="dxf_geometry",
        ))

    return rows


def _build_roof(pm: dict) -> list[dict]:
    rows: list[dict] = []
    roof_area    = _g(pm, "roof_area_m2")   or 0.0
    roof_perim   = _g(pm, "roof_perimeter_m") or 0.0
    floor_area   = _g(pm, "floor_area_m2")  or 0.0
    src, conf    = _src(pm, "geometry", "roof_area_m2")

    if roof_area > 0:
        rows.append(_qrow(
            "Roof", "Roof Sheeting", "corrugated iron / CGI",
            round(roof_area, 2), "m2",
            "measured", "DXF ROOF LWPOLYLINE area",
            f"{src}: roof_area_m2={roof_area:.2f}",
            conf, v2_extractor_source=src,
        ))
        rows.append(_qrow(
            "Roof", "Sisalation", "sarking / insulation",
            sisalation_rolls(roof_area), "rolls",
            "derived", f"ceil(roof_area / {73.0})",
            f"derived from roof_area_m2={roof_area:.2f}",
            conf, v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Roof", "Roof Battens", "timber/steel",
            roof_batten_lm(roof_area), "lm",
            "derived", f"roof_area / ({900}/1000)",
            f"derived from roof_area_m2={roof_area:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Roof", "Roof Fixings", "screws/bolts",
            roof_fixings_boxes(roof_area), "boxes",
            "derived", "ceil(roof_area / 10)",
            f"derived from roof_area_m2={roof_area:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))

    if roof_perim > 0:
        rows.append(_qrow(
            "Roof", "Gutters", "eaves gutter",
            gutter_lm(roof_perim), "lm",
            "derived", "roof_perimeter / 2",
            f"derived from roof_perimeter_m={roof_perim:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Roof", "Fascia", "fascia board",
            fascia_lm(roof_perim), "lm",
            "derived", "roof_perimeter",
            f"derived from roof_perimeter_m={roof_perim:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Roof", "Ridge Cap", "ridge capping",
            ridgecap_lm(roof_perim), "lm",
            "derived", "roof_perimeter / 4",
            f"derived from roof_perimeter_m={roof_perim:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))
        ext_perim = _g(pm, "ext_wall_perimeter_m") or roof_perim
        rows.append(_qrow(
            "Roof", "Downpipes", "PVC downpipe",
            downpipe_count(ext_perim), "nr",
            "derived", "max(2, ceil(perimeter/10)//2)",
            f"derived from ext_wall_perimeter_m={ext_perim:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))

    return rows


def _build_openings(pm: dict) -> list[dict]:
    rows: list[dict] = []
    door_count   = _o(pm, "door_count")   or 0
    window_count = _o(pm, "window_count") or 0
    door_src,   door_conf   = _src(pm, "openings", "door_count")
    window_src, window_conf = _src(pm, "openings", "window_count")

    if door_count > 0:
        rows.append(_qrow(
            "Openings", "Doors", "all types",
            door_count, "nr",
            "measured", "count from source",
            f"{door_src}: door_count={door_count}",
            door_conf, v2_extractor_source=door_src,
        ))

    if window_count > 0:
        rows.append(_qrow(
            "Openings", "Windows", "all types",
            window_count, "nr",
            "measured", "count from source",
            f"{window_src}: window_count={window_count}",
            window_conf, v2_extractor_source=window_src,
        ))

    # Per-type breakdown from PDF if available
    for door in pm.get("openings", {}).get("doors", []):
        if isinstance(door, dict) and door.get("mark"):
            rows.append(_qrow(
                "Openings", "Door (type)", door.get("mark", ""),
                door.get("quantity", 1), "nr",
                "measured", "PDF door schedule",
                "pdf_schedule: door schedule",
                "HIGH", v2_extractor_source="pdf_schedule",
            ))

    for win in pm.get("openings", {}).get("windows", []):
        if isinstance(win, dict) and win.get("mark"):
            rows.append(_qrow(
                "Openings", "Window (type)", win.get("mark", ""),
                win.get("quantity", 1), "nr",
                "measured", "PDF window schedule",
                "pdf_schedule: window schedule",
                "HIGH", v2_extractor_source="pdf_schedule",
            ))

    return rows


def _build_linings(pm: dict) -> list[dict]:
    rows: list[dict] = []
    ceiling_area   = _g(pm, "ceiling_area_m2")      or 0.0
    ext_wall_perim = _g(pm, "ext_wall_perimeter_m") or 0.0
    src_ceil, conf_ceil = _src(pm, "geometry", "ceiling_area_m2")

    if ceiling_area > 0:
        rows.append(_qrow(
            "Linings", "Ceiling Lining", "FC sheet",
            fc_ceiling_sheets(ceiling_area), "sheets",
            "derived", f"ceil(ceiling_area * {1.05} / {2.88})",
            f"derived from ceiling_area_m2={ceiling_area:.2f} [{src_ceil}]",
            conf_ceil, v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Linings", "Ceiling Battens", "timber/steel",
            ceiling_batten_lm(ceiling_area), "lm",
            "derived", f"ceiling_area / ({400}/1000)",
            f"derived from ceiling_area_m2={ceiling_area:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))

    if ext_wall_perim > 0:
        wall_area = ext_wall_area_m2(ext_wall_perim)
        rows.append(_qrow(
            "Linings", "External Wall Lining", "FC sheet / cladding",
            fc_wall_sheets(wall_area), "sheets",
            "derived", f"ceil(wall_area * {1.05} / {3.24})",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f} → wall_area={wall_area:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))

    return rows


def _build_finishes(pm: dict) -> list[dict]:
    rows: list[dict] = []
    floor_area = _g(pm, "floor_area_m2") or 0.0
    src, conf  = _src(pm, "geometry", "floor_area_m2")

    if floor_area > 0:
        rows.append(_qrow(
            "Finishes", "Floor Finish", "tiles / vinyl / screed",
            round(floor_area, 2), "m2",
            "measured", "DXF WALLS polygon area",
            f"{src}: floor_area_m2={floor_area:.2f}",
            conf, v2_extractor_source=src,
        ))

    # Room-by-room finishes from PDF
    for room in pm.get("rooms", []):
        if isinstance(room, dict) and room.get("name"):
            rows.append(_qrow(
                "Finishes", "Room", room.get("name", ""),
                room.get("area_m2") or 0, "m2",
                "measured" if room.get("area_m2") else "provisional",
                "PDF floor plan",
                "pdf_ai_extraction: room schedule",
                "MEDIUM" if room.get("area_m2") else "LOW",
                v2_extractor_source="pdf_schedule",
            ))

    return rows


def _build_services(pm: dict) -> list[dict]:
    rows: list[dict] = []
    floor_area = _g(pm, "floor_area_m2") or 0.0

    if floor_area > 0:
        # Provisional service items — placeholder counts
        rows.append(_qrow(
            "Services", "Electrical", "light points",
            0, "nr",
            "provisional", "manual review required",
            "no electrical schedule in sources",
            "LOW", assumption="Electrical schedule not found — manual review required",
            manual_review=True, v2_extractor_source="provisional",
        ))
        rows.append(_qrow(
            "Services", "Plumbing", "wet areas",
            0, "nr",
            "provisional", "manual review required",
            "no plumbing schedule in sources",
            "LOW", assumption="Plumbing schedule not found — manual review required",
            manual_review=True, v2_extractor_source="provisional",
        ))
    return rows


def _build_stairs(pm: dict) -> list[dict]:
    rows: list[dict] = []
    stair_ev     = _g(pm, "stair_evidence") or False
    stair_lines  = _g(pm, "stair_line_count") or 0
    pdf_stairs   = pm.get("stairs", [])

    if pdf_stairs:
        for stair in pdf_stairs:
            if isinstance(stair, dict):
                rows.append(_qrow(
                    "Stairs", "Staircase", stair.get("type", "unknown"),
                    stair.get("quantity", 1), "nr",
                    "measured", "PDF schedule",
                    "pdf_ai_extraction: stairs",
                    "HIGH", v2_extractor_source="pdf_schedule",
                ))
    elif stair_ev:
        rows.append(_qrow(
            "Stairs", "Staircase", "evidence from DXF",
            1, "nr",
            "provisional", f"DXF STAIRS LINE count={stair_lines}",
            f"dxf_geometry: STAIRS layer {stair_lines} lines",
            "MEDIUM", assumption="Stair geometry detected in DXF — type/details require manual review",
            manual_review=True, v2_extractor_source="dxf_geometry",
        ))
    return rows


def _build_external(pm: dict) -> list[dict]:
    rows: list[dict] = []
    verandah_area  = _g(pm, "verandah_area_m2")   or 0.0
    verandah_perim = _g(pm, "verandah_perimeter_m") or 0.0
    src, conf      = _src(pm, "geometry", "verandah_area_m2")

    if verandah_area > 0:
        rows.append(_qrow(
            "External", "Verandah", "deck / slab",
            round(verandah_area, 2), "m2",
            "measured", "DXF VERANDAH LWPOLYLINE area",
            f"{src}: verandah_area_m2={verandah_area:.2f}",
            conf, v2_extractor_source=src,
        ))

    if verandah_perim > 0:
        rows.append(_qrow(
            "External", "Verandah Perimeter", "balustrade / edge",
            round(verandah_perim, 2), "lm",
            "measured", "DXF VERANDAH LWPOLYLINE perimeter",
            f"dxf_geometry: verandah_perimeter_m={verandah_perim:.2f}",
            conf, v2_extractor_source="dxf_geometry",
        ))

    return rows


# ─── completeness calculator ──────────────────────────────────────────────────

def _completeness(all_rows: list[dict]) -> dict:
    packages = [
        "Structure", "Roof", "Openings", "Linings",
        "Finishes", "Services", "Stairs", "External",
    ]
    result: dict = {}
    for pkg in packages:
        pkg_rows = [r for r in all_rows if r["item_group"] == pkg]
        result[pkg] = {
            "detected":            len(pkg_rows) > 0,
            "items":               len(pkg_rows),
            "measured_items":      sum(1 for r in pkg_rows if r["quantity_basis"] == "measured"),
            "derived_items":       sum(1 for r in pkg_rows if r["quantity_basis"] == "derived"),
            "provisional_items":   sum(1 for r in pkg_rows if r["quantity_basis"] == "provisional"),
            "manual_review_items": sum(1 for r in pkg_rows if r.get("manual_review")),
            "notes":               "",
        }
    return result


# ─── main entry ───────────────────────────────────────────────────────────────

def build_quantity_model(project_model: dict) -> dict:
    """
    Build neutral quantity model from project_model.

    CRITICAL: All quantities come from project_model (built from DXF/IFC/PDF/BOM).
    No quantities are looked up from any BOQ file.

    Returns:
        {
          "quantities":            list of quantity rows,
          "completeness":          per-package summary,
          "source_priorities_used": per-category summary,
          "totals_by_basis":       summary counts,
        }
    """
    log.info("Building quantity model from project model...")

    all_rows: list[dict] = []
    all_rows.extend(_build_structure(project_model))
    all_rows.extend(_build_roof(project_model))
    all_rows.extend(_build_openings(project_model))
    all_rows.extend(_build_linings(project_model))
    all_rows.extend(_build_finishes(project_model))
    all_rows.extend(_build_services(project_model))
    all_rows.extend(_build_stairs(project_model))
    all_rows.extend(_build_external(project_model))

    completeness = _completeness(all_rows)

    totals_by_basis = {
        "measured":      sum(1 for r in all_rows if r["quantity_basis"] == "measured"),
        "derived":       sum(1 for r in all_rows if r["quantity_basis"] == "derived"),
        "provisional":   sum(1 for r in all_rows if r["quantity_basis"] == "provisional"),
        "manual_review": sum(1 for r in all_rows if r.get("manual_review")),
        "total":         len(all_rows),
    }

    struct_priority = project_model.get("structural", {}).get("source_priority_used", "unknown")
    source_priorities = {
        "structural_members": struct_priority,
        "geometry":           "dxf_geometry",
        "openings":           project_model.get("openings", {}).get("door_count", {}).get("source", "unknown"),
        "finishes":           "pdf_schedule / dxf_geometry",
    }

    log.info(
        "Quantity model: %d rows | measured=%d derived=%d provisional=%d",
        len(all_rows),
        totals_by_basis["measured"],
        totals_by_basis["derived"],
        totals_by_basis["provisional"],
    )

    return {
        "quantities":             all_rows,
        "completeness":           completeness,
        "source_priorities_used": source_priorities,
        "totals_by_basis":        totals_by_basis,
    }
