"""
floor_system_quantifier.py — Floor system quantification.

Priority order (highest first):
  1. FrameCAD manufacturing summary (floor panel tabs)
  2. IFC floor joist members
  3. Structural panel schedule (from PDF)
  4. Derived from floor area + config spacing (lowest confidence)

Produces both:
  A. Engineering totals (lm summaries for QA)
  B. Procurement assemblies (panel sets, bearer pairs, joists, sheeting, fixings)

CRITICAL: No quantities from BOQ reference files.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from v3_boq_system.normalize.element_model import FloorElement, FloorSystemElement, ProjectElementModel

log = logging.getLogger("boq.v3.floor_system")

# ── BOQ row builder ───────────────────────────────────────────────────────────

def _row(
    package:         str,
    item_name:       str,
    unit:            str,
    quantity:        Any,
    status:          str,
    basis:           str,
    evidence:        str,
    rule:            str,
    confidence:      str,
    manual_review:   bool = False,
    notes:           str  = "",
    item_code:       str  = "",
) -> dict:
    return {
        "item_name":       item_name,
        "item_code":       item_code,
        "unit":            unit,
        "quantity":        quantity,
        "package":         package,
        "quantity_status": status,
        "quantity_basis":  basis,
        "source_evidence": evidence,
        "derivation_rule": rule,
        "confidence":      confidence,
        "manual_review":   manual_review,
        "notes":           notes,
    }


def quantify_floor_system(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    """
    Produce BOQ rows for the floor system.

    Returns a list of procurement rows.
    """
    rows: list[dict] = []
    struct_cfg = config.get("structural", {})
    lining_cfg = config.get("lining", {})

    floor_elements = model.floors
    floor_systems  = model.floor_systems
    footings       = model.footings

    if not floor_elements:
        log.info("No floor elements — skipping floor system quantifier")
        return rows

    floor_area  = sum(f.area_m2 for f in floor_elements)
    ext_perim   = sum(f.perimeter_m for f in floor_elements)

    # ── CASE 1: FrameCAD BOM has floor panel tabs ─────────────────────────────
    bom_floor_panels = [fs for fs in floor_systems if fs.source == "framecad_bom"]
    if bom_floor_panels:
        log.info("Floor system: FrameCAD BOM source (HIGH confidence)")
        for fp in bom_floor_panels:
            if fp.joist_count > 0 and fp.joist_length_mm > 0:
                # Per-member detail available — emit family-level procurement rows
                joist_lm = round(fp.joist_count * fp.joist_length_mm / 1000, 2)
                profile_label = fp.notes.split("Profile:")[1].split(".")[0].strip() if fp.notes and "Profile:" in fp.notes else "LGS C-section"
                rows.append(_row(
                    "floor_system",
                    f"Floor Joist — {profile_label} × {fp.joist_length_mm} mm",
                    "nr", fp.joist_count,
                    "measured", "framecad_bom: floor panel tab members",
                    f"framecad_bom: {fp.source_reference}",
                    "count from BOM member schedule",
                    "HIGH",
                    notes=(
                        f"Total joist lm: {joist_lm:.2f} lm. "
                        + (f"Load class: {fp.load_class}." if fp.load_class else "")
                    ),
                ))
                rows.append(_row(
                    "floor_system",
                    f"Floor Joist — {profile_label} (total lm)",
                    "lm", joist_lm,
                    "measured", "framecad_bom: count × length",
                    f"framecad_bom: {fp.joist_count} × {fp.joist_length_mm} mm",
                    f"{fp.joist_count} × {fp.joist_length_mm}/1000",
                    "HIGH",
                ))
            else:
                # Tab total only (no per-member detail)
                rows.append(_row(
                    "floor_system", f"Floor Panel (FrameCAD) — {fp.element_id}",
                    "lm",
                    round(fp.total_joist_lm, 2),
                    "measured", "framecad_bom: floor panel tab total",
                    f"framecad_bom: {fp.source_reference}",
                    "direct from BOM tab total",
                    "HIGH",
                    notes="FrameCAD manufacturing summary floor panel total.",
                ))
            # Bearer pairs: estimate from floor area when not in schedule
            bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000
            span = round(math.sqrt(floor_area), 1) if floor_area > 0 else 0.0
            bearer_pairs = math.ceil(span / bearer_spacing) if span > 0 else 0
            if bearer_pairs > 0:
                rows.append(_row(
                    "floor_system", "Floor Bearer (pair)",
                    "nr", bearer_pairs,
                    "calculated",
                    f"derived: floor_span / bearer_spacing = {span:.1f} / {bearer_spacing:.1f}",
                    f"framecad_bom + derived: floor_area={floor_area:.2f} m²",
                    f"ceil({span:.1f}/{bearer_spacing:.1f})",
                    "MEDIUM",
                    notes=(
                        f"Bearer pairs derived from floor area. "
                        f"Verify bearer schedule from FrameCAD engineer."
                    ),
                ))
        rows += _floor_sheeting_rows(floor_area, "framecad_bom", "HIGH", lining_cfg)
        return rows

    # ── CASE 2: IFC floor joists ──────────────────────────────────────────────
    ifc_floor_systems = [fs for fs in floor_systems if fs.source == "ifc_model"]
    if ifc_floor_systems:
        total_joist_lm = sum(fs.total_joist_lm for fs in ifc_floor_systems)
        if total_joist_lm > 0:
            log.info("Floor system: IFC source, floor_joist_lm=%.2f", total_joist_lm)
            # Estimate joist count from length and spacing
            joist_spacing = struct_cfg.get("floor_joist_spacing_mm", 450) / 1000
            joist_est_count = math.ceil(total_joist_lm / (floor_area ** 0.5)) if floor_area > 0 else 0

            rows.append(_row(
                "floor_system", "Floor Joist (LGS)",
                "lm",
                round(total_joist_lm, 2),
                "measured", "ifc_model: floor_joist classification",
                "ifc_model: IfcMember floor_joist",
                "direct from IFC",
                "HIGH",
            ))
            # Estimate bearer pairs from floor area and bearer spacing
            bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000
            bearer_pairs   = math.ceil(floor_area ** 0.5 / bearer_spacing) if floor_area > 0 else 0
            bearer_run     = round(floor_area ** 0.5, 1) if floor_area > 0 else 0.0

            if bearer_pairs > 0:
                rows.append(_row(
                    "floor_system", "Floor Bearer (pair)",
                    "nr",
                    bearer_pairs,
                    "calculated",
                    f"derived: floor_span / bearer_spacing = {bearer_run:.1f} / {bearer_spacing:.1f}",
                    f"derived from floor_area={floor_area:.2f} m²",
                    f"ceil(sqrt(floor_area) / {bearer_spacing})",
                    "LOW",
                    manual_review=True,
                    notes=f"Bearer spacing assumed {int(bearer_spacing*1000)} mm. Verify from structural drawings.",
                ))
            rows += _floor_sheeting_rows(floor_area, "ifc_model", "MEDIUM", lining_cfg)
            return rows

    # ── CASE 3: Steel floor frame confirmed but no panel schedule ─────────────
    steel_frames = [fs for fs in floor_systems if fs.assembly_type == "steel_floor_frame"]
    if steel_frames:
        fs  = steel_frames[0]
        fa  = fs.floor_area_m2 or floor_area
        log.info("Floor system: steel floor frame (area-derived, no schedule)")

        pw  = struct_cfg.get("floor_panel_width_m",  0.6)   # cassette/joist spacing
        pl  = struct_cfg.get("floor_panel_length_m",  3.6)  # bay span
        ph  = struct_cfg.get("floor_panel_height_mm", 200)  # cassette depth
        joist_spacing = struct_cfg.get("floor_joist_spacing_mm", 450) / 1000
        bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000

        # Floor span (shorter dimension of floor plan)
        span = round(math.sqrt(fa) if fa > 0 else 0.0, 1)

        # Joist/panel count across the span
        joist_count = math.ceil(span / joist_spacing) if span > 0 else 0
        # Floor run (longer dimension)
        floor_run   = round(fa / span, 1) if span > 0 else 0.0
        joist_lm    = round(floor_run * joist_count, 1) if joist_count > 0 else 0.0

        load_note = f" Load class: {fs.load_class}." if fs.load_class else ""
        src_ev = f"{fs.source}: {fs.source_reference}"

        rows.append(_row(
            "floor_system",
            "Floor Joist / LGS Cassette",
            "lm", joist_lm,
            "inferred",
            f"derived: floor_run × joist_count (spacing {int(joist_spacing*1000)} mm)",
            src_ev,
            f"sqrt({fa:.1f})={span}m span; ceil(span/{joist_spacing})={joist_count} joists × {floor_run}m run",
            "LOW",
            manual_review=True,
            notes=(
                f"Steel floor frame confirmed (FrameCAD layout). No panel schedule — "
                f"derived from floor area {fa:.1f} m² at {int(joist_spacing*1000)} mm joist spacing.{load_note} "
                "Obtain panel schedule from engineer."
            ),
        ))

        bearer_pairs = math.ceil(span / bearer_spacing) if span > 0 else 0
        if bearer_pairs > 0:
            rows.append(_row(
                "floor_system",
                "Floor Bearer (pair)",
                "nr", bearer_pairs,
                "inferred",
                f"derived: floor_span / bearer_spacing = {span} / {bearer_spacing:.1f}",
                src_ev,
                f"ceil({span}/{bearer_spacing:.1f})",
                "LOW",
                manual_review=True,
                notes=f"Bearer spacing assumed {int(bearer_spacing*1000)} mm. Verify from structural drawings.",
            ))

        # Fixing cleats / connection hardware estimate
        cleats = math.ceil(joist_count * 2)  # 2 cleats per joist
        rows.append(_row(
            "floor_system",
            "Floor Joist End Cleats / Fixings",
            "nr", cleats,
            "inferred",
            f"derived: joist_count × 2 cleats each",
            src_ev,
            f"{joist_count} × 2",
            "LOW",
            manual_review=True,
            notes="2 end cleats per joist estimated. Verify against FrameCAD connection schedule.",
        ))

        rows += _floor_sheeting_rows(fa, fs.source, "LOW", lining_cfg)
        return rows

    # ── CASE 4: No floor system or slab footing — slab items in floor_system ──
    # NOTE: slab items are intentionally omitted here — they belong in the
    # Substructure (footings) package only.  This prevents duplication.
    slab_footings = [f for f in footings if f.footing_type == "slab"]
    if slab_footings or not floor_systems:
        # No separate floor_system rows — slab is fully handled by footing_quantifier.
        # Emit a single placeholder so the package is not silently empty.
        log.info("Floor system: slab on ground — items in Substructure package")
        src_ev = (f"{slab_footings[0].source}: slab_area={slab_footings[0].area_m2:.2f} m²"
                  if slab_footings else "derived: no floor system evidence")
        rows.append(_row(
            "floor_system",
            "Slab on Ground (see Substructure for quantities)",
            "item", 0,
            "placeholder",
            "slab on ground — see Substructure section",
            src_ev,
            "refer to Substructure package",
            "LOW",
            manual_review=True,
            notes=(
                "No steel floor frame or joist evidence detected. "
                "Slab on ground assumed — concrete, mesh, and formwork are in Substructure. "
                "Verify floor system type from structural drawings."
            ),
        ))

    return rows


def _floor_sheeting_rows(
    floor_area: float,
    src:        str,
    conf:       str,
    lining_cfg: dict,
) -> list[dict]:
    """Produce floor sheeting procurement rows for a given floor area."""
    if floor_area <= 0:
        return []
    sheet_area = lining_cfg.get("fc_ceiling_sheet_area_m2", 2.88)  # same sheet dims as ceiling
    waste      = lining_cfg.get("waste_factor", 1.05)
    sheet_count = math.ceil(floor_area * waste / sheet_area)

    return [
        _row(
            "floor_system", "Floor Sheet (FC / plywood)",
            "sheets",
            sheet_count,
            "calculated",
            f"ceil(floor_area × {waste} / {sheet_area})",
            f"{src}: floor_area={floor_area:.2f} m²",
            f"ceil({floor_area:.2f} × {waste} / {sheet_area})",
            conf,
            manual_review=(conf == "LOW"),
            notes="Sheet size assumed same as ceiling FC sheet (1.2×2.4). Adjust if different.",
        ),
        _row(
            "floor_system", "Floor Sheet Fixing Screws",
            "boxes",
            math.ceil(sheet_count * 16 / 200),
            "calculated",
            f"ceil(sheet_count × 16 fixings / 200 per box)",
            f"{src}: sheet_count={sheet_count}",
            "ceil(sheets × 16 / 200)",
            "LOW",
            notes="16 fixings per sheet; 200 per box.",
        ),
    ]
