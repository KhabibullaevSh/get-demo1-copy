"""
qa_checker.py — Validates output, flags anomalies.

Automated checks:
  - No high-value section is entirely zero
  - Roof sheet count consistent with roof area
  - FC sheet count consistent with wall + ceiling area
  - Door/window count matches schedule
  - Finish areas cover total floor area
  - All structural items present if floor area > 0
  - Flag any item where qty increased > 50% vs standard
  - Flag any item where qty is 0 but standard was > 0
"""


def run_qa_checks(
    project_boq: list[dict],
    standard_boq: list[dict],
    project_geometry: dict,
    change_log: list[dict],
) -> list[dict]:
    """Run all QA checks on the project BOQ.

    Args:
        project_boq: Final project BOQ items.
        standard_boq: Original standard BOQ for comparison.
        project_geometry: Extracted project geometry.
        change_log: Change log from change_detector.

    Returns:
        List of QA flag dicts with: check, status (PASS/FAIL/WARN), details, items.
    """
    flags = []

    flags.extend(_check_zero_sections(project_boq))
    flags.extend(_check_roof_consistency(project_boq, project_geometry))
    flags.extend(_check_wall_consistency(project_boq, project_geometry))
    flags.extend(_check_door_window_counts(project_boq, project_geometry))
    flags.extend(_check_floor_coverage(project_boq, project_geometry))
    flags.extend(_check_structural_presence(project_boq, project_geometry))
    flags.extend(_check_large_increases(project_boq, standard_boq))
    flags.extend(_check_zero_replacements(project_boq, standard_boq))
    flags.extend(_check_rate_coverage(project_boq))
    flags.extend(_check_confidence_distribution(project_boq))

    return flags


# --- Individual QA Checks ---

SECTION_KEYWORDS = {
    "roof": ["roof", "roofing", "ridge", "purlin", "batten"],
    "walls": ["wall", "frame", "lining", "cladding", "plasterboard"],
    "floors": ["floor", "tile", "carpet", "vinyl", "slab"],
    "structure": ["post", "beam", "column", "bearer", "joist", "footing"],
    "plumbing": ["pipe", "plumbing", "tap", "toilet", "basin", "shower"],
    "electrical": ["cable", "wire", "switch", "outlet", "light", "electrical"],
}


def _check_zero_sections(boq: list[dict]) -> list[dict]:
    """Check that no major section has all-zero quantities."""
    flags = []

    for section, keywords in SECTION_KEYWORDS.items():
        section_items = []
        for item in boq:
            desc = str(item.get("description", "")).lower()
            if any(kw in desc for kw in keywords):
                section_items.append(item)

        if not section_items:
            continue

        total_qty = sum(
            float(item.get("qty", 0) or 0) for item in section_items
        )

        if total_qty == 0 and len(section_items) > 0:
            flags.append({
                "check": f"zero_section_{section}",
                "status": "FAIL",
                "details": (
                    f"Section '{section}' has {len(section_items)} items "
                    f"but ALL quantities are zero. This is likely an error."
                ),
                "items": [item.get("item_no", "") for item in section_items[:5]],
            })
        else:
            flags.append({
                "check": f"zero_section_{section}",
                "status": "PASS",
                "details": f"Section '{section}': {len(section_items)} items, total qty {total_qty:.1f}",
                "items": [],
            })

    return flags


def _check_roof_consistency(boq: list[dict], geometry: dict) -> list[dict]:
    """Check roof sheet count vs roof area."""
    roof_area = geometry.get("roof_area", 0)
    if not roof_area:
        return []

    # Find roof sheet items
    sheet_qty = 0
    for item in boq:
        desc = str(item.get("description", "")).lower()
        if "roof" in desc and ("sheet" in desc or "iron" in desc):
            sheet_qty += float(item.get("qty", 0) or 0)

    if sheet_qty == 0:
        return [{
            "check": "roof_sheet_consistency",
            "status": "FAIL",
            "details": f"Roof area is {roof_area} m² but no roof sheets found in BOQ.",
            "items": [],
        }]

    # Typical coverage: ~5-6 sheets per m² (0.76m wide x ~variable length)
    # Very rough check — warn if ratio is extremely off
    ratio = roof_area / max(sheet_qty, 1)
    if ratio > 5 or ratio < 0.1:
        return [{
            "check": "roof_sheet_consistency",
            "status": "WARN",
            "details": (
                f"Roof area {roof_area} m² with {sheet_qty} sheets "
                f"(ratio: {ratio:.2f} m²/sheet). Verify coverage calculation."
            ),
            "items": [],
        }]

    return [{
        "check": "roof_sheet_consistency",
        "status": "PASS",
        "details": f"Roof area {roof_area} m², {sheet_qty} sheets (ratio: {ratio:.2f})",
        "items": [],
    }]


