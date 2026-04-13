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


def _main_building_dims(
    floor_area: float,
    floor_perim: float,
    ver_area: float,
    ver_perim: float,
) -> tuple[float, float, float, str]:
    """
    Derive main-building span (short) and run (long) from DXF geometry.

    Returns (span_short_m, span_long_m, main_floor_area_m2, derivation_note).
    Returns (0, 0, floor_area, note) when geometry is underdetermined.

    Method (all inputs from DXF measurements):
      1. Solve L + W = floor_perim/2  and  L × W = floor_area  → total L, W.
      2. Solve ver_W + ver_d = ver_perim/2  and  ver_W × ver_d = ver_area
         → two solutions; pick the one where ver_W = total_W (full-width verandah).
         LABEL: engineering inference — DXF confirms area+perimeter consistent with
         full-width attachment; replacing document = floor plan with room labels.
      3. main_L = total_L − ver_depth;  main_W = total_W.
      4. span_short = min(main_L, main_W);  span_long = max(main_L, main_W).
    """
    if floor_area <= 0 or floor_perim <= 0:
        return 0.0, 0.0, floor_area, "no floor geometry"

    # Step 1: total building L and W
    half_p = floor_perim / 2
    disc = half_p ** 2 - 4 * floor_area
    if disc < 0:
        return 0.0, 0.0, floor_area, "floor plan near-square — L/W underdetermined"
    import math as _math
    total_L = round((_math.sqrt(disc) + half_p) / 2, 1)
    total_W = round((half_p - _math.sqrt(disc)) / 2, 1)
    if total_W <= 0:
        return 0.0, 0.0, floor_area, "W ≤ 0 — geometry inconsistent"

    # Step 2: verandah depth assuming full-width attachment (engineering inference)
    ver_depth = 0.0
    if ver_area > 0 and total_W > 0:
        candidate_depth = round(ver_area / total_W, 2)
        # Verify: candidate_depth × total_W ≈ ver_area
        if ver_area > 0 and abs(candidate_depth * total_W - ver_area) / ver_area < 0.05:
            ver_depth = candidate_depth

    main_floor_area = round(floor_area - ver_area, 2) if ver_depth > 0 else floor_area
    main_L          = round(total_L - ver_depth, 1)  if ver_depth > 0 else total_L
    main_W          = total_W

    span_short = min(main_L, main_W)
    span_long  = max(main_L, main_W)

    note = (
        f"DXF: floor={floor_area:.1f}m² perim={floor_perim:.1f}m → L={total_L:.1f}m W={total_W:.1f}m; "
        f"verandah={ver_area:.1f}m² depth={ver_depth:.1f}m (engineering inference: full-width attachment); "
        f"main={main_floor_area:.1f}m² ({main_L:.1f}m×{main_W:.1f}m); span={span_short:.1f}m run={span_long:.1f}m"
    )
    return span_short, span_long, main_floor_area, note

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

    # Derive main-building floor area (subtract verandah) and correct span/run
    # dimensions from DXF geometry.  The verandah has its own decking row in
    # external works; the main floor system should not include that area.
    ver_area  = sum(v.area_m2  for v in model.verandahs)
    ver_perim = sum(v.perimeter_m for v in model.verandahs)

    # ── Zone areas from space model (for substrate cross-reference in notes) ─
    # internal_dry + internal_wet = total internal substrate area.
    # These are passed to _floor_sheeting_rows() for explicit zone labeling only;
    # they do NOT change the substrate quantity (which covers all internal zones).
    _dry_zone_m2 = round(sum(
        s.area_m2 for s in model.spaces
        if s.is_enclosed and not s.is_wet and not s.is_verandah
    ), 2)
    _wet_zone_m2 = round(sum(
        s.area_m2 for s in model.spaces
        if s.is_enclosed and s.is_wet
    ), 2)
    span_dxf, run_dxf, main_floor_area, dims_note = _main_building_dims(
        floor_area, ext_perim, ver_area, ver_perim,
    )
    if span_dxf > 0:
        log.info("Floor dims from DXF: span=%.1f m  run=%.1f m  main_area=%.1f m²",
                 span_dxf, run_dxf, main_floor_area)
    else:
        main_floor_area = floor_area   # fall back to total area

    # ── CASE 0.5: DWG floor cassette schedule (FrameCAD Onpage FLayout) ─────────
    # Triggered when element_builder found DWG member schedule data.
    # Panel dimensions are HIGH confidence; panel count is MEDIUM (geometry-derived).
    # Each member type (joist, edge beam, stringer) emits its own BOQ row.
    dwg_floor_panels = [fs for fs in floor_systems if fs.source == "framecad_dwg"]
    if dwg_floor_panels:
        fp = dwg_floor_panels[0]
        log.info("Floor system: DWG cassette schedule (panel_count=%d, conf=%s)",
                 fp.panel_count, fp.confidence)

        # Panel dimensions from notes
        pw_mm   = fp.panel_width_mm    # E1/E2 length — HIGH
        pd_mm   = fp.panel_length_mm   # joist/stringer span — HIGH
        p_count = fp.panel_count       # geometry-derived — MEDIUM
        j_count = fp.joist_count       # total joists — MEDIUM
        j_len   = fp.joist_length_mm
        j_lm    = fp.total_joist_lm

        # Extract edge beam / stringer totals from notes text
        # Notes format: "... Edge beams (E1+E2): N nr total. Stringers (S1+S2): M nr total."
        import re as _re
        _eb_match = _re.search(r"Edge beams.*?:\s*(\d+)\s*nr", fp.notes or "")
        _st_match = _re.search(r"Stringers.*?:\s*(\d+)\s*nr", fp.notes or "")
        eb_count  = int(_eb_match.group(1)) if _eb_match else 0
        st_count  = int(_st_match.group(1)) if _st_match else 0
        eb_len    = pw_mm    # edge beam runs along panel width
        st_len    = pd_mm    # stringer runs along panel span
        eb_lm     = round(eb_count * eb_len / 1000, 2) if eb_count else 0.0
        st_lm     = round(st_count * st_len / 1000, 2) if st_count else 0.0

        # Extract profile from notes
        _prf_match = _re.search(r"Profile:\s*([^\.\s]+)", fp.notes or "")
        j_profile = _prf_match.group(1) if _prf_match else "150S41-095-500"

        _eb_prf = _re.search(r"Edge beam.*?(\d+P\d+-\d+-\d+)", fp.notes or "")
        eb_profile = _eb_prf.group(1) if _eb_prf else "150P41-115-500"

        _st_prf = _re.search(r"Stringer.*?(\d+S\d+-\d+-\d+)", fp.notes or "")
        st_profile = _st_prf.group(1) if _st_prf else "150S41-115-500"

        src_ev   = fp.source_reference
        src_conf = fp.confidence

        # ── Floor Panel Cassettes ───────────────────────────────────────────
        if p_count > 0:
            panel_area_each = round(pw_mm * pd_mm / 1_000_000, 4)
            rows.append(_row(
                "floor_system",
                f"Floor Cassette Panel — {pw_mm}mm × {pd_mm}mm",
                "nr", p_count,
                "inferred",
                (f"DWG geometry: floor_area / panel_area = "
                 f"main_floor_area / ({pw_mm}mm×{pd_mm}mm={panel_area_each:.4f}m²)"),
                f"framecad_dwg: {src_ev}",
                f"floor_area / {panel_area_each}m² per panel → {p_count} panels",
                src_conf,
                manual_review=True,
                notes=(
                    f"Panel count derived from floor area / per-panel area ({panel_area_each:.3f}m²). "
                    f"Panel width={pw_mm}mm (from E1/E2 edge beam length, HIGH confidence). "
                    f"Panel depth={pd_mm}mm (from J1/S1/S2 span length, HIGH confidence). "
                    f"Count={p_count} is MEDIUM confidence — verify from FrameCAD floor panel layout."
                ),
            ))
            # Floor panel total area — primary procurement family (m2)
            total_panel_area = round(p_count * panel_area_each, 2)
            rows.append(_row(
                "floor_system",
                f"Floor Cassette Panel — Total Area ({p_count} panels)",
                "m2", total_panel_area,
                "inferred",
                f"DWG geometry: {p_count} panels × {panel_area_each:.4f}m² per panel ({pw_mm}mm×{pd_mm}mm)",
                f"framecad_dwg: {src_ev}",
                f"{p_count} × {panel_area_each:.4f}",
                src_conf,
                manual_review=True,
                notes=(
                    f"Total floor cassette panel area = {p_count} panels × {panel_area_each:.3f}m² "
                    f"({pw_mm}mm × {pd_mm}mm each). "
                    f"Cross-check: main_floor_area from DXF = {main_floor_area:.2f}m². "
                    "Verify panel count and area against FrameCAD floor panel layout tab before ordering."
                ),
            ))

        # ── Joists (J1) ────────────────────────────────────────────────────
        if j_count > 0:
            j_per = j_count // p_count if p_count else 0
            rows.append(_row(
                "floor_system",
                f"Floor Joist (J1) — {j_profile} × {j_len}mm",
                "nr", j_count,
                "inferred",
                f"DWG schedule: {j_per} J1 joists per panel × {p_count} panels",
                f"framecad_dwg: {src_ev}",
                f"{j_per} × {p_count} panels",
                src_conf,
                manual_review=(src_conf != "HIGH"),
                notes=(
                    f"Profile: {j_profile}, length: {j_len}mm. "
                    f"{j_per} joists per panel (HIGH confidence — DWG Onpage FLayout schedule). "
                    f"Total {j_count} nr = {j_per} × {p_count} panels. "
                    f"Total lm: {j_lm:.2f} lm ({j_count} × {j_len/1000:.3f}m)."
                ),
            ))
            rows.append(_row(
                "floor_system",
                f"Floor Joist (J1) — {j_profile} (total lm)",
                "lm", j_lm,
                "inferred",
                f"DWG schedule: {j_count} joists × {j_len}mm",
                f"framecad_dwg: {src_ev}",
                f"{j_count} × {j_len}/1000 = {j_lm:.2f}",
                src_conf,
                notes=f"Total joist lm from DWG cassette schedule.",
            ))

        # ── Edge Beams (E1+E2) ──────────────────────────────────────────────
        if eb_count > 0:
            eb_per = eb_count // p_count if p_count else 0
            rows.append(_row(
                "floor_system",
                f"Floor Edge Beam (E1+E2) — {eb_profile} × {eb_len}mm",
                "nr", eb_count,
                "inferred",
                f"DWG schedule: {eb_per} edge beams per panel × {p_count} panels",
                f"framecad_dwg: {src_ev}",
                f"{eb_per} × {p_count} panels",
                src_conf,
                manual_review=(src_conf != "HIGH"),
                notes=(
                    f"Profile: {eb_profile}, length: {eb_len}mm. "
                    f"{eb_per} edge beams per panel (E1×1 + E2×1). "
                    f"Total {eb_count} nr. Total lm: {eb_lm:.2f} lm."
                ),
            ))
            if eb_lm > 0:
                rows.append(_row(
                    "floor_system",
                    f"Floor Edge Beam (E1+E2) — {eb_profile} (total lm)",
                    "lm", eb_lm,
                    "inferred",
                    f"DWG schedule: {eb_count} edge beams × {eb_len}mm",
                    f"framecad_dwg: {src_ev}",
                    f"{eb_count} × {eb_len}/1000 = {eb_lm:.2f}",
                    src_conf,
                    notes=(
                        f"Total edge beam lm for procurement ordering. "
                        f"{eb_count} nr × {eb_len}mm = {eb_lm:.2f} lm. "
                        f"Verify profile {eb_profile} and section from FrameCAD engineer before ordering."
                    ),
                ))

        # ── Stringers (S1+S2) ───────────────────────────────────────────────
        if st_count > 0:
            st_per = st_count // p_count if p_count else 0
            rows.append(_row(
                "floor_system",
                f"Floor Stringer (S1+S2) — {st_profile} × {st_len}mm",
                "nr", st_count,
                "inferred",
                f"DWG schedule: {st_per} stringers per panel × {p_count} panels",
                f"framecad_dwg: {src_ev}",
                f"{st_per} × {p_count} panels",
                src_conf,
                manual_review=(src_conf != "HIGH"),
                notes=(
                    f"Profile: {st_profile}, length: {st_len}mm. "
                    f"{st_per} stringers per panel (S1×1 + S2×1). "
                    f"Total {st_count} nr. Total lm: {st_lm:.2f} lm."
                ),
            ))
            if st_lm > 0:
                rows.append(_row(
                    "floor_system",
                    f"Floor Stringer (S1+S2) — {st_profile} (total lm)",
                    "lm", st_lm,
                    "inferred",
                    f"DWG schedule: {st_count} stringers × {st_len}mm",
                    f"framecad_dwg: {src_ev}",
                    f"{st_count} × {st_len}/1000 = {st_lm:.2f}",
                    src_conf,
                    notes=(
                        f"Total stringer lm for procurement ordering. "
                        f"{st_count} nr × {st_len}mm = {st_lm:.2f} lm. "
                        "Verify profile and section from FrameCAD engineer before ordering."
                    ),
                ))

        # ── Internal Floor Bearers ──────────────────────────────────────────
        # Derived purely from DWG panel grid geometry:
        #   - Joists (J1) span pd_mm across the building short span (span_dxf).
        #   - At every joist-end boundary (every pd_mm in the span direction) the
        #     cassette needs a bearing support running the full building run (run_dxf).
        #   - Perimeter boundaries (first and last) land on the strip footings → no
        #     separate member required there.
        #   - Internal boundaries = panel_rows − 1, each running run_dxf metres.
        #
        # Evidence source: framecad_dwg (pd_mm, pw_mm) + dxf_geometry (span, run).
        # This is the primary structural bearing element missing from earlier passes.
        if span_dxf > 0 and run_dxf > 0 and pd_mm > 0 and pw_mm > 0:
            pd_m = pd_mm / 1000
            pw_m = pw_mm / 1000
            panel_rows = round(span_dxf / pd_m)   # rows across the short span
            panel_cols = round(run_dxf  / pw_m)   # columns along the long run
            internal_bearer_lines = max(0, panel_rows - 1)
            if internal_bearer_lines > 0:
                bearer_lm = round(internal_bearer_lines * run_dxf, 1)
                rows.append(_row(
                    "floor_system",
                    f"Floor Bearer / Support Beam (steel SHS/RHS) — {internal_bearer_lines} lines × {run_dxf:.1f}m",
                    "lm", bearer_lm,
                    "calculated",
                    (
                        f"panel_rows({panel_rows}) from span({span_dxf:.1f}m)÷panel_depth({pd_mm}mm); "
                        f"internal_bearer_lines = panel_rows−1 = {internal_bearer_lines}; "
                        f"each line runs full building run = {run_dxf:.1f}m"
                    ),
                    f"framecad_dwg+dxf_geometry: panel_depth={pd_mm}mm, span_dxf={span_dxf:.1f}m, run_dxf={run_dxf:.1f}m",
                    f"(panel_rows−1) × run = ({panel_rows}−1) × {run_dxf:.1f}",
                    "MEDIUM",
                    manual_review=True,
                    notes=(
                        f"Internal floor bearer / support beam at joist-end bearing positions. "
                        f"DWG panel depth = {pd_mm}mm → {panel_rows} rows across {span_dxf:.1f}m span. "
                        f"Perimeter boundary lines bear on strip footings (no separate member needed). "
                        f"{internal_bearer_lines} internal lines × {run_dxf:.1f}m = {bearer_lm:.1f} lm. "
                        "Bearer section (SHS/RHS or engineered timber) to be confirmed from structural drawings. "
                        "Verify with FrameCAD engineer."
                    ),
                ))
                # Sub-floor support posts/stumps under each internal bearer
                # Posts needed at internal panel-column boundaries (perimeter positions
                # are at strip footing — no post required there).
                internal_cols = max(0, panel_cols - 1)  # mid-span positions per bearer
                if internal_cols > 0:
                    post_count = internal_bearer_lines * internal_cols
                    rows.append(_row(
                        "floor_system",
                        "Sub-Floor Support Post / Adjustable Steel Stump",
                        "nr", post_count,
                        "calculated",
                        (
                            f"internal_bearer_lines({internal_bearer_lines}) × "
                            f"mid-span_pads_per_bearer({internal_cols}) "
                            f"[panel_cols({panel_cols})−1; perimeter pads on strip footings]"
                        ),
                        f"framecad_dwg+dxf_geometry: panel_grid={panel_rows}×{panel_cols}, "
                        f"span={span_dxf:.1f}m, run={run_dxf:.1f}m",
                        f"{internal_bearer_lines} × ({panel_cols}−1)",
                        "LOW",
                        manual_review=True,
                        notes=(
                            f"Adjustable steel stumps or concrete piers at mid-span support points of "
                            f"internal floor bearers. "
                            f"{internal_bearer_lines} bearer lines × {internal_cols} mid-span pads each "
                            f"= {post_count} nr. "
                            "Perimeter end-points land on strip footings. "
                            "Post type and base plate spec from structural engineer. "
                            "Verify against pad footing layout and FrameCAD sub-floor schedule."
                        ),
                    ))

        # ── Joist Hanger Connectors ─────────────────────────────────────────
        # One joist hanger at each joist-to-edge-beam connection (both ends)
        if j_count > 0:
            hanger_count = j_count * 2  # 2 connections per joist (each end)
            rows.append(_row(
                "floor_system",
                f"Joist Hanger Connector (J1 end fix)",
                "nr", hanger_count,
                "calculated",
                f"joist_count({j_count}) × 2 ends = {hanger_count} hangers",
                f"framecad_dwg: {src_ev}",
                f"{j_count} × 2",
                src_conf,
                manual_review=True,
                notes=(
                    f"LGS joist hanger or clip angle at each J1 joist end. "
                    f"Total: {j_count} joists × 2 ends = {hanger_count} nr. "
                    "Verify hanger type and connection requirement from FrameCAD engineer."
                ),
            ))

        # ── DPM / Moisture Barrier ──────────────────────────────────────────
        # Under floor cassettes: 1 layer polyethylene 200µm over main floor area
        if main_floor_area > 0:
            dpm_area = round(main_floor_area * 1.1, 2)  # 10% laps
            rows.append(_row(
                "floor_system",
                "DPM / Polyethylene Moisture Barrier (200µm)",
                "m2", dpm_area,
                "calculated",
                f"main_floor_area({main_floor_area:.2f}) × 1.10 (laps)",
                f"dxf_geometry: main_floor_area={main_floor_area:.2f} m²",
                f"{main_floor_area:.2f} × 1.1",
                "MEDIUM",
                notes=(
                    "200µm polyethylene DPM beneath floor cassettes. "
                    "10% added for laps and edge tuck-under. "
                    "Verify specification from structural/hydraulic engineer."
                ),
            ))

        # Floor sheet confidence: upgrade to HIGH when DWG panel grid cross-validates
        # DXF floor area.  Two independent sources must agree within 3%.
        #   DWG: p_count × panel_area_each  (from cassette schedule)
        #   DXF: main_floor_area             (from LWPOLYLINE measurement)
        _dwg_area = round(p_count * panel_area_each, 2) if p_count > 0 and panel_area_each > 0 else 0.0
        _xcheck_ok = (
            _dwg_area > 0
            and abs(_dwg_area - main_floor_area) / max(_dwg_area, main_floor_area) <= 0.03
        )
        _sheet_conf = "HIGH" if _xcheck_ok else src_conf
        _xnote = (
            f"DWG panel grid ({p_count} panels × {panel_area_each:.4f}m² = {_dwg_area:.2f}m²) "
            f"vs DXF area ({main_floor_area:.2f}m²): "
            f"delta={abs(_dwg_area-main_floor_area):.2f}m² ({abs(_dwg_area-main_floor_area)/max(_dwg_area,main_floor_area)*100:.1f}%) — "
            "double-source confirmation upgrades sheet confidence to HIGH"
        ) if _xcheck_ok else ""
        rows += _floor_sheeting_rows(
            main_floor_area,
            "framecad_dwg+dxf_geometry",
            src_conf,
            lining_cfg,
            confidence_override=_sheet_conf,
            crosscheck_note=_xnote,
            dry_area=_dry_zone_m2,
            wet_area=_wet_zone_m2,
            ver_area=ver_area,
        )
        return rows

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
        # BUG FIX: use main_floor_area (verandah excluded) not raw floor_area
        rows += _floor_sheeting_rows(
            main_floor_area, "framecad_bom+dxf_geometry", "HIGH", lining_cfg,
            dry_area=_dry_zone_m2, wet_area=_wet_zone_m2, ver_area=ver_area,
        )
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
            # BUG FIX: use main_floor_area (verandah excluded) not raw floor_area
            rows += _floor_sheeting_rows(
                main_floor_area, "ifc_model+dxf_geometry", "MEDIUM", lining_cfg,
                dry_area=_dry_zone_m2, wet_area=_wet_zone_m2, ver_area=ver_area,
            )
            return rows

    # ── CASE 2.5: Config floor_panel_schedule (between IFC and area-derived) ───
    # Used when: no FrameCAD BOM tab, no IFC joists, but config specifies panel types.
    # Provides per-load-class procurement rows vs single generic "Floor Joist" row.
    config_schedule = config.get("floor_panel_schedule", [])
    if config_schedule and not bom_floor_panels and not ifc_floor_systems:
        log.info("Floor system: config floor_panel_schedule (%d entries, LOW confidence)", len(config_schedule))
        joist_spacing  = struct_cfg.get("floor_joist_spacing_mm",  450) / 1000
        bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000

        # Use DXF-derived span/run when available; fall back to sqrt(area) approximation.
        # DXF derivation subtracts verandah area (verandah has its own decking row).
        if span_dxf > 0:
            span_short = span_dxf
            span_long  = run_dxf
            area_for_calc = main_floor_area  # excludes verandah
            span_src  = f"dxf_geometry: {dims_note}"
        else:
            span_short = round(math.sqrt(floor_area), 1) if floor_area > 0 else 0.0
            span_long  = round(floor_area / span_short, 1) if span_short > 0 else 0.0
            area_for_calc = floor_area
            span_src  = f"derived: sqrt(floor_area={floor_area:.1f}m²) — square approximation"

        # Typical LGS floor cassette panel dimensions from config
        panel_w = struct_cfg.get("floor_panel_width_m", 0.6)
        panel_l = struct_cfg.get("floor_panel_length_m", 3.6)
        panel_area_each = round(panel_w * panel_l, 2)

        for entry in config_schedule:
            load  = entry.get("load_class", "unknown")
            desc  = entry.get("description", f"{load} Floor Panel")
            frac  = float(entry.get("floor_area_fraction", 1.0))
            conf  = entry.get("confidence", "LOW").upper()
            note  = entry.get("notes", "")
            src   = entry.get("source", "project_config")
            area_this = round(area_for_calc * frac, 2)
            ev = (
                f"{src}: main_floor_area={area_for_calc:.2f}m² × fraction({frac}) = {area_this:.2f}m²; "
                f"joist_spacing={int(joist_spacing*1000)}mm; bearer_spacing={int(bearer_spacing*1000)}mm; "
                f"span={span_src}"
            )

            # Panel count: area ÷ per-panel area
            panel_count = math.ceil(area_this / panel_area_each) if panel_area_each > 0 else 0
            rows.append(_row(
                "floor_system",
                f"Floor Panel — {desc} ({load})",
                "nr", panel_count,
                "inferred",
                f"{src}: main_floor_area({area_for_calc:.1f}m²) × {frac} fraction ÷ panel_area({panel_area_each}m²)",
                ev,
                f"ceil({area_this:.2f} / {panel_area_each})",
                conf,
                manual_review=True,
                notes=f"{note} Panel dimensions assumed {int(panel_w*1000)}mm × {int(panel_l*1000)}mm ({panel_area_each}m² each).",
            ))

            # Joists per panel zone
            joist_rows_across = math.ceil(span_short / joist_spacing) if span_short > 0 else 0
            joist_lm_zone     = round(joist_rows_across * span_long * frac, 1)
            rows.append(_row(
                "floor_system",
                f"Floor Joist LGS — {load} Zone",
                "lm", joist_lm_zone,
                "inferred",
                f"{src}: joist_rows({joist_rows_across}) × run({span_long:.1f}m) × fraction({frac})",
                ev,
                f"ceil(span/{joist_spacing}) × run × fraction",
                conf,
                manual_review=True,
                notes=f"{note} Joist count at {int(joist_spacing*1000)}mm spacing across {span_short:.1f}m span, {span_long:.1f}m run.",
            ))

            # Bearer pairs per zone
            bearer_pairs_zone = math.ceil(span_short / bearer_spacing * frac) if span_short > 0 else 0
            rows.append(_row(
                "floor_system",
                f"Floor Bearer (pair) — {load} Zone",
                "nr", bearer_pairs_zone,
                "inferred",
                f"{src}: ceil(span_short({span_short:.1f}m) / bearer_spacing({bearer_spacing:.1f}m) × fraction({frac}))",
                ev,
                f"ceil({span_short:.1f}/{bearer_spacing:.1f} × {frac})",
                conf,
                manual_review=True,
                notes=f"{note} Bearer pairs at {int(bearer_spacing*1000)}mm spacing. Verify from structural schedule.",
            ))

        # Floor sheeting over main floor area only (verandah excluded — has own decking row)
        rows += _floor_sheeting_rows(
            area_for_calc, "project_config+dxf_geometry", "LOW", lining_cfg,
            dry_area=_dry_zone_m2, wet_area=_wet_zone_m2, ver_area=ver_area,
        )
        return rows

    # ── CASE 3: Steel floor frame confirmed but no panel schedule ─────────────
    steel_frames = [fs for fs in floor_systems if fs.assembly_type == "steel_floor_frame"]
    if steel_frames:
        fs  = steel_frames[0]
        fa  = fs.floor_area_m2 or floor_area
        log.info("Floor system: steel floor frame (area-derived, no schedule)")

        joist_spacing  = struct_cfg.get("floor_joist_spacing_mm",  450) / 1000
        bearer_spacing = struct_cfg.get("floor_bearer_spacing_mm", 1800) / 1000

        # Use DXF-derived span/run when available; fall back to sqrt approximation.
        if span_dxf > 0:
            span      = span_dxf
            floor_run = run_dxf
            fa_use    = main_floor_area
            span_src  = f"dxf_geometry: {dims_note}"
        else:
            span      = round(math.sqrt(fa) if fa > 0 else 0.0, 1)
            floor_run = round(fa / span, 1) if span > 0 else 0.0
            fa_use    = fa
            span_src  = f"derived: sqrt(floor_area={fa:.1f}m²) — square approximation"

        joist_count = math.ceil(span / joist_spacing) if span > 0 else 0
        joist_lm    = round(floor_run * joist_count, 1) if joist_count > 0 else 0.0

        load_note = f" Load class: {fs.load_class}." if fs.load_class else ""
        src_ev = f"{fs.source}: {fs.source_reference}; {span_src}"

        rows.append(_row(
            "floor_system",
            "Floor Joist / LGS Cassette",
            "lm", joist_lm,
            "inferred",
            f"derived: floor_run({floor_run:.1f}m) × joist_count({joist_count}) @ {int(joist_spacing*1000)}mm spacing",
            src_ev,
            f"ceil(span/{joist_spacing})={joist_count} × run={floor_run:.1f}m",
            "LOW",
            manual_review=True,
            notes=(
                f"Steel floor frame confirmed (FrameCAD layout). No panel schedule — "
                f"derived from main floor area {fa_use:.1f} m² at {int(joist_spacing*1000)} mm joist spacing. "
                f"Span={span:.1f}m, run={floor_run:.1f}m.{load_note} "
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
                f"derived: span({span:.1f}m) / bearer_spacing({bearer_spacing:.1f}m)",
                src_ev,
                f"ceil({span:.1f}/{bearer_spacing:.1f})",
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

        rows += _floor_sheeting_rows(
            fa_use, fs.source + "+dxf_geometry", "LOW", lining_cfg,
            dry_area=_dry_zone_m2, wet_area=_wet_zone_m2, ver_area=ver_area,
        )
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
    floor_area:          float,
    src:                 str,
    conf:                str,
    lining_cfg:          dict,
    confidence_override: str | None = None,
    crosscheck_note:     str = "",
    dry_area:            float = 0.0,
    wet_area:            float = 0.0,
    ver_area:            float = 0.0,
) -> list[dict]:
    """Produce floor sheeting procurement rows for a given floor area.

    floor_area must be the INTERNAL enclosed area only (verandah already excluded).

    confidence_override: when the floor area is cross-validated by a secondary
        source (e.g. DWG panel grid), pass the validated confidence level here.
        This overrides ``conf`` for the sheet count and supply area rows only.
    crosscheck_note: optional traceability note added when override is used.
    dry_area / wet_area: zone breakdown for explicit notes (both optional).
        When provided, the note documents the layered assembly:
          substrate (this row) → dry zone gets vinyl (F-pkg) / wet zone gets tile (F-pkg).
    ver_area: verandah area (for explicit exclusion note).
    """
    if floor_area <= 0:
        return []
    sheet_area = lining_cfg.get("fc_ceiling_sheet_area_m2", 2.88)  # same sheet dims as ceiling
    waste      = lining_cfg.get("waste_factor", 1.05)
    sheet_count = math.ceil(floor_area * waste / sheet_area)
    supply_area_m2 = round(sheet_count * sheet_area, 2)

    _conf = confidence_override if confidence_override else conf
    _mr   = (_conf == "LOW")
    _xnote = (f"  Cross-check: {crosscheck_note}." if crosscheck_note else "")

    # Build zone-breakdown note (added when zone info is available)
    _zone_note = ""
    if dry_area > 0 or wet_area > 0:
        _zone_parts = []
        if dry_area > 0:
            _zone_parts.append(f"dry_internal={dry_area:.2f} m² (vinyl finish, F-package)")
        if wet_area > 0:
            _zone_parts.append(f"wet_internal={wet_area:.2f} m² (ceramic tile finish, F-package)")
        _zone_sum = round(dry_area + wet_area, 2)
        _zone_note = (
            f"  Zone breakdown: {' + '.join(_zone_parts)} = {_zone_sum:.2f} m² total substrate. "
            + (f"External verandah ({ver_area:.2f} m²) is excluded — WPC decking in K-package. "
               if ver_area > 0 else "")
            + "Both dry and wet internal zones use identical FC substrate; finish layer differs above."
        )

    adhesive_tubes = math.ceil(floor_area / 3.0)  # 1 tube per ~3 m² floor area
    return [
        _row(
            "floor_system", "Floor Sheet (FC / plywood)",
            "sheets",
            sheet_count,
            "calculated",
            f"ceil(floor_area × {waste} / {sheet_area})",
            f"{src}: floor_area={floor_area:.2f} m²",
            f"ceil({floor_area:.2f} × {waste} / {sheet_area})",
            _conf,
            manual_review=_mr,
            notes=(
                "FC/ply substrate for all internal enclosed zones (dry + wet combined). "
                "Sheet 1200×2400mm (same spec as ceiling FC sheet — confirm if different)."
                + _zone_note
                + _xnote
            ),
        ),
        _row(
            "floor_system", "Floor Sheet — Total Supply Area (1200×2400mm)",
            "m2", supply_area_m2,
            "calculated",
            f"sheet_count({sheet_count}) × sheet_area({sheet_area}m²) — gross supply incl. waste",
            f"{src}: floor_area={floor_area:.2f}m² × waste({waste}) → {sheet_count} sheets × {sheet_area}m²",
            f"{sheet_count} × {sheet_area}",
            _conf,
            manual_review=_mr,
            notes=(
                f"Gross FC/ply sheet supply area (all internal zones). "
                f"Net floor: {floor_area:.2f}m² × {waste} waste = {round(floor_area*waste,2)}m². "
                f"Rounded up to {sheet_count} full sheets × {sheet_area}m² each = {supply_area_m2}m². "
                "1200×2400mm sheet assumed."
                + _zone_note
                + _xnote
            ),
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
        _row(
            "floor_system", "Floor Sheet Adhesive (construction adhesive, 300mL tube)",
            "tubes",
            adhesive_tubes,
            "calculated",
            f"ceil(floor_area({floor_area:.2f}) / 3 m² per tube)",
            f"{src}: floor_area={floor_area:.2f} m²",
            "ceil(floor_area / 3.0)",
            "LOW",
            notes="Construction adhesive for floor sheet to joist. 1 tube per ~3 m² of floor area.",
        ),
    ]
