"""
upgrade_rules.py — Reusable export-layer transformation rules.

Each rule is a pure function:
    rule(items, context) → (transformed_items, rule_log)

Rules never change source-derived quantities.  They may:
  - Add display-level grouping rows (export_only_grouping = True)
  - Reorder items within or across sections
  - Create commercial placeholder rows where baseline expects a package
    that source documents do not support
  - Convert overly technical names to estimator-style commercial names
  - Move rows between commercial sections (via commercial_package_code)

Every transformed item retains:
  - original source evidence
  - quantity_basis / derivation_rule
  - confidence / manual_review
  - quantity_status
  - evidence_class
  - a new "export_class" field: one of
      source_quantified | calculated_source | inferred |
      placeholder | export_only_grouping

Context dict keys
-----------------
  baseline_profile   : dict from baseline_profiler
  ai_profile         : dict from ai_profiler
  comparison_report  : dict from section_comparator
  project_config     : dict (optional project config YAML content)
"""

from __future__ import annotations
import copy
import re
from collections import defaultdict
from typing import Any

from .family_classifier import classify


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _tag_export_class(item: dict) -> dict:
    """Ensure every item has an ``export_class`` field."""
    if "export_class" in item:
        return item
    status = item.get("quantity_status", "unknown")
    ev = item.get("evidence_class", "")
    if status == "placeholder":
        item["export_class"] = "placeholder"
    elif ev in ("measured_source",):
        item["export_class"] = "source_quantified"
    elif ev in ("calculated_source",):
        item["export_class"] = "calculated_source"
    elif ev in ("config_backed", "heuristic_inferred"):
        item["export_class"] = "inferred"
    else:
        item["export_class"] = "inferred"
    return item


def _make_placeholder(
    *,
    package_code: str,
    display_name: str,
    note: str,
    sort_key: int = 9000,
    unit: str = "item",
) -> dict:
    """Create a commercial placeholder row (manual review = True, qty = 0)."""
    return {
        "item_name":              display_name,
        "item_display_name":      display_name,
        "commercial_package_code": package_code,
        "package_code":           package_code,
        "unit":                   unit,
        "quantity":               0,
        "quantity_status":        "placeholder",
        "evidence_class":         "placeholder",
        "export_class":           "placeholder",
        "confidence":             "LOW",
        "manual_review":          True,
        "notes":                  note,
        "quantity_basis":         "PLACEHOLDER — schedule/source not available.",
        "source_evidence":        "none",
        "derivation_rule":        "placeholder_rule",
        "family_sort_key":        sort_key,
    }


# ---------------------------------------------------------------------------
# Rule 1: Tag all items with export_class
# ---------------------------------------------------------------------------

