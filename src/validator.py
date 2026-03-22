"""
validator.py — AI-assisted and rule-based validation of merged project data.
"""

from __future__ import annotations
import logging
import math
from typing import Any

from src import ai_client
from src.config import (
    BATTEN_CEILING_SPACING_MM, BATTEN_ROOF_SPACING_MM,
    SHEET_AREA_FC, DEFAULT_CONFLICT_TOLERANCE, Confidence,
)
from src.utils import safe_float, save_json
from src.config import OUTPUT_REPORTS

log = logging.getLogger("boq.validator")

_SYSTEM = ai_client.SYSTEM_PROMPT_MASTER

_PROMPT_VALIDATE = """You are reviewing extracted drawing data for BOQ preparation.

Validate consistency and identify risk before quantities are written into the final BOQ.
Use conservative estimator logic. Return JSON only.

Perform these checks:
1. Internal consistency checks
2. Cross-source checks
3. BOQ risk checks
4. Missing-scope checks

Required output schema:
{
  "validated_items": [
    {"item_group": "", "item_name": "", "recommended_value": null, "unit": "",
     "preferred_source": "", "supporting_sources": [], "confidence": "", "comment": ""}
  ],
  "conflicts": [
    {"item_group": "", "item_name": "", "source_a": "", "value_a": "",
     "source_b": "", "value_b": "", "severity": "LOW|MEDIUM|HIGH", "recommended_action": ""}
  ],
  "missing_scope": [
    {"category": "", "description": "", "risk": "LOW|MEDIUM|HIGH"}
  ],
  "relationship_checks": [
    {"check_name": "", "status": "PASS|FAIL|WARNING", "details": ""}
  ],
  "overall_notes": []
}

Perform these relationship checks where data exists:
- Door/window schedule totals vs plan evidence
- Stair riser count vs floor level change
- Ceiling area vs floor area
- Roof battens vs roof area and spacing
- Ceiling battens vs ceiling area and spacing
- FC sheet quantities vs wall lining areas
- Finishes coverage for all major room types
- Wet areas differentiated from general rooms
- Structural items defined by BOM/IFC/schedule or only inferred

Rules:
- Do not invent corrected quantities.
- Explain why something appears wrong and what source should govern.
- Distinguish between "conflict", "missing", and "not enough information"."""


def validate(merged: dict, project_args: dict | None = None) -> dict[str, Any]:
    """Run rule-based + AI validation on merged data.

    Returns validation result dict.
    """
    result: dict[str, Any] = {
        "validated_items": [],
        "conflicts": [],
        "missing_scope": [],
        "relationship_checks": [],
        "overall_notes": [],
        "warnings": [],
    }

    # Always run rule-based checks (no AI needed)
    _rule_checks(merged, result)

    # Add conflicts from merger
    result["conflicts"].extend(merged.get("conflicts", []))

    # AI validation if available
    if ai_client.is_available():
        _ai_validate(merged, result, project_args)

    log.info(
        "Validation: %d checks, %d conflicts, %d missing scope items",
        len(result["relationship_checks"]),
        len(result["conflicts"]),
        len(result["missing_scope"]),
    )
    return result


# ─── Rule-based checks ────────────────────────────────────────────────────────

