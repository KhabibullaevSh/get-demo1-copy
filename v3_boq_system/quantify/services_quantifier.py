"""
services_quantifier.py — Room-based services inference.

When direct MEP schedules are not available, this module infers probable
fixture bundles from detected room types using room_templates.yaml.

All inferred items are tagged:
  quantity_status = inferred
  confidence = medium or low
  manual_review = true

The module also produces whole-building service allowances.
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import ProjectElementModel, RoomElement

log = logging.getLogger("boq.v3.services")


def _row(
    package, item_name, unit, quantity, status, basis, evidence, rule,
    confidence, manual_review=True, notes="", item_code="",
) -> dict:
    return {
        "item_name": item_name, "item_code": item_code,
        "unit": unit, "quantity": quantity, "package": package,
        "quantity_status": status, "quantity_basis": basis,
        "source_evidence": evidence, "derivation_rule": rule,
        "confidence": confidence, "manual_review": manual_review, "notes": notes,
    }


def _classify_room(room_name: str, templates: dict) -> str:
    """Classify a room name against room_templates.yaml patterns."""
    lower = room_name.lower()
    patterns = templates.get("room_type_patterns", {})
    for rtype, keywords in patterns.items():
        if rtype == "unknown":
            continue
        for kw in (keywords or []):
            if kw.lower() in lower:
                return rtype
    return "unknown"


def quantify_services(
    model:          ProjectElementModel,
    config:         dict,
    room_templates: dict,
) -> list[dict]:
    """
    Infer services from room schedule, falling back to building-type profiles.

    Returns list of BOQ rows.
    """
    rows:    list[dict] = []
    svc_cfg  = config.get("services", {})
    floor_area = sum(f.area_m2 for f in model.floors) if model.floors else 0.0

    templates_dict = room_templates.get("room_templates", {})
    whole_bldg     = room_templates.get("whole_building_services", [])
    smoke_coverage = config.get("services", {}).get("smoke_detector_coverage_m2", 40)

    # ── Room-based inference ──────────────────────────────────────────────────
    rooms_to_process = model.rooms

    if rooms_to_process:
        log.info("Services: processing %d rooms from schedule", len(rooms_to_process))
        # Track wet rooms for waterproofing
        wet_area_total = 0.0

        for room in rooms_to_process:
            rtype = room.room_type
            if rtype == "unknown":
                rtype = _classify_room(room.room_name, room_templates)

            tmpl = templates_dict.get(rtype, templates_dict.get("unknown", {}))
            fixtures = tmpl.get("fixtures", [])

            for fix in fixtures:
                conf = fix.get("confidence", "low").upper()
                rows.append(_row(
                    "services",
                    fix["name"],
                    fix.get("unit", "nr"),
                    fix.get("qty", 1),
                    "inferred",
                    f"room_template:{rtype}",
                    f"room_schedule: {room.room_name} (type={rtype})",
                    f"room_template:{rtype} → {fix['name']}",
                    conf,
                    manual_review=True,
                    notes=fix.get("note", f"Inferred from room type '{rtype}'."),
                ))

            # Wet area wall tiling — inferred for wet rooms, LOW confidence
            if tmpl.get("wet_area") and room.area_m2 > 0:
                # Estimate wall tile area: perimeter × tile_height (1.8 m typical splash zone)
                perim_est = room.perimeter_m if room.perimeter_m > 0 else round(4 * math.sqrt(room.area_m2), 1)
                tile_h = 1.8   # standard splash-zone tile height (m)
                tile_area = round(perim_est * tile_h, 2)
                rows.append(_row(
                    "services",
                    f"Wet Area Wall Tiling — {room.room_name}",
                    "m2", tile_area,
                    "inferred",
                    f"perim_est({perim_est:.1f}m) × tile_height({tile_h}m)",
                    f"room: {room.room_name} (area={room.area_m2:.2f} m², wet_area=True)",
                    f"perim × {tile_h}m splash zone",
                    "LOW",
                    manual_review=True,
                    notes=(
                        f"Inferred: perimeter ({perim_est:.1f} m) × {tile_h*1000:.0f} mm splash-zone height. "
                        "No finish schedule in source documents. Verify tile type, height, and area "
                        "from architectural drawings. Deduct openings."
                    ),
                ))

            # Wet area waterproofing — use actual room area when available
            if tmpl.get("wet_area"):
                tmpl_area = tmpl.get("waterproofing_m2_each", 0)
                if room.area_m2 > 0:
                    # Floor area + 150 mm upstand on all walls (estimated from room perimeter)
                    room_perim_est = room.perimeter_m if room.perimeter_m > 0 else round(4 * math.sqrt(room.area_m2), 1)
                    upstand_h = 0.15   # 150 mm upstand
                    wfp_area  = round(room.area_m2 + room_perim_est * upstand_h, 2)
                    basis     = (
                        f"room_area({room.area_m2:.2f}) + perim({room_perim_est:.1f})×upstand({upstand_h}m)"
                    )
                    conf_wfp  = "MEDIUM"
                elif tmpl_area > 0:
                    wfp_area  = tmpl_area
                    basis     = f"room_template:{rtype} waterproofing_m2_each={tmpl_area}"
                    conf_wfp  = "LOW"
                else:
                    continue
                wet_area_total += wfp_area
                rows.append(_row(
                    "services",
                    "Wet Area Waterproofing — Membrane",
                    "m2", wfp_area,
                    "inferred",
                    basis,
                    f"room: {room.room_name} (area={room.area_m2:.2f} m²)",
                    basis,
                    conf_wfp,
                    notes=(
                        f"Waterproofing: floor area + 150 mm upstand on walls for {room.room_name}. "
                        "Verify room dimensions and upstand height from architectural drawings."
                    ),
                ))

    else:
        # No room schedule — use building type profile
        bldg_type = svc_cfg.get("building_type_service_profile", "unknown")
        log.info("Services: no room schedule — using building type profile '%s'", bldg_type)

        # Generic allowances based on building type
        if bldg_type in ("pharmacy", "commercial_low_rise", "office"):
            generic_services = [
                ("Builder's Works — Electrical",  "item", 0),
                ("Builder's Works — Plumbing",    "item", 0),
                ("Wet Area Waterproofing",         "m2",   0),
                ("Sanitary Fixtures (Provisional Allowance)", "item", 0),
            ]
        else:
            generic_services = [
                ("Builder's Works — Electrical",  "item", 0),
                ("Builder's Works — Plumbing",    "item", 0),
                ("Wet Area Waterproofing",         "m2",   0),
            ]

        for name, unit, qty in generic_services:
            rows.append(_row(
                "services", name, unit, qty,
                "placeholder",
                "no room schedule — building type profile placeholder",
                f"building_type={bldg_type}: no room schedule in sources",
                "manual review required",
                "LOW",
                manual_review=True,
                notes=(
                    f"No room schedule available. Add quantity from architectural drawings. "
                    f"Building type: {bldg_type}."
                ),
            ))

    # ── Mechanical / air conditioning placeholder ─────────────────────────────
    # Commercial buildings in tropical climates require air conditioning.
    # No mechanical schedule or equipment schedule is available in source documents.
    bldg_type = svc_cfg.get("building_type_service_profile", "unknown")
    if bldg_type in ("pharmacy", "commercial_low_rise", "office", "medical"):
        rows.append(_row(
            "services",
            "Air Conditioning / Mechanical Ventilation — PLACEHOLDER",
            "item", 0,
            "placeholder",
            "no mechanical schedule in source documents",
            f"building_type={bldg_type}: commercial building requires mechanical services",
            "manual review required",
            "LOW",
            manual_review=True,
            notes=(
                "PLACEHOLDER. Commercial building in tropical climate likely requires split-system "
                "or cassette AC units. No mechanical schedule or equipment schedule in source "
                "documents. Obtain from mechanical engineer or building services consultant. "
                "Include: outdoor units, indoor units, refrigerant pipework, electrical supply, "
                "condensate drainage, ceiling penetrations."
            ),
        ))
        rows.append(_row(
            "services",
            "Exhaust Fan — Wet Area (toilet / laundry)",
            "item", 0,
            "placeholder",
            "no mechanical schedule — wet area rooms require exhaust ventilation",
            f"building_type={bldg_type}: wet area ventilation required by BCA F4.6",
            "manual review required",
            "LOW",
            manual_review=True,
            notes=(
                "PLACEHOLDER. BCA F4.6 requires mechanical exhaust in rooms without openable "
                "windows. Confirm exhaust fan type, ducting, and external discharge location "
                "from architectural drawings."
            ),
        ))

    # ── Whole-building services ────────────────────────────────────────────────
    for item in whole_bldg:
        qty_rule = item.get("qty_rule", "")
        if qty_rule and floor_area > 0:
            try:
                qty = eval(qty_rule, {"__builtins__": {}},
                           {"ceil": math.ceil, "floor_area": floor_area})
            except Exception:
                qty = 0
        else:
            qty = 1

        rows.append(_row(
            "services",
            item["name"],
            item.get("unit", "item"),
            qty,
            "inferred",
            "whole_building_services template",
            f"floor_area={floor_area:.2f} m² (building-wide)",
            item.get("qty_rule", "= 1 per building"),
            item.get("confidence", "low").upper(),
            manual_review=True,
            notes=item.get("note", "Whole-building service allowance."),
        ))

    return rows


def quantify_finishes(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    """
    Produce finish items: floor finish, paint.
    Intended to be called alongside services quantifier.
    """
    rows: list[dict] = []
    cfg = config.get("finishes", {})
    floor_type  = cfg.get("floor_finish_type", "tiles")
    arch_d_lm   = cfg.get("architrave_door_lm_each", 6.0)
    arch_w_lm   = cfg.get("architrave_window_lm_each", 4.8)

    floor_area  = sum(f.area_m2 for f in model.floors)
    ext_walls   = [w for w in model.walls if w.wall_type == "external"]
    ext_lm      = sum(w.length_m for w in ext_walls)
    ext_h       = max((w.height_m for w in ext_walls), default=2.4)

    if floor_area > 0:
        rows.append({
            "item_name": f"Floor Finish — {floor_type.title()} / screed",
            "item_code": "", "unit": "m2",
            "quantity": round(floor_area, 2), "package": "finishes",
            "quantity_status": "measured",
            "quantity_basis": "DXF WALLS polygon area",
            "source_evidence": f"dxf_geometry: floor_area={floor_area:.2f} m²",
            "derivation_rule": "= floor_area_m2",
            "confidence": "HIGH", "manual_review": False, "notes": "",
        })

    if ext_lm > 0:
        paint_ext = round(ext_lm * ext_h, 2)
        rows.append({
            "item_name": "Paint — External",
            "item_code": "", "unit": "m2",
            "quantity": paint_ext, "package": "finishes",
            "quantity_status": "calculated",
            "quantity_basis": "ext_wall_perimeter × wall_height",
            "source_evidence": f"dxf_geometry: ext_wall_lm={ext_lm:.2f} × h={ext_h:.1f}",
            "derivation_rule": f"{ext_lm:.2f} × {ext_h:.1f}",
            "confidence": "MEDIUM", "manual_review": False,
            "notes": "Gross area; no deduction for openings.",
        })

    # Internal paint: ceiling + int wall BOTH faces
    # WallElement.area_m2 already includes faces=2 for internal partitions
    int_walls      = [w for w in model.walls if w.wall_type == "internal"]
    ceil_area      = sum(c.area_m2 for c in model.ceilings)
    int_wall_area  = round(sum(w.area_m2 for w in int_walls), 2)   # both faces
    paint_int      = round(ceil_area + int_wall_area, 2)
    if paint_int > 0:
        int_lm = sum(w.length_m for w in int_walls)
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
                f"int_wall_lm={int_lm:.2f} m × 2 faces [{(int_walls[0].source if int_walls else 'n/a')}]"
            ),
            "derivation_rule": "ceiling_area + sum(int_wall.area_m2)  [area_m2 = lm × h × 2 faces]",
            "confidence": "LOW" if not int_walls or int_walls[0].confidence == "LOW" else "MEDIUM",
            "manual_review": not int_walls or int_walls[0].confidence == "LOW",
            "notes": (
                "Both faces of internal partitions included. "
                + ("Internal wall lm estimated — verify from drawings." if int_walls and int_walls[0].confidence == "LOW" else "")
            ),
        })

    # Note: Architrave is computed in opening_quantifier.py with per-opening traceability.
    # Do not duplicate here.

    return rows
