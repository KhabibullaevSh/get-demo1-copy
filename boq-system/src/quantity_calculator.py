"""
quantity_calculator.py — Recalculates quantities for changed items.

For each item in the standard BOQ:
  A. UNCHANGED → copy qty directly (confidence: HIGH)
  B. CHANGED   → recalculate using rules library + project geometry (confidence: MED)
  C. REMOVED   → mark as 0, flag in output

Optionally asks GPT-4o to identify NEW items not in the standard BOQ.
"""

import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# Mapping from geometry elements to BOQ item keywords
ELEMENT_TO_KEYWORDS = {
    "total_floor_area": [
        "floor", "flooring", "tile", "carpet", "vinyl", "skirting",
        "underlay", "floor finish", "slab",
    ],
    "total_wall_length": [
        "wall", "frame", "lining", "plasterboard", "gyprock",
        "paint", "cladding",
    ],
    "external_wall_length": [
        "external", "weatherboard", "cladding", "ext wall",
        "external lining", "wrap", "building paper",
    ],
    "internal_wall_length": [
        "internal", "int wall", "internal lining", "partition",
    ],
    "roof_area": [
        "roof", "roofing", "batten", "purlin", "underlay",
        "insulation", "ridge", "roof sheet",
    ],
    "roof_perimeter": [
        "fascia", "barge", "gutter", "downpipe", "soffit", "eaves",
    ],
    "verandah_area": [
        "verandah", "veranda", "deck", "decking", "porch",
    ],
    "ceiling_area": [
        "ceiling", "ceil", "ceiling batten", "ceiling lining",
    ],
    "door_count": [
        "door", "door frame", "hinge", "handle", "lock", "door hardware",
    ],
    "window_count": [
        "window", "glazing", "window frame", "louvre",
    ],
    "post_count": [
        "post", "column", "pier", "footing", "stirrup",
    ],
    "stair_count": [
        "stair", "tread", "riser", "stringer", "handrail", "baluster",
    ],
}


def calculate_quantities(
    standard_boq: list[dict],
    change_log: list[dict],
    project_geometry: dict,
    rules: list[dict],
    pdf_data: dict | None = None,
) -> list[dict]:
    """Build the project BOQ by recalculating changed items.

    Args:
        standard_boq: List of standard BOQ items from loader.
        change_log: Change log from change_detector.
        project_geometry: Extracted project geometry.
        rules: Rules library from standard model.
        pdf_data: Optional PDF extraction data.

    Returns:
        List of project BOQ items with confidence and source tags.
    """
    # Build lookup of changed elements
    changed_elements = {}
    for entry in change_log:
        if entry.get("changed", False):
            changed_elements[entry["element"]] = entry

    project_boq = []

    for item in standard_boq:
        boq_item = dict(item)  # Copy all fields

        # Determine if this item is affected by any change
        affected_by = _find_affected_element(item, changed_elements)

        if affected_by is None:
            # UNCHANGED — copy directly
            boq_item["confidence"] = "HIGH"
            boq_item["source"] = "standard"
            boq_item["notes"] = boq_item.get("notes", "") or ""
        else:
            # CHANGED — recalculate
            change = changed_elements[affected_by]
            new_qty = _recalculate_qty(item, change, project_geometry, rules)

            if new_qty == 0 and item.get("qty", 0) > 0:
                # REMOVED
                boq_item["qty"] = 0
                boq_item["confidence"] = "MEDIUM"
                boq_item["source"] = "removed"
                boq_item["notes"] = (
                    f"Removed: {affected_by} no longer applicable. "
                    f"Standard was {item.get('qty', 0)}"
                )
            else:
                boq_item["qty"] = new_qty
                boq_item["confidence"] = "MEDIUM"
                boq_item["source"] = "recalculated"
                boq_item["notes"] = (
                    f"Recalculated: {affected_by} changed by "
                    f"{change['delta_pct']:+.1f}%. "
                    f"Standard was {item.get('qty', 0)}, now {new_qty}"
                )

        project_boq.append(boq_item)

    return project_boq


def discover_new_items(
    project_boq: list[dict],
    change_log: list[dict],
    project_geometry: dict,
    pdf_data: dict | None = None,
) -> list[dict]:
    """Use GPT-4o to identify NEW items not in the standard BOQ.

    Args:
        project_boq: Current project BOQ (after recalculation).
        change_log: Change log from change_detector.
        project_geometry: Extracted project geometry.
        pdf_data: Optional PDF extraction data.

    Returns:
        List of new BOQ items to append.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []

    client = OpenAI(api_key=api_key)

    existing_descriptions = [
        item.get("description", "") for item in project_boq
        if item.get("description")
    ]

    changes_summary = [
        f"- {c['element']}: {c['standard_value']} → {c['project_value']} ({c['delta_pct']:+.1f}%)"
        for c in change_log if c.get("changed")
    ]

    prompt = f"""You are a quantity surveyor for construction projects in Papua New Guinea.

