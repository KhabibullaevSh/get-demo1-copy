"""
geometry_reconciler.py — Build the CanonicalGeometryModel from ProjectElementModel.

This module is the bridge between the raw element model (extractors + element_builder)
and the canonical geometry layer.  It applies multi-source fusion, classification
rules, and pre-computes net areas.

PARTS B + C of the geometry fusion architecture:
  B — Candidate generation: reads openings, walls, spaces from element model
  C — Reconciliation:       classifies each candidate, deduces net areas,
                            assigns TruthClass, and assembles the canonical model

Source priority (applied throughout):
  1. DXF geometry      → HIGH / MEASURED
  2. FrameCAD / PDF   → MEDIUM / CALCULATED
  3. IFC              → MEDIUM / MEASURED (secondary)
  4. config           → LOW  / CONFIG_FALLBACK

INVARIANT: No BOQ reference quantities enter this module.  Pure geometry.

Called from main.py step [2d] after all extractors and graphical reconciliation
have run, before the quantifiers are invoked.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from v3_boq_system.normalize.element_model import (
    OpeningElement,
    ProjectElementModel,
    SpaceElement,
    WallElement,
)
from v3_boq_system.normalize.canonical_objects import (
    CanonicalCladdingFace,
    CanonicalFloorZone,
    CanonicalGeometryModel,
    CanonicalOpening,
    CanonicalSpace,
    CanonicalWallFace,
    TruthClass,
)

log = logging.getLogger("boq.v3.geometry_reconciler")

# ── Classification constants ───────────────────────────────────────────────────

# Entrance door threshold — separates external entry doors (≥ 850 mm) from
# internal partition doors (< 850 mm).  Australian commercial standard.
_EXT_DOOR_MIN_W: float = 0.85   # metres

# Source string → TruthClass mapping (substring match, priority order)
_SOURCE_TRUTH_MAP: list[tuple[str, str]] = [
    ("dxf_geometry",               TruthClass.MEASURED),
    ("dxf",                        TruthClass.MEASURED),
    ("ifc_model",                  TruthClass.MEASURED),
    ("ifc",                        TruthClass.MEASURED),
    ("framecad_panel_label_ocr",   TruthClass.CALCULATED),
    ("framecad_bom",               TruthClass.CALCULATED),
    ("framecad_layout",            TruthClass.CALCULATED),
    ("framecad",                   TruthClass.CALCULATED),
    ("pdf_schedule",               TruthClass.CALCULATED),
    ("derived_ratio",              TruthClass.INFERRED),
    ("derived",                    TruthClass.INFERRED),
    ("config_schedule",            TruthClass.CONFIG_FALLBACK),
    ("config",                     TruthClass.CONFIG_FALLBACK),
    ("unknown",                    TruthClass.INFERRED),
]


def _truth_from_source(source: str) -> str:
    """Map a source string → TruthClass (first prefix match wins)."""
    src = (source or "").lower()
    for key, tc in _SOURCE_TRUTH_MAP:
        if key in src:
            return tc
    return TruthClass.INFERRED


def _conf_rank(c: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(c, 0)


def _max_conf(walls: list[WallElement]) -> str:
    return max((w.confidence for w in walls), key=_conf_rank)


# ═══════════════════════════════════════════════════════════════════════════════
# PART B — Candidate generators
# ═══════════════════════════════════════════════════════════════════════════════

def _build_canonical_openings(
    model:            ProjectElementModel,
    louvre_h_default: float,
) -> list[CanonicalOpening]:
    """
    PART B: Generate CanonicalOpening candidates from element model openings.

    Classification logic applied here:
      - Doors with width ≥ _EXT_DOOR_MIN_W → is_entrance=True, is_cladding_face=True
      - Doors with width  < _EXT_DOOR_MIN_W → is_partition=True, is_cladding_face=False
      - Windows that are external → is_cladding_face=True
      - Windows with height_m == 0.0 → louvre height default applied, height_fallback_used=True
      - opening_area_m2 always includes quantity (fixes the "× qty" bug at canonical level)
    """
    canonical: list[CanonicalOpening] = []

    for op in model.openings:
        is_door   = op.opening_type == "door"
        is_window = op.opening_type == "window"

        is_entrance  = is_door and op.width_m >= _EXT_DOOR_MIN_W
        is_partition = is_door and op.width_m < _EXT_DOOR_MIN_W

        # Height resolution with explicit fallback tracking
        if is_window and op.height_m == 0.0:
            height_used      = louvre_h_default
            height_fallback  = True
        elif op.height_m > 0:
            height_used      = op.height_m
            height_fallback  = False
        else:
            # Door with no height — use 2.1 m Australian residential standard
            height_used     = 2.1
            height_fallback = True

        # Cladding face: entrance doors + all is_external windows with width > 0
        is_clad = (is_entrance and op.is_external) or (
            is_window and op.is_external and op.width_m > 0
        )

        # Canonical deduction area — ALWAYS quantity-correct
        opening_area = round(op.width_m * height_used * op.quantity, 3)

        # TruthClass — downgrade from MEASURED if height came from fallback
        base_tc = _truth_from_source(op.source)
        if height_fallback and base_tc == TruthClass.MEASURED:
            # Width is measured, height is not → CALCULATED overall
            tc = TruthClass.CALCULATED
        else:
            tc = base_tc

        # Build human-readable notes for traceability
        note_parts: list[str] = []
        if height_fallback:
            note_parts.append(
                f"height_fallback: raw={op.height_m:.3f} m → using {height_used:.3f} m "
                f"({'louvre_default' if is_window else 'door_standard'})"
            )
        if is_partition:
            note_parts.append(
                f"partition_door: width={op.width_m:.3f} m < {_EXT_DOOR_MIN_W:.3f} m "
                "threshold — excluded from external cladding face"
            )
        elif is_entrance:
            note_parts.append(
                f"entrance_door: width={op.width_m:.3f} m ≥ {_EXT_DOOR_MIN_W:.3f} m — "
                "included in cladding deduction"
            )
        note_parts.append(
            f"area={opening_area:.3f} m² ({op.quantity}×{op.width_m:.3f}×{height_used:.3f})"
        )

        cop = CanonicalOpening(
            id                   = f"cop_{op.element_id or op.mark}",
            mark                 = op.mark,
            opening_type         = op.opening_type,
            width_m              = op.width_m,
            height_m_raw         = op.height_m,
            height_used          = height_used,
            height_fallback_used = height_fallback,
            quantity             = op.quantity,
            is_external          = op.is_external,
            is_entrance          = is_entrance,
            is_cladding_face     = is_clad,
            is_partition         = is_partition,
            opening_area_m2      = opening_area,
            level                = "GF",
            source_files         = ([op.source_reference]
                                    if op.source_reference else [op.source]),
            source_entity_ids    = [op.element_id] if op.element_id else [],
            confidence           = op.confidence,
            truth_class          = tc,
            fallback_used        = height_fallback,
            notes                = "; ".join(note_parts),
        )
        canonical.append(cop)

        log.debug(
            "  opening %-18s: entrance=%-5s partition=%-5s clad=%-5s "
            "area=%6.3f (qty=%d×%.3f×%.3f) tc=%s",
            cop.mark, cop.is_entrance, cop.is_partition, cop.is_cladding_face,
            cop.opening_area_m2, op.quantity, op.width_m, height_used, tc,
        )

    return canonical


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — Reconciliation
# ═══════════════════════════════════════════════════════════════════════════════

def _build_canonical_wall_faces(
    model:             ProjectElementModel,
    canonical_openings: list[CanonicalOpening],
) -> list[CanonicalWallFace]:
    """
    PART C: Build CanonicalWallFace from element model walls.

    External face: deducts cladding-face openings (entrance doors + windows).
    Internal face: deducts partition doors from combined both-face area.

    Net area is pre-computed here.  Quantifiers read net_area_m2 directly.
    """
    faces: list[CanonicalWallFace] = []

    # ── External wall face ────────────────────────────────────────────────────
    ext_walls = [w for w in model.walls if w.wall_type == "external"]
    if ext_walls:
        ext_lm     = sum(w.length_m for w in ext_walls)
        ext_h      = max(w.height_m for w in ext_walls)
        ext_src    = ext_walls[0].source
        ext_conf   = _max_conf(ext_walls)
        gross_area = round(ext_lm * ext_h, 2)

        clad_ops   = [o for o in canonical_openings if o.is_cladding_face]
        ded_area   = round(sum(o.opening_area_m2 for o in clad_ops), 3)
        net_area   = round(max(0.0, gross_area - ded_area), 2)

        tc = _truth_from_source(ext_src)
        faces.append(CanonicalWallFace(
            id                   = "wf_external",
            wall_type            = "external",
            length_m             = ext_lm,
            height_m             = ext_h,
            gross_area_m2        = gross_area,
            net_area_m2          = net_area,
            opening_deduction_m2 = ded_area,
            opening_ids          = [o.id for o in clad_ops],
            is_cladding_face     = True,
            source_files         = [ext_src],
            source_entity_ids    = [w.element_id for w in ext_walls
                                    if w.element_id],
            confidence           = ext_conf,
            truth_class          = tc,
            fallback_used        = (tc == TruthClass.CONFIG_FALLBACK),
            notes                = (
                f"External wall face: {ext_lm:.2f} m × {ext_h:.1f} m = "
                f"{gross_area:.2f} m² gross. "
                f"Cladding deductions: {len(clad_ops)} opening types, "
                f"{ded_area:.3f} m². Net: {net_area:.2f} m²."
            ),
        ))

    # ── Internal wall face (both sides combined) ──────────────────────────────
    int_walls = [w for w in model.walls if w.wall_type == "internal"]
    if int_walls:
        int_lm     = sum(w.length_m for w in int_walls)
        int_h      = max(w.height_m for w in int_walls)
        int_src    = int_walls[0].source
        int_conf   = int_walls[0].confidence
        # WallElement.area_m2 = length × height × faces (faces=2 for internal)
        gross_both = round(sum(w.area_m2 for w in int_walls), 2)

        # Partition doors cut through BOTH faces of the partition wall
        part_ops   = [o for o in canonical_openings if o.is_partition]
        ded_both   = round(sum(o.opening_area_m2 * 2 for o in part_ops), 3)
        net_both   = round(max(0.0, gross_both - ded_both), 2)

        tc = _truth_from_source(int_src)
        faces.append(CanonicalWallFace(
            id                   = "wf_internal",
            wall_type            = "internal",
            length_m             = int_lm,
            height_m             = int_h,
            gross_area_m2        = gross_both,
            net_area_m2          = net_both,
            opening_deduction_m2 = ded_both,
            opening_ids          = [o.id for o in part_ops],
            is_cladding_face     = False,
            source_files         = [int_src],
            source_entity_ids    = [w.element_id for w in int_walls
                                    if w.element_id],
            confidence           = int_conf,
            truth_class          = tc,
            fallback_used        = (tc in (TruthClass.CONFIG_FALLBACK,
                                           TruthClass.INFERRED)),
            notes                = (
                f"Internal wall both faces: {int_lm:.2f} m × {int_h:.1f} m × 2 = "
                f"{gross_both:.2f} m² gross. "
                f"Partition door deductions: {len(part_ops)} types, "
                f"{ded_both:.3f} m² (× 2 faces). Net: {net_both:.2f} m²."
            ),
        ))

    return faces


def _build_cladding_faces(
    wall_faces:         list[CanonicalWallFace],
    canonical_openings: list[CanonicalOpening],
    louvre_h_default:   float,
) -> list[CanonicalCladdingFace]:
    """Build CanonicalCladdingFace from the external CanonicalWallFace."""
    ext_face = next((wf for wf in wall_faces if wf.wall_type == "external"), None)
    if ext_face is None:
        return []

    door_ops   = [o for o in canonical_openings
                  if o.is_cladding_face and o.opening_type == "door"]
    window_ops = [o for o in canonical_openings
                  if o.is_cladding_face and o.opening_type == "window"]
    door_ded   = round(sum(o.opening_area_m2 for o in door_ops), 3)
    win_ded    = round(sum(o.opening_area_m2 for o in window_ops), 3)

    # Build deduction detail for notes
    door_detail = "; ".join(
        f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_used:.3f}m)"
        for o in door_ops
    )
    win_detail = "; ".join(
        f"{o.mark}×{o.quantity}({o.width_m:.3f}×{o.height_used:.3f}m"
        + (" [louvre_fallback]" if o.height_fallback_used else "") + ")"
        for o in window_ops
    )

    return [CanonicalCladdingFace(
        id                   = "cf_external_gf",
        wall_face_id         = ext_face.id,
        gross_area_m2        = ext_face.gross_area_m2,
        net_area_m2          = ext_face.net_area_m2,
        opening_deduction_m2 = ext_face.opening_deduction_m2,
        opening_ids          = ext_face.opening_ids[:],
        ext_lm               = ext_face.length_m,
        wall_height_m        = ext_face.height_m,
        door_deduction_m2    = door_ded,
        window_deduction_m2  = win_ded,
        door_opening_ids     = [o.id for o in door_ops],
        window_opening_ids   = [o.id for o in window_ops],
        louvre_height_default_m = louvre_h_default,
        source_files         = ext_face.source_files[:],
        source_entity_ids    = ext_face.source_entity_ids[:],
        confidence           = ext_face.confidence,
        truth_class          = ext_face.truth_class,
        fallback_used        = ext_face.fallback_used,
        notes                = (
            f"Gross={ext_face.gross_area_m2:.2f} m² "
            f"({ext_face.length_m:.2f} m × {ext_face.height_m:.1f} m). "
            f"Entrance doors: {door_ded:.3f} m² [{door_detail or 'none'}]. "
            f"Windows: {win_ded:.3f} m² [{win_detail or 'none'}] "
            f"(louvre_h_default={louvre_h_default:.2f} m). "
            f"Net cladding: {ext_face.net_area_m2:.2f} m². "
            "Partition doors excluded (width < 0.85 m — internal walls only)."
        ),
    )]


def _build_canonical_spaces(
    model: ProjectElementModel,
) -> list[CanonicalSpace]:
    """
    PART C + D: Build CanonicalSpace with explicit TruthClass from SpaceElement.

    Maps source_type → TruthClass and perimeter_source → human-readable label.
    Config-sourced spaces always get CONFIG_FALLBACK; DXF-backed ones get MEASURED
    (polygon) or CALCULATED (wall-network perimeter without full polygon).
    """
    result: list[CanonicalSpace] = []

    for sp in model.spaces:
        st = (sp.source_type or "config").lower()

        if ("dxf" in st or "ifc" in st) and sp.polygon:
            tc        = TruthClass.MEASURED
            perim_src = "measured"
        elif ("dxf" in st or "ifc" in st) and sp.perimeter_m > 0:
            tc        = TruthClass.CALCULATED
            perim_src = "calculated_from_geometry"
        elif st == "config" and sp.perimeter_m > 0:
            # Perimeter explicitly listed in config (not estimated from area)
            tc        = TruthClass.CONFIG_FALLBACK
            perim_src = "config_specified"
        else:
            tc        = TruthClass.CONFIG_FALLBACK
            perim_src = "estimated"

        conf = sp.confidence if sp.confidence else "LOW"

        note_parts: list[str] = []
        if tc == TruthClass.CONFIG_FALLBACK:
            note_parts.append(
                f"truth_class=config_fallback: area/perimeter from project_config "
                f"(not measured from drawings). perimeter_source={perim_src}."
            )
        if sp.classification_notes:
            note_parts.append(sp.classification_notes)

        csp = CanonicalSpace(
            id               = f"csp_{sp.space_id or sp.space_name.lower().replace(' ', '_')}",
            space_name       = sp.space_name,
            space_type       = sp.space_type,
            area_m2          = sp.area_m2,
            perimeter_m      = sp.perimeter_m,
            perimeter_source = perim_src,
            ceiling_area_m2  = sp.ceiling_area_m2,
            is_wet           = sp.is_wet,
            is_external      = sp.is_external,
            is_verandah      = sp.is_verandah,
            is_enclosed      = sp.is_enclosed,
            finish_floor     = sp.finish_floor_type,
            finish_wall      = sp.finish_wall_type,
            finish_ceiling   = sp.finish_ceiling_type,
            finish_source    = (sp.quantity_basis or "inferred_from_type"),
            level            = sp.level or "GF",
            polygon          = list(sp.polygon) if sp.polygon else [],
            source_files     = ([sp.source_ref] if sp.source_ref else []),
            source_entity_ids = list(sp.contributing_space_refs or []),
            confidence       = conf,
            truth_class      = tc,
            fallback_used    = (tc == TruthClass.CONFIG_FALLBACK),
            notes            = "; ".join(p for p in note_parts if p),
        )
        result.append(csp)

    return result


def _build_floor_zones(
    canonical_spaces: list[CanonicalSpace],
) -> list[CanonicalFloorZone]:
    """
    PART C: Aggregate canonical spaces into finish-procurement floor zones.

    Zones:
      internal_wet   — wet rooms (tile finish)
      internal_dry   — enclosed dry rooms, grouped by finish type
      verandah       — verandah / covered external

    Each zone carries the minimum (weakest) TruthClass of its constituent spaces
    so that config-backed rooms propagate LOW/CONFIG_FALLBACK status.
    """
    zones: list[CanonicalFloorZone] = []

    # Wet zone
    wet = [s for s in canonical_spaces if s.is_wet and s.is_enclosed]
    if wet:
        tc      = TruthClass.weakest([s.truth_class for s in wet])
        conf    = min((s.confidence for s in wet), key=_conf_rank)
        zones.append(CanonicalFloorZone(
            id           = "fz_wet",
            zone_name    = "Wet Areas",
            zone_type    = "internal_wet",
            finish_type  = wet[0].finish_floor or "ceramic_tile",
            area_m2      = round(sum(s.area_m2 for s in wet), 2),
            perimeter_m  = round(sum(s.perimeter_m for s in wet), 2),
            space_ids    = [s.id for s in wet],
            confidence   = conf,
            truth_class  = tc,
            fallback_used = (tc == TruthClass.CONFIG_FALLBACK),
            notes        = (
                f"Wet floor zone: {[s.space_name for s in wet]}. "
                f"area={round(sum(s.area_m2 for s in wet), 2):.2f} m². "
                + ("Config-fallback areas — verify from drawings."
                   if tc == TruthClass.CONFIG_FALLBACK else "")
            ),
        ))

    # Dry internal zones — grouped by finish type
    dry = [s for s in canonical_spaces
           if not s.is_wet and s.is_enclosed and not s.is_external]
    finish_groups: dict[str, list[CanonicalSpace]] = {}
    for s in dry:
        ft = s.finish_floor or "vinyl_plank"
        finish_groups.setdefault(ft, []).append(s)
    for ft, group in finish_groups.items():
        tc   = TruthClass.weakest([s.truth_class for s in group])
        conf = min((s.confidence for s in group), key=_conf_rank)
        zones.append(CanonicalFloorZone(
            id           = f"fz_dry_{ft.replace('_', '').replace('-', '')}",
            zone_name    = f"Internal Dry — {ft.replace('_', ' ').title()}",
            zone_type    = "internal_dry",
            finish_type  = ft,
            area_m2      = round(sum(s.area_m2 for s in group), 2),
            space_ids    = [s.id for s in group],
            confidence   = conf,
            truth_class  = tc,
            fallback_used = (tc == TruthClass.CONFIG_FALLBACK),
            notes        = (
                f"Dry floor zone ({ft}): {[s.space_name for s in group]}. "
                f"area={round(sum(s.area_m2 for s in group), 2):.2f} m²."
            ),
        ))

    # Verandah zone
    ver = [s for s in canonical_spaces if s.is_verandah]
    if ver:
        tc   = TruthClass.weakest([s.truth_class for s in ver])
        conf = min((s.confidence for s in ver), key=_conf_rank)
        zones.append(CanonicalFloorZone(
            id           = "fz_verandah",
            zone_name    = "Verandah",
            zone_type    = "verandah",
            finish_type  = ver[0].finish_floor or "decking",
            area_m2      = round(sum(s.area_m2 for s in ver), 2),
            space_ids    = [s.id for s in ver],
            confidence   = conf,
            truth_class  = tc,
            fallback_used = (tc == TruthClass.CONFIG_FALLBACK),
            notes        = (
                f"Verandah/external covered zone: {[s.space_name for s in ver]}. "
                f"area={round(sum(s.area_m2 for s in ver), 2):.2f} m²."
            ),
        ))

    return zones


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def build_canonical_geometry(
    model:  ProjectElementModel,
    config: dict,
) -> CanonicalGeometryModel:
    """
    Build the CanonicalGeometryModel from a fully-populated ProjectElementModel.

    Should be called AFTER:
      - All extractors have run
      - element_builder has built the element model
      - space_builder has built the space model (element_model.spaces populated)
      - graphical reconciler has run (window heights promoted where available)

    Args:
        model:  Populated ProjectElementModel (post-graphical-reconciliation)
        config: Project config dict

    Returns:
        CanonicalGeometryModel containing all canonical objects.
    """
    lining_cfg       = config.get("lining", {})
    louvre_h_default = lining_cfg.get("default_louvre_height_m", 0.75)

    log.info(
        "Building canonical geometry: openings=%d walls=%d spaces=%d",
        len(model.openings), len(model.walls), len(model.spaces),
    )

    # B — Candidates
    canonical_openings = _build_canonical_openings(model, louvre_h_default)

    # C — Reconciliation
    canonical_wall_faces = _build_canonical_wall_faces(model, canonical_openings)
    cladding_faces       = _build_cladding_faces(canonical_wall_faces,
                                                  canonical_openings, louvre_h_default)
    canonical_spaces     = _build_canonical_spaces(model)
    floor_zones          = _build_floor_zones(canonical_spaces)

    geom = CanonicalGeometryModel(
        openings       = canonical_openings,
        wall_faces     = canonical_wall_faces,
        cladding_faces = cladding_faces,
        spaces         = canonical_spaces,
        floor_zones    = floor_zones,
    )

    s = geom.summary_dict()
    log.info(
        "Canonical geometry built: openings=%d (entrance=%d partition=%d clad=%d) "
        "wall_faces=%d cladding_faces=%d spaces=%d (wet=%d ver=%d cfg_fallback=%d) "
        "floor_zones=%d",
        s["openings"], s["entrance_doors"], s["partition_doors"],
        s["cladding_face_openings"], s["wall_faces"], s["cladding_faces"],
        s["spaces"], s["wet_spaces"], s["verandah_spaces"],
        s["config_fallback_spaces"], s["floor_zones"],
    )

    return geom
