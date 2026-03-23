"""
completeness_checker.py — Check extraction completeness per BOQ package.

Returns per-package completeness metrics including measured / derived /
provisional / manual_review item counts, and notes on missing evidence.
"""
from __future__ import annotations

import logging

log = logging.getLogger("boq.v2.completeness_checker")

# Minimum expected items per package (for gap detection)
_EXPECTED_MINIMUMS: dict[str, int] = {
    "Structure": 3,
    "Roof":      4,
    "Openings":  2,
    "Linings":   2,
    "Finishes":  1,
    "Services":  0,   # often provisional
    "Stairs":    0,   # only if stair evidence found
    "External":  0,   # only if verandah found
}

# Evidence fields required per package
_REQUIRED_EVIDENCE: dict[str, list[str]] = {
    "Structure": ["floor_area_m2", "wall_stud_lm"],
    "Roof":      ["roof_area_m2"],
    "Openings":  ["door_count", "window_count"],
    "Linings":   ["ceiling_area_m2"],
    "Finishes":  ["floor_area_m2"],
    "Services":  [],
    "Stairs":    [],
    "External":  [],
}


def check_completeness(project_model: dict, quantity_model: dict) -> dict:
    """
    Returns per-package completeness dict.

    Structure of each package entry:
    {
      "detected":            bool,
      "items":               int,
      "measured_items":      int,
      "derived_items":       int,
      "provisional_items":   int,
      "manual_review_items": int,
      "notes":               str,
      "missing_evidence":    list[str],
    }
    """
    quantities = quantity_model.get("quantities", [])
    geometry   = project_model.get("geometry", {})
    structural = project_model.get("structural", {})
    openings   = project_model.get("openings", {})

    # Helper: extract scalar from provenance node
    def _val(section_dict: dict, key: str):
        node = section_dict.get(key, {})
        if isinstance(node, dict):
            return node.get("value")
        return node

    # All available evidence keys
    evidence_available: dict[str, bool] = {
        "floor_area_m2":        bool(_val(geometry,   "floor_area_m2")),
        "roof_area_m2":         bool(_val(geometry,   "roof_area_m2")),
        "ceiling_area_m2":      bool(_val(geometry,   "ceiling_area_m2")),
        "wall_stud_lm":         bool(_val(structural, "wall_stud_lm")),
        "door_count":           bool(_val(openings,   "door_count")),
        "window_count":         bool(_val(openings,   "window_count")),
        "verandah_area_m2":     bool(_val(geometry,   "verandah_area_m2")),
        "stair_evidence":       bool(_val(geometry,   "stair_evidence")),
    }

    packages = [
        "Structure", "Roof", "Openings", "Linings",
        "Finishes", "Services", "Stairs", "External",
    ]

    result: dict = {}
    for pkg in packages:
        pkg_rows = [r for r in quantities if r.get("item_group") == pkg]
        measured      = [r for r in pkg_rows if r.get("quantity_basis") == "measured"]
        derived       = [r for r in pkg_rows if r.get("quantity_basis") == "derived"]
        provisional   = [r for r in pkg_rows if r.get("quantity_basis") == "provisional"]
        manual_review = [r for r in pkg_rows if r.get("manual_review")]

        # Check for missing evidence
        missing: list[str] = []
        for evidence_key in _REQUIRED_EVIDENCE.get(pkg, []):
            if not evidence_available.get(evidence_key, False):
                missing.append(evidence_key)

        # Notes
        notes_parts: list[str] = []
        min_expected = _EXPECTED_MINIMUMS.get(pkg, 0)
        if len(pkg_rows) < min_expected:
            notes_parts.append(
                f"Only {len(pkg_rows)} items found; expected at least {min_expected}"
            )
        if missing:
            notes_parts.append(f"Missing evidence: {', '.join(missing)}")
        if provisional:
            notes_parts.append(f"{len(provisional)} provisional items require manual review")

        result[pkg] = {
            "detected":            len(pkg_rows) > 0,
            "items":               len(pkg_rows),
            "measured_items":      len(measured),
            "derived_items":       len(derived),
            "provisional_items":   len(provisional),
            "manual_review_items": len(manual_review),
            "notes":               "; ".join(notes_parts) if notes_parts else "OK",
            "missing_evidence":    missing,
        }

    log.info(
        "Completeness check: %s",
        {pkg: f"{v['items']} items" for pkg, v in result.items()},
    )
    return result
