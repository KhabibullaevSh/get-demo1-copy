"""
project_model.py — Merge all extracted data into a single project model.

Priority rules:
  floor_area:           DXF WALLS polygon  > IFC spaces  > PDF notes
  ext_wall_perimeter:   DXF WALLS polygon
  roof_area:            DXF ROOF polygon   > IFC
  door_count:           PDF schedule       > DXF blocks  > IFC
  window_count:         PDF schedule       > DXF blocks  > IFC
  structural members:   FrameCAD BOM       > IFC IfcElementQuantity > DXF derived
  rooms:                PDF                > IFC spaces
  finishes:             PDF schedule       > IFC materials

Every value in the model carries explicit provenance: {value, source, confidence}.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("boq.v2.project_model")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _val(value, source: str, confidence: str = "MEDIUM") -> dict:
    """Wrap a scalar value with provenance metadata."""
    return {"value": value, "source": source, "confidence": confidence}


def _pick_first(*candidates: tuple):
    """
    Pick the first candidate whose value is truthy.
    Each candidate is (value, source, confidence).
    Returns _val dict.
    """
    for value, source, confidence in candidates:
        if value:
            return _val(value, source, confidence)
    # Return last candidate even if falsy
    v, s, c = candidates[-1] if candidates else (None, "none", "LOW")
    return _val(v, s, c)


def _pick_count(*candidates: tuple):
    """Pick first candidate where value > 0."""
    for value, source, confidence in candidates:
        if isinstance(value, int) and value > 0:
            return _val(value, source, confidence)
    v, s, c = candidates[-1] if candidates else (0, "none", "LOW")
    return _val(v, s, c)


def _pick_lm(*candidates: tuple):
    """Pick first structural lm candidate > 0."""
    for value, source, confidence in candidates:
        if isinstance(value, (int, float)) and value > 0:
            return _val(round(float(value), 2), source, confidence)
    v, s, c = candidates[-1] if candidates else (0.0, "none", "LOW")
    return _val(round(float(v), 2), s, c)


# ─── main builder ─────────────────────────────────────────────────────────────

def build_project_model(
    dxf_data:         dict,
    ifc_data:         dict,
    pdf_data:         dict,
    framecad_data:    dict,
    source_inventory: list[dict],
    classification:   dict,
    project_name:     str = "unknown",
) -> dict:
    """
    Merge extractor outputs into a single project model with provenance.

    Returns comprehensive dict.  No quantities are looked up from any BOQ file.
    """
    warnings: list[str] = []

    # Collect all extractor warnings
    for src, data in (
        ("dxf",      dxf_data),
        ("ifc",      ifc_data),
        ("pdf",      pdf_data),
        ("framecad", framecad_data),
    ):
        for w in data.get("warnings", []):
            warnings.append(f"[{src}] {w}")

    # ── Determine structural source priority ──────────────────────────────
    framecad_available = framecad_data.get("found", False)
    ifc_has_members    = ifc_data.get("column_count", 0) + ifc_data.get("beam_count", 0) > 0

    if framecad_available:
        struct_priority = "framecad_bom"
    elif ifc_has_members:
        struct_priority = "ifc_model"
    else:
        struct_priority = "dxf_derived"

    log.info("Structural source priority: %s", struct_priority)

    # ── Geometry ──────────────────────────────────────────────────────────
    floor_area_m2 = _pick_first(
        (dxf_data.get("floor_area_m2"),        "dxf_geometry", "HIGH"),
        (ifc_data.get("floor_area_m2"),         "ifc_model",    "MEDIUM"),
        (0.0,                                   "none",         "LOW"),
    )
    ext_wall_perim = _val(
        dxf_data.get("ext_wall_perimeter_m", 0.0),
        "dxf_geometry",
        "HIGH" if dxf_data.get("ext_wall_perimeter_m", 0) > 0 else "LOW",
    )
    roof_area = _pick_first(
        (dxf_data.get("roof_area_m2"),          "dxf_geometry", "HIGH"),
        (ifc_data.get("roof_area_m2"),          "ifc_model",    "MEDIUM"),
        (0.0,                                   "none",         "LOW"),
    )
    roof_perim = _val(
        dxf_data.get("roof_perimeter_m", 0.0),
        "dxf_geometry",
        "HIGH" if dxf_data.get("roof_perimeter_m", 0) > 0 else "LOW",
    )
    verandah_area = _pick_first(
        (dxf_data.get("verandah_area_m2"),      "dxf_geometry", "HIGH"),
        (0.0,                                   "none",         "LOW"),
    )
    verandah_perim = _val(
        dxf_data.get("verandah_perimeter_m", 0.0),
        "dxf_geometry",
        "HIGH" if dxf_data.get("verandah_perimeter_m", 0) > 0 else "LOW",
    )
    ceiling_area = _pick_first(
        (dxf_data.get("ceiling_area_m2"),       "dxf_geometry", "HIGH"),
        (dxf_data.get("floor_area_m2"),         "dxf_geometry", "MEDIUM"),
        (0.0,                                   "none",         "LOW"),
    )

    # ── Openings ──────────────────────────────────────────────────────────
    pdf_door_count   = len(pdf_data.get("doors",   [])) or None
    pdf_window_count = len(pdf_data.get("windows", [])) or None

    door_count = _pick_count(
        (pdf_door_count,                        "pdf_schedule",     "HIGH"),
        (dxf_data.get("door_count"),            "dxf_blocks",       "HIGH"),
        (ifc_data.get("door_count"),            "ifc_model",        "MEDIUM"),
        (0,                                     "none",             "LOW"),
    )
    window_count = _pick_count(
        (pdf_window_count,                      "pdf_schedule",     "HIGH"),
        (dxf_data.get("window_count"),          "dxf_blocks",       "HIGH"),
        (ifc_data.get("window_count"),          "ifc_model",        "MEDIUM"),
        (0,                                     "none",             "LOW"),
    )

    # ── Structural ────────────────────────────────────────────────────────
    bom_totals = framecad_data.get("totals", {})

    def _ifc_sum(*keys: str) -> float:
        """Sum multiple IFC result keys (named + inferred variants)."""
        return sum(ifc_data.get(k, 0.0) for k in keys)

    def _struct_lm(key_bom: str, *ifc_keys: str) -> dict:
        if framecad_available:
            return _pick_lm((bom_totals.get(key_bom, 0.0), "framecad_bom", "HIGH"))
        if ifc_has_members:
            total = _ifc_sum(*ifc_keys)
            return _pick_lm((total, "ifc_model", "HIGH"))
        return _val(0.0, "dxf_derived", "LOW")

    # Wall stud = named W-type + inferred full-height + short stud
    wall_stud_lm  = _struct_lm(
        "wall_stud_lm",
        "wall_frame_stud_lm",
        "wall_stud_inferred_lm",
        "wall_stud_short_inferred_lm",
    )
    # Wall plate = named T-type top plates + inferred plates from bare-desc
    wall_plate_lm = _struct_lm(
        "plate_lm",
        "wall_frame_top_plate_lm",
        "wall_plate_lm",
        "wall_plate_inferred_lm",
    )
    # Wall noggin = named N-type + inferred noggins from bare-desc
    wall_noggin_lm = _struct_lm(
        "noggin_lm",
        "wall_noggin_lm",
        "wall_noggin_inferred_lm",
    )
    # Bottom plate (separate — anomalous lengths flagged in extractor)
    wall_btm_plate_lm = _struct_lm(
        "bottom_plate_lm",
        "wall_frame_bottom_plate_lm",
    )
    roof_rafter_lm = _struct_lm("rafter_lm",   "roof_rafter_lm")
    roof_plate_lm  = _struct_lm("plate_lm",    "roof_plate_lm")
    roof_noggin_lm = _struct_lm("noggin_lm",   "roof_noggin_lm")
    verandah_lm    = _struct_lm("verandah_lm", "verandah_frame_lm")
    floor_joist_lm = _struct_lm("joist_lm",    "floor_joist_lm", "lgs_unclassified_lm")
    girt_lm        = _struct_lm("girt_lm",     "girt_lm")

    # SHS / RHS structural steel — NOT LGS, always separate BOQ item
    steel_shs_lm = _val(
        round(_ifc_sum("steel_shs_lm"), 2),
        "ifc_model",
        "HIGH" if ifc_has_members else "LOW",
    )

    structural = {
        "wall_stud_lm":              wall_stud_lm,
        "wall_plate_lm":             wall_plate_lm,
        "wall_btm_plate_lm":         wall_btm_plate_lm,
        "wall_noggin_lm":            wall_noggin_lm,
        "roof_rafter_lm":            roof_rafter_lm,
        "roof_plate_lm":             roof_plate_lm,
        "roof_noggin_lm":            roof_noggin_lm,
        "floor_joist_lm":            floor_joist_lm,
        "verandah_frame_lm":         verandah_lm,
        "girt_lm":                   girt_lm,
        "steel_shs_lm":              steel_shs_lm,
        "lgs_unclassified_lm":       _val(
            round(_ifc_sum("lgs_unclassified_lm"), 2), "ifc_model", "LOW"
        ),
        "ifc_manual_review_lm":      _val(
            round(ifc_data.get("manual_review_lm", 0.0), 2), "ifc_model", "MEDIUM"
        ),
        "total_column_lm":           _val(ifc_data.get("total_column_lm", 0.0), "ifc_model", "HIGH"),
        "total_beam_lm":             _val(ifc_data.get("total_beam_lm",   0.0), "ifc_model", "HIGH"),
        "column_count":              _val(ifc_data.get("column_count",    0),   "ifc_model", "HIGH"),
        "beam_count":                _val(ifc_data.get("beam_count",      0),   "ifc_model", "HIGH"),
        "source_priority_used":      struct_priority,
        "member_breakdown":          ifc_data.get("member_breakdown", {}),
        "ifc_classification_notes":  ifc_data.get("classification_notes", []),
    }

    # ── Openings detail ───────────────────────────────────────────────────
    openings = {
        "door_count":   door_count,
        "window_count": window_count,
        "doors":        pdf_data.get("doors",   []),
        "windows":      pdf_data.get("windows", []),
    }

    # ── Post count ────────────────────────────────────────────────────────
    post_count = _pick_count(
        (dxf_data.get("post_count"),            "dxf_geometry", "HIGH"),
        (ifc_data.get("column_count"),          "ifc_model",    "MEDIUM"),
        (0,                                     "none",         "LOW"),
    )

    # ── Stair evidence ────────────────────────────────────────────────────
    stair_evidence = (
        dxf_data.get("stair_evidence", False)
        or bool(pdf_data.get("stairs", []))
    )

    geometry = {
        "floor_area_m2":        floor_area_m2,
        "verandah_area_m2":     verandah_area,
        "ceiling_area_m2":      ceiling_area,
        "ext_wall_perimeter_m": ext_wall_perim,
        "roof_area_m2":         roof_area,
        "roof_perimeter_m":     roof_perim,
        "verandah_perimeter_m": verandah_perim,
        "post_count":           post_count,
        "stair_evidence":       _val(stair_evidence, "dxf_geometry", "MEDIUM"),
        "stair_line_count":     _val(dxf_data.get("stair_line_count", 0), "dxf_geometry", "MEDIUM"),
    }

    model = {
        "project_name":     project_name,
        "project_mode":     classification.get("project_mode", "custom_project"),
        "model_code":       classification.get("matched_model_code"),
        "classification":   classification,
        "geometry":         geometry,
        "structural":       structural,
        "openings":         openings,
        "rooms":            pdf_data.get("rooms",    []),
        "finishes":         pdf_data.get("finishes", []),
        "stairs":           pdf_data.get("stairs",   []),
        "notes":            pdf_data.get("notes",    []),
        "source_inventory": source_inventory,
        "raw_dxf":          dxf_data,
        "raw_ifc":          ifc_data,
        "raw_framecad":     framecad_data,
        "extraction_warnings": warnings,
    }

    log.info(
        "Project model built: floor=%.1f m², roof=%.1f m², "
        "doors=%s, windows=%s, struct_priority=%s",
        floor_area_m2["value"],
        roof_area["value"],
        door_count["value"],
        window_count["value"],
        struct_priority,
    )
    return model
