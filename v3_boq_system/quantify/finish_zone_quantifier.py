"""
finish_zone_quantifier.py — Drive finish BOQ rows from the space model.

PASS 2: compute finish zone area summaries from SpaceElement list.
PASS 3: emit BOQ rows with full space-model traceability.

Replaces quantify_finishes() in services_quantifier.py.
Sources floor finish areas from SpaceElement list; falls back to config when
no spaces are populated (ensures backward compatibility).

When canonical_geom is provided (step [2d] in main.py), uses the pre-aggregated
CanonicalFloorZone objects for improved traceability and consistent truth_class
propagation.  Falls back to element model spaces when canonical is absent.

Non-negotiable rule: no quantities sourced from BOQ template files.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from v3_boq_system.normalize.element_model import ProjectElementModel

if TYPE_CHECKING:
    from v3_boq_system.normalize.canonical_objects import CanonicalGeometryModel

log = logging.getLogger("boq.v3.finish_zone")


def quantify_finish_zones(
    element_model:  ProjectElementModel,
    config:         dict,
    canonical_geom: "Optional[CanonicalGeometryModel]" = None,
) -> list[dict]:
    """
    Produce finish BOQ rows from the space model.

    Priority:
      1. canonical_geom.floor_zones (if available) — best traceability
      2. element_model.spaces (space model path)
      3. config room_schedule fallback (when spaces list is empty)

    Floor finish (dry + wet) rows cite contributing_space_refs and
    quantity_rule_used for full traceability.  Paint rows are derived from
    wall geometry (unchanged).
    """
    rows:   list[dict] = []
    spaces  = element_model.spaces

    # Canonical path — use pre-aggregated floor zones when available
    if canonical_geom is not None and canonical_geom.floor_zones:
        rows += _floor_finish_from_canonical(canonical_geom)
        log.debug("finish_zones: using canonical floor zones (%d)", len(canonical_geom.floor_zones))
    elif spaces:
        rows += _floor_finish_from_spaces(element_model, config, spaces)
        log.debug("finish_zones: using element model spaces (%d)", len(spaces))
    else:
        rows += _floor_finish_from_config(element_model, config)
        log.debug("finish_zones: using config fallback")

    rows += _paint_rows(element_model)
    return rows


# ── Floor finish — canonical floor zones path ────────────────────────────────

def _floor_finish_from_canonical(canonical_geom: "CanonicalGeometryModel") -> list[dict]:
    """
    Produce floor finish rows from canonical CanonicalFloorZone objects.

    Uses pre-aggregated zones with explicit TruthClass and evidence lists.
    Produces identical item_name/unit/quantity structure to the space model path,
    but sources the evidence from canonical geometry for better traceability.

    Verandah zones are excluded here — WPC decking is in the K-package.
    """
    import math as _math
    from v3_boq_system.normalize.canonical_objects import TruthClass

    rows: list[dict] = []

    dry_zones = [fz for fz in canonical_geom.floor_zones if fz.zone_type == "internal_dry"]
    wet_zones = [fz for fz in canonical_geom.floor_zones if fz.zone_type == "internal_wet"]
    ver_area  = sum(
        fz.area_m2 for fz in canonical_geom.floor_zones if fz.zone_type == "verandah"
    )

    # ── Dry zone ──────────────────────────────────────────────────────────────
    for fz in dry_zones:
        if fz.area_m2 <= 0:
            continue
        tc_status = TruthClass.to_quantity_status(fz.truth_class)
        _src = (
            f"canonical_geometry/floor_zone/{fz.id}: "
            f"{fz.zone_name} = {fz.area_m2:.2f} m² "
            f"({len(fz.space_ids)} spaces, truth_class={fz.truth_class})"
        )
        rows.append({
            "item_name":  "Floor Finish — Dry Zone (vinyl plank)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   fz.area_m2,
            "package":    "finishes",
            "quantity_status":      tc_status,
            "quantity_basis":       f"canonical_floor_zone/{fz.zone_type}: sum(space areas) [zone: {fz.id}]",
            "source_evidence":      _src,
            "derivation_rule":      "sum(space.area_m2 for s in canonical dry_zone.space_ids)",
            "confidence":           fz.confidence,
            "manual_review":        fz.confidence != "HIGH",
            "notes": (
                f"[Zone: internal_dry / canonical] {fz.zone_name}. "
                f"Verandah ({ver_area:.1f} m²) excluded — WPC decking in K-package. "
                f"truth_class={fz.truth_class}. "
                + (fz.notes or "")
            ),
            "contributing_space_refs": fz.space_ids,
            "quantity_rule_used":
                "canonical_floor_zone.area_m2 (pre-aggregated in geometry_reconciler)",
        })
        _vinyl_waste  = 1.10
        _vinyl_supply = round(fz.area_m2 * _vinyl_waste, 1)
        rows.append({
            "item_name":  "Vinyl Plank — Supply Total (10% cut waste)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   _vinyl_supply,
            "package":    "finishes",
            "quantity_status":  tc_status,
            "quantity_basis":   f"canonical_dry_zone({fz.area_m2:.2f}) × 1.10 cut waste",
            "source_evidence":  _src,
            "derivation_rule":  f"{fz.area_m2:.2f} × 1.10",
            "confidence":       fz.confidence,
            "manual_review":    True,
            "notes": (
                f"[Zone: internal_dry / canonical] Vinyl plank supply: "
                f"{fz.area_m2:.2f}m² net + 10% cut waste = {_vinyl_supply}m². "
                "Spec: LVT/SPC vinyl plank — thickness and finish to be confirmed."
            ),
        })

    # ── Wet zone ──────────────────────────────────────────────────────────────
    for fz in wet_zones:
        if fz.area_m2 <= 0:
            continue
        tc_status = TruthClass.to_quantity_status(fz.truth_class)
        _src = (
            f"canonical_geometry/floor_zone/{fz.id}: "
            f"{fz.zone_name} = {fz.area_m2:.2f} m² "
            f"({len(fz.space_ids)} spaces, truth_class={fz.truth_class})"
        )
        rows.append({
            "item_name":  "Floor Finish — Wet Zone (ceramic tile)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   fz.area_m2,
            "package":    "finishes",
            "quantity_status":      tc_status,
            "quantity_basis":       f"canonical_floor_zone/{fz.zone_type}: sum(wet space areas) [zone: {fz.id}]",
            "source_evidence":      _src,
            "derivation_rule":      "sum(space.area_m2 for s in canonical wet_zone.space_ids)",
            "confidence":           fz.confidence,
            "manual_review":        fz.confidence != "HIGH",
            "notes": (
                f"[Zone: internal_wet / canonical] {fz.zone_name}. "
                f"Ceramic tile (non-slip). truth_class={fz.truth_class}. "
                "Verify wet zone classification with architect. "
                + (fz.notes or "")
            ),
            "contributing_space_refs": fz.space_ids,
            "quantity_rule_used":
                "canonical_floor_zone.area_m2 (pre-aggregated in geometry_reconciler)",
        })
        _tile_waste  = 1.15
        _tile_supply = round(fz.area_m2 * _tile_waste, 2)
        rows.append({
            "item_name": "Ceramic Floor Tile — Supply Total (15% cut waste)",
            "item_code": "", "unit": "m2",
            "quantity": _tile_supply, "package": "finishes",
            "quantity_status": tc_status,
            "quantity_basis": f"canonical_wet_zone({fz.area_m2:.2f}) × 1.15 cut waste",
            "source_evidence": _src,
            "derivation_rule": f"{fz.area_m2:.2f} × 1.15",
            "confidence": fz.confidence, "manual_review": True,
            "notes": (
                f"Ceramic floor tile supply: {fz.area_m2:.2f}m² + 15% cut waste "
                f"= {_tile_supply}m². "
                "Tile spec (size, finish, slip rating) to be confirmed by architect."
            ),
        })
        tile_adh_bags = _math.ceil(fz.area_m2 / 4.0)
        rows.append({
            "item_name": "Floor Tile Adhesive — Wet Area (20kg bag)",
            "item_code": "", "unit": "bags",
            "quantity": tile_adh_bags, "package": "finishes",
            "quantity_status": tc_status,
            "quantity_basis": "ceil(canonical_wet_zone_area / 4 m² per bag)",
            "source_evidence": _src,
            "derivation_rule": "ceil(wet_area / 4.0)",
            "confidence": "LOW", "manual_review": True,
            "notes": f"20kg bag of tile adhesive ≈ 4 m² coverage on floor. Verify tile size and spec.",
        })
        grout_bags = _math.ceil(fz.area_m2 / 6.0)
        rows.append({
            "item_name": "Floor Tile Grout — Wet Area (3kg bag)",
            "item_code": "", "unit": "bags",
            "quantity": grout_bags, "package": "finishes",
            "quantity_status": tc_status,
            "quantity_basis": "ceil(canonical_wet_zone_area / 6 m² per bag)",
            "source_evidence": _src,
            "derivation_rule": "ceil(wet_area / 6.0)",
            "confidence": "LOW", "manual_review": True,
            "notes": f"3kg bag of grout ≈ 6 m² floor tile coverage. Adjust for joint width.",
        })

    return rows


# ── Floor finish — space model path ──────────────────────────────────────────

def _floor_finish_from_spaces(
    element_model: ProjectElementModel,
    config:        dict,
    spaces,
) -> list[dict]:
    from v3_boq_system.normalize.space_builder import compute_finish_zone_summary

    zone = compute_finish_zone_summary(spaces)
    dry_area = zone["dry_internal_floor_area_m2"]
    wet_area = zone["wet_floor_area_m2"]
    ver_area = zone["verandah_floor_area_m2"]

    dry_spaces = [s for s in spaces if not s.is_wet and s.is_enclosed]
    wet_spaces  = [s for s in spaces if s.is_wet and s.is_enclosed]

    # Determine if spaces are config-backed (no source geometry)
    _all_config = all(s.source_type == "config" for s in dry_spaces + wet_spaces)
    _ev_prefix  = "config_backed → space_model: " if _all_config else "space_model: "

    # Substrate cross-reference note (layered assembly traceability)
    _substrate_total = round(dry_area + wet_area, 2)
    _substrate_ref = (
        f"LAYERED ASSEMBLY — FINISH LAYER: this row is the finish above the FC sheet "
        f"substrate (G-package, {_substrate_total:.2f} m² total internal substrate). "
        f"Substrate covers all internal zones; finish type differs per zone. "
        + (f"External verandah ({ver_area:.1f} m²) uses WPC decking (K-package) — "
           "no FC substrate and no finish row here. " if ver_area > 0 else "")
    )

    rows = []

    if dry_area > 0:
        import math as _math
        dry_refs   = [f"{s.space_name} ({s.area_m2:.1f} m²)" for s in dry_spaces]
        space_ids  = [s.space_id for s in dry_spaces]
        conf       = _zone_confidence(dry_spaces)
        rows.append({
            "item_name":  "Floor Finish — Dry Zone (vinyl plank)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   dry_area,
            "package":    "finishes",
            "quantity_status":      "calculated",
            "quantity_basis":       "sum(dry_space.area_m2) from space model [zone: internal_dry]",
            "source_evidence": (
                f"{_ev_prefix}{', '.join(dry_refs)} = {dry_area:.2f} m²"
            ),
            "derivation_rule": (
                "sum(space.area_m2 for s in spaces if not s.is_wet and s.is_enclosed)"
            ),
            "confidence":    conf,
            "manual_review": conf != "HIGH",
            "notes": (
                f"[Zone: internal_dry] Vinyl plank finish for {len(dry_spaces)} dry internal space(s). "
                f"Verandah ({ver_area:.1f} m²) excluded — WPC decking in K-package. "
                + _source_note(dry_spaces)
                + "  " + _substrate_ref
            ),
            "contributing_space_refs": space_ids,
            "quantity_rule_used":
                "sum(space.area_m2 for s in spaces if not s.is_wet and s.is_enclosed)",
        })
        # Vinyl plank supply total with cut waste — primary procurement family
        _vinyl_waste = 1.10  # 10% allowance for plank cutting / room geometry
        _vinyl_supply = round(dry_area * _vinyl_waste, 1)
        rows.append({
            "item_name":  "Vinyl Plank — Supply Total (10% cut waste)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   _vinyl_supply,
            "package":    "finishes",
            "quantity_status":  "calculated",
            "quantity_basis":   f"dry_zone_area({dry_area:.2f}) × 1.10 cut waste [zone: internal_dry]",
            "source_evidence":  f"{_ev_prefix}{', '.join(dry_refs)} = {dry_area:.2f} m²",
            "derivation_rule":  f"{dry_area:.2f} × 1.10",
            "confidence":       conf,
            "manual_review":    True,
            "notes": (
                f"[Zone: internal_dry] Vinyl plank supply: {dry_area:.2f}m² net + 10% cut waste "
                f"= {_vinyl_supply}m². "
                "10% is standard for rectangular rooms with plank direction runs. "
                "Adjust for irregular layout or diagonal pattern. "
                "Spec: LVT/SPC vinyl plank — thickness and finish to be confirmed by architect."
            ),
        })

    if wet_area > 0:
        import math as _math
        wet_refs  = [f"{s.space_name} ({s.area_m2:.1f} m²)" for s in wet_spaces]
        space_ids = [s.space_id for s in wet_spaces]
        conf      = _zone_confidence(wet_spaces)
        rows.append({
            "item_name":  "Floor Finish — Wet Zone (ceramic tile)",
            "item_code":  "",
            "unit":       "m2",
            "quantity":   wet_area,
            "package":    "finishes",
            "quantity_status":      "calculated",
            "quantity_basis":       "sum(wet_space.area_m2) from space model [zone: internal_wet]",
            "source_evidence": (
                f"{_ev_prefix}{', '.join(wet_refs)} = {wet_area:.2f} m²"
            ),
            "derivation_rule": (
                "sum(space.area_m2 for s in spaces if s.is_wet and s.is_enclosed)"
            ),
            "confidence":    conf,
            "manual_review": conf != "HIGH",
            "notes": (
                f"[Zone: internal_wet] Ceramic tile (non-slip) for wet space(s): "
                f"{', '.join(s.space_name for s in wet_spaces)} ({wet_area:.2f} m²). "
                + _source_note(wet_spaces)
                + " Verify wet zone classification with architect. "
                + "  " + _substrate_ref
            ),
            "contributing_space_refs": space_ids,
            "quantity_rule_used":
                "sum(space.area_m2 for s in spaces if s.is_wet and s.is_enclosed)",
        })
        # Floor tile supply total with cut waste — primary procurement family
        _tile_waste = 1.15  # 15% cut waste for ceramic tiles
        _tile_supply = round(wet_area * _tile_waste, 2)
        rows.append({
            "item_name": "Ceramic Floor Tile — Supply Total (15% cut waste)",
            "item_code": "", "unit": "m2",
            "quantity": _tile_supply, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": f"wet_floor_area({wet_area:.2f}) × 1.15 cut waste",
            "source_evidence": f"{_ev_prefix}{', '.join(wet_refs)} = {wet_area:.2f} m²",
            "derivation_rule": f"{wet_area:.2f} × 1.15",
            "confidence": conf, "manual_review": True,
            "notes": (
                f"Ceramic floor tile supply order quantity = {wet_area:.2f}m² net area + 15% cut waste "
                f"= {_tile_supply}m². "
                "15% is standard for square tiles in a regular wet room. "
                "Tile spec (size, finish, slip rating) to be confirmed by architect. "
                "Verify with wet area tile schedule before ordering."
            ),
        })
        # Floor tile adhesive: 1 × 20kg bag covers ~4 m²
        tile_adh_bags = _math.ceil(wet_area / 4.0)
        rows.append({
            "item_name": "Floor Tile Adhesive — Wet Area (20kg bag)",
            "item_code": "", "unit": "bags",
            "quantity": tile_adh_bags, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": "ceil(wet_floor_area / 4 m² per bag)",
            "source_evidence": f"{_ev_prefix}{', '.join(wet_refs)} = {wet_area:.2f} m²",
            "derivation_rule": "ceil(wet_area / 4.0)",
            "confidence": "LOW", "manual_review": True,
            "notes": f"20kg bag of tile adhesive ≈ 4 m² coverage on floor. Verify tile size and spec.",
        })
        # Floor tile grout: 1 × 3kg bag covers ~6 m²
        grout_bags = _math.ceil(wet_area / 6.0)
        rows.append({
            "item_name": "Floor Tile Grout — Wet Area (3kg bag)",
            "item_code": "", "unit": "bags",
            "quantity": grout_bags, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": "ceil(wet_floor_area / 6 m² per bag)",
            "source_evidence": f"{_ev_prefix}{', '.join(wet_refs)} = {wet_area:.2f} m²",
            "derivation_rule": "ceil(wet_area / 6.0)",
            "confidence": "LOW", "manual_review": True,
            "notes": f"3kg bag of grout ≈ 6 m² floor tile coverage. Adjust for joint width and tile size.",
        })

    return rows


def _zone_confidence(spaces) -> str:
    """Aggregate confidence across a group of spaces."""
    if not spaces:
        return "LOW"
    confs = {s.confidence for s in spaces}
    if "HIGH" in confs and "LOW" not in confs:
        return "HIGH"
    if "LOW" in confs:
        return "LOW"
    return "MEDIUM"


def _source_note(spaces) -> str:
    sources = {s.source_type for s in spaces}
    if sources == {"config"}:
        return (
            "CONFIG-BACKED FALLBACK: Space areas from config room_schedule only. "
            "No room polygons in DXF; no IfcSpace in IFC. "
            "These are estimated areas — verify all room dimensions from architectural drawings."
        )
    if "dxf" in sources:
        return "Space areas from DXF geometry (source-derived)."
    if "ifc" in sources:
        return "Space areas from IFC IfcSpace objects (source-derived)."
    return ""


# ── Floor finish — config fallback (no space model) ──────────────────────────

def _floor_finish_from_config(
    element_model: ProjectElementModel,
    config:        dict,
) -> list[dict]:
    """
    Fallback: read floor finish areas directly from config room_schedule.
    Used when element_model.spaces is empty (space model not built yet).
    """
    rows         = []
    floor_area   = sum(f.area_m2 for f in element_model.floors)
    ver_area     = sum(v.area_m2 for v in element_model.verandahs)
    enclosed_area = round(max(0.0, floor_area - ver_area), 2)

    room_schedule = config.get("room_schedule", [])
    wet_area_sum  = round(sum(r.get("area_m2", 0.0) for r in room_schedule if r.get("is_wet_area")), 2)
    dry_area_sum  = round(sum(r.get("area_m2", 0.0) for r in room_schedule if not r.get("is_wet_area")), 2)
    rooms_total   = round(wet_area_sum + dry_area_sum, 2)
    split_ok = (
        len(room_schedule) > 0
        and wet_area_sum > 0
        and rooms_total > 0
        and abs(rooms_total - enclosed_area) / max(enclosed_area, 1.0) < 0.10
    )

    if enclosed_area <= 0:
        return rows

    if split_ok:
        if dry_area_sum > 0:
            rows.append({
                "item_name": "Floor Finish — Dry Area (vinyl plank / screed)",
                "item_code": "", "unit": "m2",
                "quantity": dry_area_sum, "package": "finishes",
                "quantity_status": "calculated",
                "quantity_basis": "sum(dry room areas from room_schedule)",
                "source_evidence": (
                    f"config room_schedule: "
                    f"{', '.join(r['name'] for r in room_schedule if not r.get('is_wet_area'))} "
                    f"= {dry_area_sum:.2f} m² (enclosed={enclosed_area:.2f} m²)"
                ),
                "derivation_rule": "sum(room_area for non-wet rooms)",
                "confidence": "MEDIUM", "manual_review": False,
                "notes": (
                    f"Dry floor finish area from room schedule. "
                    f"Verandah ({ver_area:.1f} m²) excluded — separate decking row. "
                    "Verify room areas from architectural drawings."
                ),
            })
        if wet_area_sum > 0:
            rows.append({
                "item_name": "Floor Finish — Wet Area (ceramic tile)",
                "item_code": "", "unit": "m2",
                "quantity": wet_area_sum, "package": "finishes",
                "quantity_status": "calculated",
                "quantity_basis": "sum(wet room areas from room_schedule)",
                "source_evidence": (
                    f"config room_schedule: "
                    f"{', '.join(r['name'] for r in room_schedule if r.get('is_wet_area'))} "
                    f"= {wet_area_sum:.2f} m²"
                ),
                "derivation_rule": "sum(room_area for wet rooms)",
                "confidence": "MEDIUM", "manual_review": False,
                "notes": (
                    f"Wet floor finish (ceramic tile / non-slip). "
                    f"({wet_area_sum:.2f} m²). Verify with architect."
                ),
            })
    else:
        floor_type = config.get("finishes", {}).get("floor_finish_type", "tiles")
        rows.append({
            "item_name": f"Floor Finish — {floor_type.title()} / screed",
            "item_code": "", "unit": "m2",
            "quantity": enclosed_area, "package": "finishes",
            "quantity_status": "measured",
            "quantity_basis": "DXF floor polygon − verandah area" if ver_area > 0 else "DXF WALLS polygon area",
            "source_evidence": (
                f"dxf_geometry: floor_area={floor_area:.2f} m²"
                + (f" − verandah={ver_area:.2f} m² = {enclosed_area:.2f} m²" if ver_area > 0 else "")
            ),
            "derivation_rule": "floor_area − verandah_area",
            "confidence": "HIGH", "manual_review": False,
            "notes": (
                f"Verandah ({ver_area:.1f} m²) excluded — separate decking row. "
                "No room schedule available for wet/dry split."
            ) if ver_area > 0 else "",
        })

    return rows


# ── Paint rows ────────────────────────────────────────────────────────────────

def _paint_rows(element_model: ProjectElementModel) -> list[dict]:
    import math as _math
    rows = []

    # External paint
    ext_walls = [w for w in element_model.walls if w.wall_type == "external"]
    ext_lm = sum(w.length_m for w in ext_walls)
    ext_h  = max((w.height_m for w in ext_walls), default=2.4)
    ext_paint_area = round(ext_lm * ext_h, 2) if ext_lm > 0 else 0.0

    if ext_lm > 0:
        rows.append({
            "item_name": "Paint — External",
            "item_code": "", "unit": "m2",
            "quantity": ext_paint_area, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": "ext_wall_perimeter × wall_height",
            "source_evidence": f"dxf_geometry: ext_wall_lm={ext_lm:.2f} × h={ext_h:.1f}",
            "derivation_rule": f"{ext_lm:.2f} × {ext_h:.1f}",
            "confidence": "MEDIUM", "manual_review": False,
            "notes": "Gross area; no deduction for openings.",
        })

    # Internal paint: ceiling + both faces of internal partitions
    int_walls     = [w for w in element_model.walls if w.wall_type == "internal"]
    ceil_area     = sum(c.area_m2 for c in element_model.ceilings)
    int_wall_area = round(sum(w.area_m2 for w in int_walls), 2)  # area_m2 already = lm × h × faces
    paint_int     = round(ceil_area + int_wall_area, 2)

    if paint_int > 0:
        int_lm = sum(w.length_m for w in int_walls)
        conf   = "LOW" if not int_walls or int_walls[0].confidence == "LOW" else "MEDIUM"
        rows.append({
            "item_name": "Paint — Internal",
            "item_code": "", "unit": "m2",
            "quantity": paint_int, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": (
                f"ceiling_area({ceil_area:.2f}) + int_wall_both_faces({int_wall_area:.2f})"
            ),
            "source_evidence": (
                f"ceiling_area={ceil_area:.2f} m²; "
                f"int_wall_lm={int_lm:.2f} m × 2 faces "
                f"[{int_walls[0].source if int_walls else 'n/a'}]"
            ),
            "derivation_rule": "ceiling_area + sum(int_wall.area_m2)  [area_m2 = lm × h × 2 faces]",
            "confidence": conf,
            "manual_review": conf == "LOW",
            "notes": (
                "Both faces of internal partitions included. "
                + ("Internal wall lm estimated — verify from drawings." if conf == "LOW" else "")
            ),
        })

    # Paint primer coat (exterior + interior combined)
    total_paint_area = round(ext_paint_area + paint_int, 2)
    if total_paint_area > 0:
        # 10L can covers ~12 m² (2-coat primer system)
        primer_cans = _math.ceil(total_paint_area / 12.0)
        rows.append({
            "item_name": "Paint Primer / Sealer Coat (10L can)",
            "item_code": "", "unit": "cans",
            "quantity": primer_cans, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": f"ceil((ext_paint({ext_paint_area:.2f}) + int_paint({paint_int:.2f})) / 12 m² per can)",
            "source_evidence": (
                f"total_paint_area={total_paint_area:.2f} m² "
                f"(ext={ext_paint_area:.2f} + int={paint_int:.2f})"
            ),
            "derivation_rule": "ceil(total_paint_area / 12.0)",
            "confidence": "LOW", "manual_review": True,
            "notes": (
                "Primer/sealer for FC sheet surfaces before topcoat. "
                "10L can ≈ 12 m² at 1 coat. Adjust for 2-coat system and specific product coverage. "
                "Verify primer type (FC-compatible acrylic sealer) with painter."
            ),
        })

    return rows
