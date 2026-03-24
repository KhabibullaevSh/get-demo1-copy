"""
boq_mapper.py — Map raw quantifier rows to final BOQ output items.

Responsibilities:
  1. Assign BOQ section letter/name based on package tag
  2. Look up stock codes from item library (reference only)
  3. Assign sequential item numbers
  4. Normalise units
  5. Tag manual review items

CRITICAL: Quantities are NEVER sourced from the item library.
The item library provides: stock_code, description style, unit.  That is all.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("boq.v3.boq_mapper")

# ── Section map — package tag → BOQ section ───────────────────────────────────
# Package tags are set by the quantifier modules.

_SECTION_MAP: dict[str, str] = {
    # Structural
    "structural_frame":        "A - Structural Frame",
    "roof_battens":            "A - Structural Frame",

    # Roof
    "roof_cladding":           "B - Roof",
    "roof_ridge_accessories":  "B - Roof",
    "roof_eaves_drainage":     "B - Roof",
    "roof_flashings":          "B - Roof",
    "insulation":              "C - Insulation",

    # Openings
    "openings_doors":          "D - Openings",
    "openings_windows":        "D - Openings",
    "openings_door_hardware":  "D - Openings",
    "openings_window_hardware": "D - Openings",
    "openings_finishes":       "F - Finishes",

    # Linings
    "wall_lining_external":    "E - Linings & Ceilings",
    "wall_lining_internal":    "E - Linings & Ceilings",
    "wall_lining_wet":         "E - Linings & Ceilings",
    "ceiling_lining":          "E - Linings & Ceilings",
    "ceiling_trim":            "E - Linings & Ceilings",

    # Finishes
    "finishes":                "F - Finishes",
    "finishes_trim":           "F - Finishes",

    # Floor
    "floor_system":            "G - Floor System",

    # Footings / substructure
    "footings":                "H - Substructure",

    # Services
    "services":                "I - Services",

    # Stairs / Balustrades
    "stairs":                  "J - Stairs",
    "external_balustrade":     "K - External Works",

    # External
    "external_works":          "K - External Works",
    "external_verandah":       "K - External Works",
    "external_cladding":       "K - External Works",
}

_DEFAULT_SECTION = "Z - Unclassified"


def _map_section(package: str) -> str:
    return _SECTION_MAP.get(package, _DEFAULT_SECTION)


def _lookup_stock_code(item_library: dict, keywords: list[str]) -> str:
    """Look up stock code from item library by keyword match (reference only)."""
    for item in item_library.get("items", []):
        desc_lower = item.get("description", "").lower()
        if all(kw.lower() in desc_lower for kw in keywords):
            return item.get("stock_code", "") or ""
    return ""


def map_to_boq(
    quantity_rows: list[dict],
    item_library:  dict,
) -> list[dict]:
    """
    Map raw quantifier rows to final BOQ items.

    Args:
        quantity_rows: Raw rows from all quantifiers (package-tagged dicts)
        item_library:  Loaded item library (stock codes only — no quantities used)

    Returns:
        List of final BOQ item dicts with section, item_no, stock_code.
    """
    boq_items: list[dict] = []
    item_counter: dict[str, int] = {}   # section → count

    for row in quantity_rows:
        package  = row.get("package", "")
        section  = _map_section(package)

        # Section item counter
        item_counter[section] = item_counter.get(section, 0) + 1
        item_no = f"{section.split(' - ')[0]}{item_counter[section]:02d}"

        # Stock code lookup (reference only)
        name_keywords = row.get("item_name", "").lower().split()[:3]
        stock_code = row.get("item_code") or _lookup_stock_code(item_library, name_keywords)

        boq_item: dict = {
            "item_no":         item_no,
            "boq_section":     section,
            "package":         package,
            "item_name":       row.get("item_name", ""),
            "item_code":       stock_code,
            "unit":            row.get("unit", "nr"),
            "quantity":        row.get("quantity", 0),
            "rate_pgk":        None,
            "amount_pgk":      None,

            # Traceability
            "quantity_status":  row.get("quantity_status", "placeholder"),
            "quantity_basis":   row.get("quantity_basis", ""),
            "source_evidence":  row.get("source_evidence", ""),
            "derivation_rule":  row.get("derivation_rule", ""),
            "confidence":       row.get("confidence", "LOW"),
            "manual_review":    row.get("manual_review", False),
            "notes":            row.get("notes", ""),
        }

        # Append MANUAL REVIEW flag to notes if needed
        if boq_item["manual_review"] and "MANUAL REVIEW" not in boq_item["notes"].upper():
            boq_item["notes"] = ("MANUAL REVIEW REQUIRED. " + boq_item["notes"]).strip()

        boq_items.append(boq_item)

    log.info(
        "BOQ mapper: %d rows → %d sections",
        len(boq_items),
        len(set(i["boq_section"] for i in boq_items)),
    )
    return boq_items
