"""
merger.py — Merge all extraction sources using priority rules.

Every merged value carries a full audit trail: source, confidence, competing values.
"""

from __future__ import annotations
import logging
from typing import Any

from src.config import Confidence, SOURCE_PRIORITY, DEFAULT_CONFLICT_TOLERANCE
from src.utils import safe_float

log = logging.getLogger("boq.merger")


def merge_all(
    dwg: dict,
    pdf: dict,
    bom: dict,
    titleblock: dict,
) -> dict[str, Any]:
    """Merge DWG, PDF, BOM, and title-block data into a unified project model.

    Returns a dict with these top-level keys:
      - geometry: room areas, wall lengths, roof, etc.
      - doors: merged door list
      - windows: merged window list
      - finishes: merged finish list
      - stairs: merged stair detail
      - structural: framing, battens, panels
      - metadata: project name, house type
      - conflicts: list of detected conflicts
      - audit: per-item source trail
    """
    merged: dict[str, Any] = {
        "geometry": {},
        "doors": [],
        "windows": [],
        "finishes": [],
        "stairs": [],
        "structural": {},
        "metadata": {},
        "conflicts": [],
        "audit": {},
    }

    _merge_metadata(titleblock, merged)
    _merge_geometry(dwg, pdf, bom, merged)
    _merge_doors(dwg, pdf, bom, merged)
    _merge_windows(dwg, pdf, bom, merged)
    _merge_finishes(pdf, merged)
    _merge_stairs(dwg, pdf, merged)
    _merge_structural(bom, pdf, dwg, merged)

    log.info(
        "Merge complete: geometry keys=%d  doors=%d  windows=%d  "
        "finishes=%d  stairs=%d  conflicts=%d",
        len(merged["geometry"]), len(merged["doors"]),
        len(merged["windows"]), len(merged["finishes"]),
        len(merged["stairs"]), len(merged["conflicts"]),
    )
    return merged


# ─── Metadata ─────────────────────────────────────────────────────────────────

def _merge_metadata(titleblock: dict, merged: dict) -> None:
    merged["metadata"] = {
        "project_name": titleblock.get("project_name", ""),
        "house_type": titleblock.get("house_type_detected", ""),
        "house_type_confidence": titleblock.get("house_type_confidence", "UNKNOWN"),
        "highset": titleblock.get("highset_detected"),
        "laundry_location": titleblock.get("laundry_location_detected", ""),
    }


# ─── Geometry ─────────────────────────────────────────────────────────────────