Given these changes from the standard G303 3-bedroom house design:
{chr(10).join(changes_summary) if changes_summary else "No major changes detected."}

Project geometry:
- Floor area: {project_geometry.get('total_floor_area', 0)} m²
- Wall length: {project_geometry.get('total_wall_length', 0)} lm
- Roof area: {project_geometry.get('roof_area', 0)} m²
- Doors: {project_geometry.get('door_count', 0)}
- Windows: {project_geometry.get('window_count', 0)}

These items are already in the BOQ:
{chr(10).join(existing_descriptions[:50])}

What additional BOQ items might be needed that are NOT in the existing list?
Consider items that would be required due to the changes detected.

Return JSON array only:
[{{"description": "...", "unit": "...", "estimated_qty": 0, "reason": "..."}}]

Only suggest items you are confident are needed. Return [] if no new items are needed."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.2,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        new_items_raw = json.loads(content)

    except Exception:
        return []

    # Convert to BOQ item format
    new_items = []
    for i, raw in enumerate(new_items_raw):
        new_items.append({
            "item_no": f"NEW-{i + 1:03d}",
            "stock_code": "",
            "description": raw.get("description", "Unknown item"),
            "unit": raw.get("unit", "no"),
            "qty": raw.get("estimated_qty", 0),
            "rate": None,
            "confidence": "LOW",
            "source": "new",
            "notes": f"AI-discovered: {raw.get('reason', 'Change-driven')}",
        })

    return new_items


def _find_affected_element(item: dict, changed_elements: dict) -> str | None:
    """Determine which changed element (if any) affects this BOQ item."""
    description = str(item.get("description", "")).lower()
    category = str(item.get("category", "")).lower()

    for element, keywords in ELEMENT_TO_KEYWORDS.items():
        if element not in changed_elements:
            continue
        for keyword in keywords:
            if keyword in description or keyword in category:
                return element

    return None


def _recalculate_qty(
    item: dict,
    change: dict,
    project_geometry: dict,
    rules: list[dict],
) -> float:
    """Recalculate quantity for a changed item.

    Strategy:
      1. Check if a specific rule exists for this item
      2. If rule found, apply the formula
      3. If no rule, scale proportionally by the geometry change
    """
    # Try to find a matching rule
    rule = _find_matching_rule(item, rules)

    if rule:
        return _apply_rule(rule, project_geometry)

    # Fallback: proportional scaling
    standard_qty = item.get("qty", 0)
    try:
        standard_qty = float(standard_qty) if standard_qty else 0.0
    except (ValueError, TypeError):
        return 0.0

    if standard_qty == 0:
        return 0.0

    delta_pct = change.get("delta_pct", 0.0)
    scale_factor = 1.0 + (delta_pct / 100.0)
    new_qty = standard_qty * scale_factor

    return round(new_qty, 2)


def _find_matching_rule(item: dict, rules: list[dict]) -> dict | None:
    """Find a rule that applies to this BOQ item."""
    item_code = str(item.get("stock_code", "")).strip()
    item_desc = str(item.get("description", "")).lower()

    for rule in rules:
        target = str(rule.get("target_item", "")).strip()
        if not target:
            continue
        # Match by stock code
        if item_code and target == item_code:
            return rule
        # Match by description keyword
        if target.lower() in item_desc:
            return rule

    return None


def _apply_rule(rule: dict, geometry: dict) -> float:
    """Apply a formula rule to calculate quantity from geometry.

    Formula format examples:
      - "roof_area / 0.9"           → roof battens at 900mm spacing
      - "total_floor_area * 1.1"    → floor sheets with 10% waste
      - "external_wall_length * 2.7" → external cladding (height * length)
      - "door_count * 3"            → 3 hinges per door
    """
    formula = str(rule.get("formula", "")).strip()
    if not formula:
        return 0.0

    # Replace geometry references with values
    safe_formula = formula
    for key, value in geometry.items():
        if isinstance(value, (int, float)):
            safe_formula = safe_formula.replace(key, str(float(value)))

    # Evaluate safely (only allow basic math)
    try:
        # Only allow digits, decimal points, operators, parentheses, spaces
        if not re.match(r'^[\d\s\.\+\-\*/\(\)]+$', safe_formula):
            return 0.0
        result = eval(safe_formula)  # noqa: S307
        return round(float(result), 2)
    except Exception:
        return 0.0
