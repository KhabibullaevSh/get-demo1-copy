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
    ARCHITRAVE_DOOR_LM_EACH,
    ARCHITRAVE_WINDOW_LM_EACH,
    INT_WALL_LM_RATIO,
    architrave_door_lm,
    architrave_window_lm,
    ceiling_batten_lm,
    cornice_lm,
    downpipe_count,
    ext_wall_area_m2,
    fascia_lm,
    fc_ceiling_sheets,
    fc_wall_sheets,
    gutter_lm,
    insulation_roof_m2,
    insulation_wall_m2,
    int_wall_lm_estimate,
    int_wall_one_face_m2,
    paint_external_m2,
    paint_internal_m2,
    ridgecap_lm,
    roof_batten_lm,
    roof_fixings_boxes,
    sisalation_rolls,
    skirting_lm,
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
            "derived", "roof_perimeter (all-sides eaves — hip roof assumed)",
            f"derived from roof_perimeter_m={roof_perim:.2f}",
            "MEDIUM",
            assumption="Hip roof assumed (gutters all sides). Reduce by ~40% if gable roof.",
            v2_extractor_source="derived",
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

    # Door accessories — derived from door count
    if door_count > 0:
        def _door_acc(subtype, rule):
            return _qrow(
                "Openings", "Door Accessory", subtype,
                door_count, "nr",
                "derived", rule,
                f"derived from door_count={door_count} [{door_src}]",
                door_conf, v2_extractor_source="derived",
            )
        rows.append(_door_acc("lockset",          f"1 per door × {door_count} doors"))
        rows.append(_door_acc("hinge set (pair)", f"1 pair per door × {door_count} doors"))
        rows.append(_door_acc("door stop",        f"1 per door × {door_count} doors"))

    # Window flyscreen — derived from window count (standard for PNG climate)
    if window_count > 0:
        rows.append(_qrow(
            "Openings", "Window Accessory", "flyscreen",
            window_count, "nr",
            "derived", f"1 per window × {window_count} windows",
            f"derived from window_count={window_count} [{window_src}]",
            window_conf,
            assumption="All windows assumed to have flyscreen (PNG climate standard).",
            v2_extractor_source="derived",
        ))

    return rows


def _build_linings(pm: dict) -> list[dict]:
    rows: list[dict] = []
    floor_area      = _g(pm, "floor_area_m2")         or 0.0
    verandah_area   = _g(pm, "verandah_area_m2")      or 0.0
    ext_wall_perim  = _g(pm, "ext_wall_perimeter_m")  or 0.0
    _raw_ceil       = _g(pm, "ceiling_area_m2")        or 0.0
    src_ceil, conf_ceil = _src(pm, "geometry", "ceiling_area_m2")

    # Use floor_area - verandah_area as ceiling estimate when DXF ceiling polygon
    # captures only a partial area (common when ceiling layer is not fully closed).
    _derived_ceil = round(floor_area - verandah_area, 2) if floor_area > 0 else 0.0
    if _raw_ceil > 0 and _raw_ceil >= _derived_ceil * 0.9:
        ceiling_area  = _raw_ceil
        ceil_evidence = f"{src_ceil}: ceiling_area_m2={_raw_ceil:.2f}"
    elif _derived_ceil > 0:
        ceiling_area  = _derived_ceil
        conf_ceil     = "MEDIUM"
        ceil_evidence = (
            f"derived: floor_area({floor_area:.2f}) − verandah_area({verandah_area:.2f}) = {_derived_ceil:.2f} m² "
            f"[DXF ceiling polygon={_raw_ceil:.2f} m² appears partial]"
        )
        src_ceil = "derived"
    else:
        ceiling_area  = _raw_ceil
        ceil_evidence = f"{src_ceil}: ceiling_area_m2={_raw_ceil:.2f}"

    if ceiling_area > 0:
        rows.append(_qrow(
            "Linings", "Ceiling Lining", "FC sheet",
            fc_ceiling_sheets(ceiling_area), "sheets",
            "derived", f"ceil(ceiling_area * {1.05} / {2.88})",
            f"derived from ceiling_area_m2={ceiling_area:.2f}; {ceil_evidence}",
            conf_ceil, v2_extractor_source="derived",
        ))
        rows.append(_qrow(
            "Linings", "Ceiling Battens", "timber/steel",
            ceiling_batten_lm(ceiling_area), "lm",
            "derived", f"ceiling_area / ({400}/1000)",
            f"derived from ceiling_area_m2={ceiling_area:.2f}; {ceil_evidence}",
            "MEDIUM", v2_extractor_source="derived",
        ))

    if ext_wall_perim > 0:
        wall_area = ext_wall_area_m2(ext_wall_perim)
        rows.append(_qrow(
            "Linings", "External Wall Lining", "FC sheet / cladding",
            fc_wall_sheets(wall_area), "sheets",
            "derived", f"ceil(ext_wall_area * {1.05} / {3.24})",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f} → wall_area={wall_area:.2f}",
            "MEDIUM", v2_extractor_source="derived",
        ))

        # Internal wall lining — provisional (int_wall_lm not in model, use area ratio estimate)
        if floor_area > 0:
            est_int_lm = int_wall_lm_estimate(floor_area)
            est_int_area = int_wall_one_face_m2(est_int_lm)
            rows.append(_qrow(
                "Linings", "Internal Wall Lining", "FC sheet / plasterboard",
                fc_wall_sheets(est_int_area), "sheets",
                "derived",
                f"ceil(est_int_wall_area * {1.05} / {3.24}); "
                f"est_int_wall_lm = floor_area × {INT_WALL_LM_RATIO}",
                f"derived: int_wall_lm_estimate={est_int_lm:.1f} lm "
                f"(floor_area={floor_area:.1f} × ratio {INT_WALL_LM_RATIO}) "
                f"→ one_face={est_int_area:.2f} m²",
                "LOW",
                assumption=(
                    f"Internal wall lm not measured from sources — estimated as "
                    f"floor_area × {INT_WALL_LM_RATIO} = {est_int_lm:.1f} lm. "
                    "Replace with measured value if DXF internal wall layer or "
                    "room schedule is available."
                ),
                manual_review=True,
                v2_extractor_source="derived",
            ))

        # Cornice / ceiling trim — runs along all internal faces of external walls
        rows.append(_qrow(
            "Linings", "Cornice / Ceiling Trim", "cove/cornice",
            cornice_lm(ext_wall_perim), "lm",
            "derived", "ext_wall_perimeter (all internal faces)",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f}",
            "MEDIUM",
            assumption="Assumes cornice on external walls only. Add int_wall_lm if cornice runs on internal walls.",
            v2_extractor_source="derived",
        ))

    return rows