def _merge_geometry(dwg: dict, pdf: dict, bom: dict, merged: dict) -> None:
    """Merge area/length geometry. DWG polygon preferred, then PDF explicit, then IFC."""
    dwg_sum = dwg.get("summary", {})
    pdf_roof = pdf.get("roof", {})
    ifc_geo  = bom.get("ifc_geometry", {})
    geo = merged["geometry"]
    audit = merged["audit"]

    def pick(key: str, sources: list[tuple[str, Any]], unit: str = "") -> None:
        """Pick first non-zero source. Record audit trail."""
        chosen = None
        chosen_src = ""
        candidates = []
        for src_name, val in sources:
            v = safe_float(val)
            candidates.append({"source": src_name, "value": v})
            if v and v > 0 and chosen is None:
                chosen = v
                chosen_src = src_name
        if chosen is not None:
            geo[key] = round(chosen, 3)
            audit[key] = {
                "value": round(chosen, 3),
                "unit": unit,
                "source": chosen_src,
                "candidates": candidates,
                "confidence": Confidence.HIGH.value if chosen_src in ("dwg", "pdf") else Confidence.MEDIUM.value,
            }
        # Check conflict
        numeric = [c["value"] for c in candidates if c["value"] and c["value"] > 0]
        if len(numeric) >= 2:
            _check_conflict(key, candidates, merged)

    pick("total_floor_area_m2",   [("dwg", dwg_sum.get("total_floor_area_m2")),
                                    ("pdf", _pdf_total_floor(pdf)),
                                    ("ifc", ifc_geo.get("total_floor_area_m2"))], "m²")
    pick("verandah_area_m2",      [("dwg", dwg_sum.get("verandah_area_m2"))], "m²")
    pick("ceiling_area_m2",       [("dwg", dwg_sum.get("ceiling_area_m2")),
                                    ("ifc", ifc_geo.get("ceiling_area_m2"))], "m²")
    pick("external_wall_length_m",[("dwg", dwg_sum.get("external_wall_length_m")),
                                    ("ifc", ifc_geo.get("external_wall_length_m"))], "lm")
    pick("internal_wall_length_m",[("dwg", dwg_sum.get("internal_wall_length_m")),
                                    ("ifc", ifc_geo.get("internal_wall_length_m"))], "lm")
    pick("total_wall_length_m",   [("dwg", dwg_sum.get("total_wall_length_m"))], "lm")
    pick("external_wall_area_m2", [("dwg", dwg_sum.get("external_wall_area_m2"))], "m²")
    pick("internal_wall_area_m2", [("dwg", dwg_sum.get("internal_wall_area_m2"))], "m²")
    pick("roof_area_m2",          [("dwg", dwg_sum.get("roof_area_m2")),
                                    ("pdf", safe_float(pdf_roof.get("roof_area_m2"))),
                                    ("ifc", ifc_geo.get("roof_area_m2"))], "m²")
    pick("roof_perimeter_m",      [("dwg", dwg_sum.get("roof_perimeter_m")),
                                    ("pdf", safe_float(pdf_roof.get("gutter_length_m")))], "lm")
    pick("post_count",            [("dwg", dwg_sum.get("post_count"))], "nr")
    pick("stair_flight_count",    [("dwg", dwg_sum.get("stair_flight_count"))], "nr")

    # Building bounding-box dims (needed for run-length batten / cladding calcs)
    pick("building_length_m",     [("dwg", dwg_sum.get("building_length_m")),
                                    ("ifc", ifc_geo.get("building_length_m"))], "m")
    pick("building_width_m",      [("dwg", dwg_sum.get("building_width_m")),
                                    ("ifc", ifc_geo.get("building_width_m"))],  "m")
    pick("verandah_length_m",     [("dwg", dwg_sum.get("verandah_length_m"))], "m")
    pick("verandah_width_m",      [("dwg", dwg_sum.get("verandah_width_m"))],  "m")
    pick("roof_pitch_degrees",    [("dwg", dwg_sum.get("roof_pitch_degrees"))], "deg")

    # Storey count from IFC if not in DWG
    if not geo.get("storey_count") and ifc_geo.get("storey_count"):
        geo["storey_count"] = ifc_geo["storey_count"]

    # Derived roof component lengths (GROUP 3 fix)
    _compute_roof_component_lengths(geo)

    # Rooms: DWG > IFC > PDF
    if dwg.get("rooms"):
        geo["rooms"] = dwg["rooms"]
    elif ifc_geo.get("rooms"):
        geo["rooms"] = ifc_geo["rooms"]
    elif pdf.get("rooms"):
        geo["rooms"] = pdf["rooms"]
    else:
        geo["rooms"] = []


def _pdf_total_floor(pdf: dict) -> float:
    rooms = pdf.get("rooms", [])
    total = sum(safe_float(r.get("area_m2")) for r in rooms if r.get("area_m2"))
    return total if total > 0 else 0.0


# ─── Doors ────────────────────────────────────────────────────────────────────

def _merge_doors(dwg: dict, pdf: dict, bom: dict, merged: dict) -> None:
    """Doors: DWG count is authoritative when DWG file present.
    Only fall back to PDF schedule/plan if no DWG doors extracted.
    """
    dwg_doors = dwg.get("doors", [])
    schedule_doors = [d for d in pdf.get("doors", [])
                      if d.get("source_type") in ("schedule",)]
    plan_doors = [d for d in pdf.get("doors", [])
                  if d.get("source_type") not in ("schedule",)]

    pdf_count = sum(safe_float(d.get("qty")) or 1 for d in pdf.get("doors", []))
    dwg_count = len(dwg_doors)

    # Always store PDF schedule doors for reference (marks, types, hardware)
    if schedule_doors:
        merged["pdf_schedule_doors"] = schedule_doors
    if schedule_doors or plan_doors:
        merged["pdf_schedule_windows"] = [w for w in pdf.get("windows", [])
                                           if w.get("source_type") in ("schedule",)]

    if dwg_doors:
        # DWG block count is authoritative — not susceptible to multi-page overcounting
        merged["doors"] = dwg_doors
        merged["audit"]["doors_source"] = "dwg"
        if pdf_count > 0 and abs(pdf_count - dwg_count) > 0:
            merged["conflicts"].append({
                "item_name": "door_count",
                "source_a": "dwg",
                "value_a": dwg_count,
                "source_b": "pdf",
                "value_b": int(pdf_count),
                "diff_pct": round(abs(pdf_count - dwg_count) / max(dwg_count, 1) * 100, 1),
                "severity": "MEDIUM",
                "recommended_action": (
                    f"DWG count used ({dwg_count}) — PDF over-counted ({int(pdf_count)}) "
                    "across multiple drawing pages"
                ),
            })
    elif schedule_doors:
        merged["doors"] = schedule_doors
        merged["audit"]["doors_source"] = "pdf_schedule"
    elif plan_doors:
        merged["doors"] = plan_doors
        merged["audit"]["doors_source"] = "pdf_plan"
    else:
        merged["doors"] = []
        merged["audit"]["doors_source"] = "none"


