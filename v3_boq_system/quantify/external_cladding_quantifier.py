"""
external_cladding_quantifier.py — External wall cladding (FC weatherboard / sheet) quantification.

Sources (priority order):
  1. Measured ext_wall perimeter from DXF (WallElement wall_type=external, length_m)
  2. Wall height from IFC or project_config

Produces:
  - FC Weatherboard / External Sheet Cladding (m²)
  - Board count (nr) based on board width and wall run
  - H-joiner extrusions (nr) based on board count and run per board
  - External corner flashings (nr) — from estimated corner count
  - Internal corner flashings (nr)
  - Stud clips / fixing clips (nr) per stud row
  - Stainless screws / rivets (nr)
  - Building wrap / sarking (m²) if external cladding present

All quantities derived from geometry — no reference BOQ values copied.
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import ProjectElementModel, WallElement

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
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    """
    Produce external wall cladding BOQ rows from measured wall geometry.

    Returns list of BOQ rows.
    """
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

    # ── Gross cladding area ───────────────────────────────────────────────────
    # Opening deductions: only openings that actually penetrate the external cladding face.
    # Width threshold (0.85 m) separates external entrance doors (≥850 mm) from internal
    # partition doors (<850 mm). All element-model openings carry is_external=True by
    # default, so the width heuristic is necessary to exclude partition doors.
    # Louvre windows with height_m=0 use the config default height (typically 0.75 m).
    lining_cfg       = config.get("lining", {})
    _louvre_h_default = lining_cfg.get("default_louvre_height_m", 0.75)
    _EXT_DOOR_MIN_W   = 0.85   # ≥850 mm → external entrance; <850 mm → partition door

    clad_ext_door_ops = [o for o in model.openings
                         if o.opening_type == "door"   and o.width_m >= _EXT_DOOR_MIN_W]
    clad_ext_win_ops  = [o for o in model.openings
                         if o.opening_type == "window" and o.is_external and o.width_m > 0]

    clad_door_area = round(sum(
        o.width_m * o.height_m * o.quantity
        for o in clad_ext_door_ops
        if o.height_m > 0
    ), 3)
    clad_win_area  = round(sum(
        o.width_m * (o.height_m if o.height_m > 0 else _louvre_h_default) * o.quantity
        for o in clad_ext_win_ops
    ), 3)
    opening_area = round(clad_door_area + clad_win_area, 3)

    gross_area   = round(ext_lm * ext_h, 2)
    net_area     = round(gross_area - opening_area, 2) if opening_area > 0 else gross_area

    if opening_area > 0:
        door_parts = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_m:.2f})" for o in clad_ext_door_ops
        )
        win_parts = ", ".join(
            f"{o.mark}×{o.quantity}({o.width_m:.3f}×{(o.height_m if o.height_m > 0 else _louvre_h_default):.2f})"
            for o in clad_ext_win_ops
        )
        area_note = (
            f"Gross {gross_area:.2f} m² − doors {clad_door_area:.3f} m² [{door_parts}] "
            f"− windows {clad_win_area:.3f} m² [{win_parts}] "
            f"(louvre h={_louvre_h_default:.2f} m from config default) "
            f"= net {net_area:.2f} m². "
            f"Partition doors (<{_EXT_DOOR_MIN_W:.2f} m wide) excluded — not in external cladding face."
        )
    else:
        area_note = "Gross area — deduct openings manually (no opening dimensions measured)"

    area_status  = "calculated" if opening_area > 0 else "measured"
    area_conf    = conf if opening_area > 0 else "MEDIUM"

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
    # Uses same entrance-door threshold as area deduction (_EXT_DOOR_MIN_W).
    # Partition doors (<0.85 m) are in internal walls — no external reveal trim.
    # Louvre windows with height_m=0 use _louvre_h_default (not wall height).
    trim_openings = clad_ext_door_ops + clad_ext_win_ops
    opening_trim_lm = 0.0
    for o in trim_openings:
        h_used = o.height_m if o.height_m > 0 else _louvre_h_default
        opening_trim_lm += (2 * h_used + o.width_m) * o.quantity
    opening_trim_lm = round(opening_trim_lm, 2)
    if opening_trim_lm > 0:
        rows.append(_row(
            "external_cladding",
            "Window / Door Reveal Trim (FC / timber)",
            "lm", opening_trim_lm,
            "calculated",
            "sum((2×h + w) × qty) for each external-cladding opening (entrance doors ≥0.85m + windows)",
            f"dxf_geometry: {len(trim_openings)} openings in external cladding face",
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
