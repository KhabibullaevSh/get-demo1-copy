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
    is_bom          = src_label == "framecad_bom"
    is_ifc          = src_label == "ifc_model"

    def _measured_row(element_type, subtype, qty, unit, evidence, conf,
                      rule="direct from source", assumption="", manual_review=False):
        if qty and qty > 0:
            basis = "measured" if (is_bom or is_ifc) else "provisional"
            rows.append(_qrow(
                "Structure", element_type, subtype,
                round(qty, 2) if isinstance(qty, float) else qty, unit,
                basis, rule, evidence, conf,
                assumption=assumption, manual_review=manual_review,
                v2_extractor_source=src_label,
            ))

    # ── BOM-tab structural totals (HIGH confidence when BOM present) ───────
    # When FrameCAD manufacturing summary is the source, each tab becomes
    # one authoritative BOQ row.  No stud/plate/noggin sub-splitting is
    # attempted from BOM data (that requires a per-member schedule).

    roof_panel = _s(pm, "roof_panel_lm")
    roof_truss = _s(pm, "roof_truss_lm")
    wall_frame = _s(pm, "wall_frame_lm")
    lintel     = _s(pm, "lintel_lm")
    strap      = _s(pm, "wall_strap_lm")
    verandah   = _s(pm, "verandah_frame_lm")
    shs_lm     = _s(pm, "steel_shs_lm")
    bom_total  = _s(pm, "bom_total_lgs_lm")

    conf_main = "HIGH" if (is_bom or is_ifc) else "LOW"

    # Roof Panels (purlins) — BOM "Tab Roof Panels"
    # IFC fallback: previously "lgs_unclassified" 2440.000050 group (481.74 lm exact match)
    _measured_row(
        "Roof Panel Frame", "purlin / roof panel 89S41",
        roof_panel, "lm",
        f"{src_label}: Tab Roof Panels" if is_bom else f"{src_label}: numeric-desc IfcBeam (481.74 lm)",
        conf_main,
        rule="direct from source — BOM Tab Roof Panels" if is_bom else "IFC 2440.000050 group matches BOM",
    )

    # Roof Trusses — BOM "Tab Roof Trusses"
    # IFC fallback: T-type (top chord) + B-type (bottom chord) + R-type
    _measured_row(
        "Roof Truss Frame", "truss chord + web 89S41",
        roof_truss, "lm",
        f"{src_label}: Tab Roof Trusses" if is_bom else f"{src_label}: T+B+R type members",
        conf_main,
        rule="direct from source — BOM Tab Roof Trusses" if is_bom
             else "IFC T-type (top chord) + B-type (bottom chord) + R-type",
    )

    # Wall Panels — BOM "Tab Wall Panels" (all wall members: studs + plates + noggins)
    # IFC fallback: sum of all wall member categories
    _measured_row(
        "Wall Frame", "all wall members 89S41",
        wall_frame, "lm",
        f"{src_label}: Tab Wall Panels" if is_bom else f"{src_label}: W+T+B+bare-desc wall members",
        conf_main,
        rule="direct from source — BOM Tab Wall Panels (studs + plates + noggins combined)" if is_bom
             else "IFC all wall categories summed",
    )

    # Lintel
    if lintel and lintel > 0:
        _measured_row(
            "Wall Frame", "lintel 150×32×0.95",
            lintel, "lm",
            f"{src_label}: 150x32x0.95 Lintel",
            conf_main,
            rule="direct from source — BOM lintel entry",
        )

    # Diagonal strap
    if strap and strap > 0:
        _measured_row(
            "Wall Frame", "diagonal strap 32×0.95",
            strap, "lm",
            f"{src_label}: FRAMECAD 32x0.95 Strap",
            conf_main,
            rule="direct from source — BOM strap entry",
        )

    # Verandah frame
    _measured_row(
        "Verandah Frame", "89S41 V1 panel",
        verandah, "lm",
        f"{src_label}: verandah_frame (V1-T members)",
        conf_main,
        rule="direct from source",
    )

    # Structural steel SHS (not LGS — always from IFC)
    if shs_lm and shs_lm > 0:
        rows.append(_qrow(
            "Structure", "Structural Steel Post", "75×75×4 SHS",
            round(shs_lm, 2), "lm",
            "measured", "direct from source",
            "ifc_model: SHS steel (desc=XX/00, name=75×75×4 SHS)",
            "HIGH",
            assumption="Steel hollow section posts — NOT LGS framing. "
                       "Verify post type, height, and fixing from structural drawings.",
            v2_extractor_source="ifc_model",
        ))

    # BOM verification note (zero-qty row for traceability)
    if is_bom and bom_total and bom_total > 0:
        rows.append(_qrow(
            "Structure", "BOM Total LGS Check", "89S41-075-500 all tabs",
            round(bom_total, 2), "lm",
            "measured", "BOM Job Summary total",
            "framecad_bom: Job Summary 89S41-075-500",
            "HIGH",
            assumption="Verification row only. Sum of Roof Panels + Roof Trusses + Wall Panels "
                       "should equal this figure. Do not order from this row.",
            v2_extractor_source="framecad_bom",
        ))

    # Post count from DXF STRUCTURE CIRCLE
    post_count = _g(pm, "post_count")
    if post_count and post_count > 0:
        rows.append(_qrow(
            "Structure", "Post/Column", "circular — DXF count",
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

    # BOM batten data (measured, HIGH confidence when available)
    bom_batten_lm_val = _s(pm, "roof_batten_lm")
    bom_batten_nr_val = _s(pm, "roof_batten_nr")
    batten_entries    = pm.get("structural", {}).get("bom_batten_entries", [])

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

        # Roof battens — BOM preferred, derived as fallback
        if bom_batten_lm_val and bom_batten_lm_val > 0:
            # Build evidence string from batten schedule entries
            if batten_entries:
                detail = "; ".join(
                    f"{e['qty']}×{e['length_mm']}mm grade{e['grade_mm']}"
                    for e in batten_entries
                )
                evidence = f"framecad_bom: {detail}"
                rule = f"sum(qty × length): {detail}"
            else:
                evidence = "framecad_bom: FRAMECAD BATTEN schedule"
                rule = "BOM batten schedule (qty × length)"
            rows.append(_qrow(
                "Roof", "Roof Battens", "FRAMECAD BATTEN (structural steel)",
                bom_batten_lm_val, "lm",
                "measured", rule, evidence,
                "HIGH", v2_extractor_source="framecad_bom",
            ))
            if bom_batten_nr_val and bom_batten_nr_val > 0:
                rows.append(_qrow(
                    "Roof", "Roof Battens", "count",
                    bom_batten_nr_val, "nr",
                    "measured", "BOM batten count",
                    "framecad_bom: FRAMECAD BATTEN schedule",
                    "HIGH", v2_extractor_source="framecad_bom",
                ))
        else:
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
