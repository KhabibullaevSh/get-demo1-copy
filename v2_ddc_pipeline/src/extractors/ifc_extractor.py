"""
ifc_extractor.py — Extract structural quantities from FrameCAD IFC models.

The FrameCAD IFC has IfcElementQuantity records with "Member Length" (mm) for
every structural element.  Member type is encoded in the element Description
field:  e.g. "G1-W5" → mark_group="G1", member_type_char="W".

Returns all linear-metre quantities by member category.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("boq.v2.ifc_extractor")

MM_TO_M = 1 / 1_000

# Member type classification by description suffix character
MEMBER_TYPE_MAP: dict[str, str] = {
    "W": "wall_stud",
    "P": "top_plate",
    "N": "noggin",
    "R": "rafter",
    "T": "tie_strap",
    "L": "lintel",
    "B": "bottom_plate",
    "C": "corner_stud",
    "J": "joist",
    "G": "girt",
    "S": "stud",
    "X": "unclassified",
}


def _parse_description(desc: str) -> tuple[str, str]:
    """
    Parse element description like 'G1-W5' → (mark_group='G1', member_char='W').
    Returns ('?', '?') if unparseable.
    """
    if not desc:
        return "?", "?"
    parts = desc.split("-")
    mark_group  = parts[0].strip() if parts else "?"
    member_char = "?"
    if len(parts) >= 2 and parts[-1]:
        member_char = parts[-1][0].upper()
    return mark_group, member_char


def _category_from_ifc_type_and_char(ifc_type: str, member_char: str, mark_group: str) -> str:
    """
    Map (ifc_type, member_char, mark_group) → aggregated structural category.
    Mark groups starting with V → verandah frame.
    """
    is_verandah = mark_group.upper().startswith("V")

    if ifc_type == "IfcColumn":
        if member_char in ("W", "B", "C", "S"):
            return "verandah_frame" if is_verandah else "wall_frame_stud"
        if member_char == "P":
            return "verandah_frame" if is_verandah else "wall_plate"
        if member_char == "N":
            return "verandah_frame" if is_verandah else "wall_noggin"
        if member_char == "G":
            return "girt"
        return "verandah_frame" if is_verandah else "wall_other"

    if ifc_type == "IfcBeam":
        if member_char in ("R",):
            return "roof_rafter"
        if member_char == "N":
            return "roof_noggin"
        if member_char == "P":
            return "roof_plate"
        if member_char == "L":
            return "roof_lintel"
        if member_char == "J":
            return "floor_joist"
        if member_char == "G":
            return "girt"
        if is_verandah:
            return "verandah_frame"
        return "roof_other"

    return "unclassified"


def extract_ifc(ifc_path: Path) -> dict:
    """
    Extract member quantities from *ifc_path*.

    Returns structured dict with linear-metre totals by category.
    On failure returns minimal dict with warning.
    """
    warnings: list[str] = []
    result: dict = {
        "wall_frame_stud_lm": 0.0,
        "wall_plate_lm":      0.0,
        "wall_noggin_lm":     0.0,
        "wall_other_lm":      0.0,
        "girt_lm":            0.0,
        "roof_rafter_lm":     0.0,
        "roof_plate_lm":      0.0,
        "roof_noggin_lm":     0.0,
        "roof_lintel_lm":     0.0,
        "roof_other_lm":      0.0,
        "floor_joist_lm":     0.0,
        "verandah_frame_lm":  0.0,
        "unclassified_lm":    0.0,
        "total_column_lm":    0.0,
        "total_beam_lm":      0.0,
        "column_count":       0,
        "beam_count":         0,
        "door_count":         0,
        "window_count":       0,
        "space_count":        0,
        "storey_count":       0,
        "member_breakdown":   {},
        "source":             "ifc_model",
        "source_file":        str(ifc_path),
        "schema":             "unknown",
        "warnings":           warnings,
    }

    try:
        import ifcopenshell
    except ImportError:
        warnings.append("ifcopenshell not installed — IFC extraction skipped")
        log.error("ifcopenshell not installed")
        return result

    try:
        ifc = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        warnings.append(f"Failed to open IFC: {exc}")
        log.error("Failed to open IFC %s: %s", ifc_path, exc)
        return result

    result["schema"] = ifc.schema

    # Category accumulator: category → lm
    cat_lm: dict[str, float]   = defaultdict(float)
    # Type accumulators
    col_lm = 0.0
    beam_lm = 0.0
    col_count = 0
    beam_count = 0

    # Per mark-group breakdown: mark_group → {count, lm}
    breakdown: dict[str, dict] = defaultdict(lambda: {"count": 0, "lm": 0.0})

    # Iterate IfcRelDefinesByProperties to find IfcElementQuantity
    total_qty_sets = 0
    total_lengths  = 0
    for rel in ifc.by_type("IfcRelDefinesByProperties"):
        pdef = rel.RelatingPropertyDefinition
        if not pdef.is_a("IfcElementQuantity"):
            continue
        total_qty_sets += 1

        length_val: float | None = None
        for q in (pdef.Quantities or []):
            if q.Name == "Member Length":
                lv = getattr(q, "LengthValue", None)
                if lv is not None:
                    length_val = float(lv) * MM_TO_M
                    total_lengths += 1
                    break

        if length_val is None or length_val <= 0:
            continue

        for elem in rel.RelatedObjects:
            ifc_type = elem.is_a()
            if ifc_type not in ("IfcColumn", "IfcBeam"):
                continue

            desc       = (elem.Description or "").strip()
            mark_group, member_char = _parse_description(desc)
            category   = _category_from_ifc_type_and_char(ifc_type, member_char, mark_group)

            cat_lm[category] += length_val

            if ifc_type == "IfcColumn":
                col_lm    += length_val
                col_count += 1
            else:
                beam_lm    += length_val
                beam_count += 1

            breakdown[mark_group]["count"] += 1
            breakdown[mark_group]["lm"]     = round(
                breakdown[mark_group]["lm"] + length_val, 3
            )

    log.info(
        "IFC: %d qty-sets, %d length values, %d columns (%.1f lm), %d beams (%.1f lm)",
        total_qty_sets, total_lengths, col_count, col_lm, beam_count, beam_lm,
    )

    # Write aggregated category totals
    for cat, lm in cat_lm.items():
        key = f"{cat}_lm"
        result[key] = round(lm, 2)

    result["total_column_lm"] = round(col_lm, 2)
    result["total_beam_lm"]   = round(beam_lm, 2)
    result["column_count"]    = col_count
    result["beam_count"]      = beam_count

    # Convert breakdown to regular dict for JSON serialisation
    result["member_breakdown"] = {
        mg: {"count": v["count"], "lm": round(v["lm"], 2)}
        for mg, v in sorted(breakdown.items())
    }

    # Counts from IfcDoor / IfcWindow / IfcSpace / IfcBuildingStorey
    try:
        result["door_count"]   = len(ifc.by_type("IfcDoor"))
        result["window_count"] = len(ifc.by_type("IfcWindow"))
        result["space_count"]  = len(ifc.by_type("IfcSpace"))
        result["storey_count"] = len(ifc.by_type("IfcBuildingStorey"))
    except Exception as exc:
        warnings.append(f"Error counting IFC entity types: {exc}")

    if col_count + beam_count == 0:
        warnings.append("No IfcColumn/IfcBeam elements with Member Length found in IFC")

    return result
