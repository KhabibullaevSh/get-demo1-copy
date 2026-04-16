"""
external_cladding_quantifier.py — External wall cladding (FC weatherboard / sheet) quantification.

Sources (priority order):
  1. CanonicalCladdingFace (canonical geometry layer — pre-computed net area,
     opening deductions with correct quantity multipliers, explicit truth_class)
  2. Measured ext_wall perimeter from DXF (WallElement wall_type=external, length_m)
  3. Wall height from IFC or project_config

When a CanonicalGeometryModel is available (passed as canonical_geom), the opening
deduction section reads directly from CanonicalCladdingFace — no re-derivation.
All board-count, H-joiner, corner, and accessory rows still use the wall dimensions
from the element model (unchanged).

All quantities derived from geometry — no reference BOQ values copied.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Optional

from v3_boq_system.normalize.element_model import ProjectElementModel, WallElement

if TYPE_CHECKING:
    from v3_boq_system.normalize.canonical_objects import CanonicalGeometryModel

log = logging.getLogger("boq.v3.ext_cladding")


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


def _corner_count_from_perimeter(ext_lm: float, wall_h: float) -> int:
    """
    Fallback corner estimate from perimeter alone (LOW confidence).
    Used only when building L/W cannot be derived.
    """
    if ext_lm > 30:
        return 6
    return 4


def _derive_LW(floor_area: float, floor_perim: float) -> tuple[float, float]:
    """
    Solve for building long (L) and short (W) dimensions from floor area + perimeter.
    Returns (L, W) in metres, or (0, 0) if geometry is invalid.
    """
    if floor_area <= 0 or floor_perim <= 0:
        return 0.0, 0.0
    half_p = floor_perim / 2          # L + W
    disc   = half_p ** 2 - 4 * floor_area
    if disc < 0:
        return 0.0, 0.0
    L = round((half_p + math.sqrt(disc)) / 2, 1)
    W = round((half_p - math.sqrt(disc)) / 2, 1)
    return (L, W) if W > 0 else (0.0, 0.0)


def quantify_external_cladding(
    model:         ProjectElementModel,
    config:        dict,
    canonical_geom: "Optional[CanonicalGeometryModel]" = None,
) -> list[dict]:
    """
    Produce external wall cladding BOQ rows from measured wall geometry.

    When canonical_geom is provided, uses CanonicalCladdingFace for the
    opening deduction section (pre-computed, quantity-correct, explicit truth_class).
    All board-count and accessory rows still derive from element model wall dimensions.

    Returns list of BOQ rows.
    """
    from v3_boq_system.normalize.canonical_objects import TruthClass
    rows: list[dict] = []

    ext_walls = [w for w in model.walls if w.wall_type == "external"]
    if not ext_walls:
        log.info("External cladding: no external walls in model — skipping")
        return rows

    ext_lm   = sum(w.length_m for w in ext_walls)
    ext_h    = max((w.height_m for w in ext_walls), default=2.4)
    src      = ext_walls[0].source
    conf     = ext_walls[0].confidence

    # Structural config
    struct_cfg = config.get("structural", {})
    stud_spacing_mm = struct_cfg.get("wall_stud_spacing_mm", 600)

    # Cladding config (with sensible defaults for FC weatherboard)
    clad_cfg       = config.get("external_cladding", {})
    board_exposure_mm = clad_cfg.get("board_exposure_mm", 200)   # exposed face width
    board_length_mm   = clad_cfg.get("board_length_mm", 4200)    # stock board length
    waste_factor      = clad_cfg.get("waste_factor", 1.05)

    # ── Gross cladding area — canonical-first ─────────────────────────────────
    # When canonical_geom is available, the opening deduction section reads directly
    # from CanonicalCladdingFace (pre-computed, quantity-correct, explicit truth_class).
    # The canonical face was built by geometry_reconciler after graphical reconciliation,
    # so window heights from FrameCAD labels are already resolved.
    #
    # Fallback (no canonical_geom): applies the same classification logic inline.
    lining_cfg        = config.get("lining", {})
    _louvre_h_default = lining_cfg.get("default_louvre_height_m", 0.75)
    _EXT_DOOR_MIN_W   = 0.85

    gross_area = round(ext_lm * ext_h, 2)

    _canon_cf = canonical_geom.primary_cladding_face() if canonical_geom else None

    if _canon_cf is not None:
        # ── Path A: canonical geometry (preferred) ───────────────────────────
        opening_area   = _canon_cf.opening_deduction_m2
        clad_door_area = _canon_cf.door_deduction_m2
        clad_win_area  = _canon_cf.window_deduction_m2
        net_area       = _canon_cf.net_area_m2
        area_status    = TruthClass.to_quantity_status(_canon_cf.truth_class)
        area_conf      = _canon_cf.confidence
        area_note      = (
            f"[canonical_geometry] {_canon_cf.notes}"
            if opening_area > 0
            else "Gross area (canonical_geometry: no openings deducted)"
        )
        _louvre_h_default = _canon_cf.louvre_height_default_m
        # Reconstruct per-type detail parts for basis strings
        clad_ext_door_ops = [o for o in canonical_geom.openings if o.is_cladding_face
                             and o.opening_type == "door"] if canonical_geom else []
        clad_ext_win_ops  = [o for o in canonical_geom.openings if o.is_cladding_face
                             and o.opening_type == "window"] if canonical_geom else []
        door_parts = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_used:.3f}m)"
            for o in clad_ext_door_ops
        )
        win_parts = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_used:.3f}m"
            + (" [louvre_fallback]" if o.height_fallback_used else "") + ")"
            for o in clad_ext_win_ops
        )

    else:
        # ── Path B: element model fallback ────────────────────────────────────
        clad_ext_door_ops_raw = [o for o in model.openings
                                 if o.opening_type == "door" and o.width_m >= _EXT_DOOR_MIN_W]
        clad_ext_win_ops_raw  = [o for o in model.openings
                                 if o.opening_type == "window" and o.is_external
                                 and o.width_m > 0]
        clad_door_area = round(sum(
            o.width_m * o.height_m * o.quantity
            for o in clad_ext_door_ops_raw if o.height_m > 0
        ), 3)
        clad_win_area  = round(sum(
            o.width_m * (o.height_m if o.height_m > 0 else _louvre_h_default) * o.quantity
            for o in clad_ext_win_ops_raw
        ), 3)
        opening_area  = round(clad_door_area + clad_win_area, 3)
        net_area      = round(max(0.0, gross_area - opening_area), 2)
        area_status   = "calculated" if opening_area > 0 else "measured"
        area_conf     = conf if opening_area > 0 else "MEDIUM"
        door_parts    = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_m:.2f})"
            for o in clad_ext_door_ops_raw
        )
        win_parts = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×"
            f"{(o.height_m if o.height_m > 0 else _louvre_h_default):.2f}m)"
            for o in clad_ext_win_ops_raw
        )
        clad_ext_door_ops = clad_ext_door_ops_raw  # type: ignore[assignment]
        clad_ext_win_ops  = clad_ext_win_ops_raw   # type: ignore[assignment]

    if opening_area > 0 and _canon_cf is None:
        area_note = (
            f"[element_model] Gross {gross_area:.2f} m² "
            f"− doors {clad_door_area:.3f} m² [{door_parts}] "
            f"− windows {clad_win_area:.3f} m² [{win_parts}] "
            f"(louvre h={_louvre_h_default:.2f} m from config) "
            f"= net {net_area:.2f} m². "
            f"Partition doors (<{_EXT_DOOR_MIN_W:.2f} m) excluded."
        )
    elif not opening_area:
        area_note = "Gross area — no opening dimensions measured"

    rows.append(_row(
        "external_cladding",
        "External Wall Cladding — FC Weatherboard (supply & fix)",
        "m2", net_area,
        area_status,
        (f"ext_wall_lm({ext_lm:.2f}) × wall_h({ext_h:.1f})"
         + (f" − ext_doors({clad_door_area:.3f}) − ext_windows({clad_win_area:.3f})"
            if opening_area > 0 else "")),
        f"{src}: ext_wall_perimeter={ext_lm:.2f} m, h={ext_h:.1f} m",
        (f"{ext_lm:.2f} × {ext_h:.1f} − {opening_area:.3f}"
         if opening_area > 0 else f"{ext_lm:.2f} × {ext_h:.1f}"),
        area_conf,
        manual_review=(not opening_area > 0),
        notes=area_note,
    ))

    # ── Derive building L and W from floor geometry ───────────────────────────
    # More accurate than perimeter-heuristics alone: derive long (L) and short (W)
    # dimensions by solving area + perimeter simultaneously.
    floor_area_m2  = sum(f.area_m2 for f in model.floors)
    floor_perim_m  = sum(f.perimeter_m for f in model.floors)
    L_m, W_m = _derive_LW(floor_area_m2, floor_perim_m)
    geom_available = (L_m > 0 and W_m > 0)
    board_length_m = board_length_mm / 1000

    # ── Board count ───────────────────────────────────────────────────────────
    rows_of_boards = math.ceil(ext_h * 1000 / board_exposure_mm)
    run_per_row_m  = ext_lm
    boards_per_row = math.ceil(run_per_row_m / board_length_m)
    total_boards   = math.ceil(boards_per_row * rows_of_boards * waste_factor)

    rows.append(_row(
        "external_cladding",
        "FC Weatherboard — Board Count",
        "nr", total_boards,
        "calculated",
        (
            f"rows_of_boards={rows_of_boards} (h={ext_h*1000:.0f}mm / {board_exposure_mm}mm exp) × "
            f"boards_per_row={boards_per_row} (lm={ext_lm:.1f}m / {board_length_m:.1f}m) × "
            f"waste={waste_factor}"
        ),
        f"{src}: ext_lm={ext_lm:.2f} m, wall_h={ext_h:.1f} m",
        "ceil(h/exp) × ceil(lm/board_l) × waste",
        "MEDIUM",
        manual_review=True,
        notes=(
            f"Weatherboard: {board_exposure_mm}mm exposure, {board_length_mm}mm stock length. "
            "Count includes {:.0f}% waste.  Verify board profile and lap from spec.".format((waste_factor - 1) * 100)
        ),
    ))

    # ── Weatherboard total supply lm (primary procurement family) ─────────────
    total_wb_lm = round(total_boards * board_length_m, 1)
    rows.append(_row(
        "external_cladding",
        f"FC Weatherboard — Total Supply lm ({board_length_mm}mm stock)",
        "lm", total_wb_lm,
        "calculated",
        f"board_count({total_boards}) × board_length({board_length_m:.1f}m) = {total_wb_lm} lm",
        f"{src}: ext_lm={ext_lm:.2f} m, wall_h={ext_h:.1f} m",
        f"{total_boards} × {board_length_m:.1f}",
        "MEDIUM",
        manual_review=True,
        notes=(
            f"Total FC weatherboard supply in lineal metres. "
            f"{total_boards} boards × {board_length_m:.1f}m stock length = {total_wb_lm} lm. "
            f"Includes {(waste_factor-1)*100:.0f}% waste. "
            "Verify board profile and lap detail with cladding specification."
        ),
    ))

    # ── H-joiners (per-façade method when L/W available) ─────────────────────
    # H-joiners occur at butt-joins WITHIN a wall run, not at corners.
    # Per-façade method avoids over-counting by treating each face independently.
    if geom_available:
        joiners_long  = max(0, math.ceil(L_m / board_length_m) - 1)  # per long face
        joiners_short = max(0, math.ceil(W_m / board_length_m) - 1)  # per short face
        # Rectangle has 2 long faces and 2 short faces
        h_joiner_count = (joiners_long * 2 + joiners_short * 2) * rows_of_boards
        joiner_basis = (
            f"per-façade: 2×L-face ({joiners_long}×2={joiners_long*2} joiners/row) + "
            f"2×W-face ({joiners_short}×2={joiners_short*2} joiners/row) "
            f"× {rows_of_boards} rows; "
            f"derived from floor L={L_m:.1f}m W={W_m:.1f}m"
        )
        joiner_src  = (f"dxf_geometry: floor_area={floor_area_m2:.1f}m² "
                       f"+ ext_perim={floor_perim_m:.1f}m → L={L_m:.1f}m W={W_m:.1f}m")
        joiner_conf = "MEDIUM"
        joiner_mr   = False
    else:
        h_joiner_count = max(0, (boards_per_row - 1)) * rows_of_boards
        joiner_basis = f"(boards_per_row−1) × rows = ({boards_per_row}−1) × {rows_of_boards}"
        joiner_src   = f"derived from board count: boards_per_row={boards_per_row}, rows={rows_of_boards}"
        joiner_conf  = "LOW"
        joiner_mr    = True

    rows.append(_row(
        "external_cladding",
        "FC Weatherboard H-Joiner Extrusion",
        "nr", h_joiner_count,
        "calculated",
        joiner_basis,
        joiner_src,
        "per-façade: joiners per face × rows_of_boards" if geom_available else "(boards_per_row − 1) × rows_of_boards",
        joiner_conf,
        manual_review=joiner_mr,
        notes=(
            "H-joiners per façade face (joiners at butt-joins only, not corners). "
            "Verify count from cladding layout."
            if geom_available
            else "One H-joiner per butt-join between boards in same row. Verify from cladding layout."
        ),
    ))

    # ── Corner flashings (geometry-derived when L/W available) ───────────────
    if geom_available:
        # Check if the footprint is rectangular (L×W perimeter matches measured perimeter)
        rect_perim = 2 * (L_m + W_m)
        if abs(rect_perim - floor_perim_m) < 0.5:
            # Rectangular building → 4 external corners, 0 internal
            ext_corners  = 4
            int_corners  = 0
            corner_basis = (
                f"rectangular footprint: L={L_m:.1f}m × W={W_m:.1f}m "
                f"(2×(L+W)={rect_perim:.1f}m ≈ measured perim {floor_perim_m:.1f}m)"
            )
            corner_conf  = "MEDIUM"
            corner_mr    = False
        else:
            # Non-rectangular — use perimeter heuristic
            ext_corners  = _corner_count_from_perimeter(ext_lm, ext_h)
            int_corners  = max(0, ext_corners - 4)
            corner_basis = f"estimated from perimeter ({ext_lm:.1f}m) — non-rectangular footprint"
            corner_conf  = "LOW"
            corner_mr    = True
    else:
        ext_corners  = _corner_count_from_perimeter(ext_lm, ext_h)
        int_corners  = max(0, ext_corners - 4)
        corner_basis = f"estimated from perimeter ({ext_lm:.1f}m)"
        corner_conf  = "LOW"
        corner_mr    = True

    rows.append(_row(
        "external_cladding",
        "External Corner Flashing (FC cladding)",
        "nr", ext_corners,
        "inferred",
        corner_basis,
        f"dxf_geometry: ext_wall_perimeter={ext_lm:.1f}m"
        + (f", floor_area={floor_area_m2:.1f}m² → L={L_m:.1f}m W={W_m:.1f}m" if geom_available else ""),
        "geometry-derived corner count" if geom_available else "estimated from perimeter",
        corner_conf,
        manual_review=corner_mr,
        notes=(
            f"Corner count from building geometry (L={L_m:.1f}m × W={W_m:.1f}m rectangular = 4 corners). "
            "Verify against floor plan."
            if (geom_available and corner_conf == "MEDIUM")
            else "Corner count estimated. Verify from floor plan — count each change of direction."
        ),
    ))

    if int_corners > 0:
        rows.append(_row(
            "external_cladding",
            "Internal Corner Flashing (FC cladding)",
            "nr", int_corners,
            "inferred",
            f"estimated {int_corners} internal corners",
            f"derived: ext_corners={ext_corners}",
            "ext_corners − 4",
            "LOW",
            manual_review=True,
            notes="Internal corner count estimated. Verify from floor plan.",
        ))

    # ── Corner bead / trim ────────────────────────────────────────────────────
    corner_trim_lm = round(ext_corners * ext_h, 2)
    rows.append(_row(
        "external_cladding",
        "External Corner Trim / Angle Bead",
        "lm", corner_trim_lm,
        "calculated",
        f"ext_corners({ext_corners}) × wall_h({ext_h:.1f}m)",
        f"derived: {ext_corners} corners × {ext_h:.1f} m",
        "corners × wall_height",
        corner_conf,
        manual_review=corner_mr,
        notes="One trim per corner, full wall height. Verify profile and spec.",
    ))

    # ── Stud clips / cladding clips ───────────────────────────────────────────
    # One clip per stud per board row
    studs_per_lm     = 1000 / stud_spacing_mm
    total_stud_count = math.ceil(ext_lm * studs_per_lm)
    clip_count       = total_stud_count * rows_of_boards

    rows.append(_row(
        "external_cladding",
        "FC Weatherboard Stud Fixing Clip",
        "nr", clip_count,
        "calculated",
        (
            f"studs({total_stud_count}) × rows_of_boards({rows_of_boards}) "
            f"[stud_spacing={stud_spacing_mm}mm]"
        ),
        f"{src}: ext_lm={ext_lm:.2f} m, stud_spacing={stud_spacing_mm}mm",
        "ceil(ext_lm × studs_per_m) × rows_of_boards",
        "MEDIUM",
        manual_review=False,
        notes=f"Stud clips at {stud_spacing_mm}mm centres. Verify clip type from manufacturer spec.",
    ))

    # ── Stainless screws ──────────────────────────────────────────────────────
    # 2 screws per clip (top and bottom of board at each stud)
    screw_count = clip_count * 2

    rows.append(_row(
        "external_cladding",
        "Stainless Steel Screw — Cladding Fix",
        "nr", screw_count,
        "calculated",
        f"stud_clips({clip_count}) × 2 screws per clip",
        f"derived from clip count: {clip_count}",
        "clip_count × 2",
        "LOW",
        manual_review=False,
        notes="2 × stainless screws per fixing clip. Verify size and type from spec.",
    ))

    # ── Building wrap / sarking ───────────────────────────────────────────────
    sarking_area = round(gross_area * 1.05, 2)   # 5% overlap
    rows.append(_row(
        "external_cladding",
        "Building Wrap / Sarking — External Wall",
        "m2", sarking_area,
        "calculated",
        f"gross_wall_area({gross_area:.2f}) × 1.05 overlap",
        f"{src}: ext_lm={ext_lm:.2f} m × wall_h={ext_h:.1f} m",
        "ext_lm × wall_h × 1.05",
        "MEDIUM",
        manual_review=False,
        notes="Includes 5% for laps. Gross area — no deduction for openings in sarking schedule.",
    ))

    # ── Building wrap lap tape ────────────────────────────────────────────────
    # 1 roll (50m × 65mm) per ~100 m² of sarking
    tape_rolls = math.ceil(sarking_area / 100)
    rows.append(_row(
        "external_cladding",
        "Building Wrap Lap Tape (50m × 65mm roll)",
        "rolls", tape_rolls,
        "calculated",
        f"ceil(sarking_area({sarking_area:.2f}) / 100 m² per roll)",
        f"derived from sarking_area={sarking_area:.2f} m²",
        "ceil(sarking_area / 100)",
        "LOW",
        notes="Lap tape for building wrap horizontal and vertical overlaps. 1 roll per ~100 m².",
    ))

    # ── Expansion joint sealant ───────────────────────────────────────────────
    # H-joiner butt-joins require sealant backing: ~1 tube (300mL) per 10 joiners
    if h_joiner_count > 0:
        sealant_tubes = math.ceil(h_joiner_count / 10)
        rows.append(_row(
            "external_cladding",
            "Expansion Joint Sealant — H-Joiner (300mL tube)",
            "tubes", sealant_tubes,
            "calculated",
            f"ceil(h_joiner_count({h_joiner_count}) / 10 joiners per tube)",
            f"derived from h_joiner_count={h_joiner_count}",
            "ceil(h_joiners / 10)",
            "LOW",
            notes=(
                f"Polyurethane sealant at FC weatherboard H-joiner butt joins. "
                f"{h_joiner_count} joiners ÷ 10 per tube = {sealant_tubes} tubes. "
                "Verify sealant colour match and compatibility with FC manufacturer spec."
            ),
        ))

    # ── Window / door reveal trim ─────────────────────────────────────────────
    # Jamb / reveal trim at openings in the external cladding face.
    # When canonical_geom is available, uses CanonicalOpening.height_used (already
    # resolved including louvre fallback).  Partition doors excluded by is_cladding_face.
    opening_trim_lm = 0.0
    if _canon_cf is not None and canonical_geom is not None:
        # Canonical path — height already resolved, is_cladding_face already filtered
        for o in canonical_geom.cladding_face_openings():
            opening_trim_lm += (2 * o.height_used + o.width_m) * o.quantity
        _trim_src = f"canonical_geometry: {len(canonical_geom.cladding_face_openings())} openings in cladding face"
    else:
        # Fallback path — uses element model openings with inline height resolution
        trim_openings = clad_ext_door_ops + clad_ext_win_ops  # type: ignore[operator]
        for o in trim_openings:
            h_used = o.height_m if o.height_m > 0 else _louvre_h_default  # type: ignore[union-attr]
            opening_trim_lm += (2 * h_used + o.width_m) * o.quantity  # type: ignore[union-attr]
        _trim_src = f"dxf_geometry: {len(trim_openings)} openings in external cladding face"  # type: ignore[arg-type]
    opening_trim_lm = round(opening_trim_lm, 2)
    if opening_trim_lm > 0:
        rows.append(_row(
            "external_cladding",
            "Window / Door Reveal Trim (FC / timber)",
            "lm", opening_trim_lm,
            "calculated",
            "sum((2×h + w) × qty) for each external-cladding opening (entrance doors ≥0.85m + windows)",
            _trim_src,
            "sum((2h+w)×qty per opening)",
            "MEDIUM",
            manual_review=True,
            notes=(
                "Jamb / reveal trim at external cladding openings: 2 × height + 1 × width per opening. "
                f"Louvre h={_louvre_h_default:.2f} m (config default). "
                f"Partition doors (<{_EXT_DOOR_MIN_W:.2f} m wide) excluded — they are in internal walls. "
                "Verify trim profile and dimensions with cladding specification."
            ),
        ))

    return rows
