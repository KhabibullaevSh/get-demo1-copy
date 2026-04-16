"""
lining_quantifier.py — Wall and ceiling lining quantification.

Uses measured wall lengths and IFC stud data where available,
NOT simple area factor estimates.

Produces items for:
  - External wall lining (FC sheets, joiners, screws)
  - Internal wall lining (both faces)
  - Wet-area wall lining
  - Ceiling lining (FC sheets, battens)
  - Cornice / ceiling trim
  - Skirting board
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import (
    CeilingElement,
    ProjectElementModel,
    RoomElement,
    WallElement,
)

log = logging.getLogger("boq.v3.lining")


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


def quantify_linings(
    model:  ProjectElementModel,
    config: dict,
    assembly_rules: dict,
) -> list[dict]:
    """
    Produce BOQ rows for all lining packages.

    Uses the assembly_rules dict for per-m² item derivation.
    """
    rows: list[dict] = []
    lining_cfg  = config.get("lining", {})
    struct_cfg  = config.get("structural", {})
    wall_height = struct_cfg.get("wall_height_m", 2.4)

    fc_wall_area  = lining_cfg.get("fc_wall_sheet_area_m2",    3.24)
    fc_ceil_area  = lining_cfg.get("fc_ceiling_sheet_area_m2", 2.88)
    waste         = lining_cfg.get("waste_factor", 1.05)
    ceil_batten_s = config.get("structural", {}).get("ceiling_batten_spacing_mm", 400) / 1000

    # ── Opening deductions ────────────────────────────────────────────────────
    # Source: DXF block geometry (HIGH for widths; height_m=0 on louvres uses config default).
    # Element builder marks all DXF doors as is_external=True by default.
    # Width heuristic for this pharmacy: doors ≥ 0.85 m = external entrance;
    # doors < 0.85 m = assumed internal partition door.
    louvre_h_default = lining_cfg.get("default_louvre_height_m", 0.75)
    _EXT_DOOR_MIN_W  = 0.85   # mm-threshold separating entrance (≥850) from room doors (<850)

    ext_door_ops = [o for o in model.openings
                    if o.opening_type == "door" and o.width_m >= _EXT_DOOR_MIN_W]
    ext_win_ops  = [o for o in model.openings
                    if o.opening_type == "window" and o.is_external]
    int_door_ops = [o for o in model.openings
                    if o.opening_type == "door" and o.width_m < _EXT_DOOR_MIN_W]

    ext_door_area      = round(sum(o.width_m * o.height_m * o.quantity
                                   for o in ext_door_ops), 3)
    ext_win_area       = round(sum(o.width_m
                                   * (o.height_m if o.height_m > 0 else louvre_h_default)
                                   * o.quantity for o in ext_win_ops), 3)
    int_door_area_1f   = round(sum(o.width_m * o.height_m * o.quantity
                                   for o in int_door_ops), 3)   # one face each
    int_door_area_2f   = round(int_door_area_1f * 2, 3)         # both partition faces
    ext_opening_deduct = round(ext_door_area + ext_win_area, 3)

    # ── External wall lining ──────────────────────────────────────────────────
    ext_walls = [w for w in model.walls if w.wall_type == "external"]
    if ext_walls:
        ext_lm    = sum(w.length_m for w in ext_walls)
        ext_h     = max(w.height_m for w in ext_walls)
        ext_area  = round(ext_lm * ext_h, 2)
        ext_conf  = max((w.confidence for w in ext_walls), key=lambda c: {"HIGH":3,"MEDIUM":2,"LOW":1}.get(c,0))
        ext_src   = ext_walls[0].source

        # Deduct external door(s) and window openings from interior lining area
        ext_area_net = round(max(0.0, ext_area - ext_opening_deduct), 2)
        sheets    = math.ceil(ext_area_net * waste / fc_wall_area)

        _deduct_note = ""
        if ext_opening_deduct > 0:
            door_parts = ", ".join(
                f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_m:.2f})" for o in ext_door_ops
            )
            win_h_used = louvre_h_default
            win_parts  = ", ".join(
                f"{o.mark}×{o.quantity}({o.width_m:.3f}×{win_h_used:.2f})" for o in ext_win_ops
            )
            _deduct_note = (
                f"Opening deductions: doors={ext_door_area:.3f} m² [{door_parts}]; "
                f"windows={ext_win_area:.3f} m² [{win_parts}] "
                f"(louvre height={win_h_used:.2f} m from config default). "
                f"Gross={ext_area:.2f} m² − {ext_opening_deduct:.3f} m² = net={ext_area_net:.2f} m²."
            )

        rows.append(_row(
            "wall_lining_external",
            "External Wall Lining — FC Sheet (6mm, 1200×2700)",
            "sheets", sheets,
            "calculated",
            f"ceil(ext_wall_area_net × {waste} / {fc_wall_area})",
            (f"{ext_src}: ext_wall_lm={ext_lm:.2f} m × h={ext_h:.1f} m → gross={ext_area:.2f} m²"
             + (f" − openings={ext_opening_deduct:.3f} m² → net={ext_area_net:.2f} m²"
                if ext_opening_deduct > 0 else "")),
            f"ceil({ext_area_net:.2f} × {waste} / {fc_wall_area})",
            "MEDIUM" if ext_opening_deduct > 0 else ext_conf,
            notes=_deduct_note,
        ))
        ext_sheet_supply_m2 = round(sheets * fc_wall_area, 2)
        rows.append(_row(
            "wall_lining_external",
            "External Wall Lining — FC Sheet Total Area (1200×2700mm)",
            "m2", ext_sheet_supply_m2,
            "calculated",
            f"sheet_count({sheets}) × sheet_area({fc_wall_area}m²) — gross supply incl. waste",
            f"{ext_src}: ext_wall_area_net={ext_area_net:.2f}m² → {sheets} sheets × {fc_wall_area}m²",
            f"{sheets} × {fc_wall_area}",
            "MEDIUM" if ext_opening_deduct > 0 else ext_conf,
            notes=(
                f"Gross external wall FC sheet supply area. "
                f"Net wall area: {ext_area_net:.2f}m² × {waste} waste = {round(ext_area_net*waste,2)}m². "
                f"Rounded to {sheets} full sheets × {fc_wall_area}m² = {ext_sheet_supply_m2}m². "
                "Use for bulk supply order alongside sheet count."
            ),
        ))
        rows.append(_row(
            "wall_lining_external", "External Wall Lining — FC Sheet Screws",
            "boxes", math.ceil(ext_area_net * 25 / 200),
            "calculated", f"ceil(wall_area_net × 25 screws/m² / 200/box)",
            f"derived from ext_wall_area_net={ext_area_net:.2f} m²",
            "ceil(area × 25 / 200)",
            "LOW",
        ))
        rows.append(_row(
            "wall_lining_external", "External Wall Lining — FC Sheet Adhesive",
            "tubes", math.ceil(ext_area_net / 2.5),
            "calculated", "ceil(wall_area_net / 2.5 m² per tube)",
            f"derived from ext_wall_area_net={ext_area_net:.2f} m²",
            "ceil(area / 2.5)",
            "LOW",
            notes="Construction adhesive (e.g. Bostik or equivalent). 1 tube per ~2.5 m² of FC sheet.",
        ))
        # External wall FC sheet joiner strips — previously only internal walls had this.
        # One vertical joiner strip per 1.2 m sheet-width run along each external wall face.
        # Source: same geometry as internal wall joiners (ext_lm + sheet width).
        fc_wall_w_ext = lining_cfg.get("fc_wall_sheet_w", 1.2)
        ext_strip_cols = math.ceil(ext_lm / fc_wall_w_ext)
        ext_joiner_lm  = round(ext_strip_cols * ext_h, 1)
        rows.append(_row(
            "wall_lining_external", "External Wall Lining — FC Sheet Joiner Strip",
            "lm", ext_joiner_lm,
            "calculated",
            f"ceil(ext_lm({ext_lm:.1f}) / {fc_wall_w_ext}m sheet_w) × h({ext_h:.1f}m)",
            f"{ext_src}: ext_wall_lm={ext_lm:.2f} m",
            f"ceil({ext_lm:.1f}/{fc_wall_w_ext}) × {ext_h:.1f}",
            "MEDIUM",
            notes=f"1 vertical joiner strip per {fc_wall_w_ext:.1f}m sheet-width run × {ext_h:.1f}m height on external wall face.",
        ))

    # ── Internal wall lining (both faces) ────────────────────────────────────
    int_walls = [w for w in model.walls if w.wall_type == "internal"]
    if int_walls:
        int_lm      = sum(w.length_m for w in int_walls)
        int_h       = max(w.height_m for w in int_walls)
        # WallElement.area_m2 already includes faces=2 for internal partitions.
        # Use that so sheets cover both faces without double-calculating.
        int_area_both     = round(sum(w.area_m2 for w in int_walls), 2)  # both faces gross
        # Deduct assumed-internal door openings (both faces of each door opening)
        int_area_both_net = round(max(0.0, int_area_both - int_door_area_2f), 2)
        int_conf    = int_walls[0].confidence
        int_src     = int_walls[0].source
        int_note    = int_walls[0].notes or (
            "Internal wall lm estimated from floor area ratio — replace with measured value."
            if int_conf == "LOW" else ""
        )

        sheets_int = math.ceil(int_area_both_net * waste / fc_wall_area)
        fc_wall_w  = lining_cfg.get("fc_wall_sheet_w", 1.2)

        _int_deduct_note = ""
        if int_door_area_2f > 0:
            int_door_parts = ", ".join(
                f"{o.mark}×{o.quantity}×2f({o.width_m:.3f}×{o.height_m:.2f})"
                for o in int_door_ops
            )
            _int_deduct_note = (
                f"Opening deductions (assumed-internal doors, both partition faces): "
                f"{int_door_area_2f:.3f} m² [{int_door_parts}]. "
                f"Gross={int_area_both:.2f} m² − {int_door_area_2f:.3f} m² = net={int_area_both_net:.2f} m². "
                f"Doors classified as internal by width < {_EXT_DOOR_MIN_W:.2f} m threshold. "
                + (int_note or "")
            )
        else:
            _int_deduct_note = int_note

        rows.append(_row(
            "wall_lining_internal",
            "Internal Wall Lining — FC Sheet (6mm, 1200×2700)",
            "sheets", sheets_int,
            "calculated",
            f"ceil(int_wall_area_net × {waste} / {fc_wall_area})",
            (f"{int_src}: int_wall_lm={int_lm:.2f} m × h={int_h:.1f} m × 2 faces = {int_area_both:.2f} m²"
             + (f" − int_doors={int_door_area_2f:.3f} m² → net={int_area_both_net:.2f} m²"
                if int_door_area_2f > 0 else "")),
            f"ceil({int_area_both_net:.2f} × {waste} / {fc_wall_area})",
            int_conf,
            manual_review=(int_conf == "LOW"),
            notes=_int_deduct_note,
        ))
        int_sheet_supply_m2 = round(sheets_int * fc_wall_area, 2)
        rows.append(_row(
            "wall_lining_internal",
            "Internal Wall Lining — FC Sheet Total Area (1200×2700mm)",
            "m2", int_sheet_supply_m2,
            "calculated",
            f"sheet_count({sheets_int}) × sheet_area({fc_wall_area}m²) — gross supply incl. waste",
            f"{int_src}: int_wall_area_both_net={int_area_both_net:.2f}m² → {sheets_int} sheets × {fc_wall_area}m²",
            f"{sheets_int} × {fc_wall_area}",
            int_conf,
            manual_review=(int_conf == "LOW"),
            notes=(
                f"Gross internal wall FC sheet supply area (both partition faces). "
                f"Net area: {int_area_both_net:.2f}m² × {waste} waste = {round(int_area_both_net*waste,2)}m². "
                f"Rounded to {sheets_int} full sheets × {fc_wall_area}m² = {int_sheet_supply_m2}m². "
                "Use for bulk supply order alongside sheet count."
            ),
        ))
        rows.append(_row(
            "wall_lining_internal", "Internal Wall Lining — FC Sheet Screws",
            "boxes", math.ceil(int_area_both_net * 25 / 200),
            "calculated", f"ceil(both_face_area_net × 25 screws/m² / 200/box)",
            f"derived from int_wall_area_both_net={int_area_both_net:.2f} m²",
            "ceil(area × 25 / 200)",
            "LOW",
            manual_review=(int_conf == "LOW"),
            notes=_int_deduct_note,
        ))
        rows.append(_row(
            "wall_lining_internal", "Internal Wall Lining — FC Sheet Adhesive",
            "tubes", math.ceil(int_area_both_net / 2.5),
            "calculated", "ceil(both_face_area_net / 2.5 m² per tube)",
            f"derived from int_wall_area_both_net={int_area_both_net:.2f} m²",
            "ceil(area / 2.5)",
            "LOW",
            manual_review=(int_conf == "LOW"),
            notes="Construction adhesive for FC sheet to stud/batten. 1 tube per ~2.5 m².",
        ))

        # FC sheet joiner strips — 1 vertical strip per 1.2 m of wall face length
        face_lm      = round(int_lm * 2, 1)   # both faces total run
        strip_cols   = math.ceil(face_lm / fc_wall_w)
        joiner_lm    = round(strip_cols * int_h, 1)
        rows.append(_row(
            "wall_lining_internal", "Internal Wall Lining — FC Sheet Joiner Strip",
            "lm", joiner_lm,
            "calculated",
            f"ceil(face_lm / {fc_wall_w}) × h = ceil({face_lm:.1f}/{fc_wall_w}) × {int_h:.1f}",
            f"{int_src}: int_wall_lm={int_lm:.2f} m × 2 faces = {face_lm:.1f} lm",
            f"ceil({face_lm:.1f}/{fc_wall_w}) × {int_h:.1f}",
            "LOW",
            notes=f"1 vertical joiner strip per {fc_wall_w:.1f} m sheet-width run × {int_h:.1f} m height.",
        ))

    # ── Wet-area wall lining (if wet spaces/rooms detected) ──────────────────
    # Prefer SpaceElement (may have DXF-backed perimeter from wall-network zones)
    # over the legacy RoomElement sqrt estimate.
    wet_spaces = model.wet_spaces()
    wet_rooms  = model.wet_rooms()

    if wet_spaces:
        # Use SpaceElement.perimeter_m when available (may be DXF-backed).
        # Fall back to 4×sqrt(area) only when perimeter_m == 0.
        wet_area_total  = 0.0
        wet_floor_total = 0.0
        _perim_parts: list[str] = []
        for sp in wet_spaces:
            if sp.area_m2 > 0:
                if sp.perimeter_m > 0:
                    perim_m = sp.perimeter_m
                    _psrc   = sp.source_type   # dxf_wall_network or config
                    _perim_parts.append(
                        f"{sp.space_name}: perim={perim_m:.2f} m ({_psrc})"
                    )
                else:
                    # PB: rectangle estimate 2×(√area+1) is tighter than 4×√area
                    perim_m = round(2 * (math.sqrt(sp.area_m2) + 1), 1)
                    _perim_parts.append(
                        f"{sp.space_name}: perim≈{perim_m:.2f} m (2×(√{sp.area_m2:.1f}+1), rect-est)"
                    )
                wet_area_total  += perim_m * wall_height
                wet_floor_total += sp.area_m2
        _wet_names    = [sp.space_name for sp in wet_spaces]
        _perim_basis  = "; ".join(_perim_parts)
        _perim_rule   = "sum(space.perimeter_m × h) → ceil(×waste/sheet_area)"
        _has_dxf_perim = any(sp.perimeter_m > 0 and sp.source_type != "config"
                             for sp in wet_spaces)
        _wall_conf     = "MEDIUM" if _has_dxf_perim else "LOW"
        _wall_mr       = not _has_dxf_perim

    elif wet_rooms:
        # Legacy fallback: RoomElement — PB: use 2×(√area+1) rectangle estimate
        wet_area_total  = 0.0
        wet_floor_total = 0.0
        for room in wet_rooms:
            if room.area_m2 > 0:
                wet_area_total  += round(2 * (math.sqrt(room.area_m2) + 1), 1) * wall_height
                wet_floor_total += room.area_m2
        _wet_names    = [r.room_name for r in wet_rooms]
        _perim_basis  = f"rooms={_wet_names}: 2×(√area+1) rect estimate"
        _perim_rule   = "sum(2×(sqrt(room_area)+1)×h) → ceil(×waste/sheet_area)"
        _wall_conf    = "LOW"
        _wall_mr      = True
        _has_dxf_perim = False
    else:
        wet_area_total = 0.0

    if wet_area_total > 0:
        wet_sheets = math.ceil(wet_area_total * waste / fc_wall_area)
        # Describe perimeter source for the notes field
        _has_config_perim = (
            not _has_dxf_perim
            and wet_spaces
            and any(sp.perimeter_m > 0 for sp in wet_spaces)
        )
        if _has_dxf_perim:
            _perim_src_note = "perimeter from DXF wall-network geometry (HIGH)."
        elif _has_config_perim:
            _perim_src_note = (
                "perimeter from project_config (LOW — config-specified, not measured from drawings). "
                "Verify room perimeter against wet area schedule."
            )
        else:
            _perim_src_note = (
                "perimeter estimated from floor area (LOW — no measured perimeter available). "
                "Verify room dimensions before ordering."
            )
        rows.append(_row(
            "wall_lining_wet",
            "Wet Area Wall Lining — Waterproof Board / FC",
            "sheets", wet_sheets,
            "inferred",
            f"ceil(wet_room_wall_area × {waste} / {fc_wall_area})",
            f"spaces={_wet_names}: total_wet_wall_area≈{wet_area_total:.2f} m² [{_perim_basis}]",
            _perim_rule,
            _wall_conf,
            manual_review=_wall_mr,
            notes=(
                "Wet area lining: wall area = perimeter × wall height. "
                + _perim_src_note
                + " Verify count against wet area wall schedule."
            ),
        ))
        wet_sheet_supply_m2 = round(wet_sheets * fc_wall_area, 2)
        rows.append(_row(
            "wall_lining_wet",
            "Wet Area Wall Lining — FC Sheet Total Area (1200×2700mm)",
            "m2", wet_sheet_supply_m2,
            "inferred",
            f"sheet_count({wet_sheets}) × sheet_area({fc_wall_area}m²) — gross supply incl. waste",
            f"spaces={_wet_names}: wet_wall_area≈{wet_area_total:.2f}m² → {wet_sheets} sheets × {fc_wall_area}m²",
            f"{wet_sheets} × {fc_wall_area}",
            _wall_conf,
            manual_review=_wall_mr,
            notes=(
                f"Gross wet area FC/waterproof board supply area. "
                f"Wall area: {wet_area_total:.2f}m² × {waste} waste = {round(wet_area_total*waste,2)}m². "
                f"Rounded to {wet_sheets} full sheets × {fc_wall_area}m² = {wet_sheet_supply_m2}m². "
                "Verify from wet area wall schedule. Match sheet spec to waterproofing system."
            ),
        ))
        # Tile adhesive for wet area walls: 1 × 20kg bag covers ~4 m²
        tile_adh_bags = math.ceil(wet_area_total / 4.0)
        rows.append(_row(
            "wall_lining_wet", "Wet Area Tile Adhesive (20kg bag)",
            "bags", tile_adh_bags,
            "calculated", f"ceil(wet_wall_area({wet_area_total:.2f}) / 4 m² per bag)",
            f"spaces={_wet_names}: wet_wall_area≈{wet_area_total:.2f} m²",
            "ceil(area / 4.0)",
            _wall_conf, manual_review=_wall_mr,
            notes="20kg bag of tile adhesive ≈ 4 m² coverage. Verify spec with tiler.",
        ))
        # Grout for wet area wall tiles: 1 × 3kg bag covers ~6 m²
        grout_bags = math.ceil(wet_area_total / 6.0)
        rows.append(_row(
            "wall_lining_wet", "Wet Area Wall Tile Grout (3kg bag)",
            "bags", grout_bags,
            "calculated", f"ceil(wet_wall_area({wet_area_total:.2f}) / 6 m² per bag)",
            f"spaces={_wet_names}: wet_wall_area≈{wet_area_total:.2f} m²",
            "ceil(area / 6.0)",
            _wall_conf, manual_review=_wall_mr,
            notes="3kg bag of grout ≈ 6 m² wall tile coverage. Adjust for tile size and joint width.",
        ))
        # Waterproof membrane for wet area floor + 200mm wall upstand
        if wet_floor_total > 0:
            # Use DXF perimeter for upstand when available; else rect estimate (PB)
            if wet_spaces and any(sp.perimeter_m > 0 for sp in wet_spaces):
                _wpm_perim = sum(sp.perimeter_m if sp.perimeter_m > 0
                                 else 2 * (math.sqrt(sp.area_m2) + 1)
                                 for sp in wet_spaces if sp.area_m2 > 0)
            else:
                _wpm_perim = 2 * (math.sqrt(wet_floor_total) + 1)
            wpm_area = round(wet_floor_total + _wpm_perim * 0.2, 2)
            rows.append(_row(
                "wall_lining_wet", "Wet Area Waterproof Membrane (floor + upstand)",
                "m2", wpm_area,
                "inferred",
                f"wet_floor_area({wet_floor_total:.2f}) + perimeter×200mm upstand",
                f"spaces={_wet_names}: wet_floor_area≈{wet_floor_total:.2f} m²; perim≈{_wpm_perim:.2f} m",
                "floor_area + (perim × 0.2m upstand)",
                _wall_conf, manual_review=_wall_mr,
                notes=(
                    "Waterproof membrane: floor area + ~200mm wall upstand. "
                    "Verify method (liquid-applied vs sheet) from hydraulic engineer."
                ),
            ))

    # ── Ceiling lining ────────────────────────────────────────────────────────
    ceil_elements = model.ceilings
    if ceil_elements:
        ceil_area = sum(c.area_m2 for c in ceil_elements)
        ceil_conf = ceil_elements[0].confidence
        ceil_src  = ceil_elements[0].source
        ceil_sheets = math.ceil(ceil_area * waste / fc_ceil_area)
        ceil_batten_lm = round(ceil_area / ceil_batten_s, 1)

        rows.append(_row(
            "ceiling_lining",
            "Ceiling Lining — FC Sheet (6mm, 1200×2400)",
            "sheets", ceil_sheets,
            "calculated",
            f"ceil(ceiling_area × {waste} / {fc_ceil_area})",
            f"{ceil_src}: ceiling_area={ceil_area:.2f} m²",
            f"ceil({ceil_area:.2f} × {waste} / {fc_ceil_area})",
            ceil_conf,
        ))
        rows.append(_row(
            "ceiling_lining",
            "Ceiling Batten (LGS / timber)",
            "lm", ceil_batten_lm,
            "calculated",
            f"ceiling_area / ({ceil_batten_s * 1000:.0f} mm spacing)",
            f"{ceil_src}: ceiling_area={ceil_area:.2f} m²",
            f"{ceil_area:.2f} / {ceil_batten_s}",
            "MEDIUM",
        ))
        rows.append(_row(
            "ceiling_lining",
            "Ceiling FC Sheet Screws",
            "boxes", math.ceil(ceil_area * 20 / 200),
            "calculated", "ceil(ceil_area × 20 screws/m² / 200/box)",
            f"derived from ceiling_area={ceil_area:.2f} m²",
            "ceil(area × 20 / 200)",
            "LOW",
        ))
        rows.append(_row(
            "ceiling_lining", "Ceiling FC Sheet Adhesive / Joint Compound",
            "tubes", math.ceil(ceil_area / 3.0),
            "calculated", "ceil(ceiling_area / 3 m² per tube)",
            f"derived from ceiling_area={ceil_area:.2f} m²",
            "ceil(area / 3.0)",
            "LOW",
            notes="Construction adhesive for ceiling FC sheet. 1 tube per ~3 m².",
        ))

    # ── Verandah soffit lining ────────────────────────────────────────────────
    # Verandah soffit (underside of verandah roof) is a separate FC sheet area.
    # Source: model.verandahs → area_m2 from DXF geometry (HIGH confidence).
    # Soffit area = verandah floor area (soffit directly above the verandah deck).
    verandahs = model.verandahs
    if verandahs:
        ver_soffit_area = sum(v.area_m2 for v in verandahs)
        ver_src         = verandahs[0].source
        ver_conf        = verandahs[0].confidence
        if ver_soffit_area > 0:
            ver_sheets      = math.ceil(ver_soffit_area * waste / fc_ceil_area)
            ver_batten_lm   = round(ver_soffit_area / ceil_batten_s, 1)

            rows.append(_row(
                "ceiling_lining",
                "Verandah Soffit Lining — FC Sheet (6mm, 1200×2400)",
                "sheets", ver_sheets,
                "calculated",
                f"ceil(verandah_soffit_area × {waste} / {fc_ceil_area})",
                f"{ver_src}: verandah_area={ver_soffit_area:.2f} m² (soffit = verandah deck area)",
                f"ceil({ver_soffit_area:.2f} × {waste} / {fc_ceil_area})",
                ver_conf,
                notes=(
                    f"Verandah soffit lining: {ver_soffit_area:.2f} m² from DXF verandah polygon. "
                    "Soffit area treated equal to verandah floor area. Verify from architectural drawings."
                ),
            ))
            rows.append(_row(
                "ceiling_lining",
                "Verandah Soffit Batten (LGS / timber)",
                "lm", ver_batten_lm,
                "calculated",
                f"verandah_soffit_area / ({ceil_batten_s * 1000:.0f} mm spacing)",
                f"{ver_src}: verandah_soffit_area={ver_soffit_area:.2f} m²",
                f"{ver_soffit_area:.2f} / {ceil_batten_s}",
                "MEDIUM",
                notes=(
                    f"Verandah soffit batten: {ver_soffit_area:.2f} m² at "
                    f"{int(ceil_batten_s * 1000)} mm spacing = {ver_batten_lm:.1f} lm."
                ),
            ))

    # ── Ceiling FC Sheet combined total (ceiling + soffit) ───────────────────
    _ceil_sheet_rows = [
        r for r in rows
        if r.get("unit") == "sheets"
        and any(kw in r.get("item_name", "") for kw in ("Ceiling Lining", "Verandah Soffit"))
    ]
    if _ceil_sheet_rows:
        _total_ceil_sheets = sum(r["quantity"] for r in _ceil_sheet_rows)
        _total_ceil_area_m2 = round(_total_ceil_sheets * fc_ceil_area, 2)
        _ceil_parts = [
            f"{r['item_name'].split('—')[0].strip()}({r['quantity']})"
            for r in _ceil_sheet_rows
        ]
        rows.append(_row(
            "ceiling_lining",
            "FC Ceiling Sheet — Total Supply (ceiling + soffit)",
            "sheets", _total_ceil_sheets,
            "calculated",
            "sum of ceiling + verandah soffit FC sheet counts",
            f"derived: {' + '.join(_ceil_parts)} = {_total_ceil_sheets}",
            "sum(sheet_count: ceiling + soffit zones)",
            "MEDIUM",
            notes=(
                f"Combined ceiling FC sheet procurement total (ceiling zone + verandah soffit). "
                f"{_total_ceil_sheets} sheets × {fc_ceil_area}m² (1200×2400mm) = {_total_ceil_area_m2}m² gross. "
                "Individual zone counts (E13, E17) remain for installation trade breakdown."
            ),
        ))

    # ── Cornice / ceiling trim ────────────────────────────────────────────────
    if ext_walls:
        ext_perim = sum(w.length_m for w in ext_walls)
        rows.append(_row(
            "ceiling_trim",
            "Cornice / Ceiling Trim",
            "lm", round(ext_perim, 1),
            "calculated",
            "= ext_wall_perimeter (all internal faces of external walls)",
            f"dxf_geometry: ext_wall_perimeter={ext_perim:.2f} m",
            "cornice = ext_wall_perimeter",
            "MEDIUM",
            notes="Assumes cornice on external walls only. Add int_wall_lm if cornice on internal walls too.",
        ))
        # Cornice end caps / internal mitre sets at corners
        # Rectangular building = 4 internal corners for cornice returns
        corner_sets = 4   # conservative minimum for rectangular plan
        rows.append(_row(
            "ceiling_trim", "Cornice End Cap / Internal Mitre Set",
            "sets", corner_sets,
            "inferred",
            "4 corner sets (rectangular plan — 4 internal cornice returns)",
            f"dxf_geometry: rectangular footprint (4 internal corners assumed)",
            "rectangular plan → 4 corners",
            "LOW",
            manual_review=True,
            notes=(
                "4 internal corner mitre sets assumed for rectangular plan. "
                "Adjust if non-rectangular or if external cornice returns required. "
                "Verify with cornice supplier."
            ),
        ))

    # ── Total FC Sheet supply — primary procurement family summary ────────────
    # Accumulate all sheet-based items already in rows to produce a family total.
    # This is a procurement-level summary only — individual zone rows remain.
    total_sheets = sum(
        r["quantity"] for r in rows
        if r.get("unit") == "sheets" and "FC Sheet" in r.get("item_name", "")
    )
    if total_sheets > 0:
        # Identify which zone rows contributed
        sheet_rows = [
            r for r in rows
            if r.get("unit") == "sheets" and "FC Sheet" in r.get("item_name", "")
        ]
        zone_parts = [f"{r['item_name'].split('—')[0].strip()}({r['quantity']})" for r in sheet_rows]
        rows.append(_row(
            "ceiling_lining",
            "FC Sheet — Total Supply (all lining zones combined)",
            "sheets", total_sheets,
            "calculated",
            "sum of all FC sheet rows across ext_wall + int_wall + wet_area + ceiling + verandah_soffit zones",
            f"derived: {' + '.join(zone_parts)} = {total_sheets}",
            "sum(sheet_count per zone)",
            "MEDIUM",
            notes=(
                "Primary FC sheet procurement family total. "
                "Sum of: external wall, internal wall, wet area lining, ceiling, verandah soffit. "
                "Use for bulk sheet supply order. Individual zone counts remain for installation breakdown."
            ),
        ))

    # ── Skirting board ────────────────────────────────────────────────────────
    # External walls: skirting on interior face only = ext_lm
    # Internal partitions: skirting on BOTH faces = int_lm × 2
    # Door openings interrupt skirting: deduct door width from respective face count.
    if ext_walls or int_walls:
        ext_lm_sk = sum(w.length_m for w in ext_walls)
        int_lm_sk = sum(w.length_m for w in int_walls)

        # Deduct external entrance door widths (one interior face of external wall)
        ext_door_sk_deduct = round(sum(o.width_m * o.quantity for o in ext_door_ops), 3)
        # Deduct internal partition door widths × 2 faces each
        int_door_sk_deduct = round(sum(o.width_m * o.quantity for o in int_door_ops) * 2, 3)
        sk_total_gross = round(ext_lm_sk + int_lm_sk * 2, 1)
        sk_total       = round(sk_total_gross - ext_door_sk_deduct - int_door_sk_deduct, 1)
        sk_conf        = "MEDIUM" if int_walls and int_walls[0].confidence != "LOW" else "LOW"

        _sk_deduct_note = ""
        if ext_door_sk_deduct > 0 or int_door_sk_deduct > 0:
            _sk_deduct_note = (
                f"Door gap deductions: ext_doors={ext_door_sk_deduct:.3f} m "
                f"({', '.join(f'{o.mark}×{o.quantity}' for o in ext_door_ops)}); "
                f"int_doors_×2f={int_door_sk_deduct:.3f} m "
                f"({', '.join(f'{o.mark}×{o.quantity}' for o in int_door_ops)}). "
                f"Gross={sk_total_gross:.1f} m → net={sk_total:.1f} m. "
            )

        rows.append(_row(
            "finishes_trim",
            "Skirting Board",
            "lm", sk_total,
            "calculated",
            (f"ext_wall_lm({ext_lm_sk:.1f}) + int_wall_lm×2({int_lm_sk:.1f}×2)"
             + (f" − door_gaps({ext_door_sk_deduct:.2f}+{int_door_sk_deduct:.2f})"
                if ext_door_sk_deduct + int_door_sk_deduct > 0 else "")),
            (f"ext_walls: {ext_lm_sk:.2f} m [{ext_walls[0].source if ext_walls else ''}]; "
             f"int_walls: {int_lm_sk:.2f} m [{int_walls[0].source if int_walls else ''}]"),
            "ext_wall_perimeter + int_wall_lm × 2 − door_widths",
            sk_conf,
            manual_review=(sk_conf == "LOW"),
            notes=(
                "Skirting = external wall interior face + both faces of internal partitions, "
                "minus door opening widths. "
                + _sk_deduct_note
                + ("Internal wall lm estimated — verify from drawings." if sk_conf == "LOW" else "")
            ),
        ))

    return rows
