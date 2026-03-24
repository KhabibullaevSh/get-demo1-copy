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

    # ── External wall lining ──────────────────────────────────────────────────
    ext_walls = [w for w in model.walls if w.wall_type == "external"]
    if ext_walls:
        ext_lm    = sum(w.length_m for w in ext_walls)
        ext_h     = max(w.height_m for w in ext_walls)
        ext_area  = round(ext_lm * ext_h, 2)
        ext_conf  = max((w.confidence for w in ext_walls), key=lambda c: {"HIGH":3,"MEDIUM":2,"LOW":1}.get(c,0))
        ext_src   = ext_walls[0].source
        sheets    = math.ceil(ext_area * waste / fc_wall_area)

        rows.append(_row(
            "wall_lining_external",
            "External Wall Lining — FC Sheet (6mm, 1200×2700)",
            "sheets", sheets,
            "calculated",
            f"ceil(ext_wall_area × {waste} / {fc_wall_area})",
            f"{ext_src}: ext_wall_lm={ext_lm:.2f} m × h={ext_h:.1f} m → area={ext_area:.2f} m²",
            f"ceil({ext_area:.2f} × {waste} / {fc_wall_area})",
            ext_conf,
        ))
        rows.append(_row(
            "wall_lining_external", "External Wall Lining — FC Sheet Screws",
            "boxes", math.ceil(ext_area * 25 / 200),
            "calculated", f"ceil(wall_area × 25 screws/m² / 200/box)",
            f"derived from ext_wall_area={ext_area:.2f} m²",
            "ceil(area × 25 / 200)",
            "LOW",
        ))

    # ── Internal wall lining (both faces) ────────────────────────────────────
    int_walls = [w for w in model.walls if w.wall_type == "internal"]
    if int_walls:
        int_lm      = sum(w.length_m for w in int_walls)
        int_h       = max(w.height_m for w in int_walls)
        # WallElement.area_m2 already includes faces=2 for internal partitions.
        # Use that so sheets cover both faces without double-calculating.
        int_area_both = round(sum(w.area_m2 for w in int_walls), 2)  # both faces
        int_conf    = int_walls[0].confidence
        int_src     = int_walls[0].source
        int_note    = int_walls[0].notes or (
            "Internal wall lm estimated from floor area ratio — replace with measured value."
            if int_conf == "LOW" else ""
        )

        sheets_int = math.ceil(int_area_both * waste / fc_wall_area)
        fc_wall_w  = lining_cfg.get("fc_wall_sheet_w", 1.2)

        rows.append(_row(
            "wall_lining_internal",
            "Internal Wall Lining — FC Sheet (6mm, 1200×2700)",
            "sheets", sheets_int,
            "calculated",
            f"ceil(int_wall_area_both_faces × {waste} / {fc_wall_area})",
            f"{int_src}: int_wall_lm={int_lm:.2f} m × h={int_h:.1f} m × 2 faces = {int_area_both:.2f} m²",
            f"ceil({int_area_both:.2f} × {waste} / {fc_wall_area})",
            int_conf,
            manual_review=(int_conf == "LOW"),
            notes=int_note,
        ))
        rows.append(_row(
            "wall_lining_internal", "Internal Wall Lining — FC Sheet Screws",
            "boxes", math.ceil(int_area_both * 25 / 200),
            "calculated", f"ceil(both_face_area × 25 screws/m² / 200/box)",
            f"derived from int_wall_area_both={int_area_both:.2f} m²",
            "ceil(area × 25 / 200)",
            "LOW",
            manual_review=(int_conf == "LOW"),
            notes=int_note,
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

    # ── Wet-area wall lining (if wet rooms detected) ─────────────────────────
    wet_rooms = model.wet_rooms()
    if wet_rooms:
        # Wet area lining = perimeter × height for each wet room
        # If room area is known, estimate perimeter as 4×sqrt(area)
        wet_area_total = 0.0
        for room in wet_rooms:
            if room.area_m2 > 0:
                perim_est = round(4 * math.sqrt(room.area_m2), 1)
                wet_area_total += perim_est * wall_height
        if wet_area_total > 0:
            wet_sheets = math.ceil(wet_area_total * waste / fc_wall_area)
            rows.append(_row(
                "wall_lining_wet",
                "Wet Area Wall Lining — Waterproof Board / FC",
                "sheets", wet_sheets,
                "inferred",
                f"ceil(wet_room_wall_area × {waste} / {fc_wall_area})",
                f"rooms={[r.room_name for r in wet_rooms]}: total_wet_wall_area≈{wet_area_total:.2f} m²",
                f"sum(4×sqrt(room_area)×h) → ceil(×{waste}/{fc_wall_area})",
                "LOW",
                manual_review=True,
                notes="Wet area lining derived from room perimeter estimate. Verify room dimensions.",
            ))
        else:
            rows.append(_row(
                "wall_lining_wet",
                "Wet Area Wall Lining — Waterproof Board / FC",
                "sheets", 0,
                "placeholder",
                "wet rooms detected but no area data available",
                f"rooms={[r.room_name for r in wet_rooms]}",
                "manual review required",
                "LOW",
                manual_review=True,
                notes="Wet area rooms detected. Area data not available — measure from drawings.",
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

    # ── Skirting board ────────────────────────────────────────────────────────
    # External walls: skirting on interior face only = ext_lm
    # Internal partitions: skirting on BOTH faces = int_lm × 2
    if ext_walls or int_walls:
        ext_lm_sk = sum(w.length_m for w in ext_walls)
        int_lm_sk = sum(w.length_m for w in int_walls)
        sk_total  = round(ext_lm_sk + int_lm_sk * 2, 1)
        sk_conf   = "MEDIUM" if int_walls and int_walls[0].confidence != "LOW" else "LOW"

        rows.append(_row(
            "finishes_trim",
            "Skirting Board",
            "lm", sk_total,
            "calculated",
            f"ext_wall_lm({ext_lm_sk:.1f}) + int_wall_lm×2({int_lm_sk:.1f}×2)",
            (f"ext_walls: {ext_lm_sk:.2f} m [{ext_walls[0].source if ext_walls else ''}]; "
             f"int_walls: {int_lm_sk:.2f} m [{int_walls[0].source if int_walls else ''}]"),
            "ext_wall_perimeter + int_wall_lm × 2",
            sk_conf,
            manual_review=(sk_conf == "LOW"),
            notes=(
                "Skirting = external wall interior face + both faces of internal partitions. "
                + ("Internal wall lm estimated — verify from drawings." if sk_conf == "LOW" else "")
            ),
        ))

    return rows
