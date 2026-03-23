"""
boq_mapper.py — Map neutral quantity model rows to BOQ output rows.

Key invariants:
  - quantity_basis and quantity_rule_used always come from the quantity model.
  - source_evidence traces back to IFC / DXF / BOM.
  - The item_library provides stock_code and description style ONLY.
  - No quantity is ever looked up from the item_library.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("boq.v2.boq_mapper")

# ─── Static section map ───────────────────────────────────────────────────────

_SECTION_MAP: dict[str, str] = {
    "Structure": "A - Structural Frame",
    "Roof":      "B - Roof",
    "Openings":  "C - Openings",
    "Linings":   "D - Linings & Ceilings",
    "Finishes":  "E - Finishes",
    "Services":  "F - Services",
    "Stairs":    "G - Stairs",
    "External":  "H - External Works",
}

# ─── helpers ──────────────────────────────────────────────────────────────────

def _lookup_stock_code(item_library: dict, keywords: list[str]) -> str:
    for item in item_library.get("items", []):
        desc_lower = item.get("description", "").lower()
        if all(kw.lower() in desc_lower for kw in keywords):
            return item.get("stock_code", "") or ""
    return ""


def _lookup_description(item_library: dict, keywords: list[str], fallback: str) -> str:
    for item in item_library.get("items", []):
        desc_lower = item.get("description", "").lower()
        if all(kw.lower() in desc_lower for kw in keywords):
            return item.get("description", fallback)
    return fallback


# ─── main mapper ──────────────────────────────────────────────────────────────

def map_to_boq_items(
    quantity_model: dict,
    item_library:   dict,
) -> list[dict]:
    """
    Map quantity model rows to BOQ output items.

    Returns list of BOQ item dicts ready for writing to Excel / JSON.
    Each item carries full provenance.  No quantities are sourced from item_library.
    """
    boq_items: list[dict] = []
    item_no = 1

    for qrow in quantity_model.get("quantities", []):
        pkg        = qrow.get("item_group", "")
        etype      = qrow.get("element_type", "")
        subtype    = qrow.get("subtype", "")
        qty        = qrow.get("quantity", 0)
        unit       = qrow.get("unit", "")
        basis      = qrow.get("quantity_basis", "provisional")
        rule       = qrow.get("quantity_rule_used", "")
        evidence   = qrow.get("source_evidence", "")
        confidence = qrow.get("confidence", "LOW")
        assumption = qrow.get("assumption", "")
        manual_rev = qrow.get("manual_review", False)
        ext_src    = qrow.get("v2_extractor_source", "")

        # Look up stock code and description from reference library (no quantities)
        keywords = [w for w in (etype.split() + subtype.split()) if len(w) > 3]
        stock_code  = _lookup_stock_code(item_library, keywords[:2]) if keywords else ""
        description = f"{etype} — {subtype}" if subtype else etype

        section = _SECTION_MAP.get(pkg, pkg)

        notes_parts: list[str] = []
        if assumption:
            notes_parts.append(assumption)
        if manual_rev:
            notes_parts.append("MANUAL REVIEW REQUIRED")
        notes = "; ".join(notes_parts)

        boq_items.append({
            "item_no":             item_no,
            "boq_section":         section,
            "stock_code":          stock_code,
            "description":         description,
            "unit":                unit,
            "quantity":            qty,
            "rate_pgk":            None,    # pricing is a separate step
            "amount_pgk":          None,
            "quantity_basis":      basis,
            "quantity_rule_used":  rule,
            "source_evidence":     evidence,
            "confidence":          confidence,
            "notes":               notes,
            "manual_review":       manual_rev,
            "v2_extractor_source": ext_src,
        })
        item_no += 1

    log.info(
        "BOQ mapper: %d items across %d sections",
        len(boq_items),
        len({i["boq_section"] for i in boq_items}),
    )
    return boq_items