def _build_insulation(pm: dict) -> list[dict]:
    """Insulation package — wall batts and roof batts derived from geometry."""
    rows: list[dict] = []
    ext_wall_perim = _g(pm, "ext_wall_perimeter_m") or 0.0
    roof_area      = _g(pm, "roof_area_m2")          or 0.0
    src_roof, conf_roof = _src(pm, "geometry", "roof_area_m2")

    if ext_wall_perim > 0:
        wall_ins_area = insulation_wall_m2(ext_wall_perim)
        rows.append(_qrow(
            "Insulation", "Insulation Batts", "external wall",
            wall_ins_area, "m2",
            "derived", "ext_wall_perimeter × wall_height (2.4 m)",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f}",
            "MEDIUM",
            assumption="Gross wall area; deduct openings if required.",
            v2_extractor_source="derived",
        ))

    if roof_area > 0:
        rows.append(_qrow(
            "Insulation", "Insulation Batts", "roof / ceiling",
            insulation_roof_m2(roof_area), "m2",
            "derived", "= roof_area_m2 (direct)",
            f"derived from {src_roof}: roof_area_m2={roof_area:.2f}",
            conf_roof,
            assumption="Roof batts area taken as full roof area (including sarking overlap).",
            v2_extractor_source="derived",
        ))

    return rows


