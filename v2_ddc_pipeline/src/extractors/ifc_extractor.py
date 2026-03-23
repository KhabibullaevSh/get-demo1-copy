"""
ifc_extractor.py — Extract structural quantities from FrameCAD IFC models.

FrameCAD description format
───────────────────────────
Two formats observed in Angau Pharmacy IFC:

  A) "{panel}-{type_char}{n}"  e.g. "G1-W5", "N1-T2", "V1-T1", "N7-B1", "G1-R4"
     panel     = panel group prefix  (G1, N1–N7, V1, ...)
     type_char = W wall-stud | T top-plate | B bottom-plate
                 R short-rafter | N noggin | P plate | L lintel
                 J floor-joist | G girt | C corner-stud | S stud

  B) "{panel_id}" only (no hyphen)  e.g. "L1", "N8", "XX", "00", "2440.000050"
     — member type is NOT encoded; inferred from IFC element type + length.
     Special cases:
       "XX" or "00" + name contains "SHS"  → structural steel hollow section
       Numeric desc (e.g. "2440.000050")   → lgs_unclassified (FrameCAD artifact)
       "L{n}" / "N{n}" IfcColumn, len>2.5m → wall_stud_inferred
       "L{n}" / "N{n}" IfcColumn, len≤2.5m → wall_stud_short_inferred (cripple/sill)
       "L{n}" / "N{n}" IfcBeam, len>2.0m   → wall_plate_inferred
       "L{n}" / "N{n}" IfcBeam, 0.3–2.0m   → wall_noggin_inferred
       "L{n}" / "N{n}" IfcBeam, len<0.3m   → wall_connector_inferred

T-type anomaly (validated 23 Mar 2026)
───────────────────────────────────────
FrameCAD uses T = Top plate (track), NOT tie-strap.
All T-type members are IfcBeam at 7.770 m or 7.746 m (panel widths) — consistent
with a double top plate (T2 + T3 members).  582 lm total.

B-type anomaly (validated 23 Mar 2026)
───────────────────────────────────────
B = Bottom plate (track).  BUT all 21 B members have length = 15.000 m exactly,
which is anomalous (Angau Pharmacy is ≤12 m wide).  This looks like FrameCAD
writing the total bottom-track material length per panel group as a single element.
315 lm total — kept as bottom_plate but flagged manual_review.

SHS steel (validated 23 Mar 2026)
───────────────────────────────────
87 IfcColumn + 1 IfcBeam with name "75 x 75 x 4.0 SHS", desc "XX" or "00".
These are structural steel hollow section posts/beams — NOT LGS framing.
Must be a separate BOQ item.

2440-desc members (unresolved 23 Mar 2026)
────────────────────────────────────────────
128 IfcBeam, name "89S41-075-500", desc "2440.000050" (numeric FrameCAD artifact).
Length ~3.7 m each, total 481.7 lm.  Likely floor-joist cassette or ceiling purlin.
Marked lgs_unclassified / manual_review until a FrameCAD BOM confirms purpose.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("boq.v2.ifc_extractor")

MM_TO_M = 1 / 1_000

# Length thresholds for inferring type from bare-desc members
_STUD_MIN_LM        = 2.5   # IfcColumn ≥ this → full-height stud
_PLATE_MIN_LM       = 2.0   # IfcBeam   ≥ this → top/bottom plate
_NOGGIN_MIN_LM      = 0.3   # IfcBeam   ≥ this → noggin; < this → connector
_BOTTOM_PLATE_ANOM  = 5.0   # B-type IfcBeam ≥ this → flag as anomalous length

_NUMERIC_DESC_RE    = re.compile(r"^\d")   # starts with a digit → FrameCAD artifact


def _is_shs_steel(name: str) -> bool:
    """True if the member name indicates structural steel hollow section."""
    return "SHS" in name.upper() or "RHS" in name.upper() or "CHS" in name.upper()


def _parse_description(desc: str, name: str) -> tuple[str, str, str]:
    """
    Parse FrameCAD description.

    Returns:
        (mark_group, member_char, desc_class)
        desc_class: 'hyphen' | 'shs_steel' | 'numeric_artifact' | 'bare_panel'
    """
    desc = (desc or "").strip()
    name = (name or "").strip()

    if not desc:
        return "?", "?", "bare_panel"

    # SHS / RHS / CHS steel (desc XX, 00, or any + SHS in name)
    if _is_shs_steel(name):
        return desc, "SHS", "shs_steel"

    # Numeric artifact (e.g. "2440.000050")
    if _NUMERIC_DESC_RE.match(desc):
        return desc, "?", "numeric_artifact"

    # Format A: "panel-type_char{n}"
    if "-" in desc:
        parts = desc.split("-")
        mark_group  = parts[0].strip() or "?"
        last        = parts[-1].strip()
        member_char = last[0].upper() if last else "?"
        return mark_group, member_char, "hyphen"

    # Format B: bare panel id (no hyphen, no SHS, not numeric)
    return desc, "?", "bare_panel"


def _classify_hyphen(ifc_type: str, member_char: str, mark_group: str,
                     length_m: float) -> tuple[str, bool]:
    """
    Classify a member that has a hyphen-format description.
    Returns (category, manual_review_flag).
    """
    is_verandah = mark_group.upper().startswith("V")

    # W = wall stud (vertical LGS member)
    if member_char == "W":
        return ("verandah_frame" if is_verandah else "wall_frame_stud", False)

    # T = top plate / top track (horizontal IfcBeam)
    if member_char == "T":
        if is_verandah:
            return "verandah_frame", False
        # Applies to both IfcBeam (top plate) and IfcColumn (unlikely but safe)
        return "wall_frame_top_plate", False

    # B = bottom plate (horizontal IfcBeam); flag anomalous if ≥ threshold
    if member_char == "B":
        anom = length_m >= _BOTTOM_PLATE_ANOM
        if is_verandah:
            return "verandah_frame", anom
        return "wall_frame_bottom_plate", anom

    # N = noggin
    if member_char == "N":
        if is_verandah:
            return "verandah_frame", False
        return "wall_noggin", False

    # P = plate (structural plate; context-dependent)
    if member_char == "P":
        if is_verandah:
            return "verandah_frame", False
        return ("roof_plate" if ifc_type == "IfcBeam" else "wall_plate"), False

    # R = short rafter / rolled end
    if member_char == "R":
        return "roof_rafter", False

    # L = lintel
    if member_char == "L":
        return "lintel", False

    # J = floor joist
    if member_char == "J":
        return "floor_joist", False

    # G = girt
    if member_char == "G":
        return "girt", False

    # C / S = corner stud / stud alias
    if member_char in ("C", "S"):
        return ("verandah_frame" if is_verandah else "wall_frame_stud"), False

    # Catch-all with verandah awareness
    if is_verandah:
        return "verandah_frame", False

    return ("wall_other" if ifc_type == "IfcColumn" else "roof_other"), True


def _classify_bare_panel(ifc_type: str, length_m: float) -> tuple[str, bool]:
    """
    Infer category from IFC element type + member length when no type code present.
    These are FrameCAD panel members (L1-L30, N1-N16) without type suffixes.
    Returns (category, manual_review_flag).
    """
    if ifc_type == "IfcColumn":
        if length_m >= _STUD_MIN_LM:
            return "wall_stud_inferred", False        # full-height stud (3.022 m typical)
        return "wall_stud_short_inferred", True        # cripple / sill / head piece

    # IfcBeam
    if length_m >= _PLATE_MIN_LM:
        return "wall_plate_inferred", False            # top or bottom plate (3.7-4.3 m)
    if length_m >= _NOGGIN_MIN_LM:
        return "wall_noggin_inferred", False           # short horizontal (0.9-1.1 m)
    return "wall_connector_inferred", True             # very short — clip / strap


def extract_ifc(ifc_path: Path) -> dict:
    """
    Extract member quantities from *ifc_path*.

    Returns structured dict with linear-metre totals by category, plus
    per-category manual_review_lm for flagged anomalies.
    """
    warnings_list: list[str] = []
    result: dict = {
        # ── Named categories (hyphen-format members) ──
        "wall_frame_stud_lm":         0.0,
        "wall_frame_top_plate_lm":    0.0,
        "wall_frame_bottom_plate_lm": 0.0,
        "wall_plate_lm":              0.0,
        "wall_noggin_lm":             0.0,
        "wall_other_lm":              0.0,
        "lintel_lm":                  0.0,
        "girt_lm":                    0.0,
        "roof_rafter_lm":             0.0,
        "roof_plate_lm":              0.0,
        "roof_other_lm":              0.0,
        "floor_joist_lm":             0.0,
        "verandah_frame_lm":          0.0,
        # ── Inferred categories (bare-desc members, length-based) ──
        "wall_stud_inferred_lm":      0.0,
        "wall_stud_short_inferred_lm":0.0,
        "wall_plate_inferred_lm":     0.0,
        "wall_noggin_inferred_lm":    0.0,
        "wall_connector_inferred_lm": 0.0,
        # ── Special / unresolved ──
        "steel_shs_lm":               0.0,   # SHS / RHS / CHS steel (not LGS)
        "lgs_unclassified_lm":        0.0,   # numeric-artifact desc (e.g. 2440.000050)
        "unclassified_lm":            0.0,
        # ── Manual review accumulator ──
        "manual_review_lm":           0.0,
        # ── Raw totals ──
        "total_column_lm":            0.0,
        "total_beam_lm":              0.0,
        "column_count":               0,
        "beam_count":                 0,
        "door_count":                 0,
        "window_count":               0,
        "space_count":                0,
        "storey_count":               0,
        # ── Classification quality ──
        "classification_notes":       [],
        "member_breakdown":           {},
        "source":                     "ifc_model",
        "source_file":                str(ifc_path),
        "schema":                     "unknown",
        "warnings":                   warnings_list,
    }

    try:
        import ifcopenshell
    except ImportError:
        warnings_list.append("ifcopenshell not installed — IFC extraction skipped")
        return result

    try:
        ifc = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        warnings_list.append(f"Failed to open IFC: {exc}")
        return result

    result["schema"] = ifc.schema

    cat_lm:   dict[str, float] = defaultdict(float)
    mr_lm:    float            = 0.0   # manual_review accumulator
    col_lm    = 0.0
    beam_lm   = 0.0
    col_count = 0
    beam_count = 0
    breakdown: dict[str, dict] = defaultdict(lambda: {"count": 0, "lm": 0.0})

    # Build elem→length lookup first for efficiency
    eq_by_elem: dict[str, float] = {}
    for rel in ifc.by_type("IfcRelDefinesByProperties"):
        pdef = rel.RelatingPropertyDefinition
        if not pdef.is_a("IfcElementQuantity"):
            continue
        length_val: float | None = None
        for q in (pdef.Quantities or []):
            if q.Name == "Member Length":
                lv = getattr(q, "LengthValue", None)
                if lv is not None:
                    length_val = float(lv) * MM_TO_M
                    break
        if length_val is None or length_val <= 0:
            continue
        for elem in rel.RelatedObjects:
            if elem.is_a() in ("IfcColumn", "IfcBeam"):
                # Keep first length found per element
                if elem.GlobalId not in eq_by_elem:
                    eq_by_elem[elem.GlobalId] = length_val

    # Classify each element
    for elem in ifc.by_type("IfcColumn") + ifc.by_type("IfcBeam"):
        length_m = eq_by_elem.get(elem.GlobalId)
        if length_m is None or length_m <= 0:
            continue

        ifc_type   = elem.is_a()
        desc_raw   = (elem.Description or "").strip()
        name_raw   = (elem.Name or "").strip()

        mark_group, member_char, desc_class = _parse_description(desc_raw, name_raw)

        if desc_class == "shs_steel":
            category  = "steel_shs"
            manual_rv = False

        elif desc_class == "numeric_artifact":
            category  = "lgs_unclassified"
            manual_rv = True

        elif desc_class == "hyphen":
            category, manual_rv = _classify_hyphen(ifc_type, member_char, mark_group, length_m)

        else:  # bare_panel
            category, manual_rv = _classify_bare_panel(ifc_type, length_m)

        cat_lm[category] += length_m
        if manual_rv:
            mr_lm += length_m

        if ifc_type == "IfcColumn":
            col_lm    += length_m
            col_count += 1
        else:
            beam_lm    += length_m
            beam_count += 1

        bk_key = f"{mark_group}:{desc_class}"
        breakdown[bk_key]["count"] += 1
        breakdown[bk_key]["lm"]     = round(breakdown[bk_key]["lm"] + length_m, 3)

    # Write category totals
    for cat, lm in cat_lm.items():
        result[f"{cat}_lm"] = round(lm, 2)

    result["manual_review_lm"]    = round(mr_lm, 2)
    result["total_column_lm"]     = round(col_lm, 2)
    result["total_beam_lm"]       = round(beam_lm, 2)
    result["column_count"]        = col_count
    result["beam_count"]          = beam_count

    # Classification summary notes
    notes = result["classification_notes"]
    total_lm = col_lm + beam_lm

    # T-type note
    t_lm = cat_lm.get("wall_frame_top_plate", 0.0)
    if t_lm > 0:
        notes.append(
            f"T-type members ({t_lm:.1f} lm): classified as wall_frame_top_plate "
            f"(FrameCAD top tracks — 7.77 m panel width, double-layer)"
        )

    # B-type anomaly note
    b_lm = cat_lm.get("wall_frame_bottom_plate", 0.0)
    if b_lm > 0:
        notes.append(
            f"B-type members ({b_lm:.1f} lm): wall_frame_bottom_plate — "
            f"WARNING: all 15.0 m lengths are anomalous (likely cumulative total, not cut lengths). "
            f"Manual review required before ordering."
        )

    # SHS note
    shs_lm = cat_lm.get("steel_shs", 0.0)
    if shs_lm > 0:
        notes.append(
            f"SHS steel ({shs_lm:.1f} lm): structural steel hollow sections (75×75×4 SHS). "
            f"Separate BOQ item — NOT included in LGS wall frame totals."
        )

    # Numeric artifact note
    art_lm = cat_lm.get("lgs_unclassified", 0.0)
    if art_lm > 0:
        notes.append(
            f"Numeric-desc members ({art_lm:.1f} lm): FrameCAD export artifact "
            f"(desc='2440.000050', 128 × ~3.7 m LGS beams). "
            f"Likely floor-joist cassette or ceiling purlin. "
            f"Cannot confirm without FrameCAD BOM — manual_review."
        )

    # Inferred bare-panel summary
    inferred_lm = sum(cat_lm.get(c, 0.0) for c in (
        "wall_stud_inferred", "wall_stud_short_inferred",
        "wall_plate_inferred", "wall_noggin_inferred", "wall_connector_inferred",
    ))
    if inferred_lm > 0:
        notes.append(
            f"Bare-desc panel members ({inferred_lm:.1f} lm): "
            f"L1-L30 / N1-N16 without type codes — "
            f"member type inferred from IFC element type + length threshold. "
            f"Confirm with FrameCAD BOM for precise stud/plate/noggin split."
        )

    if total_lm > 0:
        unresolved_lm = cat_lm.get("unclassified", 0.0)
        unresolved_pct = 100 * unresolved_lm / total_lm
        notes.append(
            f"Classification coverage: {100*(1 - unresolved_lm/total_lm):.1f}% "
            f"({unresolved_pct:.1f}% still unclassified)"
        )

    result["member_breakdown"] = {
        k: {"count": v["count"], "lm": round(v["lm"], 2)}
        for k, v in sorted(breakdown.items())
    }

    try:
        result["door_count"]   = len(ifc.by_type("IfcDoor"))
        result["window_count"] = len(ifc.by_type("IfcWindow"))
        result["space_count"]  = len(ifc.by_type("IfcSpace"))
        result["storey_count"] = len(ifc.by_type("IfcBuildingStorey"))
    except Exception as exc:
        warnings_list.append(f"Error counting IFC entity types: {exc}")

    if col_count + beam_count == 0:
        warnings_list.append("No IfcColumn/IfcBeam elements with Member Length found")

    log.info(
        "IFC: %d cols (%.1f lm), %d beams (%.1f lm) | "
        "wall_stud=%.0f top_plate=%.0f btm_plate=%.0f verandah=%.0f "
        "shs=%.0f unclassified_lgs=%.0f manual_review=%.0f",
        col_count, col_lm, beam_count, beam_lm,
        cat_lm.get("wall_frame_stud", 0),
        cat_lm.get("wall_frame_top_plate", 0),
        cat_lm.get("wall_frame_bottom_plate", 0),
        cat_lm.get("verandah_frame", 0),
        cat_lm.get("steel_shs", 0),
        cat_lm.get("lgs_unclassified", 0),
        mr_lm,
    )

    return result
