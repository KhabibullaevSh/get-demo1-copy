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

    # Determine whether footing dimensions are project-specific (explicitly set
    # in the project config file) or only generic defaults.  When both depth and
    # width are present in the loaded config dict, treat them as project-specific
    # and use MEDIUM confidence for derived concrete volumes.
    _dim_depth_explicit = "strip_footing_depth_m" in fgt_cfg
    _dim_width_explicit = "strip_footing_width_m" in fgt_cfg
    _dim_pad_explicit   = ("pad_size_m" in fgt_cfg) and ("pad_depth_m" in fgt_cfg)
    _conc_conf = "MEDIUM" if (_dim_depth_explicit and _dim_width_explicit) else "LOW"
    _pad_conc_conf = "MEDIUM" if _dim_pad_explicit else "LOW"

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
            f"perimeter({ext_perim:.1f}m) × depth({depth}m) × width({width}m)",
            (
                f"dxf_geometry: ext_perim={ext_perim:.1f}m (MEDIUM); "
                f"project_config: depth={depth}m width={width}m"
                + (" (project-specific)" if _dim_depth_explicit else " (default)")
            ),
            f"{ext_perim:.1f} × {depth} × {width}",
            _conc_conf,
            manual_review=(_conc_conf == "LOW"),
            notes=(
                f"Strip footing concrete: {ext_perim:.1f}m × {depth}m deep × {width}m wide = {vol:.2f}m³. "
                + ("Dimensions from project_config — project-specific. " if _dim_depth_explicit else "Dimensions assumed — verify from structural drawings. ")
                + "Confirm grade (min N25) with structural engineer."
            ),
        ))
        rows.append(_row(
            "footings", "Strip Footing — Formwork",
            "lm", round(ext_perim, 1),
            "calculated", "= perimeter (strip footing edge form)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "= ext_wall_perimeter",
            "MEDIUM",
        ))
        # Reinforcement: 2 continuous Y12 bars per strip footing run
        reo_lm_ext = round(ext_perim * 2, 1)
        reo_lengths = math.ceil(reo_lm_ext / 6.0)  # standard 6 m stock lengths
        rows.append(_row(
            "footings", "Strip Footing — Reo Bar Y12 (2 bars, ext perimeter)",
            "lm", reo_lm_ext,
            "calculated",
            f"ext_perim({ext_perim:.1f}m) × 2 bars = {reo_lm_ext:.1f} lm ({reo_lengths} × 6m lengths)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "ext_perim × 2 bars",
            "LOW",
            manual_review=True,
            notes=(
                f"2 × Y12 continuous bars assumed for strip footing bearing. "
                f"Total: {reo_lm_ext:.1f} lm = {reo_lengths} × 6m stock lengths. "
                "Verify bar size, spacing, and lap length from structural engineer."
            ),
        ))
        # Termite barrier — physical management along all external bearing lines
        rows.append(_row(
            "footings", "Termite Barrier — Physical Membrane (ext perimeter)",
            "lm", round(ext_perim, 1),
            "inferred",
            "= ext_wall_perimeter (physical termite management at all bearer-to-footing interfaces)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "= ext_wall_perimeter",
            "LOW",
            manual_review=True,
            notes=(
                "Physical termite management (barrier membrane / Granitgard or equivalent) "
                "at all footing/bearer interfaces. Verify treatment type and specification "
                "from pest management consultant. Chemical treatment not included."
            ),
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
        # Bar chairs for external strip footing reo bars
        ext_chairs = math.ceil(ext_perim / 0.6)
        rows.append(_row(
            "footings", "Bar Chair / Reo Spacer — External Strip Footing",
            "nr", ext_chairs,
            "calculated",
            f"ceil(ext_perim({ext_perim:.1f}m) / 0.6m spacing)",
            f"{src}: perimeter={ext_perim:.1f} m",
            "ceil(ext_perim / 0.6)",
            "LOW",
            notes=f"Bar chairs at ~600mm spacing to support Y12 reo bars in external strip footings.",
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
                "footings", "Strip Footing Internal — Formwork",
                "lm", round(int_wall_lm, 1),
                "calculated", "= int_wall_lm (strip footing edge form, internal bearing lines)",
                f"dxf_geometry: int_wall_lm={int_wall_lm:.1f} m",
                "= int_wall_lm",
                "MEDIUM",
                notes=(
                    f"Edge formwork for internal bearing line strip footings. "
                    f"Matches external formwork method (H03). Total: {int_wall_lm:.1f} lm."
                ),
            ))
            rows.append(_row(
                "footings", "Strip Footing Internal — Concrete Volume",
                "m3", int_vol,
                "calculated",
                f"int_lm({int_wall_lm:.1f}m) × depth({depth}m) × width({width}m)",
                (
                    f"dxf_geometry: int_wall_lm={int_wall_lm:.1f}m (MEDIUM); "
                    f"project_config: depth={depth}m width={width}m"
                    + (" (project-specific)" if _dim_depth_explicit else " (default)")
                ),
                f"{int_wall_lm:.1f} × {depth} × {width}",
                _conc_conf,
                manual_review=(_conc_conf == "LOW"),
                notes=(
                    f"Internal bearing strip footing concrete: {int_wall_lm:.1f}m × {depth}m deep × {width}m wide = {int_vol:.2f}m³. "
                    + ("Dimensions from project_config — project-specific. " if _dim_depth_explicit else "Dimensions assumed — verify from structural drawings. ")
                    + "Confirm grade (min N25) with structural engineer."
                ),
            ))
            # Reo bar for internal bearing lines
            reo_lm_int = round(int_wall_lm * 2, 1)
            reo_len_int = math.ceil(reo_lm_int / 6.0)
            rows.append(_row(
                "footings", "Strip Footing Internal — Reo Bar Y12 (2 bars per run)",
                "lm", reo_lm_int,
                "calculated",
                f"int_wall_lm({int_wall_lm:.1f}m) × 2 bars = {reo_lm_int:.1f} lm ({reo_len_int} × 6m lengths)",
                f"dxf_geometry: int_wall_lm={int_wall_lm:.1f} m",
                "int_wall_lm × 2 bars",
                "LOW",
                manual_review=True,
                notes=(
                    f"2 × Y12 bars for internal bearing strip footings. "
                    f"Total: {reo_lm_int:.1f} lm = {reo_len_int} × 6m lengths. Verify from structural engineer."
                ),
            ))
            # Termite barrier — internal bearing lines
            rows.append(_row(
                "footings", "Termite Barrier — Physical Membrane (internal bearing lines)",
                "lm", round(int_wall_lm, 1),
                "inferred",
                f"= int_wall_lm (physical termite management at all internal bearer interfaces)",
                f"dxf_geometry: int_wall_lm={int_wall_lm:.1f} m",
                "= int_wall_lm",
                "LOW",
                manual_review=True,
                notes=(
                    "Physical termite management at internal bearing interfaces. "
                    "Verify treatment specification from pest management consultant."
                ),
            ))
            # Bar chairs / reo spacers for internal strip footings
            int_chairs = math.ceil(int_wall_lm / 0.6)
            rows.append(_row(
                "footings", "Bar Chair / Reo Spacer — Internal Strip Footings",
                "nr", int_chairs,
                "calculated",
                f"ceil(int_wall_lm({int_wall_lm:.1f}m) / 0.6m spacing)",
                f"dxf_geometry: int_wall_lm={int_wall_lm:.1f} m",
                "ceil(int_lm / 0.6)",
                "LOW",
                notes=f"Bar chairs at ~600mm spacing to support Y12 reo in internal strip footings.",
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
            f"pad_count({total_pads}) × {pad_size}m×{pad_size}m×{pad_depth}m = {pad_vol:.3f}m³ each",
            (
                f"dxf_geometry: pad_count={total_pads} (MEDIUM); "
                f"project_config: pad_size={pad_size}m pad_depth={pad_depth}m"
                + (" (project-specific)" if _dim_pad_explicit else " (default)")
            ),
            f"{total_pads} × {pad_vol:.3f}",
            _pad_conc_conf,
            manual_review=(_pad_conc_conf == "LOW"),
            notes=(
                f"{total_pads} pads × {int(pad_size*1000)}mm × {int(pad_size*1000)}mm × {int(pad_depth*1000)}mm = {round(total_pads*pad_vol,3):.3f}m³. "
                + ("Pad dimensions from project_config — project-specific. " if _dim_pad_explicit else "Pad dimensions assumed — verify from structural drawings. ")
                + "Confirm grade (min N25) and reinforcement with structural engineer."
            ),
        ))

    # ── Concrete family total summary ─────────────────────────────────────────
    # Sum all concrete volumes across strip and pad footings for a family procurement row.
    conc_rows = [r for r in rows if "Concrete" in r.get("item_name", "") and r.get("unit") == "m3"]
    total_conc_m3 = round(sum(r.get("quantity", 0) for r in conc_rows), 3)
    if total_conc_m3 > 0:
        conc_parts = [f"{r['item_name'].split('—')[-1].strip() if '—' in r['item_name'] else r['item_name']}({r['quantity']:.3f}m³)"
                      for r in conc_rows]
        # Total confidence = min of component confidences
        _conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        _total_conf_rank = min(_conf_rank.get(r.get("confidence","LOW"), 1) for r in conc_rows)
        _total_conc_conf = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}[_total_conf_rank]
        rows.append(_row(
            "footings", "Concrete Supply — Substructure Total (all footings)",
            "m3", total_conc_m3,
            "calculated",
            "sum of all footing concrete volumes (strip ext + strip int + pad footings)",
            f"derived: {' + '.join(conc_parts)} = {total_conc_m3:.3f} m³",
            "sum(concrete_m3 per footing type)",
            _total_conc_conf,
            manual_review=(_total_conc_conf == "LOW"),
            notes=(
                "Primary concrete family procurement total for all substructure elements. "
                f"Includes: {'; '.join(conc_parts)}. "
                "Specify grade (N25 minimum for footings) and confirm with structural engineer. "
                "Order as single concrete pour or split by element type as required."
            ),
        ))

    return rows
