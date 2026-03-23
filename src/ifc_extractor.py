"""
ifc_extractor.py — Extract structural and geometry data from IFC files.

Populates the `structural` and `geometry` dicts consumed by quantity_calculator.py.

Struct keys produced (fed directly into _resolve_* functions):
    wall_frame_lm       total LGS wall frame linear metres
    wall_frame_source   "ifc"
    roof_truss_qty      total truss linear metres (LGS meter-rate)
    roof_truss_source   "ifc"
    ceiling_batten_lm   ceiling batten linear metres
    roof_batten_lm      roof batten linear metres
    verandah_batten_lm  verandah/soffit batten linear metres
    floor_panel_qty     floor panel count (standard panels)
    floor_panel_source  "ifc"
    floor_panel_count   (alias for floor_panel_qty)
    floor_joist_lm      floor joist linear metres
    bracing_lm          diagonal bracing linear metres
    post_qty            steel post count

Geometry keys produced (supplement DWG extraction):
    total_floor_area_m2
    building_length_m
    building_width_m
    external_wall_length_m
    internal_wall_length_m
    roof_area_m2
    storey_count
    rooms               list of {name, area_m2, level}
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

log = logging.getLogger("boq.ifc_extractor")


# ─── Public entry point ───────────────────────────────────────────────────────

def extract_ifc(ifc_path: str | Path) -> dict:
    """
    Open an IFC file and return a dict with keys:
        structural: dict    — consumed by quantity_calculator struct arg
        geometry:   dict    — supplements DWG geometry extraction
        warnings:   list    — non-fatal issues encountered
        source:     "ifc"
    Returns empty structural/geometry dicts (with warnings) if extraction fails.
    """
    result = {
        "structural": {},
        "geometry":   {},
        "warnings":   [],
        "source":     "ifc",
    }

    try:
        import ifcopenshell
        import ifcopenshell.util.element as ifc_util
        import ifcopenshell.util.placement as ifc_place
    except ImportError:
        msg = "ifcopenshell not installed — IFC extraction skipped. Run: pip install ifcopenshell"
        log.warning(msg)
        result["warnings"].append(msg)
        return result

    ifc_path = Path(ifc_path)
    if not ifc_path.exists():
        msg = f"IFC file not found: {ifc_path}"
        log.warning(msg)
        result["warnings"].append(msg)
        return result

    try:
        ifc = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        msg = f"Failed to open IFC file {ifc_path.name}: {exc}"
        log.warning(msg)
        result["warnings"].append(msg)
        return result

    log.info("IFC opened: %s  schema=%s", ifc_path.name, ifc.schema)

    struct   = {}
    geo      = {}
    warnings = []

    # ── 1. Storeys + rooms ────────────────────────────────────────────────────
    _extract_spaces(ifc, geo, warnings)

    # ── 2. Walls → wall_frame_lm ──────────────────────────────────────────────
    _extract_walls(ifc, struct, geo, warnings)

    # ── 3. Slabs / floors ─────────────────────────────────────────────────────
    _extract_slabs(ifc, struct, geo, warnings)

    # ── 4. Roof elements (members / beams labeled as trusses/battens) ─────────
    _extract_roof(ifc, struct, geo, warnings)

    # ── 5. Columns / posts ────────────────────────────────────────────────────
    _extract_columns(ifc, struct, warnings)

    # ── 6. Members (beams, purlins, battens, joists, bracing) ─────────────────
    _extract_members(ifc, struct, warnings)

    # ── 7. Building footprint from IfcBuilding / IfcSite geometry ─────────────
    _extract_building_dims(ifc, geo, warnings)

    # ── 8. Derive any missing geo fields ──────────────────────────────────────
    _derive_geo(geo)

    # ── 9. Log summary ────────────────────────────────────────────────────────
    log.info(
        "IFC extract: wall_frame=%.1flm  trusses=%.1f  ceiling_bat=%.1f  "
        "roof_bat=%.1f  floor_panels=%s  posts=%s  rooms=%d",
        struct.get("wall_frame_lm", 0),
        struct.get("roof_truss_qty", 0),
        struct.get("ceiling_batten_lm", 0),
        struct.get("roof_batten_lm", 0),
        struct.get("floor_panel_qty", "—"),
        struct.get("post_qty", "—"),
        len(geo.get("rooms", [])),
    )

    result["structural"] = struct
    result["geometry"]   = geo
    result["warnings"]   = warnings
    return result


# ─── Extraction helpers ───────────────────────────────────────────────────────

def _extract_spaces(ifc, geo: dict, warnings: list) -> None:
    """Extract IfcSpace → rooms list + storey info."""
    rooms = []
    storeys = set()

    for space in ifc.by_type("IfcSpace"):
        name  = _get_name(space)
        area  = _get_property(space, "GrossFloorArea") or \
                _get_property(space, "NetFloorArea") or \
                _get_quantity(space, "GrossFloorArea") or \
                _get_quantity(space, "NetFloorArea") or 0.0
        level = _get_storey_name(space)
        if level:
            storeys.add(level)
        rooms.append({
            "name":    name,
            "area_m2": round(float(area), 3) if area else 0.0,
            "level":   level or "Ground Floor",
            "source":  "ifc",
        })

    if rooms:
        geo["rooms"] = rooms
        total_area   = sum(r["area_m2"] for r in rooms if r["area_m2"] > 0)
        if total_area > 0 and not geo.get("total_floor_area_m2"):
            geo["total_floor_area_m2"] = round(total_area, 2)
            geo["total_floor_area_source"] = "ifc_spaces"
        log.debug("IFC spaces: %d rooms  total_area=%.1fm²", len(rooms), total_area)
    else:
        warnings.append("No IfcSpace elements found — room schedule will be blank")

    if storeys:
        geo["storey_count"] = len(storeys)
        geo["storeys"]      = sorted(storeys)


def _extract_walls(ifc, struct: dict, geo: dict, warnings: list) -> None:
    """Extract IfcWall / IfcWallStandardCase → wall_frame_lm."""
    ext_lm = 0.0
    int_lm = 0.0

    for wall in ifc.by_type("IfcWall") + ifc.by_type("IfcWallStandardCase"):
        length = (
            _get_quantity(wall, "Length") or
            _get_quantity(wall, "GrossLength") or
            _get_property(wall, "Length") or 0.0
        )
        length = float(length)
        if length <= 0:
            continue

        is_ext = _is_external(wall)
        if is_ext:
            ext_lm += length
        else:
            int_lm += length

    total_lm = round(ext_lm + int_lm, 2)
    if total_lm > 0:
        struct["wall_frame_lm"]     = total_lm
        struct["wall_frame_source"] = "ifc"
        if ext_lm > 0 and not geo.get("external_wall_length_m"):
            geo["external_wall_length_m"] = round(ext_lm, 2)
        if int_lm > 0 and not geo.get("internal_wall_length_m"):
            geo["internal_wall_length_m"] = round(int_lm, 2)
        log.debug("IFC walls: ext=%.1flm  int=%.1flm  total=%.1flm", ext_lm, int_lm, total_lm)
    else:
        warnings.append("No wall lengths extracted from IFC — wall_frame_lm will use DWG fallback")


def _extract_slabs(ifc, struct: dict, geo: dict, warnings: list) -> None:
    """Extract IfcSlab → floor area and panel estimates."""
    floor_area = 0.0
    roof_area  = 0.0

    for slab in ifc.by_type("IfcSlab"):
        predefined = (getattr(slab, "PredefinedType", "") or "").upper()
        area = (
            _get_quantity(slab, "GrossArea") or
            _get_quantity(slab, "NetArea") or
            _get_property(slab, "GrossArea") or 0.0
        )
        area = float(area)
        if area <= 0:
            continue
        if predefined in ("ROOF", "LANDING"):
            roof_area += area
        else:
            floor_area += area

    if floor_area > 0 and not geo.get("total_floor_area_m2"):
        geo["total_floor_area_m2"] = round(floor_area, 2)
        geo["total_floor_area_source"] = "ifc_slab"
    if roof_area > 0 and not geo.get("roof_area_m2"):
        geo["roof_area_m2"] = round(roof_area, 2)

    log.debug("IFC slabs: floor=%.1fm²  roof=%.1fm²", floor_area, roof_area)


def _extract_roof(ifc, struct: dict, geo: dict, warnings: list) -> None:
    """Extract IfcRoof → roof area."""
    for roof in ifc.by_type("IfcRoof"):
        area = (
            _get_quantity(roof, "GrossArea") or
            _get_quantity(roof, "NetArea") or
            _get_property(roof, "GrossArea") or 0.0
        )
        area = float(area)
        if area > 0 and not geo.get("roof_area_m2"):
            geo["roof_area_m2"] = round(area, 2)
            log.debug("IFC roof area: %.1fm²", area)
            break


def _extract_columns(ifc, struct: dict, warnings: list) -> None:
    """Extract IfcColumn → post_qty."""
    columns = ifc.by_type("IfcColumn")
    if columns:
        struct["post_qty"]    = len(columns)
        struct["post_source"] = "ifc"
        log.debug("IFC columns/posts: %d", len(columns))
    else:
        warnings.append("No IfcColumn elements — post_qty will use DWG fallback")


def _extract_members(ifc, struct: dict, warnings: list) -> None:
    """
    Extract IfcMember / IfcBeam → categorise by name/description into:
        roof_truss_qty      (LGS truss members, lm total)
        ceiling_batten_lm
        roof_batten_lm
        verandah_batten_lm
        floor_joist_lm
        bracing_lm

    Supports Framecad naming conventions:
    - Member names like 89S41-075-500 (pattern: digits+letter+digits-digits-digits)
    - Description prefix: W/B = wall_frame, T = wall_frame (top track), R = roof_truss
    - Lengths in IfcElementQuantity "Member Length" (mm → convert to metres)
    """
    import re as _re

    # Framecad section-code pattern: e.g. 89S41-075-500, 75C41-075-600
    _FRAMECAD_PATTERN = _re.compile(r'^\d+[A-Z]\d+-\d+-\d+$', _re.IGNORECASE)

    truss_lm         = 0.0
    ceil_batten_lm   = 0.0
    roof_batten_lm   = 0.0
    ver_batten_lm    = 0.0
    floor_joist_lm   = 0.0
    bracing_lm       = 0.0
    wall_frame_lm    = 0.0   # Framecad wall members accumulated separately
    unclassified     = 0

    member_types = ifc.by_type("IfcMember") + ifc.by_type("IfcBeam")

    for member in member_types:
        raw_name = (_get_name(member) or "")
        desc_raw = (getattr(member, "Description", "") or "")
        name     = raw_name.lower()
        desc     = desc_raw.lower()
        label    = name + " " + desc

        # ── Get length — try "Member Length" first (Framecad), then standard names ──
        length = (
            _get_quantity(member, "Member Length") or
            _get_quantity(member, "Length") or
            _get_quantity(member, "GrossLength") or
            _get_property(member, "Length") or 0.0
        )
        length = float(length)
        if length <= 0:
            continue

        # ── Framecad detection ─────────────────────────────────────────────────
        is_framecad = bool(_FRAMECAD_PATTERN.match(raw_name.strip()))

        if is_framecad:
            # Convert mm → metres for Framecad member lengths
            length_m = length / 1000.0

            # Classify by Description prefix
            desc_prefix = desc_raw.strip()[:1].upper() if desc_raw.strip() else ""
            if desc_prefix in ("W", "B", "T"):
                # W = wall stud, B = bottom plate, T = top track → wall_frame
                wall_frame_lm += length_m
            elif desc_prefix == "R":
                # R = rafter → roof_truss
                truss_lm += length_m
            else:
                # No prefix or unknown → classify by section code suffix
                # S-section = stud (wall), C-section = ceiling/purlin
                code_letter = raw_name[len(raw_name.split("-")[0]) - 1].upper() if raw_name else ""
                if code_letter == "S":
                    wall_frame_lm += length_m
                elif code_letter in ("C", "Z"):
                    ceil_batten_lm += length_m
                else:
                    unclassified += 1
            continue

        # ── Standard keyword classification (already in metres) ────────────────
        if any(k in label for k in ["truss", "rafter", "c89", "c150 truss", "lgs truss"]):
            truss_lm += length
        elif any(k in label for k in ["ceiling batten", "ceil batten", "clg batten"]):
            ceil_batten_lm += length
        elif any(k in label for k in ["verandah batten", "soffit batten", "ver batten"]):
            ver_batten_lm += length
        elif any(k in label for k in ["roof batten", "top hat", "purlin", "batten"]):
            roof_batten_lm += length
        elif any(k in label for k in ["joist", "floor joist", "bearer", "floor bearer"]):
            floor_joist_lm += length
        elif any(k in label for k in ["brace", "bracing", "diagonal", "strap"]):
            bracing_lm += length
        elif any(k in label for k in ["wall", "stud", "track", "plate"]):
            wall_frame_lm += length
        else:
            unclassified += 1

    # Write to struct — only where IFC had data
    # Framecad wall_frame_lm supplements or replaces the wall extraction
    if wall_frame_lm > 0:
        existing_wf = struct.get("wall_frame_lm", 0.0) or 0.0
        struct["wall_frame_lm"]     = round(existing_wf + wall_frame_lm, 2)
        struct["wall_frame_source"] = "ifc_framecad"
    if truss_lm > 0:
        struct["roof_truss_qty"]    = round(truss_lm, 3)
        struct["roof_truss_source"] = "ifc"
    if ceil_batten_lm > 0:
        struct["ceiling_batten_lm"] = round(ceil_batten_lm, 2)
    if roof_batten_lm > 0:
        struct["roof_batten_lm"]    = round(roof_batten_lm, 2)
    if ver_batten_lm > 0:
        struct["verandah_batten_lm"] = round(ver_batten_lm, 2)
    if floor_joist_lm > 0:
        struct["floor_joist_lm"]    = round(floor_joist_lm, 2)
    if bracing_lm > 0:
        struct["bracing_lm"]        = round(bracing_lm, 2)

    if unclassified > 0:
        warnings.append(
            f"{unclassified} IFC members could not be classified "
            f"(no matching keyword in name/description) — check member names in model"
        )

    log.debug(
        "IFC members: wall_frame=%.1f  truss=%.1f  ceil_bat=%.1f  roof_bat=%.1f  "
        "ver_bat=%.1f  joist=%.1f  brace=%.1f  unclassified=%d",
        wall_frame_lm, truss_lm, ceil_batten_lm, roof_batten_lm,
        ver_batten_lm, floor_joist_lm, bracing_lm, unclassified,
    )


def _extract_building_dims(ifc, geo: dict, warnings: list) -> None:
    """Try to read building footprint from IfcBuilding or IfcBuildingStorey properties."""
    for building in ifc.by_type("IfcBuilding"):
        length = _get_property(building, "BuildingLength") or \
                 _get_property(building, "Length") or \
                 _get_property(building, "FootprintLength")
        width  = _get_property(building, "BuildingWidth") or \
                 _get_property(building, "Width") or \
                 _get_property(building, "FootprintWidth")

        if length and not geo.get("building_length_m"):
            geo["building_length_m"] = round(float(length), 2)
        if width and not geo.get("building_width_m"):
            geo["building_width_m"]  = round(float(width), 2)

    # Fallback: infer length/width from floor area + aspect ratio if available
    if (not geo.get("building_length_m") and
            geo.get("total_floor_area_m2") and
            geo.get("external_wall_length_m")):
        # perimeter = 2(L+W), area = L×W → quadratic
        P = safe_float(geo["external_wall_length_m"])
        A = safe_float(geo["total_floor_area_m2"])
        # L + W = P/2;  L × W = A  →  L² − (P/2)L + A = 0
        half_p = P / 2.0
        disc   = half_p ** 2 - 4 * A
        if disc >= 0:
            L = round((half_p + math.sqrt(disc)) / 2.0, 2)
            W = round(half_p - L, 2)
            if L > 0 and W > 0:
                geo["building_length_m"] = L
                geo["building_width_m"]  = W
                geo["building_dims_source"] = "derived_from_perimeter_area"


def _derive_geo(geo: dict) -> None:
    """Fill in any geometry fields derivable from what we have."""
    # total_floor_area from rooms if not set
    if not geo.get("total_floor_area_m2") and geo.get("rooms"):
        area = sum(r.get("area_m2", 0) for r in geo["rooms"])
        if area > 0:
            geo["total_floor_area_m2"] = round(area, 2)

    # ceiling_area ≈ floor_area
    if not geo.get("ceiling_area_m2") and geo.get("total_floor_area_m2"):
        geo["ceiling_area_m2"] = geo["total_floor_area_m2"]


# ─── IFC property/quantity helpers ───────────────────────────────────────────

def _get_name(element) -> str:
    return str(getattr(element, "Name", "") or getattr(element, "LongName", "") or "").strip()


def _get_storey_name(element) -> str | None:
    """Walk up the decomposition tree to find the containing storey."""
    try:
        for rel in element.ContainedInStructure or []:
            structure = rel.RelatingStructure
            ifc_type  = structure.is_a()
            if "Storey" in ifc_type or "Building" in ifc_type:
                return _get_name(structure) or structure.is_a()
    except Exception:
        pass
    return None


def _is_external(wall) -> bool:
    """Check IsExternal property — default False if not found."""
    val = _get_property(wall, "IsExternal")
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "external")


def _get_property(element, prop_name: str) -> Any:
    """Search all IfcPropertySet on element for a named property."""
    try:
        for definition in element.IsDefinedBy or []:
            if not definition.is_a("IfcRelDefinesByProperties"):
                continue
            pset = definition.RelatingPropertyDefinition
            if not pset.is_a("IfcPropertySet"):
                continue
            for prop in pset.HasProperties or []:
                if prop.Name == prop_name:
                    nom = getattr(prop, "NominalValue", None)
                    if nom is not None:
                        return getattr(nom, "wrappedValue", nom)
    except Exception:
        pass
    return None


def _get_quantity(element, qty_name: str) -> float | None:
    """Search IfcElementQuantity on element for a named quantity."""
    try:
        for definition in element.IsDefinedBy or []:
            if not definition.is_a("IfcRelDefinesByProperties"):
                continue
            qset = definition.RelatingPropertyDefinition
            if not qset.is_a("IfcElementQuantity"):
                continue
            for qty in qset.Quantities or []:
                if qty.Name == qty_name:
                    # IfcQuantityLength / Area / Volume / Count
                    for attr in ("LengthValue", "AreaValue", "VolumeValue", "CountValue"):
                        val = getattr(qty, attr, None)
                        if val is not None:
                            return float(val)
    except Exception:
        pass
    return None


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
