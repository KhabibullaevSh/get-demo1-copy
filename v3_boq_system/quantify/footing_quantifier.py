"""
footing_quantifier.py — Substructure and footing quantification.

Sources (priority order):
  1. PDF footing schedule (count, dimensions, reinforcement)
  2. Structural notes / foundation plan geometry
  3. IFC IfcFooting elements
  4. Derived from floor area + config (lowest confidence)

Produces:
  - Concrete volume
  - Formwork
  - Mesh / reinforcement
  - Vapour barrier
  - Excavation allowance
  - Tie wire / chairs
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import FootingElement, ProjectElementModel

log = logging.getLogger("boq.v3.footings")


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


def quantify_footings(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    rows: list[dict] = []
    fgt_cfg  = config.get("footings", {})
    fgt_type = fgt_cfg.get("type", "slab_on_ground")

    footings   = model.footings
    floors     = model.floors
    floor_area = sum(f.area_m2 for f in floors)
    ext_perim  = sum(f.perimeter_m for f in floors)

    if not footings and floor_area == 0:
        return rows

    # Detect steel floor frame — skip slab, emit strip footings instead
    has_steel_floor = any(
        fs.assembly_type in ("steel_floor_frame", "floor_panel", "floor_joist")
        for fs in model.floor_systems
    )

    # ── Strip footings (for steel floor frame bearing lines) ─────────────────
    if has_steel_floor and ext_perim > 0:
        depth = fgt_cfg.get("strip_footing_depth_m", 0.50)
        width = fgt_cfg.get("strip_footing_width_m", 0.40)
        vol   = round(ext_perim * depth * width, 2)
        src   = "derived_steel_floor"
        conf  = "LOW"
        note  = (f"Strip footing assumed for steel floor frame bearing lines. "
                 f"Assumed {int(depth*1000)} mm deep × {int(width*1000)} mm wide. "
                 "Verify type and dimensions from structural drawings.")
        rows.append(_row(
            "footings", "Strip Footing — External Perimeter",
            "lm", round(ext_perim, 1),
            "calculated", "= ext_wall_perimeter (steel floor bearing line)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "= ext_wall_perimeter",
            "MEDIUM",
            notes=note,
        ))
        rows.append(_row(
            "footings", "Strip Footing — Concrete Volume",
            "m3", vol,
            "calculated",
            f"perimeter × {depth} m depth × {width} m width",
            f"{src}: {ext_perim:.1f} × {depth} × {width}",
            f"{ext_perim:.1f} × {depth} × {width}",
            conf,
            manual_review=True,
            notes=note,
        ))
        rows.append(_row(
            "footings", "Strip Footing — Formwork",
            "lm", round(ext_perim, 1),
            "calculated", "= perimeter (strip footing edge form)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "= ext_wall_perimeter",
            "MEDIUM",
        ))
        rows.append(_row(
            "footings", "Bulk Earthworks / Level (provisional)",
            "m3", 0,
            "placeholder", "site survey required",
            "no site survey in sources",
            "manual review required",
            "LOW",
            manual_review=True,
            notes="Excavation volume cannot be derived from architectural documents.",
        ))
        # Also emit internal bearing line footing based on internal wall total
        int_wall_lm = sum(
            w.length_m for w in model.walls if w.wall_type == "internal"
        )
        if int_wall_lm > 0:
            depth  = fgt_cfg.get("strip_footing_depth_m", 0.50)
            width  = fgt_cfg.get("strip_footing_width_m", 0.40)
            int_vol = round(int_wall_lm * depth * width, 2)
            rows.append(_row(
                "footings", "Strip Footing — Internal Bearing Lines",
                "lm", round(int_wall_lm, 1),
                "calculated", "= int_wall_lm (DXF measured internal wall runs)",
                f"dxf_geometry: int_wall_lm={int_wall_lm:.1f} m",
                "= int_wall_perimeter",
                "MEDIUM",
                notes=(
                    f"Internal bearing lines assumed at each internal wall run. "
                    f"Total: {int_wall_lm:.1f} lm. Verify from structural drawings — "
                    "actual bearing lines may differ from partition walls."
                ),
            ))
            rows.append(_row(
                "footings", "Strip Footing Internal — Concrete Volume",
                "m3", int_vol,
                "calculated",
                f"int_lm × {depth} m depth × {width} m width",
                f"derived: {int_wall_lm:.1f} × {depth} × {width}",
                f"{int_wall_lm:.1f} × {depth} × {width}",
                "LOW",
                manual_review=True,
                notes=f"Assumed {int(depth*1000)} mm deep × {int(width*1000)} mm wide. Verify.",
            ))
        # Fall through to pad footing section below

    # ── Slab on ground ────────────────────────────────────────────────────────
    slabs = [f for f in footings if f.footing_type == "slab"]
    if (slabs or fgt_type == "slab_on_ground") and not has_steel_floor:
        slab = slabs[0] if slabs else None
        area   = slab.area_m2   if slab else floor_area
        perim  = slab.perimeter_m if slab else ext_perim
        thick  = (slab.thickness_mm if slab else fgt_cfg.get("slab_thickness_mm", 100)) or 100
        conc_m3 = round(area * thick / 1000, 2)
        mesh   = (slab.reinforcement if slab else fgt_cfg.get("mesh_type", "SL72"))
        src    = slab.source if slab else "derived"
        conf   = slab.confidence if slab else "LOW"
        note   = slab.notes if slab else "Slab on ground assumed — verify from structural drawings."

        # Concrete
        rows.append(_row(
            "footings", "Slab on Ground — Concrete Pour",
            "m2", round(area, 2),
            "inferred" if conf == "LOW" else "measured",
            f"{src}: slab_area",
            f"{src}: area={area:.2f} m²",
            "= floor_area_m2",
            conf,
            manual_review=(conf == "LOW"),
            notes=note,
        ))
        rows.append(_row(
            "footings", "Slab Concrete Volume",
            "m3", conc_m3,
            "calculated",
            f"slab_area × {thick} mm / 1000",
            f"{src}: area={area:.2f} × thickness={thick} mm",
            f"{area:.2f} × {thick}/1000",
            "LOW",
            manual_review=True,
            notes=f"Assumes {thick} mm slab. Verify from structural drawings.",
        ))

        # Mesh
        rows.append(_row(
            "footings", f"Slab Mesh ({mesh})",
            "m2", round(area * 1.1, 2),
            "calculated", "slab_area × 1.10 (10% lap waste)",
            f"{src}: area={area:.2f} m²",
            "area × 1.1",
            "LOW",
            manual_review=True,
            notes=f"Mesh type {mesh} assumed. Verify from structural engineer.",
        ))

        # Vapour barrier
        rows.append(_row(
            "footings", "Vapour Barrier (200 µm)",
            "m2", round(area * 1.1, 2),
            "calculated", "slab_area × 1.10",
            f"{src}: area={area:.2f} m²",
            "area × 1.1",
            "LOW",
        ))

        # Edge formwork
        if perim > 0:
            rows.append(_row(
                "footings", "Slab Edge Formwork",
                "lm", round(perim, 1),
                "calculated", "= ext_wall_perimeter",
                f"{src}: perimeter={perim:.2f} m",
                "= perimeter",
                "MEDIUM",
            ))

        # Chairs / spacers
        chairs = math.ceil(area / 1.0)   # 1 per m² approx
        rows.append(_row(
            "footings", "Bar Chair / Mesh Spacer",
            "nr", chairs,
            "calculated", "1 per m² of slab area",
            f"derived from area={area:.2f} m²",
            "ceil(area / 1.0)",
            "LOW",
        ))

        # Excavation / bulk earthworks
        rows.append(_row(
            "footings", "Bulk Earthworks / Level (provisional)",
            "m3", 0,
            "placeholder", "site survey required",
            "no site survey in sources",
            "manual review required",
            "LOW",
            manual_review=True,
            notes="Excavation volume cannot be derived from architectural documents. "
                  "Confirm with civil engineer / site survey.",
        ))

    # ── Pad footings ──────────────────────────────────────────────────────────
    pads = [f for f in footings if f.footing_type == "pad"]
    if pads:
        total_pads = sum(f.count for f in pads)
        pad_size  = fgt_cfg.get("pad_size_m", 0.70)
        pad_depth = fgt_cfg.get("pad_depth_m", 0.70)
        pad_vol   = round(pad_size ** 2 * pad_depth, 3)

        rows.append(_row(
            "footings", "Pad Footing (concrete)",
            "nr", total_pads,
            "measured" if pads[0].source != "derived" else "inferred",
            f"{pads[0].source}: pad_count",
            f"{pads[0].source}: count={total_pads}",
            "direct count",
            pads[0].confidence,
            manual_review=(pads[0].confidence == "LOW"),
        ))
        rows.append(_row(
            "footings", "Pad Footing Concrete (m3)",
            "m3", round(total_pads * pad_vol, 3),
            "calculated",
            f"count × {pad_size}×{pad_size}×{pad_depth} m = {pad_vol:.3f} m³ each",
            f"derived: {total_pads} pads × {pad_vol:.3f} m³",
            f"{total_pads} × {pad_vol:.3f}",
            "LOW",
            manual_review=True,
            notes=f"Assumes {int(pad_size*1000)}×{int(pad_size*1000)}×{int(pad_depth*1000)} mm pads. "
                  "Verify from structural engineer.",
        ))

    return rows
