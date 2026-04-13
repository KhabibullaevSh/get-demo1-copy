"""
export_style_rules.py — Estimator-mode transforms.

Provides three composable functions called by upgrade_rules.rule_estimator_transforms
when context["export_style"] == "estimator".

Functions
---------
apply_estimator_section_remaps(items) → (items, log)
    Reserved for future section remaps.  Currently a no-op — battens stay
    in 50107 (Structural) as per BOQ_FOR_AI reference structure.

apply_estimator_names(items) → (items, log)
    Applies richer QS-style display names on top of the commercial name rules.

apply_placeholder_renames(items) → (items, log)
    Upgrades weak service placeholder names to Provisional Sum style.

get_estimator_rules() → list[callable]
    Returns the ordered list of functions above.

NON-NEGOTIABLE RULES (preserved from project brief):
  - item_name is NEVER changed — only item_display_name
  - engine package_code is NEVER changed — only commercial_package_code
  - Quantities are NEVER changed by this module
"""
from __future__ import annotations
import copy


# ---------------------------------------------------------------------------
# Rule A: Section remaps  (no-op — reserved for future use)
# ---------------------------------------------------------------------------

def apply_estimator_section_remaps(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Reserved for future estimator section remaps.

    Previously remapped roof_batten/ceiling_batten 50107 → 50112.
    Reverted: BOQ_FOR_AI reference keeps all battens in 50107 under the
    Frame / Battens commercial block.  No remaps are active.
    """
    return list(items), []


# ---------------------------------------------------------------------------
# Rule B: Estimator-style display names
# ---------------------------------------------------------------------------

# Each entry: (fragment_in_item_name_lower, estimator_display_name)
# First matching fragment wins — more specific entries should come first.
_ESTIMATOR_NAME_RULES: list[tuple[str, str]] = [
    # Structural framing — FrameCAD system names
    ("framecad wall frame",   "Wall Framing System — LGS"),
    ("wall frame",            "Wall Framing System — LGS"),
    ("framecad roof truss",   "Roof Truss System — LGS"),
    ("roof truss",            "Roof Truss System — LGS"),
    ("framecad roof panel",   "Roof Panel System — LGS"),
    ("roof panel",            "Roof Panel System — LGS"),
    # Roof cladding
    ("kliplok",               "Roof Cladding — Lysaght Klip-Lok"),
    ("trimdek",               "Roof Cladding — Lysaght Trimdek"),
    ("lysaght",               "Roof Cladding — Lysaght"),
    ("corrugated roofing",    "Roof Cladding — Corrugated Iron"),
    # Openings
    ("door leaf",             "Door — Leaf Supply"),
    ("door frame set",        "Door — Frame Set"),
    ("window louvre frame",   "Window — Louvre Frame"),
    ("window louvre blade",   "Window — Louvre Blade"),
    # Hydraulics / electrical service placeholders → Provisional Sum naming
    ("hydraulics | builder",  "Hydraulics — Builder's Works (Provisional Sum)"),
    ("hydraulics builder",    "Hydraulics — Builder's Works (Provisional Sum)"),
    ("electrical | builder",  "Electrical Services — Builder's Works (Provisional Sum)"),
    ("electrical builder",    "Electrical Services — Builder's Works (Provisional Sum)"),
    # FFE
    ("wc pan",                "WC Pan — Supply & Fix"),
    ("wc cistern",            "WC Cistern — Supply & Fix"),
    ("hand basin",            "Hand Basin — Supply & Fix"),
    ("kitchen sink",          "Kitchen Sink — Supply & Fix"),
    ("tapware",               "Tapware — Supply & Fix"),
    ("mirror / medicine",     "Mirror / Medicine Cabinet — Supply & Fix"),
    ("toilet roll holder",    "Toilet Roll Holder — Supply & Fix"),
    ("hand towel rail",       "Hand Towel Rail — Supply & Fix"),
    ("soap dispenser",        "Soap Dispenser — Supply & Fix"),
]


def apply_estimator_names(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Apply richer QS-style display name overrides.

    Only updates item_display_name.  item_name is always preserved.
    Subgroup headers (export_only_grouping) are not modified.
    """
    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        item = copy.deepcopy(item)
        if item.get("export_class") == "export_only_grouping":
            result.append(item)
            continue
        desc = (item.get("item_name", "") or "").lower()
        for fragment, est_name in _ESTIMATOR_NAME_RULES:
            if fragment.lower() in desc:
                old = item.get("item_display_name", "")
                if old != est_name:
                    item["item_display_name"] = est_name
                    log.append({
                        "rule": "apply_estimator_names",
                        "item_name": item.get("item_name"),
                        "old_display": old,
                        "new_display": est_name,
                    })
                break
        result.append(item)

    return result, log


# ---------------------------------------------------------------------------
# Rule C: Service placeholder rename
# ---------------------------------------------------------------------------

# Maps current display name → estimator-grade name
_PLACEHOLDER_RENAMES: dict[str, str] = {
    "Hydraulics | Builder's Works (Allowance)": (
        "Hydraulics — Builder's Works (Provisional Sum)"
    ),
    "Electrical | Builder's Works (Allowance)": (
        "Electrical Services — Builder's Works (Provisional Sum)"
    ),
}


def apply_placeholder_renames(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Rename weak service placeholder names to Provisional Sum style."""
    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        item = copy.deepcopy(item)
        disp = item.get("item_display_name", "") or item.get("item_name", "")
        if disp in _PLACEHOLDER_RENAMES:
            new_name = _PLACEHOLDER_RENAMES[disp]
            item["item_display_name"] = new_name
            log.append({
                "rule": "apply_placeholder_renames",
                "old_display": disp,
                "new_display": new_name,
            })
        result.append(item)

    return result, log


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def get_estimator_rules() -> list:
    """Return ordered list of estimator-mode transform functions.

    Order matters:
    1. Section remaps first — so subgroup_mapper sees the correct section
       assignments when inserting headers (done in rule_insert_subgroup_headers).
    2. Names second — applied after section remaps.
    3. Placeholder renames last (most specific overrides).
    """
    return [
        apply_estimator_section_remaps,
        apply_estimator_names,
        apply_placeholder_renames,
    ]