def _build_finishes(pm: dict) -> list[dict]:
    rows: list[dict] = []
    floor_area     = _g(pm, "floor_area_m2")         or 0.0
    ceiling_area   = _g(pm, "ceiling_area_m2")        or 0.0
    ext_wall_perim = _g(pm, "ext_wall_perimeter_m")  or 0.0
    door_count     = _o(pm, "door_count")             or 0
    window_count   = _o(pm, "window_count")           or 0
    src, conf      = _src(pm, "geometry", "floor_area_m2")

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

    # Skirting board — derived from perimeters
    if ext_wall_perim > 0 and floor_area > 0:
        est_int_lm = int_wall_lm_estimate(floor_area)
        sk_lm = skirting_lm(ext_wall_perim, est_int_lm)
        rows.append(_qrow(
            "Finishes", "Skirting Board", "timber/MDF skirting",
            sk_lm, "lm",
            "derived",
            f"ext_wall_perimeter + est_int_wall_lm = {ext_wall_perim:.1f} + {est_int_lm:.1f}",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f}; "
            f"est_int_wall_lm={est_int_lm:.1f} (floor_area × {INT_WALL_LM_RATIO})",
            "LOW",
            assumption=(
                "Internal wall lm estimated from floor area ratio. "
                "Replace with measured value when available."
            ),
            manual_review=True,
            v2_extractor_source="derived",
        ))

    # Architraves — derived from door/window counts
    if door_count > 0:
        arch_d = architrave_door_lm(door_count)
        rows.append(_qrow(
            "Finishes", "Architrave", "door",
            arch_d, "lm",
            "derived",
            f"door_count × {ARCHITRAVE_DOOR_LM_EACH} lm = {door_count} × {ARCHITRAVE_DOOR_LM_EACH}",
            f"derived from door_count={door_count} [{_src(pm,'openings','door_count')[0]}]",
            "MEDIUM",
            v2_extractor_source="derived",
        ))

    if window_count > 0:
        arch_w = architrave_window_lm(window_count)
        rows.append(_qrow(
            "Finishes", "Architrave", "window",
            arch_w, "lm",
            "derived",
            f"window_count × {ARCHITRAVE_WINDOW_LM_EACH} lm = {window_count} × {ARCHITRAVE_WINDOW_LM_EACH}",
            f"derived from window_count={window_count} [{_src(pm,'openings','window_count')[0]}]",
            "MEDIUM",
            v2_extractor_source="derived",
        ))

    # Paint — external wall face
    if ext_wall_perim > 0:
        p_ext = paint_external_m2(ext_wall_perim)
        rows.append(_qrow(
            "Finishes", "Paint", "external",
            p_ext, "m2",
            "derived", "ext_wall_perimeter × wall_height (2.4 m)",
            f"derived from ext_wall_perimeter_m={ext_wall_perim:.2f}",
            "MEDIUM",
            assumption="Gross area; no deduction for openings.",
            v2_extractor_source="derived",
        ))

    # Paint — internal (ceiling + internal walls)
    if floor_area > 0:
        verandah_area_fin = _g(pm, "verandah_area_m2") or 0.0
        _raw_ceil_fin  = _g(pm, "ceiling_area_m2") or 0.0
        _deriv_ceil_fin = round(floor_area - verandah_area_fin, 2) if floor_area > 0 else 0.0
        if _raw_ceil_fin > 0 and _raw_ceil_fin >= _deriv_ceil_fin * 0.9:
            ceil_for_paint  = _raw_ceil_fin
            ceil_paint_note = f"dxf ceiling layer={_raw_ceil_fin:.2f} m²"
        else:
            ceil_for_paint  = _deriv_ceil_fin
            ceil_paint_note = f"floor({floor_area:.2f})−verandah({verandah_area_fin:.2f})={_deriv_ceil_fin:.2f} m²"
        est_int_lm = int_wall_lm_estimate(floor_area)
        p_int = paint_internal_m2(ceil_for_paint, est_int_lm)
        rows.append(_qrow(
            "Finishes", "Paint", "internal",
            p_int, "m2",
            "derived",
            f"ceiling_area + (est_int_wall_lm × 2.4 m) = {ceil_for_paint:.1f} + {est_int_lm:.1f}×2.4",
            f"derived: ceiling_area={ceil_paint_note}; est_int_wall_lm={est_int_lm:.1f}",
            "LOW",
            assumption=(
                "Ceiling area: " + ceil_paint_note + ". "
                "Internal wall lm estimated from floor area ratio. "
                "Includes ext-wall internal face in int_wall estimate. Manual check recommended."
            ),
            manual_review=True,
            v2_extractor_source="derived",
        ))

    return rows


