"""
dependent_scaler.py — Auto-scales fixings, screws, brackets from bulk changes.

Uses the Rules Library to recalculate dependent items when their parent
bulk items change. For example, if roof batten count increases by 15%,
roof screws scale by 15% automatically.

Source tag: "rule-scaled"
"""


def scale_dependent_items(
    project_boq: list[dict],
    change_log: list[dict],
    rules: list[dict],
) -> list[dict]:
    """Apply rules-based scaling to dependent items in the BOQ.

    Args:
        project_boq: Current project BOQ after quantity calculation.
        change_log: Change log from change_detector.
        rules: Rules library from standard model.

    Returns:
        Updated project BOQ with dependent items scaled.
    """
    # Build scaling map: which items depend on which changes
    scaling_rules = _build_scaling_map(rules)

    # Build change lookup
    change_map = {}
    for entry in change_log:
        if entry.get("changed"):
            change_map[entry["element"]] = entry

    # Apply scaling
    for i, item in enumerate(project_boq):
        if item.get("source") in ("recalculated", "rule-scaled"):
            # Already recalculated — skip
            continue

        if item.get("source") != "standard":
            continue

        # Check if this item has a dependent scaling rule
        scaling = _find_scaling_rule(item, scaling_rules)
        if not scaling:
            continue

        # Check if the parent element changed
        depends_on = scaling.get("depends_on", "")
        if depends_on not in change_map:
            continue

        change = change_map[depends_on]
        delta_pct = change.get("delta_pct", 0.0)

        if abs(delta_pct) < 0.5:
            continue

        # Apply scaling
        original_qty = item.get("qty", 0)
        try:
            original_qty = float(original_qty) if original_qty else 0.0
        except (ValueError, TypeError):
            continue

        if original_qty == 0:
            continue

        scale_factor = 1.0 + (delta_pct / 100.0)

        # Apply any custom multiplier from the rule
        multiplier = scaling.get("multiplier", 1.0)
        try:
            multiplier = float(multiplier) if multiplier else 1.0
        except (ValueError, TypeError):
            multiplier = 1.0

        new_qty = round(original_qty * scale_factor * multiplier, 2)

        project_boq[i] = dict(item)
        project_boq[i]["qty"] = new_qty
        project_boq[i]["confidence"] = "MEDIUM"
        project_boq[i]["source"] = "rule-scaled"
        project_boq[i]["notes"] = (
            f"Rule-scaled: {depends_on} changed by {delta_pct:+.1f}%, "
            f"multiplier {multiplier}. Was {original_qty}, now {new_qty}"
        )

    return project_boq


def _build_scaling_map(rules: list[dict]) -> list[dict]:
    """Parse rules library into a list of scaling rules.

    Expected rule format:
      rule_id: R-xxx
      target_item: stock code or description keyword
      depends_on: geometry element name (e.g. roof_area)
      formula: scaling formula or just 'proportional'
      description: human-readable explanation
      multiplier: optional factor (default 1.0)
    """
    scaling_rules = []

    for rule in rules:
        depends_on = str(rule.get("depends_on", "")).strip().lower().replace(" ", "_")
        target = str(rule.get("target_item", "")).strip()
        formula = str(rule.get("formula", "")).strip().lower()

        if not depends_on or not target:
            continue

        # Determine if this is a dependent scaling rule
        # (as opposed to a direct calculation rule handled by quantity_calculator)
        is_dependent = any(kw in formula for kw in [
            "proportional", "scale", "same", "match",
        ]) or ("*" in formula and depends_on not in formula)

        # If formula references a geometry key directly, it's a direct calc rule
        # If it just says "proportional" or has a multiplier, it's a dependent rule
        if not is_dependent and formula and depends_on in formula:
            continue  # Direct calc — handled by quantity_calculator

        multiplier = 1.0
        if "multiplier" in rule and rule["multiplier"]:
            try:
                multiplier = float(rule["multiplier"])
            except (ValueError, TypeError):
                multiplier = 1.0

        scaling_rules.append({
            "target_item": target,
            "depends_on": depends_on,
            "formula": formula,
            "multiplier": multiplier,
            "description": rule.get("description", ""),
        })

    return scaling_rules


def _find_scaling_rule(item: dict, scaling_rules: list[dict]) -> dict | None:
    """Find a scaling rule that applies to this BOQ item."""
    item_code = str(item.get("stock_code", "")).strip()
    item_desc = str(item.get("description", "")).lower()
    item_category = str(item.get("category", "")).lower()

    for rule in scaling_rules:
        target = rule["target_item"]

        # Match by stock code
        if item_code and target == item_code:
            return rule

        # Match by description keyword
        target_lower = target.lower()
        if target_lower in item_desc:
            return rule

        # Match by category
        if target_lower in item_category:
            return rule

    return None


# Keywords that identify fixing/fastener items (used for auto-detection fallback)
FIXING_KEYWORDS = [
    "screw", "nail", "bolt", "nut", "washer", "bracket",
    "fixing", "fastener", "anchor", "clip", "tie",
    "strap", "connector", "hanger", "stirrup",
]


def auto_detect_dependents(project_boq: list[dict]) -> list[dict]:
    """Identify BOQ items that are likely fixings/fasteners.

    Useful for flagging items that should have scaling rules
    but don't yet have them in the Rules Library.
    """
    detected = []
    for item in project_boq:
        desc = str(item.get("description", "")).lower()
        if any(kw in desc for kw in FIXING_KEYWORDS):
            if item.get("source") == "standard":
                detected.append({
                    "item_no": item.get("item_no", ""),
                    "stock_code": item.get("stock_code", ""),
                    "description": item.get("description", ""),
                    "suggestion": "This fixing/fastener has no scaling rule. "
                                  "Consider adding a rule in the Rules Library.",
                })
    return detected