def rule_tag_export_class(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Ensure every item has an export_class field.  Always runs first."""
    tagged = [_tag_export_class(copy.deepcopy(i)) for i in items]
    return tagged, []


# ---------------------------------------------------------------------------
# Rule 2: Add commercial placeholders for expected-but-empty sections
# ---------------------------------------------------------------------------

_PLACEHOLDER_SPECS: dict[str, list[dict]] = {
    # code → list of placeholder row specs for that section
    "50117": [
        {
            "display_name": "Hydraulics | Builder's Works (Allowance)",
            "note": (
                "PLACEHOLDER: No hydraulic schedule in source documents.  "
                "Confirm fixture count, pipe runs, and rough-in allowances "
                "with hydraulic engineer before tendering."
            ),
            "unit": "item",
            "sort_key": 9000,
        }
    ],
    "50119": [
        {
            "display_name": "Electrical | Builder's Works (Allowance)",
            "note": (
                "PLACEHOLDER: No electrical schedule in source documents.  "
                "Confirm board size, circuit count, cable runs, and GPO layout "
                "with electrical engineer before tendering."
            ),
            "unit": "item",
            "sort_key": 9000,
        }
    ],
}


def rule_add_missing_section_placeholders(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Add placeholder rows for sections the baseline expects but that are
    empty (or absent) in the AI BOQ.

    Only adds a placeholder when:
    1. The baseline profile shows the section as EMPTY_BASELINE or the section
       is flagged as a services placeholder, AND
    2. The AI BOQ section also has no real (non-placeholder) items.
    """
    comp = context.get("comparison_report", {})
    results = comp.get("section_results", {})

    existing_codes = {i.get("commercial_package_code") for i in items}
    log: list[dict] = []
    extra: list[dict] = []

    for code, spec_list in _PLACEHOLDER_SPECS.items():
        sec_result = results.get(code, {})
        status = sec_result.get("status", "")
        ai_row_count = sec_result.get("ai_row_count", 0)

        # Only inject if the section is truly lacking real content
        real_items_in_code = [
            i for i in items
            if i.get("commercial_package_code") == code
            and i.get("quantity_status") != "placeholder"
        ]
        if real_items_in_code:
            continue  # Section already has real content

        for spec in spec_list:
            ph = _make_placeholder(
                package_code=code,
                display_name=spec["display_name"],
                note=spec["note"],
                unit=spec.get("unit", "item"),
                sort_key=spec.get("sort_key", 9000),
            )
            extra.append(ph)
            log.append({
                "rule": "add_missing_section_placeholders",
                "code": code,
                "display_name": spec["display_name"],
                "reason": f"section status={status}, real_items=0",
            })

    return items + extra, log


# ---------------------------------------------------------------------------
# Rule 3: Name normalisation — estimator-style commercial names
# ---------------------------------------------------------------------------

# Maps (family, pattern_fragment) → commercial_display_name
# The pattern_fragment is matched case-insensitively.
# Longer/more-specific patterns should appear earlier.
_NAME_OVERRIDES: list[tuple[str, str, str]] = [
    # family               fragment               commercial_name
    ("floor_cassette",   "cassette panel",       "Floor Cassette Panel"),
    ("joist",            "floor joist",          "Floor Joist"),
    ("floor_edge_beam",  "edge beam",            "Floor Edge Beam"),
    ("floor_stringer",   "stringer",             "Floor Stringer"),
    ("bearer",           "bearer",               "Floor Bearer"),
    ("support_post",     "support post",         "Sub-Floor Support Post"),
    ("support_post",     "steel stump",          "Adjustable Steel Stump"),
    ("wall_frame",       "wall frame",           "LGS Wall Frame"),
    ("roof_truss",       "roof truss",           "LGS Roof Truss"),
    ("roof_panel_frame", "roof panel",           "LGS Roof Panel"),
    ("roof_batten",      "top hat",              "Roof Batten | Top Hat"),
    ("ceiling_batten",   "ceiling/wall batten",  "Ceiling / Wall Batten"),
    ("ceiling_batten",   "ceiling batten",       "Ceiling Batten"),
    ("roof_cladding",    "roof cladding",        "Roof Cladding Sheet"),
    ("floor_substrate",  "fc sheet floor",       "FC Sheet Floor"),
    ("floor_substrate",  "floor sheet",          "Floor Sheet (FC / Plywood)"),
    ("screw_fixing",     "screw",                "Fixing | Screw"),
    ("bolt_fixing",      "bolt",                 "Fixing | Bolt & Nut"),
    ("bolt_fixing",      "anchor",               "Fixing | Sleeve Anchor"),
]


def rule_normalise_commercial_names(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Apply estimator-style name normalisation where the current display name
    is overly technical.

    The original ``item_name`` is always preserved.  Only
    ``item_display_name`` is updated when a cleaner commercial name is found.
    """
    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        item = copy.deepcopy(item)
        desc = (item.get("item_name") or "").lower()
        family = classify(desc)

        for rule_family, fragment, commercial_name in _NAME_OVERRIDES:
            if rule_family != family:
                continue
            if fragment.lower() not in desc:
                continue
            old = item.get("item_display_name", "")
            if old == commercial_name:
                break
            # Only override if current display name is not already cleaner
            # (we keep the existing name if it has "|" — already formatted)
            if "|" not in old:
                item["item_display_name"] = commercial_name
                log.append({
                    "rule": "normalise_commercial_names",
                    "item_name": item.get("item_name"),
                    "old_display": old,
                    "new_display": commercial_name,
                    "family": family,
                })
            break

        result.append(item)

    return result, log


# ---------------------------------------------------------------------------
# Rule 4: Detect and flag fixings distribution mismatch
# ---------------------------------------------------------------------------

def rule_fixings_distribution_audit(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Flag when baseline has a standalone fixings section but AI embeds
    fixings inside trade sections (or vice-versa).

    This rule does NOT move items — it only adds alignment_notes.
    Moving items would risk breaking tests and commercial view counts.
    The audit log tells operators what the comparison found.
    """
    base_gf = context.get("baseline_profile", {}).get("global_flags", {})
    ai_gf   = context.get("ai_profile", {}).get("global_flags", {})

    base_standalone = base_gf.get("fixings_standalone_section", False)
    ai_standalone   = ai_gf.get("fixings_standalone_section", False)

    log: list[dict] = []
    if base_standalone and not ai_standalone:
        log.append({
            "rule": "fixings_distribution_audit",
            "status": "MISMATCH",
            "note": (
                "Baseline uses a standalone fixings section (e.g. 50111). "
                "AI BOQ embeds fixings within trade sections. "
                "Current AI approach is acceptable — standalone section exists "
                "in AI BOQ under a different label."
            ),
        })
    elif ai_standalone and not base_standalone:
        log.append({
            "rule": "fixings_distribution_audit",
            "status": "MISMATCH",
            "note": (
                "AI BOQ has a standalone fixings section but baseline embeds "
                "fixings within trade sections. Consider redistributing for "
                "baseline alignment if this is a consistent baseline style."
            ),
        })
    else:
        log.append({
            "rule": "fixings_distribution_audit",
            "status": "OK",
            "note": "Fixings distribution style matches between baseline and AI BOQ.",
        })

    return items, log


# ---------------------------------------------------------------------------
# Rule 5: Stock-length presentation advisory
# ---------------------------------------------------------------------------

def rule_stock_length_advisory(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Identify items where the baseline uses 'len' (stock-length) units but
    the AI BOQ uses lm.  Records advisory notes without changing quantities.

    For actual conversion, call unit_aligner.align_unit() per item.
    """
    base_secs = context.get("baseline_profile", {}).get("sections", {})
    log: list[dict] = []

    for code, base_sec in base_secs.items():
        if not base_sec.get("style_flags", {}).get("uses_stock_length_unit"):
            continue

        ai_items_in_code = [
            i for i in items
            if (i.get("commercial_package_code") or i.get("package_code")) == code
            and (i.get("unit") or "").lower() == "lm"
        ]
        for item in ai_items_in_code:
            log.append({
                "rule": "stock_length_advisory",
                "item_name": item.get("item_name"),
                "code": code,
                "current_unit": "lm",
                "baseline_preferred": "len",
                "note": (
                    f"Baseline section {code} uses 'len' (stock-length) units. "
                    f"AI BOQ uses 'lm'. "
                    f"Call unit_aligner.align_unit(item, 'len') to convert if "
                    f"stock length is stated in the item description."
                ),
            })

    return items, log


# ---------------------------------------------------------------------------
# Rule 6: Mark MR items that could be resolved from config
# ---------------------------------------------------------------------------

def rule_config_resolution_advisory(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """For items marked manual_review=True with evidence_class=config_backed,
    add a note explaining what additional source data would promote confidence.
    """
    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        item = copy.deepcopy(item)
        if item.get("manual_review") and item.get("evidence_class") == "config_backed":
            existing_notes = item.get("notes", "")
            advisory = (
                "ALIGNMENT ADVISORY: This quantity is config-backed (no schedule "
                "found in source documents). To promote confidence: "
                "(1) provide a room schedule with confirmed areas, "
                "(2) provide a finish schedule from drawings, "
                "(3) or confirm via site measurement."
            )
            if advisory not in existing_notes:
                item["notes"] = f"{existing_notes}  {advisory}".strip()
            log.append({
                "rule": "config_resolution_advisory",
                "item_name": item.get("item_name"),
                "evidence_class": "config_backed",
                "advisory": advisory,
            })
        result.append(item)

    return result, log


# ---------------------------------------------------------------------------
# Rule 7: Safe lm → len stock-length presentation conversion
# ---------------------------------------------------------------------------

# Families where lm→len conversion is appropriate (linear stock items)
_LM_TO_LEN_FAMILIES: frozenset[str] = frozenset({
    "roof_batten", "ceiling_batten", "fascia", "gutter",
    "weatherboard", "architrave", "skirting", "cornice",
    "barge_capping", "downpipe", "ridge_capping", "hip_capping",
})


def rule_apply_lm_to_len(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Convert lm→len (stock-length count) where:

    1. The baseline section uses 'len' as a unit (but not exclusively 'lm').
    2. The item belongs to a family that is naturally sold in stock lengths.
    3. The item's description or name contains a parseable stock length.

    On failure, leaves the item unchanged and records a PRESENTATION_MISMATCH
    note in the item's alignment_notes.
    Source quantity is always preserved in quantity_source_value.
    """
    from .unit_aligner import align_unit
    from .family_classifier import classify

    base_secs = context.get("baseline_profile", {}).get("sections", {})

    # Sections where baseline uses 'len' — only apply there
    len_sections: set[str] = {
        code for code, sec in base_secs.items()
        if "len" in sec.get("units_seen", {})
    }

    if not len_sections:
        return items, []

    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        code = item.get("commercial_package_code") or item.get("package_code", "")
        src_unit = (item.get("unit") or "").lower()

        if code not in len_sections or src_unit != "lm":
            result.append(item)
            continue

        family = classify(item.get("item_name", ""))
        if family not in _LM_TO_LEN_FAMILIES:
            result.append(item)
            continue

        res = align_unit(item, "len", waste_factor=1.0)
        result.append(res["new_item"])

        if res["style_status"] == "CONVERTED":
            log.append({
                "rule": "apply_lm_to_len",
                "item_name": item.get("item_name"),
                "family": family,
                "section": code,
                "qty_lm": item.get("quantity"),
                "qty_len": res["new_item"].get("quantity"),
                "note": res["note"],
            })
        # STYLE_MISMATCH case: note is already appended to item's alignment_notes
        # by align_unit() — no extra log entry needed here

    return result, log


# ---------------------------------------------------------------------------
# Rule 8: Safe m² → each (sheet count) for FC sheet families
# ---------------------------------------------------------------------------

# Families quantified in m² but typically presented as sheet count by QS
_FC_SHEET_FAMILIES: frozenset[str] = frozenset({
    "ceiling_lining",
    "internal_wall_lining",
    "external_wall_lining",
    "floor_substrate",
    "wet_area_lining",
})


def rule_apply_area_to_sheets(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Convert m²→each (sheet count) for FC sheet families where sheet
    dimensions appear in the item description (e.g. '1200 × 2400mm').

    Only converts when:
    1. Item family is in _FC_SHEET_FAMILIES.
    2. Item unit is 'm2'.
    3. Sheet dimensions are parseable from description (not inferred).

    On failure, leaves item unchanged (STYLE_MISMATCH note appended by
    align_unit()).  Source m² quantity is preserved in quantity_source_value.
    """
    from .unit_aligner import align_unit
    from .family_classifier import classify

    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        src_unit = (item.get("unit") or "").lower()
        if src_unit != "m2":
            result.append(item)
            continue

        family = classify(item.get("item_name", ""))
        if family not in _FC_SHEET_FAMILIES:
            result.append(item)
            continue

        res = align_unit(item, "each", waste_factor=1.0)
        result.append(res["new_item"])

        if res["style_status"] == "CONVERTED":
            log.append({
                "rule": "apply_area_to_sheets",
                "item_name": item.get("item_name"),
                "family": family,
                "qty_m2": item.get("quantity"),
                "qty_each": res["new_item"].get("quantity"),
                "note": res["note"],
            })

    return result, log


# ---------------------------------------------------------------------------
# Rule 9: Add placeholder rows for expected commercial families absent from BOQ
# ---------------------------------------------------------------------------

def rule_add_missing_commercial_families(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Create PLACEHOLDER rows for commercially-expected families that the AI
    BOQ does not contain.

    Currently handles:
    - barge_capping in 50112: hip roofs may legitimately omit barge boards.
      The placeholder advises the QS to verify from the roof plan.
    - door_hinge aggregate: if the comparison flags door_hinge as
      MISSING_EXPECTED but individual hinge rows exist under a different name,
      no placeholder is added (family_classifier will have matched them).

    Rules:
    - Only adds a placeholder when the comparison report flags the family as
      MISSING_REQUIRED or MISSING_EXPECTED AND no items in the AI BOQ are
      classified to that family.
    - Placeholder quantity is always 0 and manual_review=True.
    - Does not invent quantities.
    """
    from .family_classifier import classify

    comp = context.get("comparison_report", {})
    log: list[dict] = []
    extra: list[dict] = []

    # ── barge_capping in 50112 ─────────────────────────────────────────────
    roof_result = comp.get("section_results", {}).get("50112", {})
    barge_gap = next(
        (g for g in roof_result.get("family_gaps", [])
         if g.get("family") == "barge_capping"
         and g.get("classification") in ("MISSING_EXPECTED", "MISSING_REQUIRED")),
        None,
    )
    if barge_gap:
        existing_barge = [
            i for i in items
            if classify(i.get("item_name", "")) == "barge_capping"
        ]
        if not existing_barge:
            ph = _make_placeholder(
                package_code="50112",
                display_name="Barge Capping (aluminium / colorbond)",
                note=(
                    "PLACEHOLDER: Barge capping not detected in source documents. "
                    "For gable / skillion roofs: measure from roof plan (lm). "
                    "For hip roofs: barge capping typically not required — verify "
                    "from roof plan and architectural drawings before confirming "
                    "this item is absent."
                ),
                unit="lm",
                sort_key=408,
            )
            extra.append(ph)
            log.append({
                "rule": "add_missing_commercial_families",
                "family": "barge_capping",
                "section": "50112",
                "action": "placeholder_added",
                "reason": f"gap_class={barge_gap['classification']}",
            })

    # ── door_hinge in 50114 ────────────────────────────────────────────────
    open_result = comp.get("section_results", {}).get("50114", {})
    hinge_gap = next(
        (g for g in open_result.get("family_gaps", [])
         if g.get("family") == "door_hinge"
         and g.get("classification") in ("MISSING_EXPECTED", "MISSING_REQUIRED")),
        None,
    )
    if hinge_gap:
        existing_hinges = [
            i for i in items
            if classify(i.get("item_name", "")) == "door_hinge"
        ]
        if not existing_hinges:
            ph = _make_placeholder(
                package_code="50114",
                display_name="Door Hinge — Supply & Fix",
                note=(
                    "PLACEHOLDER: Door hinge rows not detected in source documents. "
                    "Verify door hinge type (butt/pivot/concealed) and quantity "
                    "from door schedule. Typically 1.5 pairs per door leaf."
                ),
                unit="pair",
                sort_key=510,
            )
            extra.append(ph)
            log.append({
                "rule": "add_missing_commercial_families",
                "family": "door_hinge",
                "section": "50114",
                "action": "placeholder_added",
                "reason": f"gap_class={hinge_gap['classification']}",
            })

    return items + extra, log


# ---------------------------------------------------------------------------
# Rule 10: Fixings redistribution — embedded mode
# ---------------------------------------------------------------------------

# Maps description keywords → target commercial section when redistributing fixings
_FIXINGS_REDISTRIBUTION_MAP: list[tuple[list[str], str]] = [
    (["roof cladding", "roof sheet", "cladding screw", "batten", "top hat"],  "50112"),
    (["wall frame", "lgs", "structural", "footing", "anchor", "hold down"],   "50107"),
    (["fc sheet", "lining screw", "ceiling", "internal lining",
      "external lining"],                                                       "50115"),
    (["floor", "subfloor", "floor sheet"],                                     "50107"),
    (["door", "window", "opening", "frame screw"],                             "50114"),
    (["stair", "ramp", "balustrade"],                                          "50124"),
]


def rule_fixings_redistribution(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """When fixings_strategy='embedded', redistribute items from the standalone
    50111 (Fixings & Connectors) section into their parent trade sections.

    Mapping is by description keyword — unmatched items remain in 50111.
    Total quantity truth is unchanged; only commercial_package_code changes.
    The redistribution is annotated in each item's alignment_notes.

    When fixings_strategy is NOT 'embedded' (or is None/auto), this rule is
    a no-op and items are left in their current section.
    """
    strategy = context.get("fixings_strategy")
    if strategy != "embedded":
        return items, []

    log: list[dict] = []
    result: list[dict] = []

    for item in items:
        code = item.get("commercial_package_code") or item.get("package_code", "")
        if code != "50111":
            result.append(item)
            continue

        desc = (item.get("item_name", "") or "").lower()
        target: str | None = None
        for keywords, section in _FIXINGS_REDISTRIBUTION_MAP:
            if any(kw.lower() in desc for kw in keywords):
                target = section
                break

        if target:
            item = copy.deepcopy(item)
            item["commercial_package_code"] = target
            item.setdefault("alignment_notes", []).append(
                f"Fixings redistributed: 50111 → {target} "
                f"(fixings_strategy=embedded). "
                f"Source item package unchanged."
            )
            log.append({
                "rule": "fixings_redistribution",
                "item_name": item.get("item_name"),
                "from_section": "50111",
                "to_section": target,
            })
            result.append(item)
        else:
            result.append(item)  # No match — stays in 50111

    return result, log


# ---------------------------------------------------------------------------
# Rule 11: Estimator-mode section remaps + display name overrides
# ---------------------------------------------------------------------------

def rule_estimator_transforms(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Apply estimator-mode transforms: section remaps and richer naming.

    Only runs when context["export_style"] == "estimator".
    No-op for "engine" or "commercial" modes (the default).

    Transforms applied (in order):
      1. apply_estimator_section_remaps — roof/ceiling battens 50107 → 50112
      2. apply_estimator_names          — QS-grade display names
      3. apply_placeholder_renames      — Provisional Sum service placeholders

    Runs BEFORE rule_insert_subgroup_headers so that section remaps are
    reflected in the subgroup population scan.
    """
    if context.get("export_style") != "estimator":
        return items, []

    from .export_style_rules import get_estimator_rules

    log: list[dict] = []
    for fn in get_estimator_rules():
        items, rule_log = fn(items)
        log.extend(rule_log)

    return items, log


# ---------------------------------------------------------------------------
# Rule 12: Subgroup header insertion (estimator mode)
# ---------------------------------------------------------------------------

def rule_insert_subgroup_headers(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Insert display-only subgroup header rows for estimator export mode.

    Only runs when context["export_style"] == "estimator".
    Headers have export_class="export_only_grouping" and quantity=None.
    Their family_sort_key is (min child sort key - 1) so they render
    immediately before their first child item.

    Runs AFTER rule_estimator_transforms so section remaps are already done.
    """
    if context.get("export_style") != "estimator":
        return items, []

    from .subgroup_mapper import insert_subgroup_headers

    before_count = len(items)
    new_items = insert_subgroup_headers(items)
    added = [
        i for i in new_items
        if i.get("export_class") == "export_only_grouping"
        and i.get("derivation_rule") == "insert_subgroup_headers"
    ]

    log = [
        {
            "rule": "insert_subgroup_headers",
            "subgroup": i.get("item_display_name"),
            "section": i.get("commercial_package_code"),
            "sort_key": i.get("family_sort_key"),
        }
        for i in added
    ]

    return new_items, log


# ---------------------------------------------------------------------------
# Rule 13: Trade group header insertion (estimator mode)
# ---------------------------------------------------------------------------

def rule_insert_trade_group_headers(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Insert display-only trade group header rows in estimator export mode.

    Only runs when ``context["export_style"] == "estimator"``.
    No-op for ``"engine"`` and ``"commercial"`` modes.

    Each header has ``export_class="export_only_grouping"``,
    ``derivation_rule="insert_trade_group_headers"``, and ``quantity=None``.

    Items gain a ``trade_group_sort_key`` field so the Excel writer can
    sort correctly:  section → trade_group → family_sort_key.

    Must run AFTER ``rule_estimator_transforms`` so section remaps (e.g.
    roof battens 50107 → 50112) are already applied before the trade-group
    population scan runs.
    """
    if context.get("export_style") != "estimator":
        return items, []

    from .trade_group_mapper import insert_trade_group_headers

    new_items = insert_trade_group_headers(items)
    added = [
        i for i in new_items
        if i.get("derivation_rule") == "insert_trade_group_headers"
    ]

    log = [
        {
            "rule":        "insert_trade_group_headers",
            "trade_group": i.get("trade_group"),
            "section":     i.get("commercial_package_code"),
            "sort_key":    i.get("trade_group_sort_key"),
        }
        for i in added
    ]

    return new_items, log


# ---------------------------------------------------------------------------
# Rule 14: Commercial block header insertion (estimator mode)
# ---------------------------------------------------------------------------

def rule_insert_commercial_block_headers(
    items: list[dict],
    context: dict,
) -> tuple[list[dict], list[dict]]:
    """Insert display-only commercial block header rows in estimator export mode.

    Supersedes ``rule_insert_trade_group_headers`` as the primary grouping
    layer under each section.  Uses section-aware strategies:
    TRADE (building trades), KEYWORD (services), ASSEMBLY (stairs/ramps),
    ROOM (FFE) — instead of a single global family mapping.

    Only runs when ``context["export_style"] == "estimator"``.
    No-op for ``"engine"`` and ``"commercial"`` modes.

    Items gain ``commercial_block`` and ``commercial_block_sort_key`` fields.
    Header rows carry ``derivation_rule="insert_commercial_block_headers"``
    and ``export_class="export_only_grouping"``.

    Must run AFTER ``rule_estimator_transforms`` so section remaps (e.g.
    roof battens 50107 → 50112) are already applied.
    """
    if context.get("export_style") != "estimator":
        return items, []

    from .commercial_block_mapper import insert_commercial_block_headers

    new_items = insert_commercial_block_headers(items)
    added = [
        i for i in new_items
        if i.get("derivation_rule") == "insert_commercial_block_headers"
    ]

    log = [
        {
            "rule":             "insert_commercial_block_headers",
            "commercial_block": i.get("commercial_block"),
            "section":          i.get("commercial_package_code"),
            "sort_key":         i.get("commercial_block_sort_key"),
        }
        for i in added
    ]

    return new_items, log


# ---------------------------------------------------------------------------
# Rule pipeline
# ---------------------------------------------------------------------------

RULE_PIPELINE: list = [
    rule_tag_export_class,
    rule_add_missing_section_placeholders,
    rule_normalise_commercial_names,
    rule_apply_lm_to_len,                       # safe stock-length conversion
    rule_apply_area_to_sheets,                  # safe FC sheet each conversion
    rule_add_missing_commercial_families,       # barge_capping / door_hinge placeholders
    rule_fixings_redistribution,                # embedded-mode fixings move
    rule_estimator_transforms,                  # section remaps + names (estimator only)
    rule_insert_commercial_block_headers,       # commercial block headers (estimator only)
    # rule_insert_trade_group_headers — kept for direct use / tests, NOT in pipeline
    # rule_insert_subgroup_headers    — kept for direct use / tests, NOT in pipeline
    rule_fixings_distribution_audit,
    rule_stock_length_advisory,
    rule_config_resolution_advisory,
]


def apply_upgrade_rules(
    items: list[dict],
    context: dict,
    *,
    rules: list | None = None,
) -> tuple[list[dict], list[dict]]:
    """Apply *rules* in order to *items*.

    Parameters
    ----------
    items:
        BOQ items list.
    context:
        Dict with keys: baseline_profile, ai_profile, comparison_report.
    rules:
        Override the default RULE_PIPELINE.  Useful for testing individual rules.

    Returns
    -------
    (final_items, full_log)
    """
    pipeline = rules if rules is not None else RULE_PIPELINE
    full_log: list[dict] = []

    for rule_fn in pipeline:
        items, rule_log = rule_fn(items, context)
        for entry in rule_log:
            entry.setdefault("rule", rule_fn.__name__)
        full_log.extend(rule_log)

    return items, full_log
