"""
project_quantities.py — Build a neutral quantity model from merged project data.

Does NOT map to BOQ rows or apply G303 templates.
Every quantity traces to geometry, structural BOM, or schedule data.

Produces both:
  - direct detected quantities (floor area, wall lengths, etc.)
  - derived quantities (FC sheet counts, batten runs, insulation areas, etc.)
  - manual-review scope placeholders when scope is implied but data is weak

Output saved to output/json/{project_name}_quantities.json.
"""

from __future__ import annotations
import json
import logging
import math
from pathlib import Path

from src.config import (
    OUTPUT_DIR,
    SHEET_AREA_FC, SHEET_AREA_FC_WALL, FC_WASTE_FACTOR,
    DEFAULT_WALL_HEIGHT,
    BATTEN_ROOF_SPACING_MM, BATTEN_CEILING_SPACING_MM, BATTEN_LENGTH_MM,
    SISALATION_ROLL_M2,
)
from src.utils import safe_float

log = logging.getLogger("boq.project_quantities")

OUTPUT_JSON = OUTPUT_DIR / "json"


def build_quantity_model(merged: dict, classification: dict) -> dict:
    """
    Build a neutral quantity model from merged project data.

    Returns:
    {
      "project_mode": str,
      "matched_model": str | None,
      "quantities": [ { item_group, element_type, subtype, quantity, unit,
                         source_evidence, confidence, assumption, manual_review } ]
      "completeness": { package: { detected: bool, items: int, notes: str } }
    }
    """
    geo      = merged.get("geometry", {})
    struct   = merged.get("structural", {})
    doors    = merged.get("doors", [])
    windows  = merged.get("windows", [])
    finishes = merged.get("finishes", [])
    rooms    = geo.get("rooms", [])
    stairs   = merged.get("stairs", [])

    quantities: list[dict] = []

    # ── 1. FLOOR ──────────────────────────────────────────────────────────────
    floor_area = safe_float(geo.get("total_floor_area_m2"))
    if floor_area > 0:
        quantities.append(_q(
            "floor", "floor_area", "total",
            floor_area, "m2", "dwg_geometry / ifc_spaces", "HIGH",
        ))
    else:
        quantities.append(_q(
            "floor", "floor_area", "total",
            None, "m2", "none", "LOW",
            manual_review=True,
            assumption="No floor area found in DWG or IFC — manual entry required",
        ))

    verandah_area = safe_float(geo.get("verandah_area_m2"))
    if verandah_area > 0:
        quantities.append(_q(
            "floor", "floor_area", "verandah",
            verandah_area, "m2", "dwg_geometry", "HIGH",
        ))

    # Floor finish types from finish schedule
    floor_tile_area  = _sum_finishes(finishes, "floor", "tile")
    floor_vinyl_area = _sum_finishes(finishes, "floor", "vinyl")
    if floor_tile_area > 0:
        quantities.append(_q("floor", "floor_area", "tile", floor_tile_area, "m2",
                             "pdf_finish_schedule", "MEDIUM"))
    if floor_vinyl_area > 0:
        quantities.append(_q("floor", "floor_area", "vinyl", floor_vinyl_area, "m2",
                             "pdf_finish_schedule", "MEDIUM"))

    # Floor panel / joist from structural
    floor_panel_qty = safe_float(struct.get("floor_panel_qty"))
    if floor_panel_qty > 0:
        quantities.append(_q("floor", "floor_panel", "LGS", floor_panel_qty, "nr",
                             struct.get("floor_panel_source", "bom/ifc"), "HIGH"))

    floor_joist_lm = safe_float(struct.get("floor_joist_lm"))
    if floor_joist_lm > 0:
        quantities.append(_q("floor", "floor_joist", "LGS", floor_joist_lm, "lm",
                             "bom/ifc", "HIGH"))

    # ── 2. WALLS ──────────────────────────────────────────────────────────────
    ext_wall_lm = safe_float(geo.get("external_wall_length_m"))
    int_wall_lm = safe_float(geo.get("internal_wall_length_m"))

    if ext_wall_lm > 0:
        quantities.append(_q("walls", "external_wall", "length",
                             ext_wall_lm, "lm", "dwg_geometry / ifc", "HIGH"))

    if int_wall_lm > 0:
        quantities.append(_q("walls", "internal_wall", "length",
                             int_wall_lm, "lm", "dwg_geometry / ifc", "HIGH"))

    # Wall areas — direct if available, otherwise derive from length × height
    ext_wall_area = safe_float(geo.get("external_wall_area_m2"))
    if ext_wall_area <= 0 and ext_wall_lm > 0:
        ext_wall_area = round(ext_wall_lm * DEFAULT_WALL_HEIGHT, 2)
        quantities.append(_q("walls", "external_wall", "area",
                             ext_wall_area, "m2", "derived: ext_wall_lm × wall_height", "MEDIUM",
                             assumption=f"{ext_wall_lm}lm × {DEFAULT_WALL_HEIGHT}m height"))
    elif ext_wall_area > 0:
        quantities.append(_q("walls", "external_wall", "area",
                             ext_wall_area, "m2", "dwg_geometry", "HIGH"))

    int_wall_area = safe_float(geo.get("internal_wall_area_m2"))
    if int_wall_area <= 0 and int_wall_lm > 0:
        int_wall_area = round(int_wall_lm * DEFAULT_WALL_HEIGHT * 2, 2)  # both faces
        quantities.append(_q("walls", "internal_wall", "area",
                             int_wall_area, "m2",
                             "derived: int_wall_lm × wall_height × 2 faces", "MEDIUM",
                             assumption=f"{int_wall_lm}lm × {DEFAULT_WALL_HEIGHT}m × 2 = {int_wall_area}m²"))
    elif int_wall_area > 0:
        quantities.append(_q("walls", "internal_wall", "area",
                             int_wall_area, "m2", "dwg_geometry", "HIGH"))

    # LGS wall framing
    wall_frame_lm = safe_float(struct.get("wall_frame_lm"))
    if wall_frame_lm > 0:
        quantities.append(_q("walls", "wall_frame", "LGS", wall_frame_lm, "lm",
                             struct.get("wall_frame_source", "bom/ifc"), "HIGH"))
    elif ext_wall_lm > 0 or int_wall_lm > 0:
        # Derive from wall lengths when BOM missing
        total_wall_lm = ext_wall_lm + int_wall_lm
        quantities.append(_q("walls", "wall_frame", "LGS_derived",
                             round(total_wall_lm, 2), "lm",
                             "derived: ext_wall + int_wall lengths", "MEDIUM",
                             assumption="No BOM — wall frame estimated from DWG wall lengths",
                             manual_review=True))

    # Derived: FC external wall cladding sheets
    if ext_wall_area > 0:
        fc_ext_sheets = math.ceil(ext_wall_area * FC_WASTE_FACTOR / SHEET_AREA_FC_WALL)
        quantities.append(_q("walls", "fc_sheet_external", "sheets",
                             float(fc_ext_sheets), "nr",
                             f"derived: {ext_wall_area}m² ÷ {SHEET_AREA_FC_WALL}m² × {FC_WASTE_FACTOR} waste",
                             "MEDIUM",
                             assumption=f"FC wall sheet 1.2×2.7m. {fc_ext_sheets} sheets for {ext_wall_area:.1f}m² ext wall",
                             quantity_rule=f"ceil({ext_wall_area} m² × {FC_WASTE_FACTOR} waste ÷ {SHEET_AREA_FC_WALL} m²/sheet)"))

    # Derived: FC internal wall lining sheets
    if int_wall_area > 0:
        fc_int_sheets = math.ceil(int_wall_area * FC_WASTE_FACTOR / SHEET_AREA_FC_WALL)
        quantities.append(_q("walls", "fc_sheet_internal", "sheets",
                             float(fc_int_sheets), "nr",
                             f"derived: {int_wall_area}m² ÷ {SHEET_AREA_FC_WALL}m² × {FC_WASTE_FACTOR} waste",
                             "MEDIUM",
                             assumption=f"FC wall sheet 1.2×2.7m. {fc_int_sheets} sheets for {int_wall_area:.1f}m² int wall (both faces)",
                             quantity_rule=f"ceil({int_wall_area} m² × {FC_WASTE_FACTOR} waste ÷ {SHEET_AREA_FC_WALL} m²/sheet)"))

    # Wall insulation batts (external only)
    if ext_wall_area > 0:
        quantities.append(_q("walls", "insulation_batts", "external_wall",
                             ext_wall_area, "m2",
                             "derived: external wall area", "MEDIUM",
                             assumption="Insulation batts assumed for all external walls"))

    # ── 3. ROOF ───────────────────────────────────────────────────────────────
    roof_area = safe_float(geo.get("roof_area_m2"))
    if roof_area > 0:
        quantities.append(_q("roof", "roof_area", "total",
                             roof_area, "m2", "dwg_geometry / ifc", "HIGH"))

        # Roof cladding sheets (corrugated / Colorbond — manual type)
        quantities.append(_q("roof", "roof_cladding", "sheets",
                             roof_area, "m2",
                             "derived: roof area", "MEDIUM",
                             assumption="Roof cladding area = roof area. Confirm sheet type (Colorbond/tile)"))

        # Sisalation / sarking under roof
        sis_rolls = math.ceil(roof_area / SISALATION_ROLL_M2)
        quantities.append(_q("roof", "sisalation", "rolls",
                             float(sis_rolls), "nr",
                             f"derived: {roof_area}m² ÷ {SISALATION_ROLL_M2}m²/roll",
                             "MEDIUM",
                             assumption=f"Sisalation roll = {SISALATION_ROLL_M2}m². {sis_rolls} rolls for {roof_area:.1f}m²",
                             quantity_rule=f"ceil({roof_area} m² ÷ {SISALATION_ROLL_M2} m²/roll)"))

        # Roof insulation batts
        quantities.append(_q("roof", "insulation_batts", "roof",
                             roof_area, "m2",
                             "derived: roof area", "MEDIUM",
                             assumption="Insulation batts assumed under roof"))

    # Roof battens from structural (if available)
    roof_batten_lm = safe_float(struct.get("roof_batten_lm"))
    if roof_batten_lm > 0:
        quantities.append(_q("roof", "roof_batten", "LGS", roof_batten_lm, "lm",
                             "bom/ifc", "HIGH"))
    elif roof_area > 0:
        # Derive from roof area: rows at 900mm spacing × building width runs
        bldg_len = safe_float(geo.get("building_length_m"))
        bldg_wid = safe_float(geo.get("building_width_m"))
        if bldg_len > 0 and bldg_wid > 0:
            spacing_m = BATTEN_ROOF_SPACING_MM / 1000.0
            runs = math.ceil(bldg_wid / 2 / spacing_m) + 1  # per slope
            run_lm = round(runs * bldg_len * 2, 1)           # both slopes
        else:
            # fallback: area / spacing gives total lm
            run_lm = round(roof_area / (BATTEN_ROOF_SPACING_MM / 1000.0), 1)
        quantities.append(_q("roof", "roof_batten", "LGS_derived",
                             run_lm, "lm",
                             f"derived: roof_area ÷ {BATTEN_ROOF_SPACING_MM}mm spacing",
                             "MEDIUM",
                             assumption=f"No BOM — roof batten estimated at {BATTEN_ROOF_SPACING_MM}mm spacing",
                             manual_review=True,
                             quantity_rule=f"(roof_width / 2 ÷ {BATTEN_ROOF_SPACING_MM}mm) × bldg_length × 2 slopes"))

    # Roof trusses from structural
    truss_lm = safe_float(struct.get("roof_truss_qty"))
    if truss_lm > 0:
        quantities.append(_q("roof", "roof_truss", "LGS", truss_lm, "lm",
                             struct.get("roof_truss_source", "bom/ifc"), "HIGH"))

    # Ridge / hip (from geo)
    ridge_lm = safe_float(geo.get("ridge_length_m"))
    if ridge_lm > 0:
        quantities.append(_q("roof", "ridge_cap", "lm", ridge_lm, "lm",
                             "dwg_geometry", "HIGH"))
    elif roof_area > 0 and safe_float(geo.get("building_length_m")) > 0:
        ridge_lm = round(safe_float(geo.get("building_length_m")), 1)
        quantities.append(_q("roof", "ridge_cap", "lm_derived", ridge_lm, "lm",
                             "derived: building_length_m", "LOW",
                             assumption="Ridge cap estimated from building length — verify",
                             manual_review=True))

    # Gutter / fascia from roof perimeter
    roof_perim = safe_float(geo.get("roof_perimeter_m"))
    ext_wall_perim = ext_wall_lm  # approx
    gutter_lm = roof_perim if roof_perim > 0 else ext_wall_perim
    if gutter_lm > 0:
        quantities.append(_q("roof", "gutter", "lm",
                             round(gutter_lm, 1), "lm",
                             "derived: roof_perimeter" if roof_perim > 0 else "derived: ext_wall_length",
                             "MEDIUM",
                             assumption="Gutter length ≈ roof perimeter (eave sides only — confirm hip/gable)",
                             quantity_rule="gutter_lm = roof_perimeter (or ext_wall_length as fallback)"))
        quantities.append(_q("roof", "fascia_board", "lm",
                             round(gutter_lm, 1), "lm",
                             "derived: same as gutter", "MEDIUM",
                             assumption="Fascia runs same length as gutter"))

    # Downpipes — 1 per corner approx
    if ext_wall_lm > 0:
        corners = max(4, math.ceil(ext_wall_lm / 10))
        downpipes = max(2, corners // 2)
        quantities.append(_q("roof", "downpipe", "nr",
                             float(downpipes), "nr",
                             "derived: 1 per ~2 corners", "LOW",
                             assumption=f"Estimated {downpipes} downpipes — verify from layout",
                             manual_review=True,
                             quantity_rule="max(2, ceil(ext_wall_lm / 10) / 2)"))

    # Barge board
    barge_lm = safe_float(geo.get("barge_length_m"))
    if barge_lm <= 0 and roof_area > 0:
        # Gable ends: 2 × roof height approx
        pitch_deg = safe_float(geo.get("roof_pitch_degrees")) or 18.0
        bldg_wid  = safe_float(geo.get("building_width_m"))
        if bldg_wid > 0:
            barge_lm = round(2 * (bldg_wid / 2) / math.cos(math.radians(pitch_deg)) * 2, 1)
            quantities.append(_q("roof", "barge_board", "lm_derived",
                                 barge_lm, "lm",
                                 "derived: pitch+width geometry", "LOW",
                                 assumption="Barge estimated from pitch geometry — verify gable/hip",
                                 manual_review=True))

    # Roof fixings (tek screws) — per roofing sheet area
    if roof_area > 0:
        fixings_boxes = math.ceil(roof_area / 10)  # ~1 box per 10m²
        quantities.append(_q("roof", "roof_fixings", "boxes",
                             float(fixings_boxes), "nr",
                             "derived: roof_area / 10m² per box", "LOW",
                             assumption=f"Tek screw boxes estimated at 1 per 10m². Verify with supplier.",
                             manual_review=True,
                             quantity_rule="ceil(roof_area / 10) boxes (1 box per 10 m²)"))

    # ── 4. CEILING ────────────────────────────────────────────────────────────
    ceiling_area = safe_float(geo.get("ceiling_area_m2")) or floor_area
    if ceiling_area > 0:
        quantities.append(_q("ceiling", "ceiling_area", "total",
                             ceiling_area, "m2",
                             "dwg_geometry (≈ floor area)", "MEDIUM"))

        # FC ceiling sheets
        fc_ceil_sheets = math.ceil(ceiling_area * FC_WASTE_FACTOR / SHEET_AREA_FC)
        quantities.append(_q("ceiling", "fc_sheet_ceiling", "sheets",
                             float(fc_ceil_sheets), "nr",
                             f"derived: {ceiling_area}m² ÷ {SHEET_AREA_FC}m² × {FC_WASTE_FACTOR} waste",
                             "MEDIUM",
                             assumption=f"FC ceiling sheet 1.2×2.4m. {fc_ceil_sheets} sheets for {ceiling_area:.1f}m²",
                             quantity_rule=f"ceil({ceiling_area} m² × {FC_WASTE_FACTOR} waste ÷ {SHEET_AREA_FC} m²/sheet)"))

    # Ceiling battens from structural (if available)
    ceil_batten_lm = safe_float(struct.get("ceiling_batten_lm"))
    if ceil_batten_lm > 0:
        quantities.append(_q("ceiling", "ceiling_batten", "LGS",
                             ceil_batten_lm, "lm", "bom/ifc", "HIGH"))
    elif ceiling_area > 0:
        # Derive from area at 400mm spacing
        spacing_m = BATTEN_CEILING_SPACING_MM / 1000.0
        bldg_wid  = safe_float(geo.get("building_width_m"))
        bldg_len  = safe_float(geo.get("building_length_m"))
        if bldg_wid > 0 and bldg_len > 0:
            runs_lm = round(math.ceil(bldg_wid / spacing_m) * bldg_len, 1)
        else:
            runs_lm = round(ceiling_area / spacing_m, 1)
        quantities.append(_q("ceiling", "ceiling_batten", "LGS_derived",
                             runs_lm, "lm",
                             f"derived: ceiling_area ÷ {BATTEN_CEILING_SPACING_MM}mm spacing",
                             "MEDIUM",
                             assumption=f"No BOM — ceiling batten estimated at {BATTEN_CEILING_SPACING_MM}mm spacing",
                             manual_review=True,
                             quantity_rule=f"ceil(bldg_width ÷ {BATTEN_CEILING_SPACING_MM}mm) × bldg_length"))

    # Cornice / ceiling trim
    if ceiling_area > 0:
        # Perimeter of ceiling ≈ ext wall perimeter
        cornice_lm = ext_wall_lm if ext_wall_lm > 0 else round(math.sqrt(ceiling_area) * 4, 1)
        quantities.append(_q("ceiling", "cornice_trim", "lm",
                             round(cornice_lm, 1), "lm",
                             "derived: ceiling perimeter", "LOW",
                             assumption="Cornice/ceiling trim along all wall/ceiling junctions",
                             manual_review=True))

    # ── 5. WALL FINISHES / LININGS ────────────────────────────────────────────
    # Internal plasterboard / FC lining (already captured under walls above)
    # Skirting boards
    skirting_lm = ext_wall_lm + int_wall_lm if (ext_wall_lm + int_wall_lm) > 0 else 0
    if skirting_lm > 0:
        quantities.append(_q("finishes", "skirting_board", "lm",
                             round(skirting_lm, 1), "lm",
                             "derived: ext_wall_lm + int_wall_lm", "LOW",
                             assumption="Skirting runs all internal wall faces (deduct for openings manually)",
                             manual_review=True,
                             quantity_rule="ext_wall_lm + int_wall_lm (deduct openings manually)"))

    # Architraves (door/window perimeters)
    door_count  = sum(int(safe_float(d.get("qty")) or 1) for d in doors)
    window_count = sum(int(safe_float(w.get("qty")) or 1) for w in windows)
    if door_count > 0:
        arch_lm = round(door_count * 2 * (0.9 + 2.1), 1)  # 2 sides × (width+height)
        quantities.append(_q("finishes", "architrave_door", "lm",
                             arch_lm, "lm",
                             f"derived: {door_count} doors × 2 sides × 900×2100",
                             "LOW",
                             assumption="Standard 900×2100 door assumed",
                             manual_review=True,
                             quantity_rule=f"{door_count} doors × 2 sides × (0.9 + 2.1) m"))
    if window_count > 0:
        arch_lm_win = round(window_count * 2 * (1.2 + 1.2), 1)  # typical window
        quantities.append(_q("finishes", "architrave_window", "lm",
                             arch_lm_win, "lm",
                             f"derived: {window_count} windows × 2 sides × typical 1200×1200",
                             "LOW",
                             assumption="Typical 1200×1200 window assumed",
                             manual_review=True,
                             quantity_rule=f"{window_count} windows × 2 sides × (1.2 + 1.2) m"))

    # Paint — provisional areas
    if ext_wall_area > 0:
        quantities.append(_q("finishes", "paint_external", "m2",
                             ext_wall_area, "m2",
                             "derived: external wall area", "LOW",
                             assumption="External paint area = external wall area. Confirm coats.",
                             manual_review=True,
                             quantity_rule="ext_wall_area (confirm coats and coverage rate)"))
    if ceiling_area > 0 or int_wall_area > 0:
        internal_paint = (ceiling_area or 0) + (int_wall_area or 0)
        quantities.append(_q("finishes", "paint_internal", "m2",
                             round(internal_paint, 1), "m2",
                             "derived: ceiling_area + internal_wall_area", "LOW",
                             assumption="Internal paint = ceilings + internal wall faces",
                             manual_review=True,
                             quantity_rule="ceiling_area + int_wall_area (confirm coats)"))

    # ── 6. DOORS & WINDOWS ────────────────────────────────────────────────────
    if doors:
        door_count = sum(int(safe_float(d.get("qty")) or 1) for d in doors)
        quantities.append(_q("doors", "door_count", "all",
                             float(door_count), "nr",
                             merged.get("audit", {}).get("doors_source", "dwg/pdf"),
                             "HIGH"))
        # Per-type breakdown
        type_counts: dict[str, int] = {}
        for d in doors:
            dtype = str(d.get("type") or d.get("mark") or "unknown").strip()
            type_counts[dtype] = type_counts.get(dtype, 0) + int(safe_float(d.get("qty")) or 1)
        for dtype, cnt in type_counts.items():
            if dtype != "unknown":
                quantities.append(_q("doors", "door_count", dtype,
                                     float(cnt), "nr",
                                     "dwg/pdf_schedule", "MEDIUM"))

        # Door hardware: locksets, hinges
        quantities.append(_q("doors", "door_lockset", "nr",
                             float(door_count), "nr",
                             f"derived: {door_count} doors × 1 lockset",
                             "LOW",
                             assumption="1 lockset per door",
                             manual_review=True,
                             quantity_rule=f"{door_count} doors × 1"))
        quantities.append(_q("doors", "door_hinge_set", "nr",
                             float(door_count), "nr",
                             f"derived: {door_count} doors × 1 hinge set",
                             "LOW",
                             assumption="1 hinge set (pair) per door",
                             manual_review=True,
                             quantity_rule=f"{door_count} doors × 1"))
        # Door stops
        quantities.append(_q("doors", "door_stop", "nr",
                             float(door_count), "nr",
                             f"derived: {door_count} doors", "LOW",
                             assumption="1 door stop per door",
                             manual_review=True,
                             quantity_rule=f"{door_count} doors × 1"))

    if windows:
        win_count = sum(int(safe_float(w.get("qty")) or 1) for w in windows)
        quantities.append(_q("windows", "window_count", "all",
                             float(win_count), "nr",
                             merged.get("audit", {}).get("windows_source", "pdf/dwg"),
                             "HIGH"))
        # Window hardware: flyscreens, latches
        quantities.append(_q("windows", "flyscreen", "nr",
                             float(win_count), "nr",
                             f"derived: {win_count} windows × 1 flyscreen",
                             "LOW",
                             assumption="1 flyscreen per window",
                             manual_review=True,
                             quantity_rule=f"{win_count} windows × 1"))

    # ── 7. STAIRS ────────────────────────────────────────────────────────────
    if stairs:
        for stair in stairs:
            flights = int(safe_float(stair.get("flights") or stair.get("qty") or 1) or 1)
            steps   = int(safe_float(stair.get("steps") or stair.get("step_count") or 0) or 0)
            stype   = str(stair.get("type") or stair.get("material") or "Timber").strip()
            src     = str(stair.get("source") or "dwg/pdf")
            conf    = str(stair.get("confidence") or "MEDIUM")

            quantities.append(_q("stairs", "stair_flight", stype,
                                 float(flights), "nr", src, conf))

            if steps > 0:
                quantities.append(_q("stairs", "stair_riser", stype,
                                     float(steps * flights), "nr",
                                     f"derived: {steps} steps × {flights} flight",
                                     "MEDIUM"))
                quantities.append(_q("stairs", "stair_tread", stype,
                                     float(steps * flights), "nr",
                                     f"derived: {steps} steps × {flights} flight",
                                     "MEDIUM"))
            else:
                quantities.append(_q("stairs", "stair_riser", stype,
                                     None, "nr", "none", "LOW",
                                     manual_review=True,
                                     assumption="Step count unknown — manual count required"))

            # Balustrade / handrail — estimate from flights
            quantities.append(_q("stairs", "balustrade", "lm",
                                 None, "lm", "none", "LOW",
                                 manual_review=True,
                                 assumption="Balustrade lm pending — measure from drawings"))
            quantities.append(_q("stairs", "handrail", "lm",
                                 None, "lm", "none", "LOW",
                                 manual_review=True,
                                 assumption="Handrail lm pending — measure from drawings"))

    # ── 8. SERVICES (provisional — always included when floor area known) ─────
    if floor_area > 0:
        wet_area_rooms = [r for r in rooms
                          if any(k in str(r.get("name") or "").lower()
                                 for k in ["bath", "toilet", "wc", "laundry", "kitchen",
                                           "amenities", "ablution", "shower"])]
        has_wet_areas = len(wet_area_rooms) > 0 or any(
            any(k in str(f.get("room_name") or f.get("name") or "").lower()
                for k in ["bath", "toilet", "kitchen", "laundry"])
            for f in finishes
        )
        wet_count_note = (
            f"Wet area waterproofing for {len(wet_area_rooms)} wet area(s)"
            if wet_area_rooms
            else "Wet area waterproofing — count from room schedule"
        )
        quantities.append(_q("services", "wet_area_waterproofing", "provisional",
                             None, "nr", "room_schedule", "LOW",
                             manual_review=True,
                             assumption=wet_count_note))
        quantities.append(_q("services", "sanitary_fixtures", "provisional",
                             None, "nr", "room_schedule", "LOW",
                             manual_review=True,
                             assumption="Sanitary fixtures (basins, toilets, shower trays) — count from schedule"))
        quantities.append(_q("services", "builders_work_plumbing", "provisional",
                             None, "item", "implied_scope", "LOW",
                             manual_review=True,
                             assumption="Plumbing builder's work (penetrations, chases) — provisional allowance"))
        quantities.append(_q("services", "builders_work_electrical", "provisional",
                             None, "item", "implied_scope", "LOW",
                             manual_review=True,
                             assumption="Electrical builder's work (conduits, panels, light points) — provisional"))

    # ── 9. EXTERNAL WORKS (provisional) ───────────────────────────────────────
    if verandah_area > 0:
        quantities.append(_q("external", "verandah_decking", "m2",
                             verandah_area, "m2", "dwg_geometry", "MEDIUM",
                             assumption="Verandah decking area from DWG"))
        quantities.append(_q("external", "verandah_handrail", "lm",
                             None, "lm", "none", "LOW",
                             manual_review=True,
                             assumption="Verandah handrail lm — measure from drawings"))

    if floor_area > 0:
        quantities.append(_q("external", "site_preparation", "provisional",
                             None, "item", "implied_scope", "LOW",
                             manual_review=True,
                             assumption="Site preparation (clearing, levelling) — provisional allowance"))

    # ── 10. ROOMS (for schedule) ───────────────────────────────────────────────
    for room in rooms:
        rname = str(room.get("name") or "unknown").strip()
        rarea = safe_float(room.get("area_m2"))
        rsrc  = str(room.get("source") or "dwg/ifc")
        if rarea > 0:
            quantities.append(_q("rooms", "room_area", rname,
                                 rarea, "m2", rsrc, "HIGH"))

    # ── Build completeness report ─────────────────────────────────────────────
    completeness = _build_completeness(
        quantities, geo, struct, doors, windows, stairs, rooms, finishes,
    )

    model = {
        "project_mode":  classification.get("project_mode", "custom_project"),
        "matched_model": classification.get("matched_model_code"),
        "quantities":    quantities,
        "completeness":  completeness,
    }
    log.info(
        "Quantity model built: %d entries  mode=%s  model=%s  "
        "packages_detected=%d/%d",
        len(quantities),
        model["project_mode"],
        model["matched_model"] or "—",
        sum(1 for p in completeness.values() if p["detected"]),
        len(completeness),
    )
    return model


def save_quantity_model(model: dict, project_name: str) -> Path:
    """Save quantity model to output/json/{project_name}_quantities.json."""
    OUTPUT_JSON.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_JSON / f"{project_name}_quantities.json"
    with open(str(out_path), "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, default=str)
    log.info("Quantity model saved: %s", out_path.name)
    return out_path


# ── Completeness report ────────────────────────────────────────────────────────

def _build_completeness(
    quantities: list[dict],
    geo: dict, struct: dict,
    doors: list, windows: list, stairs: list, rooms: list, finishes: list,
) -> dict:
    """
    Build a package-level completeness summary.

    Returns dict keyed by package name:
      { "detected": bool, "items": int, "notes": str }
    """
    groups = {q["item_group"] for q in quantities}

    def pkg(name: str, detected: bool, items: int, notes: str) -> dict:
        return {"detected": detected, "items": items, "notes": notes}

    floor_area = safe_float(geo.get("total_floor_area_m2"))
    roof_area  = safe_float(geo.get("roof_area_m2"))

    structure_items = sum(1 for q in quantities
                          if q["item_group"] in ("walls", "floor", "roof")
                          and "frame" in q["element_type"] or "panel" in q["element_type"]
                          or "joist" in q["element_type"] or "truss" in q["element_type"])

    return {
        "structure": pkg(
            "structure",
            bool(safe_float(struct.get("wall_frame_lm")) or safe_float(geo.get("external_wall_length_m"))),
            sum(1 for q in quantities if q["item_group"] in ("walls",) and "frame" in q["element_type"]),
            "Wall frame lm from BOM/IFC or derived from DWG wall lengths",
        ),
        "roof": pkg(
            "roof",
            roof_area > 0,
            sum(1 for q in quantities if q["item_group"] == "roof"),
            f"Roof area {roof_area:.1f}m². Derived: cladding, sisalation, battens, flashings." if roof_area > 0 else "No roof area found",
        ),
        "openings": pkg(
            "openings",
            bool(doors or windows),
            sum(1 for q in quantities if q["item_group"] in ("doors", "windows")),
            f"{len(doors)} door(s), {len(windows)} window(s) detected",
        ),
        "linings": pkg(
            "linings",
            floor_area > 0,
            sum(1 for q in quantities
                if q["element_type"] in ("fc_sheet_ceiling", "fc_sheet_internal",
                                          "fc_sheet_external", "ceiling_area")),
            "FC sheet counts derived from wall and ceiling areas",
        ),
        "finishes": pkg(
            "finishes",
            bool(finishes) or floor_area > 0,
            sum(1 for q in quantities if q["item_group"] in ("finishes",)),
            f"Skirting, architraves, paint derived from geometry. Finish schedule: {len(finishes)} items.",
        ),
        "services": pkg(
            "services",
            bool(rooms) or floor_area > 0,
            sum(1 for q in quantities if q["item_group"] == "services"),
            "Provisional wet area / electrical / plumbing allowances",
        ),
        "stairs": pkg(
            "stairs",
            bool(stairs),
            sum(1 for q in quantities if q["item_group"] == "stairs"),
            f"{len(stairs)} stair flight(s) detected" if stairs else "No stair evidence found",
        ),
        "external": pkg(
            "external",
            safe_float(geo.get("verandah_area_m2")) > 0,
            sum(1 for q in quantities if q["item_group"] == "external"),
            "Verandah/deck areas from DWG. Site works provisional.",
        ),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_basis(source_evidence: str, quantity, manual_review: bool) -> str:
    """Infer quantity_basis from source evidence and flags."""
    if quantity is None or manual_review:
        return "manual_review"
    src = str(source_evidence).lower()
    if src.startswith("derived:") or "derived" in src:
        return "derived"
    if src in ("implied_scope", "none", ""):
        return "provisional"
    return "measured"


def _q(
    item_group: str,
    element_type: str,
    subtype: str = "",
    quantity: float | None = None,
    unit: str = "",
    source_evidence: str = "",
    confidence: str = "MEDIUM",
    assumption: str = "",
    manual_review: bool = False,
    quantity_basis: str = "",
    quantity_rule: str = "",
) -> dict:
    return {
        "item_group":      item_group,
        "element_type":    element_type,
        "subtype":         subtype,
        "quantity":        quantity,
        "unit":            unit,
        "source_evidence": source_evidence,
        "confidence":      confidence,
        "assumption":      assumption,
        "manual_review":   manual_review or quantity is None,
        "quantity_basis":  quantity_basis or _infer_basis(source_evidence, quantity, bool(manual_review)),
        "quantity_rule":   quantity_rule or assumption,
    }


def _sum_finishes(finishes: list[dict], finish_type: str, material: str) -> float:
    """Sum finish areas matching a given type and material keyword."""
    total = 0.0
    for f in finishes:
        ft    = str(f.get("finish_type") or f.get("type") or "").lower()
        fmat  = str(f.get("material") or f.get("finish") or "").lower()
        farea = safe_float(f.get("area_m2") or f.get("quantity"))
        if finish_type in ft and material in fmat and farea > 0:
            total += farea
    return total