def _check_wall_consistency(boq: list[dict], geometry: dict) -> list[dict]:
    """Check FC/plasterboard sheet count vs wall + ceiling area."""
    wall_length = geometry.get("total_wall_length", 0)
    ceiling_area = geometry.get("ceiling_area", 0)

    if not wall_length and not ceiling_area:
        return []

    # Estimate wall area (assume 2.7m height)
    wall_area = wall_length * 2.7
    total_lining_area = wall_area + ceiling_area

    # Find lining sheet items
    lining_qty = 0
    for item in boq:
        desc = str(item.get("description", "")).lower()
        if any(kw in desc for kw in ["plasterboard", "fibro", "fc sheet", "lining sheet", "gyprock"]):
            qty = float(item.get("qty", 0) or 0)
            unit = str(item.get("unit", "")).lower()
            if "m2" in unit or "m²" in unit:
                lining_qty += qty
            else:
                # Assume standard sheet ~2.4m x 1.2m = 2.88m²
                lining_qty += qty * 2.88

    if total_lining_area > 10 and lining_qty == 0:
        return [{
            "check": "wall_lining_consistency",
            "status": "WARN",
            "details": (
                f"Estimated lining area {total_lining_area:.0f} m² "
                f"but no lining sheets found in BOQ."
            ),
            "items": [],
        }]

    return [{
        "check": "wall_lining_consistency",
        "status": "PASS",
        "details": f"Lining area estimate {total_lining_area:.0f} m², sheets cover {lining_qty:.0f} m²",
        "items": [],
    }]


def _check_door_window_counts(boq: list[dict], geometry: dict) -> list[dict]:
    """Check door and window counts in BOQ match geometry."""
    flags = []

    for element, keyword in [("door_count", "door"), ("window_count", "window")]:
        geom_count = int(geometry.get(element, 0))
        boq_count = 0

        for item in boq:
            desc = str(item.get("description", "")).lower()
            unit = str(item.get("unit", "")).lower()
            if keyword in desc and unit in ("no", "ea", "each", "set"):
                # Only count the door/window units themselves, not hardware
                if any(hw in desc for hw in ["hinge", "handle", "lock", "screw", "closer"]):
                    continue
                boq_count += int(float(item.get("qty", 0) or 0))

        if geom_count > 0 and boq_count == 0:
            flags.append({
                "check": f"{keyword}_count_match",
                "status": "FAIL",
                "details": f"Geometry shows {geom_count} {keyword}s but BOQ has 0 {keyword} units.",
                "items": [],
            })
        elif abs(geom_count - boq_count) > 1:
            flags.append({
                "check": f"{keyword}_count_match",
                "status": "WARN",
                "details": (
                    f"Geometry shows {geom_count} {keyword}s, "
                    f"BOQ has {boq_count}. Difference: {abs(geom_count - boq_count)}"
                ),
                "items": [],
            })
        else:
            flags.append({
                "check": f"{keyword}_count_match",
                "status": "PASS",
                "details": f"{keyword.title()}s: geometry {geom_count}, BOQ {boq_count}",
                "items": [],
            })

    return flags


def _check_floor_coverage(boq: list[dict], geometry: dict) -> list[dict]:
    """Check that finish areas cover the total floor area."""
    floor_area = geometry.get("total_floor_area", 0)
    if not floor_area:
        return []

    finish_area = 0
    for item in boq:
        desc = str(item.get("description", "")).lower()
        unit = str(item.get("unit", "")).lower()
        if any(f in desc for f in ["floor finish", "tile", "carpet", "vinyl"]):
            if "m2" in unit or "m²" in unit:
                finish_area += float(item.get("qty", 0) or 0)

    if finish_area == 0 and floor_area > 5:
        return [{
            "check": "floor_finish_coverage",
            "status": "WARN",
            "details": f"Floor area {floor_area} m² but no floor finishes found in BOQ.",
            "items": [],
        }]

    coverage_pct = (finish_area / floor_area * 100) if floor_area > 0 else 0
    if coverage_pct < 80:
        return [{
            "check": "floor_finish_coverage",
            "status": "WARN",
            "details": (
                f"Floor finishes cover {finish_area:.1f} m² of {floor_area:.1f} m² "
                f"({coverage_pct:.0f}%). Possible gaps."
            ),
            "items": [],
        }]

    return [{
        "check": "floor_finish_coverage",
        "status": "PASS",
        "details": f"Floor finishes: {finish_area:.1f} m² of {floor_area:.1f} m² ({coverage_pct:.0f}%)",
        "items": [],
    }]


