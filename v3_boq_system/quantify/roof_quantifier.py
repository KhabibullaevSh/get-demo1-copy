"""
roof_quantifier.py — Roof structure + cladding + drainage assembly quantification.

Produces:
  - Structural frame rows (from FrameCAD/IFC)
  - Cladding / sheeting
  - Ridge caps + fixings
  - Eaves: fascia, fascia clips, birdproof foam
  - Gutters: run, joiners, stop ends, drop outlets
  - Downpipes: pipe, elbows, brackets
  - Valley / barge flashings
  - Sisalation
  - Battens (from BOM preferred)
  - Insulation
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.assemblies.assembly_engine import apply_all_roof_assemblies
from v3_boq_system.normalize.element_model import ProjectElementModel, StructuralFrameElement

log = logging.getLogger("boq.v3.roof")


def _row(
    package, item_name, unit, quantity, status, basis, evidence, rule,
    confidence, manual_review=False, notes="", item_code="",
) -> dict:
    return {
        "item_name": item_name, "item_code": item_code,
        "unit": unit, "quantity": quantity, "package": package,
        "quantity_status": status, "quantity_basis": basis,
        "source_evidence": evidence, "derivation_rule": rule,
        "confidence": confidence, "manual_review": manual_review, "notes": notes,
    }


def _downpipe_count(eaves_lm: float, config: dict) -> int:
    min_dp  = config.get("roof", {}).get("min_downpipes", 2)
    spacing = config.get("roof", {}).get("downpipe_spacing_m", 10.0)
    # Round up to nearest even number so every gutter run gets at least 1 outlet
    raw = math.ceil(eaves_lm / spacing)
    return max(min_dp, raw + (raw % 2))   # ensure even count


def _estimate_ridge_lm(
    floor_area: float,
    floor_perim: float,
    roof_type: str,
) -> tuple[float, str]:
    """
    Estimate ridge length from floor plan dimensions when not directly available.

    Returns (ridge_lm, derivation_note).
    """
    if floor_area <= 0 or floor_perim <= 0:
        return 0.0, "no floor geometry for ridge estimate"

    half_perim = floor_perim / 2          # L + W
    discriminant = half_perim ** 2 - 4 * floor_area
    if discriminant < 0:
        # Floor plan is near-square; use quarter-perimeter as fallback
        return round(floor_perim * 0.15, 1), "floor_plan≈square: ridge≈0.15×perimeter"

    root = math.sqrt(discriminant)
    long_dim  = round((half_perim + root) / 2, 1)   # longer dimension (L)
    short_dim = round((half_perim - root) / 2, 1)   # shorter dimension (W)

    if roof_type in ("gable", "shed"):
        ridge = long_dim
        note = f"gable/shed: ridge≈long_dim={long_dim:.1f} m (floor L={long_dim:.1f}, W={short_dim:.1f})"
    else:
        # Hip roof: ridge runs between the two hip points ≈ L − W
        ridge = max(0.0, round(long_dim - short_dim, 1))
        note = (
            f"hip: ridge≈L−W={long_dim:.1f}−{short_dim:.1f}={ridge:.1f} m "
            f"(floor area={floor_area:.1f} m², perim={floor_perim:.1f} m)"
        )

    return ridge, note


def quantify_roof(
    model:          ProjectElementModel,
    config:         dict,
    assembly_rules: dict,
) -> list[dict]:
    rows: list[dict] = []
    roof_cfg  = config.get("roof", {})
    struct_cfg = config.get("structural", {})

    primary_roof = model.primary_roof()
    if primary_roof is None:
        log.info("No roof element — skipping roof quantifier")
        return rows

    roof_area   = primary_roof.area_m2
    roof_perim  = primary_roof.perimeter_m
    eaves_lm    = primary_roof.eaves_length_m  or roof_perim
    ridge_lm    = primary_roof.ridge_length_m
    valley_lm   = primary_roof.valley_length_m
    barge_lm_v  = primary_roof.barge_length_m
    apron_lm    = primary_roof.apron_length_m
    roof_type   = primary_roof.roof_type or roof_cfg.get("roof_type", "hip")
    roof_src    = primary_roof.source
    roof_conf   = primary_roof.confidence

    # ── Estimate ridge length when not directly measured ─────────────────────
    ridge_derived = False
    if ridge_lm == 0 and roof_area > 0:
        floor_area_est  = sum(f.area_m2 for f in model.floors)
        floor_perim_est = sum(f.perimeter_m for f in model.floors)
        ridge_lm, ridge_note = _estimate_ridge_lm(floor_area_est, floor_perim_est, roof_type)
        ridge_derived = ridge_lm > 0
        if ridge_derived:
            log.info("Ridge length estimated from floor plan: %.1f m (%s)", ridge_lm, ridge_note)
            rows.append(_row(
                "roof_ridge_accessories",
                "Ridge Length (estimated from floor plan)",
                "lm", ridge_lm,
                "inferred",
                f"derived from floor plan: {ridge_note}",
                f"floor_area={floor_area_est:.1f} m², floor_perim={floor_perim_est:.1f} m",
                ridge_note,
                "LOW",
                manual_review=True,
                notes=(
                    f"Ridge length not in drawings. Estimated from floor plan dimensions "
                    f"assuming {roof_type} roof. Verify from roof plan or structural drawings."
                ),
            ))

    # ── Structural frame items (from model.structural_frames) ─────────────────
    frame_label_map = {
        "roof_panel":  "Roof Panel Frame (purlin / roof panel 89S41)",
        "roof_truss":  "Roof Truss Frame (truss chord + web 89S41)",
        "wall_frame":  "Wall Frame — all wall members 89S41",
        "lintel":      "Wall Frame — lintel 150×32×0.95",
        "wall_strap":  "Wall Frame — diagonal strap 32×0.95",
        "verandah_frame": "Verandah Frame — 89S41",
        "steel_shs":   "Structural Steel Post (75×75×4 SHS)",
    }
    for sf in model.structural_frames:
        if sf.frame_type == "roof_batten":
            # Roof battens are emitted in the dedicated batten section below
            # with per-family stock-length breakdown.  Skip here to avoid duplicate.
            continue
        label = frame_label_map.get(sf.frame_type, f"Structural Frame — {sf.frame_type}")
        extra_note = ""
        if sf.frame_type == "roof_truss":
            if sf.source == "ifc_model":
                extra_note = (
                    " QA NOTE: IFC truss lm may include purlins/rafters classified as trusses. "
                    "Cross-check against FrameCAD manufacturing summary — verify member classification."
                )
            elif sf.source == "framecad_bom":
                extra_note = (
                    " QA NOTE: FrameCAD BOM 'Roof Trusses' tab total includes all roof structural "
                    "members (truss chords, webs, purlin/rafter runs). This is the correct "
                    "procurement quantity. An assembled-frame-count comparison will show a lower "
                    "lm figure — the difference is expected and not a data error."
                )
        rows.append(_row(
            "structural_frame", label,
            "lm", round(sf.total_lm, 2),
            "measured", f"direct from {sf.source}",
            f"{sf.source}: {sf.source_reference}",
            "direct from source",
            sf.confidence,
            notes=extra_note.strip(),
        ))

    # BOM verification total
    bom_total = next((sf.total_lm for sf in model.structural_frames if sf.frame_type == "steel_shs"), 0)
    bom_totals_sum = sum(
        sf.total_lm for sf in model.structural_frames
        if sf.frame_type in ("roof_panel","roof_truss","wall_frame")
    )
    # NOTE: Structural fixings & connectors are now generated by
    # structural_fixings_quantifier.py (grouped rows from member density rules).
    # The single PLACEHOLDER row has been removed from this module.

    # Post/Column count from DXF
    post_count = next(
        (e.quantity for e in model.openings if "post" in e.element_id.lower()), 0
    )
    if not post_count:
        # check geometry post count from raw model (passed via element_builder)
        pass   # element_builder puts posts in structural_frames if available

    # ── Roof battens (FrameCAD preferred, derived fallback) ──────────────────
    batten_frames = [sf for sf in model.structural_frames if "batten" in sf.frame_type.lower()]
    if batten_frames:
        for bf in batten_frames:
            total_nr = sum(e.get("qty", 0) for e in bf.member_entries) if bf.member_entries else 0

            # Split entries by grade: ≥35mm = roof top-hat battens, <35mm = ceiling/wall battens.
            # FrameCAD uses different grades for different zones — this split is inferred from
            # grade alone (source text does not tag zones explicitly).
            roof_entries  = [e for e in (bf.member_entries or [])
                             if isinstance(e.get("grade_mm"), (int, float)) and e["grade_mm"] >= 35]
            other_entries = [e for e in (bf.member_entries or [])
                             if e not in roof_entries]
            roof_nr   = sum(e.get("qty", 0) for e in roof_entries)
            roof_lm   = sum(e.get("total_lm", 0.0) for e in roof_entries)
            other_nr  = sum(e.get("qty", 0) for e in other_entries)
            other_lm  = sum(e.get("total_lm", 0.0) for e in other_entries)

            # Summary lm row (BOM total — all zones)
            rows.append(_row(
                "roof_battens", "Roof Battens (FRAMECAD BATTEN — all zones)",
                "lm", round(bf.total_lm, 2),
                "measured", "framecad_bom: BATTEN schedule total",
                f"framecad_bom: {bf.source_reference}",
                "direct from BOM",
                "HIGH",
                notes=(
                    f"BOM total {total_nr} pieces ({bf.total_lm:.1f} lm) — all batten zones. "
                    f"Grade split: ≥35mm (roof top-hat) = {roof_nr} nr / {roof_lm:.1f} lm; "
                    f"<35mm (ceiling/wall) = {other_nr} nr / {other_lm:.1f} lm. "
                    "Zone separation inferred from BOM grade — see per-family rows below."
                ) if bf.member_entries else "",
            ))

            # Per-family stock-length rows with zone classification
            if bf.member_entries:
                for entry in bf.member_entries:
                    grade    = entry.get("grade_mm", "")
                    qty      = entry.get("qty", 0)
                    length   = entry.get("length_mm", 0)
                    total_lm = entry.get("total_lm", 0)
                    is_roof  = isinstance(grade, (int, float)) and grade >= 35
                    zone_label = "Roof Top-Hat Batten" if is_roof else "Ceiling/Wall Batten"
                    conf = "MEDIUM" if is_roof else "LOW"
                    rows.append(_row(
                        "roof_battens",
                        f"{zone_label} G{grade} × {length}mm",
                        "nr", qty,
                        "measured",
                        f"framecad_bom: {qty} × {length}mm = {total_lm:.1f} lm",
                        f"framecad_bom: BATTEN {grade} {qty} {length}",
                        f"{qty} nr × {length}mm",
                        conf,
                        notes=(
                            f"{'Roof top-hat' if is_roof else 'Ceiling/wall'} batten "
                            f"(grade {grade}mm {'≥' if is_roof else '<'}35mm threshold). "
                            f"{length}mm stock length. Total: {total_lm:.1f} lm. "
                            "Zone inferred from grade — confirm against FrameCAD batten schedule."
                        ),
                    ))
    else:
        batten_spacing = struct_cfg.get("roof_batten_spacing_mm", 900) / 1000
        if roof_area > 0:
            derived_batten_lm = round(roof_area / batten_spacing, 1)
            rows.append(_row(
                "roof_battens", "Roof Battens (derived)",
                "lm", derived_batten_lm,
                "calculated",
                f"roof_area / ({batten_spacing * 1000:.0f} mm spacing)",
                f"derived from roof_area={roof_area:.2f} m²",
                f"{roof_area:.2f} / {batten_spacing}",
                "MEDIUM",
            ))

    # ── Sisalation / sarking ──────────────────────────────────────────────────
    sisa_roll_m2 = roof_cfg.get("sisalation_roll_m2", 73.0)
    if roof_area > 0:
        sisa_rolls = math.ceil(roof_area / sisa_roll_m2)
        rows.append(_row(
            "roof_cladding", "Sisalation / Sarking",
            "rolls", sisa_rolls,
            "calculated",
            f"ceil(roof_area / {sisa_roll_m2} m²/roll)",
            f"{roof_src}: roof_area={roof_area:.2f} m²",
            f"ceil({roof_area:.2f} / {sisa_roll_m2})",
            roof_conf,
        ))

    # ── Assembly-based items (cladding, ridge, eaves, drainage) ──────────────
    # For hip roofs: derive the actual rafter run (horizontal eave-to-ridge distance)
    # from the corrected ridge length and eaves perimeter:
    #   L + W = eaves/2,  L − W = ridge  →  W = (eaves/2 − ridge)/2,  run = W/2
    # This gives the correct stock-length selection (vs. area/eaves which is a weighted average).
    rafter_run_m: float | None = None
    if roof_type == "hip" and ridge_lm > 0 and eaves_lm > 0:
        W_roof = (eaves_lm / 2.0 - ridge_lm) / 2.0
        if W_roof > 0:
            rafter_run_m = round(W_roof / 2.0, 2)
            log.info(
                "Hip rafter run derived: W_roof=%.2f m → rafter_run=%.2f m "
                "(ridge=%.1f lm, eaves=%.1f lm)",
                W_roof, rafter_run_m, ridge_lm, eaves_lm,
            )

    # ── Hip flashings (requires confirmed pitch from PDF schedule) ──────────
    # Generated only when pitch_deg is set on the roof element (i.e. recovered
    # from FrameCAD Truss Design Summary via pdf_schedule_extractor).
    if (roof_type == "hip"
            and rafter_run_m and rafter_run_m > 0
            and primary_roof.pitch_deg > 0.0):
        pitch_deg = primary_roof.pitch_deg
        pitch_rad = math.radians(pitch_deg)
        rise       = rafter_run_m * math.tan(pitch_rad)
        hip_plan   = rafter_run_m * math.sqrt(2)      # plan diagonal to eave corner
        hip_slope  = round(math.sqrt(hip_plan ** 2 + rise ** 2), 2)
        hip_lm_val = round(hip_slope * 4, 1)           # 4 hips on a standard hip roof
        hip_screws = math.ceil(hip_lm_val / 0.3)

        hip_evidence = (
            f"framecad_layout: pitch={pitch_deg:.1f}° (FrameCAD Truss Design Summary); "
            f"dxf_geometry: rafter_run={rafter_run_m:.2f} m → "
            f"hip_plan={hip_plan:.2f} m, rise={rise:.3f} m → "
            f"hip_slope={hip_slope:.2f} m × 4 hips = {hip_lm_val:.1f} lm"
        )
        rows.append(_row(
            "roof_flashings", "Hip Capping (metal, pre-formed)",
            "lm", hip_lm_val,
            "calculated",
            "4 × sqrt((rafter_run×√2)² + (rafter_run×tan(pitch))²)",
            hip_evidence,
            f"4×sqrt(({rafter_run_m:.2f}×√2)²+({rise:.3f})²) = 4×{hip_slope:.2f} = {hip_lm_val:.1f} lm",
            "MEDIUM",
            notes=(
                f"Hip capping over all 4 hip rafter runs. "
                f"Pitch={pitch_deg:.1f}° confirmed from FrameCAD Truss Design Summary. "
                f"Rafter run={rafter_run_m:.2f} m from DXF roof plan geometry. "
                f"Hip slope per hip={hip_slope:.2f} m × 4 = {hip_lm_val:.1f} lm."
            ),
        ))
        rows.append(_row(
            "roof_flashings", "Hip Cap Fixing Screws",
            "nr", hip_screws,
            "calculated",
            "ceil(hip_lm / 0.3)",
            hip_evidence,
            f"ceil({hip_lm_val:.1f} / 0.3) = {hip_screws}",
            "MEDIUM",
            notes="1 screw per 300 mm of hip capping.",
        ))
        log.info(
            "Hip flashings added: pitch=%.1f°, rafter_run=%.2f m, "
            "hip_slope=%.2f m, hip_lm=%.1f lm, screws=%d",
            pitch_deg, rafter_run_m, hip_slope, hip_lm_val, hip_screws,
        )

    dp_count = _downpipe_count(eaves_lm, config)
    assembly_rows = apply_all_roof_assemblies(
        roof_area_m2   = roof_area,
        eaves_lm       = eaves_lm,
        ridge_lm       = ridge_lm,
        barge_lm       = barge_lm_v,
        valley_lm      = valley_lm,
        downpipe_count = dp_count,
        rules          = assembly_rules,
        evidence_prefix= roof_src,
        roof_confidence= roof_conf,
        apron_lm       = apron_lm,
        rafter_run_m   = rafter_run_m,
    )
    # Remap package names from assembly rule names to friendly sections
    _pkg_remap = {
        "roof_cladding":  "roof_cladding",
        "roof_ridge":     "roof_ridge_accessories",
        "roof_eaves":     "roof_eaves_drainage",
        "roof_drainage":  "roof_eaves_drainage",
        "roof_valley":    "roof_flashings",
        "roof_barge":     "roof_flashings",
        "roof_hip":       "roof_flashings",
    }
    for ar in assembly_rows:
        ar["package"] = _pkg_remap.get(ar.get("package",""), ar.get("package",""))
    rows += assembly_rows

    # ── Insulation ────────────────────────────────────────────────────────────
    # Ceiling area for separate ceiling-level batt row
    ceil_elements = model.ceilings
    ceil_area_ins = round(sum(c.area_m2 for c in ceil_elements), 2) if ceil_elements else 0.0

    if roof_area > 0:
        # Split: roof cavity batts (between sarking and roof sheet) vs ceiling batts
        rows.append(_row(
            "insulation",
            "Insulation Batts — Roof Cavity (R2.5, between rafters)",
            "m2", round(roof_area, 2),
            "calculated",
            "= roof_area_m2 (batts in rafter cavity above ceiling)",
            f"{roof_src}: roof_area={roof_area:.2f} m²",
            "= roof_area",
            roof_conf,
            notes="Roof cavity batt insulation above ceiling plane. R-value to be confirmed by ESD consultant.",
        ))
        # Reflective foil / sisalation underlay beneath roof sheeting
        sark_area = round(roof_area * 1.05, 2)
        rows.append(_row(
            "insulation",
            "Reflective Foil / Sisalation Underlay (roof)",
            "m2", sark_area,
            "calculated",
            f"roof_area({roof_area:.2f}) × 1.05 overlap allowance",
            f"{roof_src}: roof_area={roof_area:.2f} m²",
            f"{roof_area:.2f} × 1.05",
            roof_conf,
            notes="Reflective foil sarking / sisalation beneath roof sheeting. 5% for laps.",
        ))
        # Sisalation lap tape
        import math as _math
        tape_rolls = _math.ceil(roof_area / 50)
        rows.append(_row(
            "insulation",
            "Sisalation Lap Tape (50mm × 30m rolls)",
            "rolls", tape_rolls,
            "calculated",
            f"ceil(roof_area({roof_area:.2f}) / 50 m² per roll)",
            f"{roof_src}: roof_area={roof_area:.2f} m²",
            f"ceil({roof_area:.2f}/50)",
            "LOW",
            notes="Lap tape for sisalation overlaps. 1 roll per ~50 m².",
        ))

    if ceil_area_ins > 0:
        rows.append(_row(
            "insulation",
            "Insulation Batts — Ceiling (R4.0, between ceiling battens)",
            "m2", ceil_area_ins,
            "calculated",
            "= ceiling_area_m2 (batts between ceiling joists/battens)",
            f"dxf_geometry: ceiling_area={ceil_area_ins:.2f} m² (CEILING HATCH layer)",
            "= ceiling_area",
            "HIGH" if (ceil_elements and ceil_elements[0].confidence == "HIGH") else "MEDIUM",
            notes=(
                f"Ceiling-level batt insulation: {ceil_area_ins:.2f} m² from DXF CEILING HATCH. "
                "R-value to be confirmed by ESD consultant. "
                "Note: 15.8 m² partial ceiling — area may differ from full floor area."
            ),
        ))

    ext_walls = [w for w in model.walls if w.wall_type == "external"]
    if ext_walls:
        ext_lm  = sum(w.length_m for w in ext_walls)
        ext_h   = max(w.height_m for w in ext_walls)
        ins_area = round(ext_lm * ext_h, 2)
        rows.append(_row(
            "insulation",
            "Insulation Batts — External Wall (R2.0)",
            "m2", ins_area,
            "calculated",
            "ext_wall_perimeter × wall_height",
            f"dxf_geometry: ext_wall_lm={ext_lm:.2f} × h={ext_h:.1f}",
            f"{ext_lm:.2f} × {ext_h:.1f}",
            "MEDIUM",
            notes="Gross wall area; deduct large openings if required. R-value to confirm with ESD.",
        ))

    # Internal wall acoustic insulation
    int_walls_ins = [w for w in model.walls if w.wall_type == "internal"]
    if int_walls_ins:
        int_lm_ins = sum(w.length_m for w in int_walls_ins)
        int_h_ins  = max(w.height_m for w in int_walls_ins)
        int_ins_area = round(int_lm_ins * int_h_ins, 2)
        rows.append(_row(
            "insulation",
            "Insulation Batts — Internal Wall (acoustic, R1.5)",
            "m2", int_ins_area,
            "calculated",
            "int_wall_lm × wall_height (single face area per partition)",
            f"dxf_geometry: int_wall_lm={int_lm_ins:.2f} × h={int_h_ins:.1f}",
            f"{int_lm_ins:.2f} × {int_h_ins:.1f}",
            int_walls_ins[0].confidence if int_walls_ins else "LOW",
            manual_review=True,
            notes=(
                "Acoustic batt insulation in internal partition stud cavities. "
                "Area based on single face per wall run (not doubled). "
                "Verify with architectural acoustic requirements."
            ),
        ))

    # ── Insulation batts total summary ─────────────────────────────────────────
    # Sum all batt rows (exclude sarking/tape which are separate products).
    _batt_rows = [r for r in rows if "Insulation Batts" in r.get("item_name", "")]
    if _batt_rows:
        import math as _math
        _total_batt_m2 = round(sum(r["quantity"] for r in _batt_rows), 2)
        _batt_parts = []
        for r in _batt_rows:
            _label = r["item_name"].replace("Insulation Batts — ", "").split("(")[0].strip()
            _batt_parts.append(f"{_label}({r['quantity']:.1f}m²)")
        rows.append(_row(
            "insulation",
            "Insulation Batts — Total Supply (all zones)",
            "m2", _total_batt_m2,
            "calculated",
            "sum of all insulation batt areas: roof cavity + ceiling + ext wall + int wall",
            f"derived: {' + '.join(_batt_parts)} = {_total_batt_m2}m²",
            "sum(batt_area per zone)",
            "MEDIUM",
            notes=(
                "Total insulation batt supply area across all zones. "
                "Includes roof cavity, ceiling, external wall, and internal wall batts. "
                "Each zone has a different R-value and batt thickness — order as separate line items. "
                "Use this total for preliminary material cost estimate only."
            ),
        ))

    return rows
