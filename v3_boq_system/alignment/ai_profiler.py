"""
ai_profiler.py — Profiles the AI-generated BOQ JSON in the same vocabulary
as baseline_profiler.py, so results can be compared section-by-section.

The AI BOQ items are already rich with metadata (evidence_class,
quantity_status, manual_review, etc.) which we surface here as additional
profile fields not available in the baseline.

Output schema
-------------
{
  "sections": {
    "<commercial_package_code>": {
      "label": str,
      "row_count": int,
      "commercial_row_count": int,   # after consolidation (if applicable)
      "families": list[str],
      "family_counts": {str: int},
      "units_seen": {str: int},
      "dominant_unit": str,
      "provenance": {
        "measured": int,
        "calculated": int,
        "inferred": int,
        "placeholder": int,
        "manual_review": int,
      },
      "evidence_classes": {str: int},
      "style_flags": {
        "uses_stock_length_unit": bool,
        "uses_set_unit": bool,
        "uses_each": bool,
        "fixings_embedded": bool,
        "has_placeholder_rows": bool,
        "uses_lm_unit": bool,
        "uses_m2_unit": bool,
        "uses_nr_unit": bool,
        "uses_m3_unit": bool,
      }
    }
  },
  "global_flags": { ... },
  "source_file": str,
  "total_items": int,
  "manual_review_total": int,
}
"""

from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

from .family_classifier import classify


def _normalise_unit(raw: str | None) -> str:
    if not raw:
        return ""
    key = str(raw).strip().lower()
    _MAP = {
        "nr": "nr", "no": "nr", "each": "each", "ea": "each",
        "lm": "lm", "m": "lm", "meter": "lm", "metre": "lm",
        "m2": "m2", "m²": "m2", "sqm": "m2",
        "m3": "m3", "m³": "m3",
        "len": "len", "length": "len", "lengths": "len",
        "set": "set", "sets": "set",
        "pair": "pair", "pairs": "pair",
        "roll": "roll", "rolls": "roll",
        "bag": "bag", "bags": "bag",
        "pcs": "pcs", "pc": "pcs",
        "kg": "kg", "t": "t",
    }
    return _MAP.get(key, key)


def profile_ai_boq(
    boq_items_path: str | Path,
    package_label_map: dict[str, str] | None = None,
) -> dict:
    """Profile the AI-generated BOQ JSON at *boq_items_path*.

    Parameters
    ----------
    boq_items_path:
        Path to the ``project_boq_items_v3.json`` file.
    package_label_map:
        Optional dict mapping package_code → human label.  If None the
        label is derived from the ``boq_section_final`` field of each item.
    """
    path = Path(boq_items_path)
    with open(path, encoding="utf-8") as fh:
        items: list[dict] = json.load(fh)

    sections: dict[str, dict] = {}

    for item in items:
        code = str(item.get("commercial_package_code") or
                   item.get("package_code") or "unknown")

        # Derive label
        if package_label_map and code in package_label_map:
            label = package_label_map[code]
        else:
            final = item.get("boq_section_final", "")
            # "50107 - Structural …" → take everything after the dash
            parts = final.split(" - ", 1)
            label = parts[1].strip() if len(parts) == 2 else final

        if code not in sections:
            sections[code] = {
                "label": label,
                "row_count": 0,
                "family_counts": Counter(),
                "units_seen": Counter(),
                "provenance": Counter(),
                "evidence_classes": Counter(),
                "has_placeholder_rows": False,
                "manual_review_count": 0,
            }

        sec = sections[code]
        sec["row_count"] += 1

        # Description — prefer display name for classification, fall back to name
        desc = (item.get("item_display_name") or
                item.get("item_name") or "")
        family = classify(desc)
        sec["family_counts"][family] += 1

        unit = _normalise_unit(item.get("unit"))
        if unit:
            sec["units_seen"][unit] += 1

        status = item.get("quantity_status", "unknown")
        sec["provenance"][status] += 1

        ev = item.get("evidence_class", "unknown")
        sec["evidence_classes"][ev] += 1

        if item.get("manual_review"):
            sec["manual_review_count"] += 1
        if status == "placeholder":
            sec["has_placeholder_rows"] = True

    # Post-process
    result_sections: dict[str, dict] = {}
    for code, sec in sections.items():
        units = dict(sec["units_seen"])
        dominant_unit = max(units, key=units.get) if units else ""
        families_list = sorted(set(sec["family_counts"].keys()))

        fixing_families = {"screw_fixing", "bolt_fixing", "grommet"}
        fixings_embedded = bool(fixing_families & set(families_list))

        result_sections[code] = {
            "label": sec["label"],
            "row_count": sec["row_count"],
            "families": families_list,
            "family_counts": dict(sec["family_counts"]),
            "units_seen": units,
            "dominant_unit": dominant_unit,
            "provenance": dict(sec["provenance"]),
            "evidence_classes": dict(sec["evidence_classes"]),
            "manual_review_count": sec["manual_review_count"],
            "style_flags": {
                "uses_stock_length_unit": "len" in units,
                "uses_set_unit": "set" in units or "pair" in units,
                "uses_each": "each" in units,
                "fixings_embedded": fixings_embedded,
                "has_placeholder_rows": sec["has_placeholder_rows"],
                "uses_lm_unit": "lm" in units,
                "uses_m2_unit": "m2" in units,
                "uses_nr_unit": "nr" in units,
                "uses_m3_unit": "m3" in units,
            },
        }

    total_mr = sum(s["manual_review_count"] for s in result_sections.values())
    all_families = {f for s in result_sections.values() for f in s["families"]}
    codes = set(result_sections.keys())

    global_flags = {
        "fixings_standalone_section": any(
            "fixing" in s["label"].lower() or "fix" in s["label"].lower()
            for s in result_sections.values()
        ),
        "ffe_section_present": any(
            "ffe" in s["label"].lower() or "furniture" in s["label"].lower()
            for s in result_sections.values()
        ),
        "stairs_section_present": any(
            "stair" in s["label"].lower() for s in result_sections.values()
        ),
        "insulation_section_present": any(
            "insulation" in s["label"].lower() for s in result_sections.values()
        ),
        "electrical_section_present": any(
            "electrical" in s["label"].lower() for s in result_sections.values()
        ),
        "hydraulics_section_present": any(
            "hydraulic" in s["label"].lower() for s in result_sections.values()
        ),
    }

    return {
        "sections": result_sections,
        "global_flags": global_flags,
        "source_file": str(path),
        "total_items": len(items),
        "manual_review_total": total_mr,
    }