def _check_structural_presence(boq: list[dict], geometry: dict) -> list[dict]:
    """Check all structural items present if floor area > 0."""
    floor_area = geometry.get("total_floor_area", 0)
    if not floor_area:
        return []

    structural_count = 0
    for item in boq:
        desc = str(item.get("description", "")).lower()
        if any(kw in desc for kw in ["post", "beam", "bearer", "joist", "footing", "column"]):
            if float(item.get("qty", 0) or 0) > 0:
                structural_count += 1

    if structural_count == 0:
        return [{
            "check": "structural_presence",
            "status": "FAIL",
            "details": f"Floor area is {floor_area} m² but no structural items found in BOQ.",
            "items": [],
        }]

    return [{
        "check": "structural_presence",
        "status": "PASS",
        "details": f"Found {structural_count} structural items.",
        "items": [],
    }]


def _check_large_increases(project_boq: list[dict], standard_boq: list[dict]) -> list[dict]:
    """Flag any item where qty increased > 50% vs standard."""
    # Build standard qty lookup
    std_qtys = {}
    for item in standard_boq:
        key = item.get("stock_code") or item.get("description", "")
        try:
            std_qtys[key] = float(item.get("qty", 0) or 0)
        except (ValueError, TypeError):
            pass

    flagged = []
    for item in project_boq:
        key = item.get("stock_code") or item.get("description", "")
        if key not in std_qtys:
            continue

        std_qty = std_qtys[key]
        proj_qty = float(item.get("qty", 0) or 0)

        if std_qty > 0 and proj_qty > 0:
            pct_change = ((proj_qty - std_qty) / std_qty) * 100
            if pct_change > 50:
                flagged.append({
                    "item_no": item.get("item_no", ""),
                    "description": item.get("description", ""),
                    "standard_qty": std_qty,
                    "project_qty": proj_qty,
                    "pct_change": round(pct_change, 1),
                })

    if flagged:
        return [{
            "check": "large_qty_increase",
            "status": "WARN",
            "details": f"{len(flagged)} items have quantity increases > 50% vs standard.",
            "items": [f"{f['description']} (+{f['pct_change']}%)" for f in flagged[:10]],
        }]

    return [{
        "check": "large_qty_increase",
        "status": "PASS",
        "details": "No items with quantity increases > 50%.",
        "items": [],
    }]


def _check_zero_replacements(project_boq: list[dict], standard_boq: list[dict]) -> list[dict]:
    """Flag items where qty is 0 but standard was > 0."""
    std_qtys = {}
    for item in standard_boq:
        key = item.get("stock_code") or item.get("description", "")
        try:
            std_qtys[key] = float(item.get("qty", 0) or 0)
        except (ValueError, TypeError):
            pass

    flagged = []
    for item in project_boq:
        key = item.get("stock_code") or item.get("description", "")
        if key not in std_qtys:
            continue

        std_qty = std_qtys[key]
        proj_qty = float(item.get("qty", 0) or 0)

        if std_qty > 0 and proj_qty == 0:
            flagged.append({
                "item_no": item.get("item_no", ""),
                "description": item.get("description", ""),
                "standard_qty": std_qty,
            })

    if flagged:
        return [{
            "check": "zero_replacement",
            "status": "WARN",
            "details": (
                f"{len(flagged)} items have qty=0 where standard had qty>0. "
                f"Possible omissions."
            ),
            "items": [f"{f['description']} (was {f['standard_qty']})" for f in flagged[:10]],
        }]

    return [{
        "check": "zero_replacement",
        "status": "PASS",
        "details": "No items unexpectedly zeroed out.",
        "items": [],
    }]


def _check_rate_coverage(boq: list[dict]) -> list[dict]:
    """Check how many items have rates assigned."""
    total = len(boq)
    with_rate = sum(1 for item in boq if item.get("rate"))
    ai_rates = sum(1 for item in boq if item.get("rate_source") == "AI-estimate")
    manual = sum(1 for item in boq if item.get("rate_source") == "manual-required")

    coverage = (with_rate / max(total, 1)) * 100

    status = "PASS" if coverage >= 90 else "WARN" if coverage >= 70 else "FAIL"

    return [{
        "check": "rate_coverage",
        "status": status,
        "details": (
            f"Rate coverage: {with_rate}/{total} items ({coverage:.0f}%). "
            f"AI-estimated: {ai_rates}. Manual required: {manual}."
        ),
        "items": [],
    }]


def _check_confidence_distribution(boq: list[dict]) -> list[dict]:
    """Report on confidence level distribution."""
    dist = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "REVIEW": 0, "unknown": 0}
    for item in boq:
        conf = str(item.get("confidence", "unknown")).upper()
        dist[conf] = dist.get(conf, 0) + 1

    total = len(boq)
    high_pct = (dist["HIGH"] / max(total, 1)) * 100

    return [{
        "check": "confidence_distribution",
        "status": "PASS" if high_pct >= 60 else "WARN",
        "details": (
            f"Confidence: HIGH={dist['HIGH']}, MED={dist['MEDIUM']}, "
            f"LOW={dist['LOW']}, REVIEW={dist.get('REVIEW', 0)} "
            f"({high_pct:.0f}% high confidence)"
        ),
        "items": [],
    }]
