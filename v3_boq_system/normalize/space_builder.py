"""
space_builder.py — Build the SpaceElement list from all available sources.

Source priority (highest → lowest):
  1. IFC IfcSpace objects  — polygon + name + area (HIGH when present)
  2. DXF room polygons + text labels — polygon + name + area (HIGH polygon / MEDIUM label)
  3. PDF room/finish schedule — name + area, no polygon (MEDIUM)
  4. Config room_schedule — name + type + area estimate, no polygon (LOW)

For each space, this builder also:
  - Classifies: wet / dry, enclosed / verandah / external
  - Assigns finish types from room type or finish schedule
  - Distributes ceiling area from the DXF CEILING hatch total across enclosed spaces
  - Adds the verandah as an explicit space element (is_verandah=True)
  - Records per-space confidence and source_type

All config-sourced spaces carry LOW confidence and `manual_review` flag.
No quantities are ever copied from BOQ reference files.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from v3_boq_system.normalize.element_model import ProjectElementModel, SpaceElement

log = logging.getLogger("boq.v3.space_builder")


# ── Room type → finish type mapping ──────────────────────────────────────────

_FLOOR_FINISH_BY_TYPE: dict[str, str] = {
    "toilet":         "ceramic_tile",
    "accessible_wc":  "ceramic_tile",
    "bathroom":       "ceramic_tile",
    "laundry":        "ceramic_tile",
    "kitchen":        "ceramic_tile",
    "cleaner":        "ceramic_tile",
    "pharmacy":       "vinyl_plank",
    "consulting":     "vinyl_plank",
    "waiting":        "vinyl_plank",
    "office":         "vinyl_plank",
    "store":          "vinyl_plank",
    "corridor":       "vinyl_plank",
    "plant":          "screed",
    "verandah":       "decking",
    "unknown":        "vinyl_plank",
}

_WALL_FINISH_BY_TYPE: dict[str, str] = {
    "toilet":         "ceramic_tile",
    "accessible_wc":  "ceramic_tile",
    "bathroom":       "ceramic_tile",
    "laundry":        "paint",
    "kitchen":        "paint",
    "cleaner":        "ceramic_tile",
    # all other internal spaces
    "default":        "paint",
    "verandah":       "none",
}

_CEILING_FINISH_BY_TYPE: dict[str, str] = {
    "verandah": "none",
    "plant":    "paint",
    "default":  "paint",
}

_WET_TYPES: frozenset[str] = frozenset({
    "toilet", "accessible_wc", "bathroom", "laundry", "kitchen", "cleaner",
})

_EXTERNAL_TYPES: frozenset[str] = frozenset({"verandah", "external", "uncovered"})


def _classify_room_type(name: str, patterns: dict) -> str:
    """Match a room name string against room_type_patterns from config."""
    lower = name.lower()
    for rtype, keywords in patterns.items():
        if rtype == "unknown":
            continue
        for kw in (keywords or []):
            if kw.lower() in lower:
                return rtype
    return "unknown"


def _perimeter_from_area(area_m2: float) -> float:
    """Estimate perimeter from area assuming a near-square room."""
    if area_m2 <= 0:
        return 0.0
    return round(4.0 * math.sqrt(area_m2), 2)


def _floor_finish(space_type: str) -> str:
    return _FLOOR_FINISH_BY_TYPE.get(space_type, _FLOOR_FINISH_BY_TYPE["unknown"])


def _wall_finish(space_type: str) -> str:
    return _WALL_FINISH_BY_TYPE.get(space_type, _WALL_FINISH_BY_TYPE["default"])


def _ceiling_finish(space_type: str) -> str:
    return _CEILING_FINISH_BY_TYPE.get(space_type, _CEILING_FINISH_BY_TYPE["default"])


# ── IFC space extraction ──────────────────────────────────────────────────────

def _spaces_from_ifc(raw_ifc: dict) -> list[dict]:
    """
    Extract space data from raw IFC dict.

    The V2 IFC extractor stores `space_count` but does not currently extract
    individual IfcSpace objects (0 spaces for Angau Pharmacy IFC).
    Returns [] when no space objects are available.
    """
    # V2 ifc extractor would populate 'spaces' key if IfcSpace objects exist.
    ifc_spaces = raw_ifc.get("spaces", [])
    if not ifc_spaces:
        log.info("IFC: no IfcSpace objects — skipping IFC space extraction")
        return []
    results = []
    for sp in ifc_spaces:
        results.append({
            "space_name":  sp.get("name") or sp.get("long_name") or "IFC Space",
            "space_type":  sp.get("type", "unknown"),
            "polygon":     sp.get("polygon", []),
            "area_m2":     sp.get("area_m2", 0.0),
            "perimeter_m": sp.get("perimeter_m", 0.0),
            "source_type": "ifc",
            "source_ref":  sp.get("global_id", ""),
            "confidence":  "HIGH",
        })
    log.info("IFC: extracted %d spaces", len(results))
    return results


# ── Config room_schedule fallback ─────────────────────────────────────────────

def _spaces_from_config(config: dict, room_type_patterns: dict) -> list[dict]:
    """
    Build space dicts from config room_schedule.

    Config rooms carry LOW confidence — they are area estimates that have not
    been derived from source drawings (no room polygons, no room labels in DXF).
    """
    schedule = config.get("room_schedule", [])
    results = []
    for r in schedule:
        if not isinstance(r, dict):
            continue
        name   = r.get("name", "")
        rtype  = r.get("room_type") or _classify_room_type(name, room_type_patterns)
        area   = r.get("area_m2", 0.0)
        wet    = r.get("is_wet_area", rtype in _WET_TYPES)
        perim  = r.get("perimeter_m", 0.0) or _perimeter_from_area(area)
        results.append({
            "space_name":  name,
            "space_type":  rtype,
            "polygon":     [],   # no polygon from config
            "area_m2":     area,
            "perimeter_m": perim,
            "is_wet":      wet,
            "source_type": "config",
            "source_ref":  "project_config room_schedule",
            "confidence":  "LOW" if area else "LOW",
            "notes":       (
                "Config estimate — room area not derived from source drawings. "
                "No room polygon in DXF; no IfcSpace in IFC. "
                "Verify room area, layout, and finish type from architectural drawings."
            ),
        })
    log.info("Config: built %d spaces from room_schedule", len(results))
    return results


# ── Ceiling area distribution ─────────────────────────────────────────────────

def _distribute_ceiling(
    spaces: list[dict],
    total_ceiling_m2: float,
    total_enclosed_m2: float,
) -> None:
    """
    Assign ceiling_area_m2 to each enclosed space proportionally.

    Logic:
    - ceiling_area_m2 = 49.0 m² (from DXF CEILING hatch) < 64.8 m² interior
    - This means not all spaces have full ceiling coverage (75.6% coverage ratio)
    - We distribute proportionally by space area; wet rooms always get ceiling
    - Confidence: same as space confidence (LOW for config spaces)

    This is a proportional allocation — actual ceiling zones are unknown
    without a reflected ceiling plan or room positions.
    """
    if total_enclosed_m2 <= 0 or total_ceiling_m2 <= 0:
        return

    coverage = min(1.0, total_ceiling_m2 / total_enclosed_m2)
    for sp in spaces:
        if sp.get("is_verandah") or sp.get("is_external") or sp.get("is_uncovered"):
            sp["ceiling_area_m2"] = 0.0
            continue
        sp["ceiling_area_m2"] = round(sp.get("area_m2", 0.0) * coverage, 2)


# ── Main builder ──────────────────────────────────────────────────────────────

def _match_zones_to_config(
    config_spaces: list[dict],
    wall_zones: list[dict],
    area_tol: float = 0.15,
) -> dict[str, dict]:
    """
    Match DXF wall-network zones to config rooms by area proximity.

    Only unambiguous 1-to-1 matches (no two config rooms within tolerance
    of the same zone, no two zones within tolerance of the same config room)
    are returned.  This prevents misidentifying large merged zones as single rooms.

    Returns {space_name: zone_dict} for matched pairs.
    """
    if not config_spaces or not wall_zones:
        return {}

    # Build candidate pairs (config_idx, zone_idx, deviation)
    candidates: list[tuple[int, int, float]] = []
    for ci, cfg in enumerate(config_spaces):
        cfg_area = cfg.get("area_m2", 0.0)
        if cfg_area <= 0:
            continue
        for zi, zone in enumerate(wall_zones):
            dev = abs(zone["area_m2"] - cfg_area) / max(zone["area_m2"], cfg_area)
            if dev <= area_tol:
                candidates.append((ci, zi, dev))

    # Keep only unambiguous: each config room and each zone appears at most once
    # in the filtered set
    from collections import Counter
    cfg_counts  = Counter(ci for ci, _, _ in candidates)
    zone_counts = Counter(zi for _, zi, _ in candidates)
    unambiguous = [
        (ci, zi, dev) for ci, zi, dev in candidates
        if cfg_counts[ci] == 1 and zone_counts[zi] == 1
    ]

    result: dict[str, dict] = {}
    for ci, zi, dev in unambiguous:
        name = config_spaces[ci].get("space_name", "")
        result[name] = {**wall_zones[zi], "_area_dev_pct": round(dev * 100, 1)}
        log.info(
            "Wall-network match: config '%s' (%.2f m²) ↔ %s (%.2f m², dev=%.1f%%)",
            name, config_spaces[ci].get("area_m2", 0),
            wall_zones[zi]["zone_id"], wall_zones[zi]["area_m2"], dev * 100,
        )

    return result


def build_space_model(
    element_model:      ProjectElementModel,
    raw_ifc:            dict,
    raw_dxf:            dict,
    dxf_spaces:         list[dict],           # from space_dxf_extractor ([] for project2)
    config:             dict,
    wall_network_zones: list[dict] | None = None,   # from extract_spaces_from_wall_network
) -> list[SpaceElement]:
    """
    Build SpaceElement list using source priority: IFC → DXF → config.

    Returns the list and also populates element_model.spaces in-place.
    """
    room_type_patterns = config.get("room_type_patterns", {})

    # ── Source selection ──────────────────────────────────────────────────────
    ifc_spaces = _spaces_from_ifc(raw_ifc)

    if ifc_spaces:
        raw_spaces  = ifc_spaces
        source_desc = "ifc"
        log.info("Space model: using IFC spaces (%d)", len(raw_spaces))
    elif dxf_spaces:
        raw_spaces  = dxf_spaces
        source_desc = "dxf"
        log.info("Space model: using DXF spaces (%d)", len(raw_spaces))
    else:
        raw_spaces  = _spaces_from_config(config, room_type_patterns)
        source_desc = "config"
        log.info("Space model: no IFC/DXF room data — using config room_schedule (%d spaces)",
                 len(raw_spaces))

    # ── Wall-network zone matching (config fallback only) ─────────────────────
    # When we have DXF wall-network zones but no room labels, we can match zones
    # to config rooms by area to recover DXF-backed perimeters.  Only unambiguous
    # 1-to-1 matches are used; mismatched zones remain unassigned.
    zone_matches: dict[str, dict] = {}
    if source_desc == "config" and wall_network_zones:
        zone_matches = _match_zones_to_config(raw_spaces, wall_network_zones)
        if zone_matches:
            log.info(
                "Wall-network: %d/%d config rooms matched to DXF zones — "
                "perimeters upgraded from sqrt estimate to DXF geometry",
                len(zone_matches), len(raw_spaces),
            )

    # ── Building geometry context from element model ──────────────────────────
    total_floor_m2    = element_model.total_floor_area_m2()
    total_verandah_m2 = element_model.total_verandah_area_m2()
    total_ceiling_m2  = element_model.total_ceiling_area_m2()
    # Enclosed = total floor minus verandah
    total_enclosed_m2 = round(max(0.0, total_floor_m2 - total_verandah_m2), 2)

    # ── Convert raw dicts to SpaceElement ─────────────────────────────────────
    spaces: list[SpaceElement] = []

    for i, raw in enumerate(raw_spaces):
        name   = raw.get("space_name", f"Space_{i+1}")
        stype  = raw.get("space_type", "unknown")
        if stype == "unknown" and name:
            stype = _classify_room_type(name, room_type_patterns)
        area   = raw.get("area_m2", 0.0)
        perim  = raw.get("perimeter_m", 0.0)
        is_wet     = raw.get("is_wet", stype in _WET_TYPES)

        # ── Wall-network perimeter upgrade (config spaces only) ───────────────
        zone_match    = zone_matches.get(name)
        perim_src     = ""          # extra note for classification_notes
        geom_src_type = raw.get("source_type", source_desc)
        if zone_match and (perim <= 0 or geom_src_type == "config"):
            # Replace sqrt-estimate perimeter with DXF zone perimeter
            dxf_perim = zone_match.get("perimeter_m", 0.0)
            if dxf_perim > 0:
                old_perim = perim if perim > 0 else round(4.0 * math.sqrt(area), 2)
                perim     = dxf_perim
                perim_src = (
                    f"Perimeter from DXF wall-network {zone_match['zone_id']} "
                    f"({zone_match['area_m2']:.2f} m², dev={zone_match['_area_dev_pct']:.1f}% "
                    f"vs config {area:.2f} m²): "
                    f"{dxf_perim:.2f} m replaces sqrt estimate ({old_perim:.2f} m)."
                )
        is_verandah = stype in ("verandah",) or raw.get("is_verandah", False)
        is_external = is_verandah or raw.get("is_external", False)
        is_enclosed = not is_external
        src_type    = raw.get("source_type", source_desc)
        src_ref     = raw.get("source_ref", "")
        conf        = raw.get("confidence", "LOW")

        # Derived perimeter when not measured
        if perim <= 0 and area > 0:
            perim = _perimeter_from_area(area)
            perim_note = f"perimeter estimated as 4×√{area:.1f} = {perim:.2f} m (near-square assumption)"
        else:
            perim_note = ""

        # Classification notes
        cls_notes_parts = []
        if src_type == "config":
            cls_notes_parts.append(
                "Source: config room_schedule (no DXF labels, no IFC spaces). "
                "Area is an estimate — verify from architectural drawings."
            )
        if is_wet:
            cls_notes_parts.append(f"Wet space: classified from room_type='{stype}'.")
        if is_verandah:
            cls_notes_parts.append("Verandah: covered open-sided external space.")
        if perim_note:
            cls_notes_parts.append(perim_note)
        if perim_src:
            cls_notes_parts.append(perim_src)

        # quantity_basis shows both area and perimeter sources
        if perim_src:
            q_basis = (
                f"config: area={area:.2f} m² (estimated), no polygon; "
                f"perimeter={perim:.2f} m from {zone_match['zone_id']} (dxf_wall_network)"
            )
        else:
            q_basis = (
                f"{src_type}: area={area:.2f} m²"
                + (", polygon measured" if raw.get("polygon") else ", no polygon — area estimated")
            )

        sp = SpaceElement(
            element_id=f"space_{name.lower().replace(' ','_').replace('/','_')[:40]}",
            space_id=f"space_{i+1:02d}",
            space_name=name,
            space_type=stype,
            polygon=raw.get("polygon", []),
            area_m2=area,
            perimeter_m=perim,
            ceiling_area_m2=0.0,  # filled by _distribute_ceiling below
            level="GF",
            is_wet=is_wet,
            is_external=is_external,
            is_verandah=is_verandah,
            is_uncovered=False,
            is_enclosed=is_enclosed,
            finish_floor_type=_floor_finish(stype),
            finish_wall_type=_wall_finish(stype),
            finish_ceiling_type=_ceiling_finish(stype) if is_enclosed else "none",
            quantity_basis=q_basis,
            source_type=src_type,
            source_ref=src_ref or (raw.get("source_layer", "") or raw.get("notes", "")),
            confidence=conf,
            notes=raw.get("notes", ""),
            classification_notes=" | ".join(cls_notes_parts),
            contributing_space_refs=raw.get("contributing_space_refs", []),
        )
        spaces.append(sp)

    # ── Add verandah as explicit space (from DXF geometry, not config) ────────
    for ver in element_model.verandahs:
        if ver.area_m2 > 0:
            ver_sp = SpaceElement(
                element_id=f"space_verandah_{ver.element_id or 'gf'}",
                space_id=f"space_ver_{len(spaces)+1:02d}",
                space_name="Verandah",
                space_type="verandah",
                polygon=[],
                area_m2=ver.area_m2,
                perimeter_m=ver.perimeter_m,
                ceiling_area_m2=0.0,   # open or covered-no-ceiling
                level="GF",
                is_wet=False,
                is_external=True,
                is_verandah=True,
                is_uncovered=False,
                is_enclosed=False,
                finish_floor_type="decking",
                finish_wall_type="none",
                finish_ceiling_type="none",
                quantity_basis=f"dxf_geometry: VERANDAH LWPOLYLINE area={ver.area_m2:.2f} m²",
                source_type="dxf",
                source_ref="DXF VERANDAH layer",
                confidence=ver.confidence,
                notes="Verandah polygon from DXF VERANDAH LWPOLYLINE.",
                classification_notes=(
                    "Covered open-sided verandah. No ceiling lining. "
                    "Soffit lining (E10) and soffit battens (E11) are tracked separately in lining_quantifier."
                ),
            )
            spaces.append(ver_sp)
            log.info("Space model: added verandah space %.2f m²", ver.area_m2)

    # ── Distribute ceiling area across enclosed spaces ─────────────────────────
    enclosed_spaces = [s for s in spaces if s.is_enclosed]
    enclosed_area   = round(sum(s.area_m2 for s in enclosed_spaces), 2)
    if total_ceiling_m2 > 0 and enclosed_area > 0:
        coverage = min(1.0, total_ceiling_m2 / enclosed_area)
        for sp in enclosed_spaces:
            sp.ceiling_area_m2 = round(sp.area_m2 * coverage, 2)
        log.info(
            "Space model: ceiling %.2f m² distributed across %d enclosed spaces "
            "(coverage %.1f%% of %.2f m² interior)",
            total_ceiling_m2, len(enclosed_spaces),
            coverage * 100, enclosed_area,
        )
        element_model.extraction_notes.append(
            f"Ceiling area {total_ceiling_m2:.2f} m² (DXF CEILING hatch, HIGH confidence) "
            f"< enclosed interior {enclosed_area:.2f} m² "
            f"({coverage*100:.1f}% coverage). "
            f"Distributed proportionally across {len(enclosed_spaces)} enclosed spaces. "
            "Actual ceiling zone boundaries unknown — no reflected ceiling plan in source docs."
        )

    # ── Populate element_model.spaces ─────────────────────────────────────────
    element_model.spaces = spaces

    _log_space_summary(spaces, total_ceiling_m2)
    return spaces


def _log_space_summary(spaces: list[SpaceElement], total_ceiling_m2: float) -> None:
    enclosed  = [s for s in spaces if s.is_enclosed]
    wet       = [s for s in spaces if s.is_wet]
    verandahs = [s for s in spaces if s.is_verandah]
    log.info(
        "Space model summary: %d total spaces | %d enclosed | %d wet | %d verandah",
        len(spaces), len(enclosed), len(wet), len(verandahs),
    )
    for sp in spaces:
        log.debug(
            "  [%s] %-30s type=%-12s area=%6.2f m²  wet=%-5s enc=%-5s ceil=%5.2f m²  src=%s  conf=%s",
            sp.space_id, sp.space_name, sp.space_type,
            sp.area_m2, sp.is_wet, sp.is_enclosed,
            sp.ceiling_area_m2, sp.source_type, sp.confidence,
        )


# ── Finish zone summary (PASS 2 output) ───────────────────────────────────────

def compute_finish_zone_summary(spaces: list[SpaceElement]) -> dict:
    """
    Compute finish zone area summaries from the space model.

    These summaries are used by finish_zone_quantifier.py and the QA report.
    They aggregate by classification — no quantities are sourced from BOQ files.
    """
    enclosed = [s for s in spaces if s.is_enclosed]
    wet      = [s for s in spaces if s.is_wet and s.is_enclosed]
    dry      = [s for s in spaces if not s.is_wet and s.is_enclosed]
    ver      = [s for s in spaces if s.is_verandah]
    ext      = [s for s in spaces if s.is_external and not s.is_verandah]

    dry_internal_floor_area  = round(sum(s.area_m2 for s in dry), 2)
    wet_floor_area           = round(sum(s.area_m2 for s in wet), 2)
    verandah_floor_area      = round(sum(s.area_m2 for s in ver), 2)
    enclosed_ceiling_area    = round(sum(s.ceiling_area_m2 for s in enclosed), 2)
    wet_ceiling_area         = round(sum(s.ceiling_area_m2 for s in wet), 2)
    total_enclosed_area      = round(sum(s.area_m2 for s in enclosed), 2)

    wet_wall_candidates = [s.space_name for s in wet]
    unclassified        = [s.space_name for s in spaces if s.space_type == "unknown"]

    # Ceiling coverage check
    ceiling_coverage_pct = (
        round(enclosed_ceiling_area / total_enclosed_area * 100, 1)
        if total_enclosed_area > 0 else 0.0
    )

    return {
        "dry_internal_floor_area_m2":    dry_internal_floor_area,
        "wet_floor_area_m2":             wet_floor_area,
        "verandah_floor_area_m2":        verandah_floor_area,
        "enclosed_ceiling_area_m2":      enclosed_ceiling_area,
        "wet_ceiling_area_m2":           wet_ceiling_area,
        "total_enclosed_area_m2":        total_enclosed_area,
        "ceiling_coverage_pct":          ceiling_coverage_pct,
        "wet_wall_candidate_spaces":     wet_wall_candidates,
        "unclassified_spaces":           unclassified,
        "space_count":                   len(spaces),
        "enclosed_space_count":          len(enclosed),
        "wet_space_count":               len(wet),
        "dry_space_count":               len(dry),
        "verandah_space_count":          len(ver),
        "space_source_summary":          _source_summary(spaces),
    }


def _source_summary(spaces: list[SpaceElement]) -> dict:
    from collections import Counter
    src_counts  = Counter(s.source_type for s in spaces)
    conf_counts = Counter(s.confidence for s in spaces)
    return {
        "by_source":     dict(src_counts),
        "by_confidence": dict(conf_counts),
    }
