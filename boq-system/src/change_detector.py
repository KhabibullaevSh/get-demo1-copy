"""
change_detector.py — Compares project geometry vs standard geometry.

Produces a change_log with delta calculations for each element.
This is the core of the delta engine: unchanged items pass through,
changed items get flagged for recalculation.
"""


# Threshold for considering a value "changed" (avoids floating-point noise)
CHANGE_THRESHOLD = 0.01  # 1% minimum delta to count as changed
ABSOLUTE_THRESHOLD = 0.05  # Absolute minimum difference


def detect_changes(
    project_geometry: dict,
    standard_geometry: dict,
) -> list[dict]:
    """Compare project geometry against standard geometry and produce a change log.

    Args:
        project_geometry: Extracted geometry from the project DXF.
        standard_geometry: Baseline geometry from standard_model_G303.xlsx.

    Returns:
        List of change_log entries, each containing:
          - element: name of the geometry element
          - standard_value: value from standard model
          - project_value: value from project
          - delta: absolute difference
          - delta_pct: percentage change
          - changed: bool flag
          - impact: description of what this change affects
    """
    change_log = []

    # Define elements to compare and their impact descriptions
    comparison_elements = {
        "total_floor_area": {
            "unit": "m²",
            "impact": "Affects: floor finishes, floor structure, skirting, underlay",
        },
        "total_wall_length": {
            "unit": "lm",
            "impact": "Affects: wall frames, linings, external cladding, paint",
        },
        "external_wall_length": {
            "unit": "lm",
            "impact": "Affects: external cladding, weatherboard, flashing",
        },
        "internal_wall_length": {
            "unit": "lm",
            "impact": "Affects: internal linings, plasterboard, paint",
        },
        "roof_area": {
            "unit": "m²",
            "impact": "Affects: roofing sheets, battens, underlay, insulation, gutters",
        },
        "roof_perimeter": {
            "unit": "lm",
            "impact": "Affects: fascia, barge boards, guttering, downpipes",
        },
        "verandah_area": {
            "unit": "m²",
            "impact": "Affects: verandah decking, bearers, joists, posts",
        },
        "ceiling_area": {
            "unit": "m²",
            "impact": "Affects: ceiling linings, battens, insulation",
        },
        "door_count": {
            "unit": "no",
            "impact": "Affects: door units, frames, hardware, hinges, handles",
        },
        "window_count": {
            "unit": "no",
            "impact": "Affects: window units, frames, glazing, flashings",
        },
        "post_count": {
            "unit": "no",
            "impact": "Affects: posts, footings, brackets, bolts",
        },
        "stair_count": {
            "unit": "no",
            "impact": "Affects: stair stringers, treads, risers, handrails, balusters",
        },
        "room_count": {
            "unit": "no",
            "impact": "Affects: room-specific finishes, doors, electrical points",
        },
    }

    for element, info in comparison_elements.items():
        std_val = _get_numeric(standard_geometry, element)
        proj_val = _get_numeric(project_geometry, element)

        delta = proj_val - std_val
        delta_pct = _calc_percentage(delta, std_val)
        changed = _is_changed(delta, delta_pct)

        change_log.append({
            "element": element,
            "standard_value": std_val,
            "project_value": proj_val,
            "unit": info["unit"],
            "delta": round(delta, 4),
            "delta_pct": round(delta_pct, 2),
            "changed": changed,
            "impact": info["impact"] if changed else "No impact — unchanged",
        })

    # Detect type-level changes for doors and windows
    door_type_changes = _detect_type_changes(
        project_geometry.get("door_types", {}),
        standard_geometry.get("door_types", {}),
        "door",
    )
    change_log.extend(door_type_changes)

    window_type_changes = _detect_type_changes(
        project_geometry.get("window_types", {}),
        standard_geometry.get("window_types", {}),
        "window",
    )
    change_log.extend(window_type_changes)

    return change_log


def _get_numeric(geometry: dict, key: str) -> float:
    """Safely extract a numeric value from geometry dict."""
    val = geometry.get(key, 0)
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _calc_percentage(delta: float, base: float) -> float:
    """Calculate percentage change, handling zero base."""
    if abs(base) < 0.0001:
        return 100.0 if abs(delta) > ABSOLUTE_THRESHOLD else 0.0
    return (delta / base) * 100.0


def _is_changed(delta: float, delta_pct: float) -> bool:
    """Determine if a delta constitutes a real change."""
    if abs(delta) < ABSOLUTE_THRESHOLD:
        return False
    if abs(delta_pct) < (CHANGE_THRESHOLD * 100):
        return False
    return True


def _detect_type_changes(
    project_types: dict,
    standard_types: dict,
    category: str,
) -> list[dict]:
    """Detect changes at the type level (e.g., door type A: 4 → 6)."""
    changes = []
    all_types = set(list(project_types.keys()) + list(standard_types.keys()))

    for type_name in sorted(all_types):
        std_count = standard_types.get(type_name, 0)
        proj_count = project_types.get(type_name, 0)

        try:
            std_count = int(std_count)
            proj_count = int(proj_count)
        except (ValueError, TypeError):
            continue

        delta = proj_count - std_count
        delta_pct = _calc_percentage(float(delta), float(std_count))
        changed = delta != 0

        if changed:
            if std_count == 0:
                status = "NEW"
                impact = f"New {category} type added to project"
            elif proj_count == 0:
                status = "REMOVED"
                impact = f"{category.title()} type removed from project"
            else:
                status = "CHANGED"
                impact = f"{category.title()} count changed — affects units, hardware, fixings"

            changes.append({
                "element": f"{category}_type_{type_name}",
                "standard_value": std_count,
                "project_value": proj_count,
                "unit": "no",
                "delta": delta,
                "delta_pct": round(delta_pct, 2),
                "changed": True,
                "impact": impact,
                "status": status,
            })

    return changes


def get_changed_elements(change_log: list[dict]) -> list[dict]:
    """Filter change_log to only changed elements."""
    return [entry for entry in change_log if entry.get("changed", False)]


def get_unchanged_elements(change_log: list[dict]) -> list[dict]:
    """Filter change_log to only unchanged elements."""
    return [entry for entry in change_log if not entry.get("changed", False)]


def summarise_changes(change_log: list[dict]) -> dict:
    """Produce a summary of all changes detected."""
    changed = get_changed_elements(change_log)
    unchanged = get_unchanged_elements(change_log)

    return {
        "total_elements_compared": len(change_log),
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "change_percentage": round(
            len(changed) / max(len(change_log), 1) * 100, 1
        ),
        "changed_elements": [c["element"] for c in changed],
        "largest_delta": max(changed, key=lambda x: abs(x["delta_pct"]))
        if changed else None,
    }
