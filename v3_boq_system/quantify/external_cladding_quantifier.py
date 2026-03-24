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
    Estimate number of external corners from perimeter.
    A simple rectangle = 4 corners; each re-entrant adds 2 (1 ext + 1 int).
    For pharmacy buildings, assume 6 external + 2 internal corners as default
    (i.e., one recess/alcove in the facade).  This is LOW confidence and must
    be verified from the floor plan.
    """
    # Heuristic: corners ≈ 4 + 2 × (perimeter / 4 / wall_h - 1) capped at reason
    # Simpler: fixed estimate from building type — rectangular = 4 ext corners
    # For perimeter > 30 m assume at least one change of direction = 6 ext corners
    if ext_lm > 30:
        return 6
    return 4


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
    # Deduct openings when area data is available; otherwise note gross
    opening_area = sum(
        o.width_m * o.height_m
        for o in model.openings
        if o.is_external and o.width_m > 0 and o.height_m > 0
    )
    gross_area   = round(ext_lm * ext_h, 2)
    net_area     = round(gross_area - opening_area, 2) if opening_area > 0 else gross_area
    area_note    = (
        f"Gross {gross_area:.2f} m² − openings {opening_area:.2f} m² = {net_area:.2f} m²"
        if opening_area > 0
        else f"Gross area — deduct openings manually (no opening dimensions measured)"
    )
    area_status  = "calculated" if opening_area > 0 else "measured"
    area_conf    = conf if opening_area > 0 else "MEDIUM"

    rows.append(_row(
        "external_cladding",
        "External Wall Cladding — FC Weatherboard (supply & fix)",
        "m2", net_area,
        area_status,
        f"ext_wall_lm({ext_lm:.2f}) × wall_h({ext_h:.1f}){' − opening_area' if opening_area > 0 else ''}",
        f"{src}: ext_wall_perimeter={ext_lm:.2f} m, h={ext_h:.1f} m",
        f"{ext_lm:.2f} × {ext_h:.1f}" + (f" − {opening_area:.2f}" if opening_area > 0 else ""),
        area_conf,
        manual_review=(not opening_area > 0),
        notes=area_note,
    ))

    # ── Board count ───────────────────────────────────────────────────────────
    # Each row of boards runs the full perimeter; number of horizontal rows = ceil(h / exposure)
    rows_of_boards   = math.ceil(ext_h * 1000 / board_exposure_mm)
    run_per_row_m    = ext_lm
    board_length_m   = board_length_mm / 1000
    boards_per_row   = math.ceil(run_per_row_m / board_length_m)
    total_boards     = math.ceil(boards_per_row * rows_of_boards * waste_factor)

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
        f"ceil(h/exp) × ceil(lm/board_l) × waste",
        "MEDIUM",
        manual_review=True,
        notes=(
            f"Weatherboard: {board_exposure_mm}mm exposure, {board_length_mm}mm stock length. "
            "Count includes {:.0f}% waste.  Verify board profile and lap from spec.".format((waste_factor - 1) * 100)
        ),
    ))

    # ── H-joiners ─────────────────────────────────────────────────────────────
    # H-joiners occur at butt-joins within each board row.
    # Joins per row = boards_per_row − 1 (each join between two boards)
    h_joiner_count = max(0, (boards_per_row - 1)) * rows_of_boards

    rows.append(_row(
        "external_cladding",
        "FC Weatherboard H-Joiner Extrusion",
        "nr", h_joiner_count,
        "calculated",
        f"(boards_per_row−1) × rows = ({boards_per_row}−1) × {rows_of_boards}",
        f"derived from board count: boards_per_row={boards_per_row}, rows={rows_of_boards}",
        "(boards_per_row − 1) × rows_of_boards",
        "LOW",
        manual_review=True,
        notes="One H-joiner per butt-join between boards in same row. Verify from cladding layout.",
    ))

    # ── Corner flashings ──────────────────────────────────────────────────────
    ext_corners = _corner_count_from_perimeter(ext_lm, ext_h)
    int_corners = max(0, ext_corners - 4)   # internal corners = extra beyond rectangle

    rows.append(_row(
        "external_cladding",
        "External Corner Flashing (FC cladding)",
        "nr", ext_corners,
        "inferred",
        f"estimated {ext_corners} external corners from perimeter ({ext_lm:.1f} m)",
        f"derived: ext_wall_perimeter={ext_lm:.1f} m",
        "estimated from perimeter — rectangular + allowance",
        "LOW",
        manual_review=True,
        notes="Corner count estimated. Verify from floor plan — count each change of direction.",
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
        "LOW",
        manual_review=True,
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

    return rows
