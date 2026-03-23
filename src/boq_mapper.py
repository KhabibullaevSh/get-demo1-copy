"""
boq_mapper.py — Map neutral quantity model to BOQ-ready items using the item library.

This module bridges project_quantities.py (neutral quantities) and boq_writer.py
(BOQ Excel output). It uses the item library for stock-code / description lookup
but does NOT impose a G303-only structure on custom projects.

Output is also saved to output/json/{project_name}_boq_items.json.
"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path

from src.config import OUTPUT_DIR
from src.utils import safe_float

log = logging.getLogger("boq.mapper")

OUTPUT_JSON = OUTPUT_DIR / "json"

# ── Fallback section names for item groups ────────────────────────────────────
_GROUP_SECTION: dict[str, str] = {
    "floor":    "FLOOR",
    "walls":    "WALL FRAMING",
    "roof":     "ROOF STRUCTURE",
    "ceiling":  "CEILING",
    "doors":    "DOORS & WINDOWS",
    "windows":  "DOORS & WINDOWS",
    "finishes": "FINISHES",
    "stairs":   "STAIRS",
    "services": "SERVICES",
    "external": "EXTERNAL WORKS",
    "rooms":    "SCHEDULE",
    "other":    "GENERAL",
}

# Granular section overrides by (item_group, element_type) ────────────────────
_ELEMENT_SECTION: dict[tuple[str, str], str] = {
    # floor
    ("floor", "floor_area"):              "FLOOR",
    ("floor", "floor_panel"):             "FLOOR STRUCTURE",
    ("floor", "floor_joist"):             "FLOOR STRUCTURE",
    # walls
    ("walls", "external_wall"):           "WALL FRAMING",
    ("walls", "internal_wall"):           "WALL FRAMING",
    ("walls", "wall_frame"):              "WALL FRAMING",
    ("walls", "fc_sheet_external"):       "WALL LININGS",
    ("walls", "fc_sheet_internal"):       "WALL LININGS",
    ("walls", "insulation_batts"):        "INSULATION",
    # roof
    ("roof", "roof_area"):                "ROOF STRUCTURE",
    ("roof", "roof_batten"):              "ROOF STRUCTURE",
    ("roof", "roof_truss"):               "ROOF STRUCTURE",
    ("roof", "roof_cladding"):            "ROOF CLADDING",
    ("roof", "sisalation"):               "ROOF",
    ("roof", "insulation_batts"):         "INSULATION",
    ("roof", "ridge_cap"):                "ROOF",
    ("roof", "gutter"):                   "ROOF DRAINAGE",
    ("roof", "fascia_board"):             "ROOF",
    ("roof", "downpipe"):                 "ROOF DRAINAGE",
    ("roof", "barge_board"):              "ROOF",
    ("roof", "roof_fixings"):             "ROOF",
    # ceiling
    ("ceiling", "ceiling_area"):          "CEILING",
    ("ceiling", "fc_sheet_ceiling"):      "CEILING LININGS",
    ("ceiling", "ceiling_batten"):        "CEILING",
    ("ceiling", "cornice_trim"):          "CEILING",
    # finishes
    ("finishes", "skirting_board"):       "FINISHES",
    ("finishes", "architrave_door"):      "FINISHES",
    ("finishes", "architrave_window"):    "FINISHES",
    ("finishes", "paint_external"):       "FINISHES",
    ("finishes", "paint_internal"):       "FINISHES",
    # doors / windows
    ("doors",   "door_count"):            "DOORS & WINDOWS",
    ("doors",   "door_lockset"):          "DOORS & WINDOWS",
    ("doors",   "door_hinge_set"):        "DOORS & WINDOWS",
    ("doors",   "door_stop"):             "DOORS & WINDOWS",
    ("windows", "window_count"):          "DOORS & WINDOWS",
    ("windows", "flyscreen"):             "DOORS & WINDOWS",
    # stairs
    ("stairs", "stair_flight"):           "STAIRS",
    ("stairs", "stair_riser"):            "STAIRS",
    ("stairs", "stair_tread"):            "STAIRS",
    ("stairs", "balustrade"):             "STAIRS",
    ("stairs", "handrail"):               "STAIRS",
    # services
    ("services", "wet_area_waterproofing"):   "SERVICES",
    ("services", "sanitary_fixtures"):        "SERVICES",
    ("services", "builders_work_plumbing"):   "SERVICES",
    ("services", "builders_work_electrical"): "SERVICES",
    # external
    ("external", "verandah_decking"):     "EXTERNAL WORKS",
    ("external", "verandah_handrail"):    "EXTERNAL WORKS",
    ("external", "site_preparation"):     "EXTERNAL WORKS",
    # rooms
    ("rooms", "room_area"):               "SCHEDULE",
}

# Human-readable descriptions for each element_type ───────────────────────────
_ELEMENT_DESC: dict[str, str] = {
    "floor_area":                  "Floor Area",
    "floor_panel":                 "Floor Panel (LGS)",
    "floor_joist":                 "Floor Joist (LGS)",
    "external_wall":               "External Wall",
    "internal_wall":               "Internal Wall",
    "wall_frame":                  "Wall Frame (LGS)",
    "fc_sheet_external":           "FC Sheet — External Wall Cladding",
    "fc_sheet_internal":           "FC Sheet — Internal Wall Lining",
    "fc_sheet_ceiling":            "FC Sheet — Ceiling",
    "insulation_batts":            "Insulation Batts",
    "sisalation":                  "Sisalation / Sarking",
    "roof_area":                   "Roof Area",
    "roof_batten":                 "Roof Batten (LGS)",
    "roof_truss":                  "Roof Truss (LGS)",
    "roof_cladding":               "Roof Cladding",
    "ridge_cap":                   "Ridge Cap",
    "gutter":                      "Gutter",
    "fascia_board":                "Fascia Board",
    "downpipe":                    "Downpipe",
    "barge_board":                 "Barge Board",
    "roof_fixings":                "Roof Fixings (Tek Screws)",
    "ceiling_area":                "Ceiling Area",
    "ceiling_batten":              "Ceiling Batten (LGS)",
    "cornice_trim":                "Cornice / Ceiling Trim",
    "skirting_board":              "Skirting Board",
    "architrave_door":             "Architrave — Door",
    "architrave_window":           "Architrave — Window",
    "paint_external":              "Paint — External",
    "paint_internal":              "Paint — Internal",
    "door_count":                  "Door",
    "door_lockset":                "Door Lockset",
    "door_hinge_set":              "Door Hinge Set (Pair)",
    "door_stop":                   "Door Stop",
    "window_count":                "Window",
    "flyscreen":                   "Flyscreen",
    "stair_flight":                "Stair",
    "stair_riser":                 "Stair Riser",
    "stair_tread":                 "Stair Tread",
    "balustrade":                  "Balustrade",
    "handrail":                    "Handrail",
    "wet_area_waterproofing":      "Wet Area Waterproofing",
    "sanitary_fixtures":           "Sanitary Fixtures (Provisional)",
    "builders_work_plumbing":      "Builder's Work — Plumbing",
    "builders_work_electrical":    "Builder's Work — Electrical",
    "verandah_decking":            "Verandah Decking",
    "verandah_handrail":           "Verandah Handrail",
    "site_preparation":            "Site Preparation (Provisional)",
    "room_area":                   "Room",
}

# Element types that must NEVER have their description/section overridden by library matching
_SKIP_LIBRARY_MATCH: set[str] = {
    # Geometry measurements — keep our description, don't match to supply items
    "floor_area", "external_wall", "internal_wall", "ceiling_area", "roof_area",
    "roof_cladding",        # keep generic "Roof Cladding" — specific type is manual
    "insulation_batts",
    "cornice_trim",
    "skirting_board",
    "paint_external",
    "paint_internal",
    "architrave_door",
    "architrave_window",
    # FC ceiling sheets — keep distinct from FC wall sheet library item
    "fc_sheet_ceiling",
    # Ceiling battens — library "batten" keyword also matches roof battens (wrong)
    "ceiling_batten",
    # Door/window hardware — keep specific descriptions, not the door/window item
    "door_lockset",
    "door_hinge_set",
    "door_stop",
    "flyscreen",
    "wet_area_waterproofing",
    "sanitary_fixtures",
    "builders_work_plumbing",
    "builders_work_electrical",
    "site_preparation",
    "verandah_handrail",
    "verandah_decking",     # keep generic until type confirmed
    "wall_frame",           # keep our description for derived wall frame lm
}


def _infer_qty_basis(source_evidence: str, quantity, manual_review: bool) -> str:
    """Infer quantity_basis from source evidence and flags (mapper-level)."""
    if quantity is None or manual_review:
        return "manual_review"
    src = str(source_evidence).lower()
    if src.startswith("derived:") or "derived" in src:
        return "derived"
    if src in ("implied_scope", "none", ""):
        return "provisional"
    return "measured"


def map_to_boq_items(
    quantity_model: dict,
    item_library: dict,
    merged: dict,
) -> list[dict]:
    """
    Map neutral quantity model entries to BOQ-ready item dicts.

    For each quantity:
      1. Try to match against the item library (by description or stock code)
      2. If matched, use library's stock code and description style
      3. If not matched, generate a BOQ item from the quantity's own data

    Also generates additional items directly from merged structural/door/window/finish data
    not captured by the neutral quantity model.

    Returns list of BOQ item dicts with both new keys (quantity, source_evidence, boq_section)
    and compatibility aliases (qty, source, category) for boq_writer._write_boq_sheet.
    """
    quantities = quantity_model.get("quantities", [])
    boq_items: list[dict] = []

    # Determine if merged data has per-type door/window info — if so, skip
    # the "all" count items from the quantity model (the mapper will produce per-type items)
    _merged_doors   = merged.get("doors", [])
    _merged_windows = merged.get("windows", [])
    _has_door_types = any(
        d.get("type") or d.get("mark") or d.get("type_mapped")
        for d in _merged_doors
        if str(d.get("type") or d.get("mark") or d.get("type_mapped") or "").lower()
           not in ("door", "unknown", "")
    )
    _has_window_types = any(
        w.get("type") or w.get("mark") or w.get("type_note") or w.get("type_mapped")
        for w in _merged_windows
        if str(w.get("type") or w.get("mark") or w.get("type_note") or w.get("type_mapped") or "").lower()
           not in ("window", "unknown", "")
    )

    for qty_entry in quantities:
        ig = qty_entry.get("item_group", "")
        et = qty_entry.get("element_type", "")
        st = qty_entry.get("subtype", "")
        # Skip "all" count items for doors/windows when per-type info is available
        if ig == "doors" and et == "door_count" and st == "all" and _has_door_types:
            continue
        if ig == "windows" and et == "window_count" and st == "all" and _has_window_types:
            continue
        boq_item = _map_single_quantity(qty_entry, item_library)
        boq_items.append(boq_item)

    # Track which quantity item_groups are already represented to avoid duplicates
    covered_groups: set[str] = set()
    for qty_entry in quantities:
        ig = qty_entry.get("item_group", "")
        et = qty_entry.get("element_type", "")
        # Mark group/element combinations that were already emitted
        covered_groups.add(f"{ig}/{et}")

    # Generate additional items from merged data not already in quantity_model
    _add_structural_items(boq_items, merged, item_library, covered_groups)
    _add_door_items(boq_items, merged, item_library, covered_groups)
    _add_window_items(boq_items, merged, item_library, covered_groups)
    _add_finish_items(boq_items, merged, item_library)
    _add_stair_items(boq_items, merged, item_library)

    # Add compatibility aliases for boq_writer._write_boq_sheet
    for item in boq_items:
        if "qty" not in item:
            item["qty"] = item.get("quantity")
        if "source" not in item:
            item["source"] = item.get("source_evidence", "")
        if "category" not in item:
            item["category"] = item.get("boq_section", "")

    log.info(
        "BOQ mapper: %d quantities → %d BOQ items  "
        "(matched=%d  unmatched=%d)",
        len(quantities), len(boq_items),
        sum(1 for i in boq_items if i.get("mapping_rule") == "library_match"),
        sum(1 for i in boq_items if i.get("mapping_rule") != "library_match"),
    )
    return boq_items


def save_boq_items(boq_items: list[dict], project_name: str) -> Path:
    """Save BOQ items to output/json/{project_name}_boq_items.json."""
    OUTPUT_JSON.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_JSON / f"{project_name}_boq_items.json"
    with open(str(out_path), "w", encoding="utf-8") as f:
        json.dump(boq_items, f, indent=2, default=str)
    log.info("BOQ items saved: %s  (%d items)", out_path.name, len(boq_items))
    return out_path


# ─── Internal mapping ────────────────────────────────────────────────────────

def _map_single_quantity(qty: dict, item_library: dict) -> dict:
    """Map one quantity entry to a BOQ item dict."""
    item_group   = qty.get("item_group", "other")
    element_type = qty.get("element_type", "")
    subtype      = qty.get("subtype", "")
    quantity     = qty.get("quantity")
    unit         = qty.get("unit", "")
    source_ev    = qty.get("source_evidence", "")
    confidence   = qty.get("confidence", "MEDIUM")
    assumption   = qty.get("assumption", "")
    manual_rev   = bool(qty.get("manual_review"))

    # Resolve section: element-type override takes strict priority over library section.
    # This prevents library matches from forcing items into wrong sections
    # (e.g., FC sheets appearing under "Ground Level Laundry").
    explicit_section = _ELEMENT_SECTION.get((item_group, element_type))
    group_section    = _GROUP_SECTION.get(item_group, "GENERAL")

    # Build a candidate description for library lookup
    candidate_desc = _build_candidate_desc(item_group, element_type, subtype)

    # Skip library matching for element types that must keep their own description/section
    skip_lib = element_type in _SKIP_LIBRARY_MATCH
    lib_entry, norm_key = (None, None) if skip_lib else _find_library_match(candidate_desc, item_library)

    # Compute quantity_basis and quantity_rule_used
    qty_basis = qty.get("quantity_basis") or _infer_qty_basis(source_ev, quantity, manual_rev)
    qty_rule  = qty.get("quantity_rule") or assumption or ""

    qty_val = _round_qty(quantity)
    if lib_entry:
        # Use explicit section when we have one; fall back to library section only for unmapped types
        final_section = explicit_section or lib_entry.get("section") or group_section
        final_desc    = lib_entry.get("description") or candidate_desc
        return {
            "boq_section":        final_section,
            "stock_code":         lib_entry.get("stock_code") or "",
            "description":        final_desc,
            "quantity":           qty_val,
            "unit":               lib_entry.get("unit") or unit,
            "source_evidence":    source_ev,
            "mapping_rule":       "library_match",
            "confidence":         confidence,
            "notes":              assumption or "",
            "manual_review":      manual_rev or qty_val is None,
            "_lib_key":           norm_key,
            "quantity_basis":     qty_basis,
            "quantity_rule_used": qty_rule,
            # Compatibility aliases
            "qty":                qty_val,
            "source":             source_ev,
            "category":           final_section,
        }
    else:
        return {
            "boq_section":        explicit_section or group_section,
            "stock_code":         "",
            "description":        candidate_desc,
            "quantity":           qty_val,
            "unit":               unit,
            "source_evidence":    source_ev,
            "mapping_rule":       "no_library_match",
            "confidence":         confidence,
            "notes":              assumption or "",
            "manual_review":      manual_rev or qty_val is None,
            "quantity_basis":     qty_basis,
            "quantity_rule_used": qty_rule,
            # Compatibility aliases
            "qty":                qty_val,
            "source":             source_ev,
            "category":           explicit_section or group_section,
        }


def _build_candidate_desc(item_group: str, element_type: str, subtype: str) -> str:
    """Build a human-readable description from quantity components."""
    # Use explicit description template when available
    base = _ELEMENT_DESC.get(element_type)
    if base:
        # Append meaningful subtypes (skip generic / technical ones)
        skip = {"all", "total", "unknown", "", "lm", "nr", "m2", "sheets",
                "rolls", "boxes", "provisional", "lm_derived",
                # LGS material grades — not meaningful in BOQ description
                "lgs", "lgs_derived", "lgs derived"}
        if subtype and subtype.lower() not in skip:
            return f"{base} — {subtype.replace('_', ' ').title()}"
        return base

    # Generic fallback
    parts: list[str] = []
    if item_group:
        parts.append(item_group.replace("_", " ").title())
    if element_type and element_type != item_group:
        et = element_type.replace("_", " ").title()
        if not parts or et not in parts[0]:
            parts.append(et)
    if subtype and subtype.lower() not in ("all", "total", "unknown", ""):
        parts.append(subtype.replace("_", " ").title())
    return " — ".join(parts) if parts else "Unknown Item"


def _normalise(text: str) -> str:
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _find_library_match(
    candidate_desc: str,
    item_library: dict,
) -> tuple[dict | None, str | None]:
    """
    Find the best matching library entry for a candidate description.

    Returns (entry, norm_key) or (None, None).
    Strategy:
      1. Exact normalised match
      2. Fuzzy word-overlap match (threshold 0.35)
      3. Keyword category fallback (for construction item categories)
    """
    if not item_library:
        return None, None

    norm_cand = _normalise(candidate_desc)

    # 1. Exact match
    if norm_cand in item_library:
        return item_library[norm_cand], norm_cand

    # 2. Fuzzy match
    cand_words = set(norm_cand.split())
    if len(cand_words) >= 2:
        best_score, best_key, best_entry = 0.0, None, None
        for norm_key, entry in item_library.items():
            key_words = set(norm_key.split())
            common    = cand_words & key_words
            if not common:
                continue
            score = len(common) / max(len(cand_words), len(key_words), 1)
            if score > best_score:
                best_score, best_key, best_entry = score, norm_key, entry

        if best_score >= 0.55:
            return best_entry, best_key

    # 3. Keyword category fallback
    try:
        from src.item_library import find_by_keyword_category
        entry, norm_key = find_by_keyword_category(candidate_desc, item_library)
        if entry:
            return entry, norm_key
    except Exception:
        pass

    return None, None


def _round_qty(qty) -> float | None:
    if qty is None:
        return None
    try:
        return round(float(qty), 3)
    except (TypeError, ValueError):
        return None


# ─── Merged-data item generators ─────────────────────────────────────────────

def _make_item(
    boq_section: str,
    description: str,
    quantity,
    unit: str,
    source_evidence: str,
    confidence: str = "MEDIUM",
    notes: str = "",
    stock_code: str = "",
    mapping_rule: str = "merged_data",
    manual_review: bool = False,
    lib_entry: dict | None = None,
    quantity_basis: str = "",
    quantity_rule_used: str = "",
) -> dict:
    """Build a complete BOQ item dict with all required keys plus aliases."""
    qty_val = _round_qty(quantity)
    if lib_entry:
        stock_code = lib_entry.get("stock_code") or stock_code
        description = lib_entry.get("description") or description
        unit = lib_entry.get("unit") or unit
        boq_section = lib_entry.get("section") or boq_section
        mapping_rule = "library_match"
    qty_basis = quantity_basis or _infer_qty_basis(source_evidence, qty_val, manual_review or qty_val is None)
    return {
        "boq_section":        boq_section,
        "stock_code":         stock_code,
        "description":        description,
        "quantity":           qty_val,
        "unit":               unit,
        "source_evidence":    source_evidence,
        "mapping_rule":       mapping_rule,
        "confidence":         confidence,
        "notes":              notes,
        "manual_review":      manual_review or qty_val is None,
        "quantity_basis":     qty_basis,
        "quantity_rule_used": quantity_rule_used or "",
        # Compatibility aliases for boq_writer._write_boq_sheet
        "qty":                qty_val,
        "source":             source_evidence,
        "category":           boq_section,
    }


def _add_structural_items(
    boq_items: list[dict],
    merged: dict,
    item_library: dict,
    covered_groups: set[str] | None = None,
) -> None:
    """Add structural framing items from merged["structural"] BOM/IFC data.

    Skips items whose item_group/element_type combination is already in covered_groups
    (i.e., already emitted from the quantity model).
    """
    struct = merged.get("structural", {})
    if not struct:
        return
    cg = covered_groups or set()

    # Wall frame linear metres — only if not already covered by quantity_model
    wf_lm = safe_float(struct.get("wall_frame_lm"))
    if wf_lm and wf_lm > 0 and "walls/wall_frame" not in cg:
        lib, _ = _find_library_match("wall frame LGS", item_library)
        boq_items.append(_make_item(
            boq_section="WALL FRAMING",
            description="Wall Frame (LGS)",
            quantity=wf_lm,
            unit="lm",
            source_evidence=struct.get("wall_frame_source", "bom/ifc"),
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))

    # Roof battens
    rb_lm = safe_float(struct.get("roof_batten_lm"))
    if rb_lm and rb_lm > 0 and "roof/roof_batten" not in cg:
        lib, _ = _find_library_match("roof batten LGS", item_library)
        boq_items.append(_make_item(
            boq_section="ROOF STRUCTURE",
            description="Roof Batten (LGS)",
            quantity=rb_lm,
            unit="lm",
            source_evidence=struct.get("roof_batten_source", "bom/ifc"),
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))

    # Ceiling battens
    cb_lm = safe_float(struct.get("ceiling_batten_lm"))
    if cb_lm and cb_lm > 0 and "ceiling/ceiling_batten" not in cg:
        lib, _ = _find_library_match("ceiling batten LGS", item_library)
        boq_items.append(_make_item(
            boq_section="CEILING",
            description="Ceiling Batten (LGS)",
            quantity=cb_lm,
            unit="lm",
            source_evidence=struct.get("ceiling_batten_source", "bom/ifc"),
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))

    # Roof trusses — only if not already covered by quantity_model
    truss_qty = safe_float(struct.get("roof_truss_qty"))
    if truss_qty and truss_qty > 0 and "roof/roof_truss" not in cg:
        lib, _ = _find_library_match("roof truss LGS", item_library)
        boq_items.append(_make_item(
            boq_section="ROOF STRUCTURE",
            description="Roof Truss (LGS)",
            quantity=truss_qty,
            unit="lm",
            source_evidence=struct.get("roof_truss_source", "bom/ifc"),
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))

    # Floor panels
    fp_qty = safe_float(struct.get("floor_panel_qty"))
    if fp_qty and fp_qty > 0 and "floor/floor_panel" not in cg:
        lib, _ = _find_library_match("floor panel LGS", item_library)
        boq_items.append(_make_item(
            boq_section="FLOOR STRUCTURE",
            description="Floor Panel (LGS)",
            quantity=fp_qty,
            unit="nr",
            source_evidence=struct.get("floor_panel_source", "bom/ifc"),
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))

    # Floor joists
    fj_lm = safe_float(struct.get("floor_joist_lm"))
    if fj_lm and fj_lm > 0 and "floor/floor_joist" not in cg:
        lib, _ = _find_library_match("floor joist LGS", item_library)
        boq_items.append(_make_item(
            boq_section="FLOOR STRUCTURE",
            description="Floor Joist (LGS)",
            quantity=fj_lm,
            unit="lm",
            source_evidence="bom/ifc",
            confidence="HIGH",
            notes="From Framecad BOM",
            lib_entry=lib,
        ))


def _add_door_items(
    boq_items: list[dict],
    merged: dict,
    item_library: dict,
    covered_groups: set[str] | None = None,
) -> None:
    """Add individual door items from merged["doors"] schedule data.

    The quantity model emits a single "doors/door_count/all" total count.
    This function replaces that with per-type door counts if type information
    is available — otherwise it skips (the quantity model total is sufficient).
    """
    doors = merged.get("doors", [])
    if not doors:
        return
    cg = covered_groups or set()

    # Collect door types — check type, mark, and type_mapped keys
    type_counts: dict[str, int] = {}
    has_type_info = False
    for d in doors:
        dtype = str(
            d.get("type") or d.get("mark") or d.get("type_mapped") or ""
        ).strip()
        if dtype and dtype.lower() not in ("door", "unknown", ""):
            has_type_info = True
        key = dtype if dtype else "Door"
        type_counts[key] = type_counts.get(key, 0) + int(safe_float(d.get("qty")) or 1)

    # If quantity_model already has doors/door_count and we have no type breakdown,
    # skip — the quantity model total is sufficient
    if "doors/door_count" in cg and not has_type_info:
        return

    # If we have type info, emit per-type items (replaces the quantity model total)
    # If no type info but quantity_model didn't cover doors, emit total
    for dtype, cnt in type_counts.items():
        desc = f"Timber Door — {dtype}" if dtype not in ("Door", "unknown", "") else "Timber Door"
        lib, _ = _find_library_match(desc, item_library)
        boq_items.append(_make_item(
            boq_section="DOORS & WINDOWS",
            description=desc,
            quantity=float(cnt),
            unit="nr",
            source_evidence="dwg/pdf_schedule",
            confidence="HIGH",
            notes=f"Door type: {dtype}" if dtype not in ("Door", "") else "",
            lib_entry=lib,
        ))


def _add_window_items(
    boq_items: list[dict],
    merged: dict,
    item_library: dict,
    covered_groups: set[str] | None = None,
) -> None:
    """Add individual window items from merged["windows"] schedule data.

    The quantity model emits a single "windows/window_count/all" total count.
    This function replaces that with per-type window counts if type information
    is available — otherwise it skips (the quantity model total is sufficient).
    """
    windows = merged.get("windows", [])
    if not windows:
        return
    cg = covered_groups or set()

    # Collect window types — check type, mark, type_note, and type_mapped keys
    type_counts: dict[str, int] = {}
    has_type_info = False
    for w in windows:
        wtype = str(
            w.get("type") or w.get("mark") or w.get("type_note") or w.get("type_mapped") or ""
        ).strip()
        if wtype and wtype.lower() not in ("window", "unknown", ""):
            has_type_info = True
        key = wtype if wtype else "Window"
        type_counts[key] = type_counts.get(key, 0) + int(safe_float(w.get("qty")) or 1)

    # If quantity_model already has windows/window_count and we have no type breakdown,
    # skip — the quantity model total is sufficient
    if "windows/window_count" in cg and not has_type_info:
        return

    for wtype, cnt in type_counts.items():
        desc = f"Timber Window — {wtype}" if wtype not in ("Window", "unknown", "") else "Timber Window"
        lib, _ = _find_library_match(desc, item_library)
        boq_items.append(_make_item(
            boq_section="DOORS & WINDOWS",
            description=desc,
            quantity=float(cnt),
            unit="nr",
            source_evidence="dwg/pdf_schedule",
            confidence="HIGH",
            notes=f"Window type: {wtype}" if wtype not in ("Window", "") else "",
            lib_entry=lib,
        ))


def _add_finish_items(
    boq_items: list[dict],
    merged: dict,
    item_library: dict,
) -> None:
    """Add finish area items from merged["finishes"] PDF schedule data."""
    finishes = merged.get("finishes", [])
    if not finishes:
        return

    # Group finishes by floor_finish type
    floor_totals: dict[str, float] = {}
    wall_totals:  dict[str, float] = {}
    for f in finishes:
        area = safe_float(f.get("area_m2") or f.get("area") or 0)
        floor_f = str(f.get("floor_finish") or f.get("floor") or "").strip()
        wall_f  = str(f.get("wall_finish") or f.get("wall") or "").strip()
        if floor_f and area > 0:
            floor_totals[floor_f] = floor_totals.get(floor_f, 0.0) + area
        if wall_f and area > 0:
            wall_totals[wall_f] = wall_totals.get(wall_f, 0.0) + area

    for finish_type, total_area in floor_totals.items():
        desc = f"Floor Finish — {finish_type}"
        lib, _ = _find_library_match(desc, item_library)
        boq_items.append(_make_item(
            boq_section="FLOOR FINISHES",
            description=desc,
            quantity=round(total_area, 2),
            unit="m2",
            source_evidence="pdf_finish_schedule",
            confidence="MEDIUM",
            notes=f"Sum of rooms with {finish_type} floor finish",
            lib_entry=lib,
        ))

    for finish_type, total_area in wall_totals.items():
        desc = f"Wall Finish — {finish_type}"
        lib, _ = _find_library_match(desc, item_library)
        boq_items.append(_make_item(
            boq_section="WALL FINISHES",
            description=desc,
            quantity=round(total_area, 2),
            unit="m2",
            source_evidence="pdf_finish_schedule",
            confidence="MEDIUM",
            notes=f"Sum of rooms with {finish_type} wall finish",
            lib_entry=lib,
        ))


def _add_stair_items(
    boq_items: list[dict],
    merged: dict,
    item_library: dict,
) -> None:
    """Add stair items from merged["stairs"] data."""
    stairs_data = merged.get("stairs", [])
    if not stairs_data:
        return

    for stair in stairs_data:
        stair_type = str(stair.get("type") or stair.get("material") or "Timber").strip()
        flights    = int(safe_float(stair.get("flights") or stair.get("qty") or 1) or 1)
        steps      = int(safe_float(stair.get("steps") or stair.get("step_count") or 0) or 0)
        desc = f"Stair — {stair_type}" if stair_type not in ("Stair",) else "Stair"
        notes_parts = []
        if flights:
            notes_parts.append(f"{flights} flight(s)")
        if steps:
            notes_parts.append(f"{steps} steps")
        lib, _ = _find_library_match(desc, item_library)
        boq_items.append(_make_item(
            boq_section="STAIRS",
            description=desc,
            quantity=float(flights),
            unit="nr",
            source_evidence=str(stair.get("source") or "dwg/pdf"),
            confidence=str(stair.get("confidence") or "MEDIUM"),
            notes=", ".join(notes_parts) if notes_parts else "",
            lib_entry=lib,
        ))