# ─── Windows ──────────────────────────────────────────────────────────────────

def _merge_windows(dwg: dict, pdf: dict, bom: dict, merged: dict) -> None:
    schedule_wins = [w for w in pdf.get("windows", [])
                     if w.get("source_type") in ("schedule",)]
    plan_wins = [w for w in pdf.get("windows", [])
                 if w.get("source_type") not in ("schedule",)]
    dwg_wins = dwg.get("windows", [])

    if schedule_wins:
        merged["windows"] = schedule_wins
        merged["audit"]["windows_source"] = "pdf_schedule"
    elif plan_wins:
        merged["windows"] = plan_wins
        merged["audit"]["windows_source"] = "pdf_plan"
    elif dwg_wins:
        merged["windows"] = dwg_wins
        merged["audit"]["windows_source"] = "dwg"
    else:
        merged["windows"] = []
        merged["audit"]["windows_source"] = "none"


# ─── Finishes ─────────────────────────────────────────────────────────────────

def _merge_finishes(pdf: dict, merged: dict) -> None:
    finishes = pdf.get("finishes", [])
    merged["finishes"] = finishes
    merged["audit"]["finishes_source"] = "pdf_schedule" if finishes else "none"


# ─── Stairs ───────────────────────────────────────────────────────────────────

def _merge_stairs(dwg: dict, pdf: dict, merged: dict) -> None:
    pdf_stairs = pdf.get("stairs", [])
    dwg_stairs = dwg.get("stairs", [])

    # Prefer structural section/detail from PDF
    detail_stairs = [s for s in pdf_stairs
                     if s.get("source_type") in ("section", "detail")]
    if detail_stairs:
        merged["stairs"] = detail_stairs
        merged["audit"]["stairs_source"] = "pdf_structural_detail"
    elif pdf_stairs:
        merged["stairs"] = pdf_stairs
        merged["audit"]["stairs_source"] = "pdf_plan"
    elif dwg_stairs:
        merged["stairs"] = dwg_stairs
        merged["audit"]["stairs_source"] = "dwg"
    else:
        merged["stairs"] = []
        merged["audit"]["stairs_source"] = "none"


# ─── Structural ───────────────────────────────────────────────────────────────

def _merge_structural(bom: dict, pdf: dict, dwg: dict, merged: dict) -> None:
    """Structural: BOM > IFC > PDF structural > DWG geometry."""
    bom_norm = bom.get("normalized", {})
    pdf_structural = pdf.get("structural", [])

    struct = merged["structural"]

    # Wall framing
    if bom_norm.get("wall_frame_lm", 0) > 0:
        struct["wall_frame_lm"] = bom_norm["wall_frame_lm"]
        struct["wall_frame_source"] = "bom"
    else:
        dwg_sum = dwg.get("summary", {})
        wl = safe_float(dwg_sum.get("total_wall_length_m"))
        if wl:
            struct["wall_frame_lm"] = wl
            struct["wall_frame_source"] = "dwg_derived"

    # Battens from BOM
    if bom_norm.get("ceiling_batten_lm", 0) > 0:
        struct["ceiling_batten_lm"] = bom_norm["ceiling_batten_lm"]
        struct["ceiling_batten_source"] = "bom"
    if bom_norm.get("roof_batten_lm", 0) > 0:
        struct["roof_batten_lm"] = bom_norm["roof_batten_lm"]
        struct["roof_batten_source"] = "bom"

    # Trusses
    if bom_norm.get("roof_truss_qty", 0) > 0:
        struct["roof_truss_qty"] = bom_norm["roof_truss_qty"]
        struct["roof_truss_source"] = "bom"

    # Floor panels
    if bom_norm.get("floor_panel_qty", 0) > 0:
        struct["floor_panel_qty"] = bom_norm["floor_panel_qty"]
        struct["floor_panel_source"] = "bom"
        struct["floor_panels_detail"] = bom_norm.get("floor_panels", [])

    # PDF structural items (battens, trusses, joists) if BOM missing
    _merge_pdf_structural(pdf_structural, struct)

    # BOM raw items for reference
    struct["bom_raw"] = bom.get("raw_items", [])
    struct["bom_warnings"] = bom.get("warnings", [])


