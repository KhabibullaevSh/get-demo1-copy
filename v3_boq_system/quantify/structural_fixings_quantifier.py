"""
structural_fixings_quantifier.py — Grouped structural fixing / connector package.

Replaces the single "Structural Fixings & Connectors — PLACEHOLDER" row from
roof_quantifier.py with ~12 grouped procurement rows derived from the element model.

No fixing schedule is available in source documents for this project.
Quantities are derived from member-density rules applied to measured frame lm,
pad counts, and strip footing lm.  All rows: LOW/MEDIUM confidence, manual_review=True.

Source priority:
  1. FrameCAD connection schedule (PDF) — NOT AVAILABLE for this project
  2. Derived from member lm × density rules   ← this module uses this
  3. Single placeholder (replaced by this module)

Derivation rules documented per item.
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import ProjectElementModel

log = logging.getLogger("boq.v3.fixings")


def _row(
    item_name, unit, quantity, status, basis, evidence, rule,
    confidence, notes="", item_code="",
) -> dict:
    return {
        "item_name":       item_name,
        "item_code":       item_code,
        "unit":            unit,
        "quantity":        quantity,
        "package":         "structural_fixings",
        "quantity_status": status,
        "quantity_basis":  basis,
        "source_evidence": evidence,
        "derivation_rule": rule,
        "confidence":      confidence,
        "manual_review":   True,
        "notes":           notes,
    }


# ── Density constants (documented, adjustable) ────────────────────────────────

# LGS framing screws at every stud-to-track connection:
# studs @ 600mm, 2 screws per end (top + bottom) = 4 per stud per metre
_WALL_SCREW_PER_M  = 4.0    # 12G-14×20 hex per metre of wall frame
_ROOF_SCREW_PER_M  = 3.0    # 10G-24×40 CSK per metre of roof panel (purlin-to-rafter)
_BAT_SCREW_PER_M   = 2.5    # 10G-14×16 wafer per metre of batten (batten-to-purlin)
_SCREW_BOX_SIZE    = 500    # screws per box (standard FrameCAD supply)

# Sleeve anchors — pad footing post base plates
_ANCHORS_PER_PAD   = 4      # M12×75 per pad (4-bolt base plate pattern)
_ANCHORS_PER_M_STR = 1.5    # M12×75 per metre of strip footing (bearer connections)

# Triple grip connectors — roof-to-wall-plate at eave line
_GRIP_SPACING_M    = 1.2    # 1 triple grip per 1.2m of eave

# Hold-down washers — cyclonic uplift connections at every post base
_WASHERS_PER_PAD   = 4      # 4 washers per pad footing

# Fix plates / gusset plates — frame-to-frame lapped connections
_FIX_PLATE_PER_M   = 0.35   # fix plates per metre of wall strap bracing

# Grommets — electrical/plumbing service penetrations through LGS studs
_GROMMET_PER_M_INT = 1.5    # grommets per metre of internal wall

# PH post brackets — steel SHS column base
_SHS_AVG_HEIGHT_M  = 3.5    # average height per steel post (assumed from typical floor-to-eave)


def quantify_structural_fixings(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    """
    Generate grouped structural fixing procurement rows from model geometry.

    Returns list of BOQ rows tagged package='structural_fixings'.
    """
    rows: list[dict] = []

    # ── Gather measured quantities from element model ─────────────────────────
    wall_lm       = sum(sf.total_lm for sf in model.structural_frames
                        if sf.frame_type in ("wall_frame", "wall_lintel", "wall_strap"))
    roof_panel_lm = sum(sf.total_lm for sf in model.structural_frames
                        if sf.frame_type == "roof_panel")
    batten_lm     = sum(sf.total_lm for sf in model.structural_frames
                        if sf.frame_type == "roof_batten")
    wall_strap_lm = sum(sf.total_lm for sf in model.structural_frames
                        if sf.frame_type == "wall_strap")
    steel_shs_lm  = sum(sf.total_lm for sf in model.structural_frames
                        if sf.frame_type == "steel_shs")

    pad_count     = sum(f.count for f in model.footings if f.footing_type == "pad")
    strip_perim   = sum(f.perimeter_m for f in model.footings if f.footing_type == "strip")

    eaves_lm      = sum(r.eaves_length_m for r in model.roofs)
    int_lm        = sum(w.length_m for w in model.walls if w.wall_type == "internal")

    total_frame   = wall_lm + roof_panel_lm + batten_lm
    src_ev_base   = (
        f"framecad_bom: wall={wall_lm:.1f}lm, roof_panel={roof_panel_lm:.1f}lm, "
        f"batten={batten_lm:.1f}lm; dxf: pad_count={pad_count}, strip_perim={strip_perim:.1f}m"
    )

    if total_frame == 0 and pad_count == 0:
        # No frame data — emit single grouped placeholder
        rows.append(_row(
            "Structural Fixings & Connectors — PLACEHOLDER (no frame data)",
            "item", 0, "placeholder",
            "no frame data in element model",
            "no structural frame data extracted",
            "manual review required",
            "LOW",
            notes="No structural frame data available. Obtain FrameCAD fixing schedule.",
        ))
        return rows

    # ─── 1. LGS Framing Screws — Wall (12G-14×20 HEX head) ──────────────────
    if wall_lm > 0:
        screw_count = round(wall_lm * _WALL_SCREW_PER_M)
        boxes       = math.ceil(screw_count / _SCREW_BOX_SIZE)
        rows.append(_row(
            "Framing Screw 12G-14×20 HEX Head (LGS stud-to-track)",
            "boxes", boxes,
            "calculated",
            f"wall_frame_lm({wall_lm:.1f}) × {_WALL_SCREW_PER_M} screws/m ÷ {_SCREW_BOX_SIZE}/box",
            src_ev_base,
            f"ceil({wall_lm:.1f} × {_WALL_SCREW_PER_M} / {_SCREW_BOX_SIZE})",
            "MEDIUM",
            notes=(
                f"Stud-to-track connections: {wall_lm:.1f}lm × {_WALL_SCREW_PER_M} per m = "
                f"~{screw_count} screws, {boxes} boxes ({_SCREW_BOX_SIZE}/box). "
                "Density: 2 screws per stud end (top+bottom). Verify with FrameCAD connection schedule."
            ),
        ))

    # ─── 2. Roofing Tek Screws — Roof Panel (10G-24×40 countersunk) ──────────
    if roof_panel_lm > 0:
        screw_count = round(roof_panel_lm * _ROOF_SCREW_PER_M)
        boxes       = math.ceil(screw_count / _SCREW_BOX_SIZE)
        rows.append(_row(
            "Framing Screw 10G-24×40 Countersunk (roof panel)",
            "boxes", boxes,
            "calculated",
            f"roof_panel_lm({roof_panel_lm:.1f}) × {_ROOF_SCREW_PER_M} screws/m ÷ {_SCREW_BOX_SIZE}/box",
            src_ev_base,
            f"ceil({roof_panel_lm:.1f} × {_ROOF_SCREW_PER_M} / {_SCREW_BOX_SIZE})",
            "MEDIUM",
            notes=(
                f"Purlin-to-rafter / roof panel connections: {roof_panel_lm:.1f}lm × "
                f"{_ROOF_SCREW_PER_M} per m = ~{screw_count} screws, {boxes} boxes. "
                "Verify with FrameCAD connection report."
            ),
        ))

    # ─── 3. Batten Screws (10G-14×16 wafer head, batten-to-purlin) ───────────
    if batten_lm > 0:
        screw_count = round(batten_lm * _BAT_SCREW_PER_M)
        boxes       = math.ceil(screw_count / _SCREW_BOX_SIZE)
        rows.append(_row(
            "Framing Screw 10G-14×16 Wafer Head (batten-to-purlin)",
            "boxes", boxes,
            "calculated",
            f"batten_lm({batten_lm:.1f}) × {_BAT_SCREW_PER_M} screws/m ÷ {_SCREW_BOX_SIZE}/box",
            src_ev_base,
            f"ceil({batten_lm:.1f} × {_BAT_SCREW_PER_M} / {_SCREW_BOX_SIZE})",
            "LOW",
            notes=(
                f"Batten-to-purlin connections: {batten_lm:.1f}lm total batten × "
                f"{_BAT_SCREW_PER_M} per m = ~{screw_count}. "
                "Includes roof, wall, and ceiling batten zones from FrameCAD BOM. "
                "Verify zone scope before ordering."
            ),
        ))

    # ─── 4. M12×75 Sleeve Anchors — Pad Footings ─────────────────────────────
    if pad_count > 0:
        anchors = pad_count * _ANCHORS_PER_PAD
        rows.append(_row(
            "Sleeve Anchor M12×75 — Pad Footings",
            "nr", anchors,
            "calculated",
            f"pad_count({pad_count}) × {_ANCHORS_PER_PAD} anchors per pad",
            f"dxf_geometry: pad_count={pad_count} (DXF STRUCTURE layer)",
            f"{pad_count} × {_ANCHORS_PER_PAD}",
            "MEDIUM",
            notes=(
                f"{pad_count} pad footings × {_ANCHORS_PER_PAD} M12×75 anchors each (4-bolt base plate). "
                "Verify anchor embedment depth and edge distance from structural engineer."
            ),
        ))

    # ─── 5. M12×75 Sleeve Anchors — Strip Footing Bearer Bearing ─────────────
    if strip_perim > 0:
        anchors = math.ceil(strip_perim * _ANCHORS_PER_M_STR)
        rows.append(_row(
            "Sleeve Anchor M12×75 — Strip Footing Bearer Connections",
            "nr", anchors,
            "calculated",
            f"strip_footing_perim({strip_perim:.1f}m) × {_ANCHORS_PER_M_STR} anchors/m",
            f"dxf_geometry: ext_perim={strip_perim:.1f}m (strip footing bearing line)",
            f"ceil({strip_perim:.1f} × {_ANCHORS_PER_M_STR})",
            "LOW",
            notes=(
                f"Bearer-to-strip-footing connections along {strip_perim:.1f}m perimeter. "
                "Bearing frequency per FrameCAD bearer schedule. Verify with structural drawings."
            ),
        ))

    # ─── 6. PH Post Holder Brackets (steel SHS column base) ──────────────────
    if steel_shs_lm > 0 or pad_count > 0:
        # Estimate post count from either steel_shs_lm (height) or pad count
        if steel_shs_lm > 0:
            post_est = math.ceil(steel_shs_lm / _SHS_AVG_HEIGHT_M)
            ev = f"ifc_model: steel_shs_lm={steel_shs_lm:.2f} / avg_height({_SHS_AVG_HEIGHT_M}m)"
        else:
            post_est = pad_count
            ev = f"dxf_geometry: pad_count={pad_count} (assumes 1 post per pad)"
        rows.append(_row(
            "PH Post Holder Bracket (PHA/PHB/PHD)",
            "nr", post_est,
            "calculated",
            (f"steel_shs_lm({steel_shs_lm:.1f}) ÷ avg_height({_SHS_AVG_HEIGHT_M}m)"
             if steel_shs_lm > 0 else f"pad_count={pad_count}"),
            ev,
            f"ceil({steel_shs_lm:.1f}/{_SHS_AVG_HEIGHT_M})" if steel_shs_lm > 0 else f"= pad_count",
            "LOW",
            notes=(
                f"Post holder brackets at column bases. Count estimated from "
                f"{'IFC steel post lm' if steel_shs_lm > 0 else 'DXF pad count'}. "
                "Verify bracket type (PHA/PHB/PHD) against post size and load from structural drawings."
            ),
        ))

    # ─── 7. Triple Grip Connector (roof-to-wall-plate) ───────────────────────
    if eaves_lm > 0:
        grips = math.ceil(eaves_lm / _GRIP_SPACING_M)
        rows.append(_row(
            "Triple Grip Connector (roof-to-wall uplift)",
            "nr", grips,
            "calculated",
            f"eaves_lm({eaves_lm:.1f}) ÷ spacing({_GRIP_SPACING_M}m)",
            f"dxf_geometry: eaves_length={eaves_lm:.1f}m",
            f"ceil({eaves_lm:.1f} / {_GRIP_SPACING_M})",
            "LOW",
            notes=(
                f"Roof-to-wall-plate cyclonic uplift connections at {_GRIP_SPACING_M}m centres "
                f"along {eaves_lm:.1f}m eave line = {grips} nr. "
                "Verify spacing and type from structural engineer (cyclone region requirement)."
            ),
        ))

    # ─── 8. Hold-Down Washer (cyclonic connection) ────────────────────────────
    if pad_count > 0:
        washers = pad_count * _WASHERS_PER_PAD
        rows.append(_row(
            "Hold-Down Washer — Cyclonic (post base)",
            "nr", washers,
            "calculated",
            f"pad_count({pad_count}) × {_WASHERS_PER_PAD} washers per pad",
            f"dxf_geometry: pad_count={pad_count}",
            f"{pad_count} × {_WASHERS_PER_PAD}",
            "LOW",
            notes=(
                f"Hold-down washers at post base connections. "
                f"{pad_count} pads × {_WASHERS_PER_PAD} = {washers} nr. "
                "Verify washer specification (size, grade) from structural drawings."
            ),
        ))

    # ─── 9. Fix Plates / Gusset Plates (bracing connections) ─────────────────
    if wall_strap_lm > 0:
        fix_plates = math.ceil(wall_strap_lm * _FIX_PLATE_PER_M)
        rows.append(_row(
            "Fix Plate / Gusset Plate (strap bracing connection)",
            "nr", fix_plates,
            "calculated",
            f"wall_strap_lm({wall_strap_lm:.1f}) × {_FIX_PLATE_PER_M} plates/m",
            f"framecad_bom: wall_strap_lm={wall_strap_lm:.2f}lm",
            f"ceil({wall_strap_lm:.1f} × {_FIX_PLATE_PER_M})",
            "LOW",
            notes=(
                f"Fix plates at strap bracing termination points: "
                f"{wall_strap_lm:.1f}lm × {_FIX_PLATE_PER_M} = {fix_plates} nr. "
                "Verify against FrameCAD connection schedule."
            ),
        ))
    else:
        # No strap data — generic allowance
        rows.append(_row(
            "Fix Plate / Gusset Plate (bracing connections — allowance)",
            "nr", 20,
            "inferred",
            "standard commercial frame allowance (no strap data)",
            "no wall_strap data — commercial building allowance",
            "allowance: 20 nr typical commercial frame",
            "LOW",
            notes="Fix plate count not derivable without strap schedule. 20 nr allowance for commercial frame.",
        ))

    # ─── 10. Diagonal Strap / Tensioner ──────────────────────────────────────
    if wall_strap_lm > 0:
        # Each strap run typically 2–4m, so count ≈ lm / 3m average run
        strap_runs = math.ceil(wall_strap_lm / 3.0)
        rows.append(_row(
            "Diagonal Strap Tensioner / Anchor (bracing strap end)",
            "nr", strap_runs * 2,  # 2 anchor ends per strap run
            "calculated",
            f"strap_runs({strap_runs}) × 2 ends",
            f"framecad_bom: wall_strap_lm={wall_strap_lm:.2f}lm",
            f"ceil({wall_strap_lm:.1f}/3.0) × 2",
            "LOW",
            notes=(
                f"Strap termination anchors: {wall_strap_lm:.1f}lm ÷ 3m avg run = {strap_runs} runs × 2 ends. "
                "Verify strap count and anchor type from FrameCAD connection schedule."
            ),
        ))

    # ─── 11. Cable Grommets (service penetrations) ───────────────────────────
    if int_lm > 0:
        grommets = math.ceil(int_lm * _GROMMET_PER_M_INT)
        rows.append(_row(
            "Cable Grommet (LGS service penetration)",
            "nr", grommets,
            "inferred",
            f"int_wall_lm({int_lm:.1f}) × {_GROMMET_PER_M_INT} grommets/m",
            f"dxf_geometry: int_wall_lm={int_lm:.1f}m",
            f"ceil({int_lm:.1f} × {_GROMMET_PER_M_INT})",
            "LOW",
            notes=(
                f"Cable/conduit grommets at LGS stud penetrations. "
                f"{int_lm:.1f}m internal wall × {_GROMMET_PER_M_INT} per m = {grommets} nr. "
                "Quantity depends on electrical/hydraulic layout. Verify from services drawings."
            ),
        ))

    # ─── 12. Structural Fixings — Remainder (PH brackets, bolts, angles) ─────
    # Items that cannot be fully quantified without a connection schedule.
    # Keep as grouped placeholder for any fixing types not covered above.
    rows.append(_row(
        "Structural Fixings Remainder — Hex Bolts / Angles / Misc (PLACEHOLDER)",
        "item", 0,
        "placeholder",
        "no FrameCAD connection schedule — grouped items not derivable",
        "no fixing schedule in source documents",
        "manual review required",
        "LOW",
        notes=(
            "PLACEHOLDER for connection schedule items not derivable from member lm alone. "
            "Typical items: M10/M12/M16 hex bolts (hold-down posts), angle brackets (knee bracing, "
            "ridge connections), spring washers, nyloc nuts. "
            "Obtain FrameCAD connection report for full schedule. "
            "Reference BOQ includes 256+ individual fixing line items — this row represents residual scope."
        ),
    ))

    log.info(
        "Structural fixings: %d grouped rows (wall=%.1f lm, roof=%.1f lm, pads=%d)",
        len(rows), wall_lm, roof_panel_lm, pad_count,
    )
    return rows