def _rule_checks(merged: dict, result: dict) -> None:
    geo = merged.get("geometry", {})
    struct = merged.get("structural", {})

    floor_area = safe_float(geo.get("total_floor_area_m2"))
    ceiling_area = safe_float(geo.get("ceiling_area_m2"))
    roof_area = safe_float(geo.get("roof_area_m2"))
    ext_wall_area = safe_float(geo.get("external_wall_area_m2"))
    int_wall_area = safe_float(geo.get("internal_wall_area_m2"))

    checks = result["relationship_checks"]

    # 1. Ceiling ≈ floor area
    if floor_area and ceiling_area:
        ratio = ceiling_area / floor_area
        status = "PASS" if 0.60 <= ratio <= 1.10 else "WARNING"
        checks.append({
            "check_name": "ceiling_vs_floor_area",
            "status": status,
            "details": f"ceiling={ceiling_area:.1f}m² floor={floor_area:.1f}m² ratio={ratio:.2f}",
        })

    # 2. Roof battens vs roof area
    roof_batten_lm = safe_float(struct.get("roof_batten_lm"))
    if roof_area and roof_batten_lm:
        expected = math.ceil(roof_area / (BATTEN_ROOF_SPACING_MM / 1000))
        ratio = roof_batten_lm / expected if expected else 0
        status = "PASS" if 0.80 <= ratio <= 1.30 else "WARNING"
        checks.append({
            "check_name": "roof_batten_vs_area",
            "status": status,
            "details": f"actual={roof_batten_lm:.0f}lm  expected≈{expected:.0f}lm  ratio={ratio:.2f}",
        })
    elif roof_area and not roof_batten_lm:
        result["missing_scope"].append({
            "category": "roof_battens",
            "description": "Roof batten quantity not confirmed (not in BOM/schedule)",
            "risk": "MEDIUM",
        })

    # 3. Ceiling battens vs ceiling area
    ceil_batten_lm = safe_float(struct.get("ceiling_batten_lm"))
    if ceiling_area and ceil_batten_lm:
        expected = math.ceil(ceiling_area / (BATTEN_CEILING_SPACING_MM / 1000))
        ratio = ceil_batten_lm / expected if expected else 0
        status = "PASS" if 0.80 <= ratio <= 1.30 else "WARNING"
        checks.append({
            "check_name": "ceiling_batten_vs_area",
            "status": status,
            "details": f"actual={ceil_batten_lm:.0f}lm  expected≈{expected:.0f}lm  ratio={ratio:.2f}",
        })
    elif ceiling_area and not ceil_batten_lm:
        result["missing_scope"].append({
            "category": "ceiling_battens",
            "description": "Ceiling batten quantity not confirmed (not in BOM/schedule)",
            "risk": "MEDIUM",
        })

    # 4. FC sheets vs wall area
    fc_items = [i for i in struct.get("bom_raw", [])
                if "fc" in (i.get("description") or "").lower()
                or "fibre" in (i.get("description") or "").lower()]
    total_wall_area = (ext_wall_area or 0) + (int_wall_area or 0)
    if not fc_items and total_wall_area > 0:
        result["missing_scope"].append({
            "category": "fc_sheets",
            "description": "FC sheet lining not found in BOM — confirm from schedule or lining notes",
            "risk": "HIGH",
        })

    # 5. Structural items data quality
    has_bom = len(struct.get("bom_raw", [])) > 0
    if not has_bom:
        result["missing_scope"].append({
            "category": "structural_framing",
            "description": "No BOM/IFC found — structural quantities will be estimated/derived",
            "risk": "HIGH",
        })

    # 6. Finishes coverage
    finishes = merged.get("finishes", [])
    if not finishes:
        result["missing_scope"].append({
            "category": "finishes",
            "description": "No finish schedule found in PDFs",
            "risk": "MEDIUM",
        })

    # 7. Stairs
    stairs = merged.get("stairs", [])
    if not stairs:
        highset = merged.get("metadata", {}).get("highset")
        if highset:
            result["missing_scope"].append({
                "category": "stairs",
                "description": "Highset project but no stair detail found",
                "risk": "HIGH",
            })

    # 8. Doors/windows
    doors = merged.get("doors", [])
    windows = merged.get("windows", [])
    if not doors:
        result["missing_scope"].append({
            "category": "doors",
            "description": "No door schedule or door counts found",
            "risk": "HIGH",
        })
    if not windows:
        result["missing_scope"].append({
            "category": "windows",
            "description": "No window schedule or window counts found",
            "risk": "HIGH",
        })


# ─── AI validation ────────────────────────────────────────────────────────────

def _ai_validate(merged: dict, result: dict, project_args: dict | None) -> None:
    """Send merged data summary to AI for additional validation."""
    import json
    summary = {
        "geometry": merged.get("geometry", {}),
        "doors_count": len(merged.get("doors", [])),
        "windows_count": len(merged.get("windows", [])),
        "finishes_count": len(merged.get("finishes", [])),
        "stairs_count": len(merged.get("stairs", [])),
        "structural_summary": {k: v for k, v in merged.get("structural", {}).items()
                               if k != "bom_raw"},
        "metadata": merged.get("metadata", {}),
        "existing_conflicts": merged.get("conflicts", []),
    }

    user_prompt = (
        _PROMPT_VALIDATE
        + f"\n\nProject data summary:\n{json.dumps(summary, indent=2, default=str)}"
    )

    ai_result = ai_client.call_json(
        user_prompt=user_prompt,
        system_prompt=_SYSTEM,
        label="validator",
        max_tokens=4096,
    )

    if ai_result and isinstance(ai_result, dict):
        result["validated_items"].extend(ai_result.get("validated_items") or [])
        result["conflicts"].extend(ai_result.get("conflicts") or [])
        result["missing_scope"].extend(ai_result.get("missing_scope") or [])
        result["relationship_checks"].extend(ai_result.get("relationship_checks") or [])
        result["overall_notes"].extend(ai_result.get("overall_notes") or [])
    else:
        result["warnings"].append("AI validation returned no result")
