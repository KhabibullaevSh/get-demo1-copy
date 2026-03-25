"""
floor_system_quantifier.py — Floor system quantification.

Priority order (highest first):
  1. FrameCAD manufacturing summary (floor panel tabs)
  2. IFC floor joist members
  3. Structural panel schedule (from PDF)
  4. Derived from floor area + config spacing (lowest confidence)

Produces both:
  A. Engineering totals (lm summaries for QA)
  B. Procurement assemblies (panel sets, bearer pairs, joists, sheeting, fixings)

CRITICAL: No quantities from BOQ reference files.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from v3_boq_system.normalize.element_model import FloorElement, FloorSystemElement, ProjectElementModel

log = logging.getLogger("boq.v3.floor_system")


def _main_building_dims(
    floor_area: float,
    floor_perim: float,
    ver_area: float,
    ver_perim: float,
) -> tuple[float, float, float, str]:
    """
    Derive main-building span (short) and run (long) from DXF geometry.

    Returns (span_short_m, span_long_m, main_floor_area_m2, derivation_note).
    Returns (0, 0, floor_area, note) when geometry is underdetermined.

    Method (all inputs from DXF measurements):
      1. Solve L + W = floor_perim/2  and  L × W = floor_area  → total L, W.
      2. Solve ver_W + ver_d = ver_perim/2  and  ver_W × ver_d = ver_area
         → two solutions; pick the one where ver_W = total_W (full-width verandah).
         LABEL: engineering inference — DXF confirms area+perimeter consistent with
         full-width attachment; replacing document = floor plan with room labels.
      3. main_L = total_L − ver_depth;  main_W = total_W.
      4. span_short = min(main_L, main_W);  span_long = max(main_L, main_W).
    """
    if floor_area <= 0 or floor_perim <= 0:
        return 0.0, 0.0, floor_area, "no floor geometry"

    # Step 1: total building L and W
    half_p = floor_perim / 2
    disc = half_p ** 2 - 4 * floor_area
    if disc < 0:
        return 0.0, 0.0, floor_area, "floor plan near-square — L/W underdetermined"
    import math as _math
    total_L = round((_math.sqrt(disc) + half_p) / 2, 1)
    total_W = round((half_p - _math.sqrt(disc)) / 2, 1)
    if total_W <= 0:
        return 0.0, 0.0, floor_area, "W ≤ 0 — geometry inconsistent"

    # Step 2: verandah depth assuming full-width attachment (engineering inference)
    ver_depth = 0.0
    if ver_area > 0 and total_W > 0:
        candidate_depth = round(ver_area / total_W, 2)
        # Verify: candidate_depth × total_W ≈ ver_area
        if ver_area > 0 and abs(candidate_depth * total_W - ver_area) / ver_area < 0.05:
            ver_depth = candidate_depth

    main_floor_area = round(floor_area - ver_area, 2) if ver_depth > 0 else floor_area
    main_L          = round(total_L - ver_depth, 1)  if ver_depth > 0 else total_L
    main_W          = total_W

    span_short = min(main_L, main_W)
    span_long  = max(main_L, main_W)

    note = (
        f"DXF: floor={floor_area:.1f}m² perim={floor_perim:.1f}m → L={total_L:.1f}m W={total_W:.1f}m; "
        f"verandah={ver_area:.1f}m² depth={ver_depth:.1f}m (engineering inference: full-width attachment); "
        f"main={main_floor_area:.1f}m² ({main_L:.1f}m×{main_W:.1f}m); span={span_short:.1f}m run={span_long:.1f}m"
    )
    return span_short, span_long, main_floor_area, note

# ── BOQ row builder ───────────────────────────────────────────────────────────

def _row(
    package:         str,
    item_name:       str,
    unit:            str,
    quantity:        Any,
    status:          str,
    basis:           str,
    evidence:        str,
    rule:            str,
    confidence:      str,
    manual_review:   bool = False,
    notes:           str  = "",
    item_code:       str  = "",
) -> dict:
    return {
        "item_name":       item_name,
        "item_code":       item_code,
        "unit":            unit,
        "quantity":        quantity,
        "package":         package,
        "quantity_status": status,
        "quantity_basis":  basis,
        "source_evidence": evidence,
        "derivation_rule": rule,
        "confidence":      confidence,
        "manual_review":   manual_review,
        "notes":           notes,
    }


def quantify_floor_system(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    """
    Produce BOQ rows for the floor system.

    Returns a list of procurement rows.
    """
    rows: list[dict] = []
    struct_cfg = config.get("structural", {})
    lining_cfg = config.get("lining", {})

    floor_elements = model.floors
    floor_systems  = model.floor_systems
    footings       = model.footings

    if not floor_elements:
        log.info("No floor elements — skipping floor system quantifier")
        return rows

    floor_area  = sum(f.area_m2 for f in floor_elements)
    ext_perim   = sum(f.perimeter_m for f in floor_elements)

    # Derive main-building floor area (subtract verandah) and correct span/run
    # dimensions from DXF geometry.  The verandah has its own decking row in
    # external works; the main floor system should not include that area.
    ver_area  = sum(v.area_m2  for v in model.verandahs)
    ver_perim = sum(v.perimeter_m for v in model.verandahs)
    span_dxf, run_dxf, main_floor_area, dims_note = _main_building_dims(
        floor_area, ext_perim, ver_area, ver_perim,
    )
    if span_dxf > 0:
        log.info("Floor dims from DXF: span=%.1f m  run=%.1f m  main_area=%.1f m²",
                 span_dxf, run_dxf, main_floor_area)
    else:
        main_floor_area = floor_area   # fall back to total area

    # ── CASE 1: FrameCAD BOM has floor panel tabs ─────────────────────────────
    bom_floor_panels = [fs for fs in floor_systems if fs.source == "framecad_bom"]
    if bom_floor_panels:
        log.info("Floor system: FrameCAD BOM source (HIGH confidence)")
        for fp in bom_floor_panels:
            if fp.joist_count > 0 and fp.joist_length_mm > 0:
                # Per-member detail available — emit family-level procurement rows
                joist_lm = round(fp.joist_count * fp.joist_length_mm / 1000, 2)
                profile_label = fp.notes.split("Profile:")[1].split(".")[0].strip() if fp.notes and "Profile:" in fp.notes else "LGS C-section"
                rows.append(_row(
                    "floor_system",
                    f"Floor Joist — {profile_label} × {fp.joist_length_mm} mm",
                    "nr", fp.joist_count,
                    "measured", "framecad_bom: floor panel tab members",
                    f"framecad_bom: {fp.source_reference}",
                    "count from BOM member schedule",
                    "HIGH",
                    notes=(
                        f"Total joist lm: {joist_lm:.2f} lm. "
                        + (f"Load class: {fp.load_class}." if fp.load_class else "")
                    ),
                ))
                rows.append(_row(
                    "floor_system",
                    f"Floor Joist — {profile_label} (total lm)",
                    "lm", joist_lm,
                    "measured", "framecad_bom: count × length",
                    f"framecad_bom: {fp.joist_count} × {fp.joist_length_mm} mm",
                    f"{fp.joist_count} × {fp.joist_length_mm}/1000",
                    "HIGH",
                ))
            else:
                # Tab total only (no per-member detail)
                rows.append(_row(
                    "floor_system", f"Floor Panel (FrameCAD) — {fp.element_id}",
                    "lm",
                    round(fp.total_joist_lm, 2),
                    "measured", "framecad_bom: floor panel tab total",
                    f"framecad_bom: {fp.source_reference}",
                    "direct from BOM tab total",
                    "HIGH",
                    notes="FrameCAD manufacturing summary floor panel total.",
                ))
            # Bearer pairs: estimate from floor area when not in schedule
            bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000
            span = round(math.sqrt(floor_area), 1) if floor_area > 0 else 0.0
            bearer_pairs = math.ceil(span / bearer_spacing) if span > 0 else 0
            if bearer_pairs > 0:
                rows.append(_row(
                    "floor_system", "Floor Bearer (pair)",
                    "nr", bearer_pairs,
                    "calculated",
                    f"derived: floor_span / bearer_spacing = {span:.1f} / {bearer_spacing:.1f}",
                    f"framecad_bom + derived: floor_area={floor_area:.2f} m²",
                    f"ceil({span:.1f}/{bearer_spacing:.1f})",
                    "MEDIUM",
                    notes=(
                        f"Bearer pairs derived from floor area. "
                        f"Verify bearer schedule from FrameCAD engineer."
                    ),
                ))
        rows += _floor_sheeting_rows(floor_area, "framecad_bom", "HIGH", lining_cfg)
        return rows

    # ── CASE 2: IFC floor joists ──────────────────────────────────────────────
    ifc_floor_systems = [fs for fs in floor_systems if fs.source == "ifc_model"]
    if ifc_floor_systems:
        total_joist_lm = sum(fs.total_joist_lm for fs in ifc_floor_systems)
        if total_joist_lm > 0:
            log.info("Floor system: IFC source, floor_joist_lm=%.2f", total_joist_lm)
            # Estimate joist count from length and spacing
            joist_spacing = struct_cfg.get("floor_joist_spacing_mm", 450) / 1000
            joist_est_count = math.ceil(total_joist_lm / (floor_area ** 0.5)) if floor_area > 0 else 0

            rows.append(_row(
                "floor_system", "Floor Joist (LGS)",
                "lm",
                round(total_joist_lm, 2),
                "measured", "ifc_model: floor_joist classification",
                "ifc_model: IfcMember floor_joist",
                "direct from IFC",
                "HIGH",
            ))
            # Estimate bearer pairs from floor area and bearer spacing
            bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000
            bearer_pairs   = math.ceil(floor_area ** 0.5 / bearer_spacing) if floor_area > 0 else 0
            bearer_run     = round(floor_area ** 0.5, 1) if floor_area > 0 else 0.0

            if bearer_pairs > 0:
                rows.append(_row(
                    "floor_system", "Floor Bearer (pair)",
                    "nr",
                    bearer_pairs,
                    "calculated",
                    f"derived: floor_span / bearer_spacing = {bearer_run:.1f} / {bearer_spacing:.1f}",
                    f"derived from floor_area={floor_area:.2f} m²",
                    f"ceil(sqrt(floor_area) / {bearer_spacing})",
                    "LOW",
                    manual_review=True,
                    notes=f"Bearer spacing assumed {int(bearer_spacing*1000)} mm. Verify from structural drawings.",
                ))
            rows += _floor_sheeting_rows(floor_area, "ifc_model", "MEDIUM", lining_cfg)
            return rows

    # ── CASE 2.5: Config floor_panel_schedule (between IFC and area-derived) ───
    # Used when: no FrameCAD BOM tab, no IFC joists, but config specifies panel types.
    # Provides per-load-class procurement rows vs single generic "Floor Joist" row.
    config_schedule = config.get("floor_panel_schedule", [])
    if config_schedule and not bom_floor_panels and not ifc_floor_systems:
        log.info("Floor system: config floor_panel_schedule (%d entries, LOW confidence)", len(config_schedule))
        joist_spacing  = struct_cfg.get("floor_joist_spacing_mm",  450) / 1000
        bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000

        # Use DXF-derived span/run when available; fall back to sqrt(area) approximation.
        # DXF derivation subtracts verandah area (verandah has its own decking row).
        if span_dxf > 0:
            span_short = span_dxf
            span_long  = run_dxf
            area_for_calc = main_floor_area  # excludes verandah
            span_src  = f"dxf_geometry: {dims_note}"
        else:
            span_short = round(math.sqrt(floor_area), 1) if floor_area > 0 else 0.0
            span_long  = round(floor_area / span_short, 1) if span_short > 0 else 0.0
            area_for_calc = floor_area
            span_src  = f"derived: sqrt(floor_area={floor_area:.1f}m²) — square approximation"

        # Typical LGS floor cassette panel dimensions from config
        panel_w = struct_cfg.get("floor_panel_width_m", 0.6)
        panel_l = struct_cfg.get("floor_panel_length_m", 3.6)
        panel_area_each = round(panel_w * panel_l, 2)

        for entry in config_schedule:
            load  = entry.get("load_class", "unknown")
            desc  = entry.get("description", f"{load} Floor Panel")
            frac  = float(entry.get("floor_area_fraction", 1.0))
            conf  = entry.get("confidence", "LOW").upper()
            note  = entry.get("notes", "")
            src   = entry.get("source", "project_config")
            area_this = round(area_for_calc * frac, 2)
            ev = (
                f"{src}: main_floor_area={area_for_calc:.2f}m² × fraction({frac}) = {area_this:.2f}m²; "
                f"joist_spacing={int(joist_spacing*1000)}mm; bearer_spacing={int(bearer_spacing*1000)}mm; "
                f"span={span_src}"
            )

            # Panel count: area ÷ per-panel area
            panel_count = math.ceil(area_this / panel_area_each) if panel_area_each > 0 else 0
            rows.append(_row(
                "floor_system",
                f"Floor Panel — {desc} ({load})",
                "nr", panel_count,
                "inferred",
                f"{src}: main_floor_area({area_for_calc:.1f}m²) × {frac} fraction ÷ panel_area({panel_area_each}m²)",
                ev,
                f"ceil({area_this:.2f} / {panel_area_each})",
                conf,
                manual_review=True,
                notes=f"{note} Panel dimensions assumed {int(panel_w*1000)}mm × {int(panel_l*1000)}mm ({panel_area_each}m² each).",
            ))

            # Joists per panel zone
            joist_rows_across = math.ceil(span_short / joist_spacing) if span_short > 0 else 0
            joist_lm_zone     = round(joist_rows_across * span_long * frac, 1)
            rows.append(_row(
                "floor_system",
                f"Floor Joist LGS — {load} Zone",
                "lm", joist_lm_zone,
                "inferred",
                f"{src}: joist_rows({joist_rows_across}) × run({span_long:.1f}m) × fraction({frac})",
                ev,
                f"ceil(span/{joist_spacing}) × run × fraction",
                conf,
                manual_review=True,
                notes=f"{note} Joist count at {int(joist_spacing*1000)}mm spacing across {span_short:.1f}m span, {span_long:.1f}m run.",
            ))

            # Bearer pairs per zone
            bearer_pairs_zone = math.ceil(span_short / bearer_spacing * frac) if span_short > 0 else 0
            rows.append(_row(
                "floor_system",
                f"Floor Bearer (pair) — {load} Zone",
                "nr", bearer_pairs_zone,
                "inferred",
                f"{src}: ceil(span_short({span_short:.1f}m) / bearer_spacing({bearer_spacing:.1f}m) × fraction({frac}))",
                ev,
                f"ceil({span_short:.1f}/{bearer_spacing:.1f} × {frac})",
                conf,
                manual_review=True,
                notes=f"{note} Bearer pairs at {int(bearer_spacing*1000)}mm spacing. Verify from structural schedule.",
            ))

        # Floor sheeting over main floor area only (verandah excluded — has own decking row)
        rows += _floor_sheeting_rows(area_for_calc, "project_config+dxf_geometry", "LOW", lining_cfg)
        return rows

    # ── CASE 3: Steel floor frame confirmed but no panel schedule ─────────────
    steel_frames = [fs for fs in floor_systems if fs.assembly_type == "steel_floor_frame"]
    if steel_frames:
        fs  = steel_frames[0]
        fa  = fs.floor_area_m2 or floor_area
        log.info("Floor system: steel floor frame (area-derived, no schedule)")

        joist_spacing  = struct_cfg.get("floor_joist_spacing_mm",  450) / 1000
        bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000

        # Use DXF-derived span/run when available; fall back to sqrt approximation.
        if span_dxf > 0:
            span      = span_dxf
            floor_run = run_dxf
            fa_use    = main_floor_area
            span_src  = f"dxf_geometry: {dims_note}"
        else:
            span      = round(math.sqrt(fa) if fa > 0 else 0.0, 1)
            floor_run = round(fa / span, 1) if span > 0 else 0.0
            fa_use    = fa
            span_src  = f"derived: sqrt(floor_area={fa:.1f}m²) — square approximation"

        joist_count = math.ceil(span / joist_spacing) if span > 0 else 0
        joist_lm    = round(floor_run * joist_count, 1) if joist_count > 0 else 0.0

        load_note = f" Load class: {fs.load_class}." if fs.load_class else ""
        src_ev = f"{fs.source}: {fs.source_reference}; {span_src}"

        rows.append(_row(
            "floor_system",
            "Floor Joist / LGS Cassette",
            "lm", joist_lm,
            "inferred",
            f"derived: floor_run({floor_run:.1f}m) × joist_count({joist_count}) @ {int(joist_spacing*1000)}mm spacing",
            src_ev,
            f"ceil(span/{joist_spacing})={joist_count} × run={floor_run:.1f}m",
            "LOW",
            manual_review=True,
            notes=(
                f"Steel floor frame confirmed (FrameCAD layout). No panel schedule — "
                f"derived from main floor area {fa_use:.1f} m² at {int(joist_spacing*1000)} mm joist spacing. "
                f"Span={span:.1f}m, run={floor_run:.1f}m.{load_note} "
                "Obtain panel schedule from engineer."
            ),
        ))

        bearer_pairs = math.ceil(span / bearer_spacing) if span > 0 else 0
        if bearer_pairs > 0:
            rows.append(_row(
                "floor_system",
                "Floor Bearer (pair)",
                "nr", bearer_pairs,
                "inferred",
                f"derived: span({span:.1f}m) / bearer_spacing({bearer_spacing:.1f}m)",
                src_ev,
                f"ceil({span:.1f}/{bearer_spacing:.1f})",
                "LOW",
                manual_review=True,
                notes=f"Bearer spacing assumed {int(bearer_spacing*1000)} mm. Verify from structural drawings.",
            ))

        # Fixing cleats / connection hardware estimate
        cleats = math.ceil(joist_count * 2)  # 2 cleats per joist
        rows.append(_row(
            "floor_system",
            "Floor Joist End Cleats / Fixings",
            "nr", cleats,
            "inferred",
            f"derived: joist_count × 2 cleats each",
            src_ev,
            f"{joist_count} × 2",
            "LOW",
            manual_review=True,
            notes="2 end cleats per joist estimated. Verify against FrameCAD connection schedule.",
        ))

        rows += _floor_sheeting_rows(fa_use, fs.source + "+dxf_geometry", "LOW", lining_cfg)
        return rows

    # ── CASE 4: No floor system or slab footing — slab items in floor_system ──
    # NOTE: slab items are intentionally omitted here — they belong in the
    # Substructure (footings) package only.  This prevents duplication.
    slab_footings = [f for f in footings if f.footing_type == "slab"]
    if slab_footings or not floor_systems:
        # No separate floor_system rows — slab is fully handled by footing_quantifier.
        # Emit a single placeholder so the package is not silently empty.
        log.info("Floor system: slab on ground — items in Substructure package")
        src_ev = (f"{slab_footings[0].source}: slab_area={slab_footings[0].area_m2:.2f} m²"
                  if slab_footings else "derived: no floor system evidence")
        rows.append(_row(
            "floor_system",
            "Slab on Ground (see Substructure for quantities)",
            "item", 0,
            "placeholder",
            "slab on ground — see Substructure section",
            src_ev,
            "refer to Substructure package",
            "LOW",
            manual_review=True,
            notes=(
                "No steel floor frame or joist evidence detected. "
                "Slab on ground assumed — concrete, mesh, and formwork are in Substructure. "
                "Verify floor system type from structural drawings."
            ),
        ))

    return rows


def _floor_sheeting_rows(
    floor_area: float,
    src:        str,
    conf:       str,
    lining_cfg: dict,
) -> list[dict]:
    """Produce floor sheeting procurement rows for a given floor area."""
    if floor_area <= 0:
        return []
    sheet_area = lining_cfg.get("fc_ceiling_sheet_area_m2", 2.88)  # same sheet dims as ceiling
    waste      = lining_cfg.get("waste_factor", 1.05)
    sheet_count = math.ceil(floor_area * waste / sheet_area)

    return [
        _row(
            "floor_system", "Floor Sheet (FC / plywood)",
            "sheets",
            sheet_count,
            "calculated",
            f"ceil(floor_area × {waste} / {sheet_area})",
            f"{src}: floor_area={floor_area:.2f} m²",
            f"ceil({floor_area:.2f} × {waste} / {sheet_area})",
            conf,
            manual_review=(conf == "LOW"),
            notes="Sheet size assumed same as ceiling FC sheet (1.2×2.4). Adjust if different.",
        ),
        _row(
            "floor_system", "Floor Sheet Fixing Screws",
            "boxes",
            math.ceil(sheet_count * 16 / 200),
            "calculated",
            f"ceil(sheet_count × 16 fixings / 200 per box)",
            f"{src}: sheet_count={sheet_count}",
            "ceil(sheets × 16 / 200)",
            "LOW",
            notes="16 fixings per sheet; 200 per box.",
        ),
    ]