def _merge_pdf_structural(pdf_items: list[dict], struct: dict) -> None:
    """Fill structural values from PDF if BOM didn't provide them."""
    for item in pdf_items:
        cat = item.get("_category", "")
        if cat == "battens":
            batten_type = (item.get("batten_type") or "").lower()
            if "ceil" in batten_type and "ceiling_batten_lm" not in struct:
                if item.get("area_or_zone"):
                    struct["ceiling_batten_note"] = item["area_or_zone"]
                    struct["ceiling_batten_source"] = "pdf_structural"
            elif "roof" in batten_type and "roof_batten_lm" not in struct:
                struct["roof_batten_note"] = item.get("area_or_zone", "")
                struct["roof_batten_source"] = "pdf_structural"
        elif cat == "trusses" and "roof_truss_qty" not in struct:
            if item.get("qty"):
                struct["roof_truss_qty"] = safe_float(item["qty"])
                struct["roof_truss_source"] = "pdf_structural"
        elif cat == "floor_panels" and "floor_panel_qty" not in struct:
            if item.get("qty"):
                struct["floor_panel_qty"] = safe_float(item["qty"])
                struct["floor_panel_source"] = "pdf_structural"


# ─── Conflict detection ───────────────────────────────────────────────────────

def _check_conflict(item: str, candidates: list[dict], merged: dict) -> None:
    numeric = [(c["source"], c["value"]) for c in candidates
               if c.get("value") and c["value"] > 0]
    if len(numeric) < 2:
        return
    vals = [v for _, v in numeric]
    lo, hi = min(vals), max(vals)
    if lo == 0:
        return
    diff = (hi - lo) / lo
    if diff > DEFAULT_CONFLICT_TOLERANCE:
        severity = "HIGH" if diff > 0.30 else "MEDIUM" if diff > 0.15 else "LOW"
        src_a, val_a = numeric[0]
        src_b, val_b = numeric[1]
        merged["conflicts"].append({
            "item_name": item,
            "source_a": src_a,
            "value_a": val_a,
            "source_b": src_b,
            "value_b": val_b,
            "diff_pct": round(diff * 100, 1),
            "severity": severity,
            "recommended_action": f"Review {item}: {src_a}={val_a} vs {src_b}={val_b} ({diff*100:.0f}% difference)",
        })


def _compute_roof_component_lengths(geo: dict) -> None:
    """Compute individual roof component lengths from building geometry.

    Ridge  = building_length - verandah_end_width  (ridge doesn't run over verandah)
    Barge  = 4 × (half_wid + ver_wid)/cos(pitch) + overhang  (main roof + verandah rakes)
    Gutter = building_length × 2  (front + rear eave runs)
    Fascia = 2×(bldg_len + bldg_wid + 4×overhang) + ver_len + 2×ver_wid
    Apron  = building_width  (one gable end only — other end has verandah)
    """
    import math
    bldg_len   = safe_float(geo.get("building_length_m", 0))
    bldg_wid   = safe_float(geo.get("building_width_m", 0))
    ver_len    = safe_float(geo.get("verandah_length_m", 0))
    ver_wid    = safe_float(geo.get("verandah_width_m", 0))
    pitch_deg  = safe_float(geo.get("roof_pitch_degrees", 18.0))
    overhang_m = safe_float(geo.get("eave_overhang_mm", 300)) / 1000.0

    if not bldg_len or not bldg_wid:
        return

    # Ridge: runs along the length, minus verandah end if verandah attached
    ver_reduction = ver_wid if ver_wid > 0 else 0.0
    ridge_m = max(0.0, bldg_len - ver_reduction)
    geo["ridge_length_m"] = round(ridge_m, 2)

    # Barge (rake) caps: rafter from ridge to eave includes verandah depth at gable ends
    # rafter_span = (half_building_width + verandah_depth) / cos(pitch) + eave_overhang
    effective_half_wid = bldg_wid / 2.0 + (ver_wid if ver_wid > 0 else 0.0)
    if pitch_deg > 0:
        rafter_span = effective_half_wid / math.cos(math.radians(pitch_deg))
    else:
        rafter_span = effective_half_wid
    rafter_span += overhang_m  # eave overhang at the bottom
    barge_m = 4 * rafter_span   # 2 ends × 2 rakes each
    geo["barge_length_m"]  = round(barge_m, 2)

    # Gutter = front + rear eave only (not barge/hip edges)
    gutter_m = bldg_len * 2.0
    geo["gutter_length_m"] = round(gutter_m, 2)

    # Fascia = building perimeter (including eave overhangs) + verandah 3 exposed sides
    # Each eave run is bldg_len + 2×overhang; each gable run is bldg_wid + 2×overhang
    ver_fascia = 0.0
    if ver_len > 0 and ver_wid > 0:
        ver_fascia = ver_len + 2 * ver_wid
    fascia_m = 2 * (bldg_len + bldg_wid + 4 * overhang_m) + ver_fascia
    geo["fascia_length_m"] = round(fascia_m, 2)

    # Apron flashing: one gable end only (verandah occupies the other gable end)
    apron_m = bldg_wid
    geo["apron_length_m"] = round(apron_m, 2)