def _build_services(pm: dict) -> list[dict]:
    """
    Services placeholder package.

    Items are always provisional (no services schedules extracted from V2
    sources).  Builder's works items represent the civil/structural scope
    provided by the building contractor to facilitate services installations.
    All items require manual review before pricing.
    """
    rows: list[dict] = []
    floor_area = _g(pm, "floor_area_m2") or 0.0
    rooms      = pm.get("rooms", [])

    # Detect wet-area evidence from room names
    wet_area_keywords = {"bathroom", "toilet", "wc", "laundry", "kitchen", "wet"}
    room_names = [r.get("name", "").lower() for r in rooms if isinstance(r, dict)]
    wet_area_detected = any(
        any(kw in name for kw in wet_area_keywords)
        for name in room_names
    )
    wet_evidence = (
        f"room schedule: {[n for n in room_names if any(k in n for k in wet_area_keywords)]}"
        if wet_area_detected
        else "no room schedule — assumed present (pharmacy/residential building)"
    )

    if floor_area > 0:
        def _prov(element_type, subtype, evidence_note=""):
            return _qrow(
                "Services", element_type, subtype,
                0, "item",
                "provisional", "manual review required",
                evidence_note or "no services schedule in sources",
                "LOW",
                assumption=f"{element_type} ({subtype}) — quantity not derivable from available sources. "
                           "Confirm scope with services engineer.",
                manual_review=True, v2_extractor_source="provisional",
            )

        rows.append(_prov(
            "Builder's Works", "electrical",
            "no electrical schedule; allow PC sum for conduit penetrations, cable trays, DB board rough-in",
        ))
        rows.append(_prov(
            "Builder's Works", "plumbing",
            "no plumbing schedule; allow PC sum for floor wastes, pipe penetrations, slab core-outs",
        ))
        rows.append(_qrow(
            "Services", "Wet Area Waterproofing", "membrane",
            0, "m2",
            "provisional", "manual review required",
            wet_evidence,
            "LOW" if not wet_area_detected else "MEDIUM",
            assumption=(
                "Wet area waterproofing required for bathrooms/toilets/kitchens. "
                "Quantity = sum of wet room floor areas. "
                + ("Room schedule not available — area must be confirmed on site." if not wet_area_detected else "")
            ),
            manual_review=True, v2_extractor_source="provisional",
        ))
        rows.append(_prov(
            "Sanitary Fixtures", "provisional allowance",
            "no fixture schedule; provisional PC sum for basin, WC, shower, sink as applicable",
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

    # Stair balustrade and handrail — provisional whenever stairs detected
    if pdf_stairs or stair_ev:
        for label, subtype in [("Balustrade", "steel / glass balustrade"), ("Handrail", "steel / timber handrail")]:
            rows.append(_qrow(
                "Stairs", label, subtype,
                0, "lm",
                "provisional", "manual review required — stair run length not measured",
                "stair geometry detected but run length not derived from available sources",
                "LOW",
                assumption=(
                    f"Stair {label.lower()} required. Length = stair run × number of flights. "
                    "Confirm from architectural drawings."
                ),
                manual_review=True, v2_extractor_source="provisional",
            ))

    return rows


def _build_external(pm: dict) -> list[dict]:
    rows: list[dict] = []
    verandah_area  = _g(pm, "verandah_area_m2")    or 0.0
    verandah_perim = _g(pm, "verandah_perimeter_m") or 0.0
    stair_ev       = _g(pm, "stair_evidence")       or False
    src, conf      = _src(pm, "geometry", "verandah_area_m2")

    if verandah_area > 0:
        rows.append(_qrow(
            "External", "Verandah Decking", "timber / composite deck",
            round(verandah_area, 2), "m2",
            "measured", "DXF VERANDAH LWPOLYLINE area",
            f"{src}: verandah_area_m2={verandah_area:.2f}",
            conf, v2_extractor_source=src,
        ))

    if verandah_perim > 0:
        rows.append(_qrow(
            "External", "Verandah Balustrade", "balustrade / edge trim",
            round(verandah_perim, 2), "lm",
            "measured", "DXF VERANDAH LWPOLYLINE perimeter",
            f"dxf_geometry: verandah_perimeter_m={verandah_perim:.2f}",
            conf, v2_extractor_source="dxf_geometry",
        ))
        rows.append(_qrow(
            "External", "Verandah Handrail", "steel / timber handrail",
            round(verandah_perim, 2), "lm",
            "derived", "= verandah_perimeter (one rail along open edge)",
            f"derived from dxf_geometry: verandah_perimeter_m={verandah_perim:.2f}",
            "MEDIUM",
            assumption=(
                "Handrail assumed on full verandah perimeter. "
                "Reduce if one side is against building wall."
            ),
            v2_extractor_source="derived",
        ))

    # Ramp — provisional if stair evidence detected (building may have access ramp)
    if stair_ev:
        rows.append(_qrow(
            "External", "Access Ramp", "concrete / timber ramp",
            0, "item",
            "provisional", "DXF STAIRS layer detected — ramp may be present",
            f"dxf_geometry: stair_evidence=True",
            "LOW",
            assumption=(
                "Stair geometry detected in DXF. Building may also require an access ramp. "
                "Confirm ramp requirement, dimensions, and material from architectural drawings."
            ),
            manual_review=True, v2_extractor_source="dxf_geometry",
        ))

    # Site preparation — always provisional (scope depends on site survey)
    rows.append(_qrow(
        "External", "Site Preparation", "clearing / levelling / fill",
        0, "item",
        "provisional", "standard allowance — no site survey data",
        "no site survey in sources",
        "LOW",
        assumption=(
            "Site preparation scope (clearing, levelling, fill, termite treatment) "
            "cannot be derived from architectural drawings alone. "
            "Confirm with civil engineer / site survey."
        ),
        manual_review=True, v2_extractor_source="provisional",
    ))

    return rows


# ─── completeness calculator ──────────────────────────────────────────────────

def _completeness(all_rows: list[dict]) -> dict:
    packages = [
        "Structure", "Roof", "Openings", "Linings", "Insulation",
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
    all_rows.extend(_build_insulation(project_model))
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
