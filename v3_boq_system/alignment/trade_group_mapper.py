"""
trade_group_mapper.py — Family→trade_group classification and header-row insertion.

Introduces the TRADE GROUP layer between SECTION and ITEM in estimator mode:

    SECTION  (e.g. 50107 - Structural & Base Steel & Footings)
      TRADE GROUP  (e.g. "Footings")    ← export_only_grouping header
        item
        item
      TRADE GROUP  (e.g. "Frame")       ← export_only_grouping header
        item

Trade group headers
-------------------
  export_class        = "export_only_grouping"
  derivation_rule     = "insert_trade_group_headers"
  quantity / unit     = None
  trade_group_sort_key = (tg_idx + 1) × _TG_SCALE - 1
  manual_review       = False

Items gain a new field
----------------------
  trade_group_sort_key = (tg_idx + 1) × _TG_SCALE + family_sort_key

  Items whose family has no trade group in the current section get:
  trade_group_sort_key = _DEFAULT_TG_SK + family_sort_key  (sorts last)

The Excel writer uses trade_group_sort_key for ordering (when present),
falling back to family_sort_key in commercial / engine modes.

NON-NEGOTIABLE: quantities are never changed by this module.
"""
from __future__ import annotations

_TG_SCALE:      int = 10_000   # sort-key gap between consecutive trade groups
_DEFAULT_TG_SK: int = 90_000   # ungrouped items sort after all trade groups


# ---------------------------------------------------------------------------
# Family → trade group  (section-agnostic defaults)
# ---------------------------------------------------------------------------

FAMILY_TO_TRADE_GROUP: dict[str, str] = {
    # ── 50107 Structural, Footings & Floor ───────────────────────────────
    "footing_concrete":          "Footings",
    "footing_formwork":          "Footings",
    "footing_reinforcement":     "Footings",
    "bar_chair":                 "Footings",
    "strip_footing":             "Footings",
    "pad_footing":               "Footings",
    "concrete_supply":           "Footings",
    "termite_barrier":           "Substructure",
    "earthworks":                "Substructure",
    "site_prep":                 "Substructure",
    "dpm":                       "Substructure",
    "floor_cassette":            "Floor System",
    "joist":                     "Floor System",
    "floor_edge_beam":           "Floor System",
    "floor_stringer":            "Floor System",
    "bearer":                    "Floor System",
    "support_post":              "Floor System",
    "joist_hanger":              "Floor System",
    "wall_frame":                "Frame",
    "roof_truss":                "Frame",
    "roof_panel_frame":          "Frame",
    "verandah_frame":            "Frame",
    "steel_post":                "Frame",
    "steel_beam":                "Frame",
    "angle_brace":               "Frame",
    "strap_brace":               "Frame",
    "hold_down":                 "Frame",
    "support_angle":             "Frame",
    # ── 50112 Roof & Roof Plumbing ───────────────────────────────────────
    "roof_batten":               "Roof Structure",
    "ceiling_batten":            "Roof Structure",
    "roof_cladding":             "Roof Covering",
    "ridge_capping":             "Roof Covering",
    "hip_capping":               "Roof Covering",
    "barge_capping":             "Roof Covering",
    "apron_flashing":            "Roof Covering",
    "sisalation":                "Roof Covering",
    "sisalation_tape":           "Roof Covering",
    "flashing":                  "Roof Covering",
    "fascia":                    "Eaves",
    "soffit":                    "Eaves",
    "birdproof_foam":            "Eaves",
    "gutter":                    "Roof Plumbing",
    "gutter_accessory":          "Roof Plumbing",
    "downpipe":                  "Roof Plumbing",
    # ── 50113 External Cladding ──────────────────────────────────────────
    "weatherboard":              "Wall Cladding",
    "external_wall_lining":      "Wall Cladding",
    "external_corner_flashing":  "Wall Cladding",
    "building_wrap":             "Wall Cladding",
    "pvc_h_joiner":              "Wall Cladding",
    "reveal_trim":               "Wall Cladding",
    "stud_clip":                 "Wall Cladding",
    "expansion_sealant":         "Wall Cladding",
    "soffit_flashing":           "Wall Cladding",
    # ── 50114 Openings ───────────────────────────────────────────────────
    "door":                      "Doors",
    "door_frame":                "Doors",
    "door_flashing":             "Doors",
    "door_hinge":                "Door Hardware",
    "door_lockset":              "Door Hardware",
    "door_stop":                 "Door Hardware",
    "door_closer":               "Door Hardware",
    "window":                    "Windows",
    "glazing":                   "Windows",
    "louvre_blade":              "Window Accessories",
    "fly_screen":                "Window Accessories",
    "window_flashing":           "Window Accessories",
    "window_security":           "Window Accessories",
    # ── 50115 Internal Linings & Finishes ────────────────────────────────
    "ceiling_lining":            "Ceiling Finishes",
    "cornice":                   "Ceiling Finishes",
    "internal_wall_lining":      "Wall Finishes",
    "wet_area_lining":           "Wall Finishes",
    "floor_finish":              "Floor Finishes",
    "paint":                     "Painting",
    "skirting":                  "Trims",
    "architrave":                "Trims",
    # ── 50118 Insulation ─────────────────────────────────────────────────
    "insulation":                "Insulation",
    # ── 50124 Stairs & Balustrades ───────────────────────────────────────
    "stair":                     "Stairs",
    "ramp":                      "Stairs",
    "balustrade":                "Balustrades",
    # ── 50129 FFE ────────────────────────────────────────────────────────
    "toilet":                    "Sanitary Fixtures",
    "sanitary_fixture":          "Sanitary Fixtures",
    "ffe_refrigeration":         "Equipment",
    "tapware":                   "Tapware",
    "mirror":                    "Tapware",
    "cabinet":                   "Cabinetry",
}


# ---------------------------------------------------------------------------
# Section-specific overrides — (section_code, family) → trade_group
# Checked before FAMILY_TO_TRADE_GROUP; handles ambiguous families that
# belong to different groups depending on which section they appear in.
# ---------------------------------------------------------------------------

_SECTION_FAMILY_OVERRIDES: dict[tuple[str, str], str] = {
    # floor_substrate in 50107 = structural floor sheeting (Floor System)
    # floor_substrate in 50115 = finished floor substrate (Floor Finishes)
    ("50107", "floor_substrate"): "Floor System",
    ("50115", "floor_substrate"): "Floor Finishes",
    # sisalation in 50112 = roof underlay (Roof Covering)
    # sisalation in 50118 = insulation product (Insulation)
    ("50112", "sisalation"):      "Roof Covering",
    ("50118", "sisalation"):      "Insulation",
    ("50112", "sisalation_tape"): "Roof Covering",
    ("50118", "sisalation_tape"): "Insulation",
}


# ---------------------------------------------------------------------------
# Ordered trade groups per commercial section
# First listed = displayed first in the section (defines sort order).
# ---------------------------------------------------------------------------

SECTION_TRADE_GROUPS: dict[str, list[str]] = {
    "50107": ["Footings", "Substructure", "Floor System", "Frame"],
    "50112": ["Roof Structure", "Roof Covering", "Eaves", "Roof Plumbing"],
    "50113": ["Wall Cladding"],
    "50114": ["Doors", "Door Hardware", "Windows", "Window Accessories"],
    "50115": ["Ceiling Finishes", "Wall Finishes", "Floor Finishes", "Painting", "Trims"],
    "50118": ["Insulation"],
    "50124": ["Stairs", "Balustrades"],
    "50129": ["Sanitary Fixtures", "Tapware", "Cabinetry", "Equipment"],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_trade_group(section_code: str, family: str) -> str | None:
    """Return the trade group for *family* in *section_code*, or None.

    Section-specific overrides are checked before the global table.
    Returns None if the resolved trade group is not listed for the section.
    """
    # Section-specific override takes precedence
    override = _SECTION_FAMILY_OVERRIDES.get((section_code, family))
    if override is not None:
        groups = SECTION_TRADE_GROUPS.get(section_code, [])
        return override if override in groups else None

    # Global default
    tg = FAMILY_TO_TRADE_GROUP.get(family)
    if tg is None:
        return None
    groups = SECTION_TRADE_GROUPS.get(section_code, [])
    return tg if tg in groups else None


def _tg_idx(section_code: str, trade_group: str) -> int:
    """0-based index of *trade_group* in its section's ordered list."""
    groups = SECTION_TRADE_GROUPS.get(section_code, [])
    try:
        return groups.index(trade_group)
    except ValueError:
        return len(groups)


def make_trade_group_header(
    section_code: str,
    trade_group:  str,
    *,
    sort_key: int,
) -> dict:
    """Create a display-only trade group header row.

    The header has ``export_class="export_only_grouping"`` and
    ``quantity=None`` — it never carries a quantity.

    *sort_key* is set by the caller to (tg_idx + 1) × _TG_SCALE - 1
    so the header sorts just before its first child item.
    """
    return {
        "item_name":               f"[TRADE GROUP] {trade_group}",
        "item_display_name":       trade_group,
        "commercial_package_code": section_code,
        "package_code":            section_code,
        "unit":                    None,
        "quantity":                None,
        "quantity_status":         "export_only_grouping",
        "evidence_class":          "export_only_grouping",
        "export_class":            "export_only_grouping",
        "confidence":              "HIGH",
        "manual_review":           False,
        "notes":                   None,
        "quantity_basis":          "TRADE GROUP HEADER — no quantity.",
        "source_evidence":         "export_layer",
        "derivation_rule":         "insert_trade_group_headers",
        "family_sort_key":         sort_key,   # for backward-compat fallback
        "trade_group_sort_key":    sort_key,
        "trade_group":             trade_group,
    }


def insert_trade_group_headers(
    items:    list[dict],
    *,
    sections: set[str] | None = None,
) -> list[dict]:
    """Classify items into trade groups, tag sort keys, and insert headers.

    Steps
    -----
    1. For each real item in a section that has trade groups:
       - Classify ``family → trade group``.
       - Assign ``trade_group_sort_key = (tg_idx + 1) × _TG_SCALE + family_sort_key``.
       - Items with no trade group get ``_DEFAULT_TG_SK + family_sort_key``
         so they appear after all trade group blocks.
    2. For each populated ``(section, trade_group)`` pair:
       - Create a header row with
         ``trade_group_sort_key = (tg_idx + 1) × _TG_SCALE - 1``
         (sorts immediately before the first child).
    3. Return the augmented items list + header rows.

    Parameters
    ----------
    items:
        Full BOQ items list.  Section remaps (rule_estimator_transforms)
        must already have run so battens are in 50112, etc.
    sections:
        Sections to process.  Defaults to all keys in SECTION_TRADE_GROUPS.
    """
    from .family_classifier import classify

    if sections is None:
        sections = set(SECTION_TRADE_GROUPS.keys())

    # Pass 1: tag items and discover which trade groups are populated
    # populated[(section, tg)] = minimum trade_group_sort_key of that group
    populated: dict[tuple[str, str], int] = {}

    for item in items:
        if item.get("export_class") == "export_only_grouping":
            continue
        code = item.get("commercial_package_code", "50199")
        if code not in sections:
            continue

        family = classify(item.get("item_name", ""))
        tg     = get_trade_group(code, family)
        fam_sk = item.get("family_sort_key", 500)

        if tg:
            idx   = _tg_idx(code, tg)
            tg_sk = (idx + 1) * _TG_SCALE + fam_sk
        else:
            tg_sk = _DEFAULT_TG_SK + fam_sk

        item["trade_group_sort_key"] = tg_sk

        if tg:
            key = (code, tg)
            if key not in populated or tg_sk < populated[key]:
                populated[key] = tg_sk

    if not populated:
        return items

    # Pass 2: build one header per populated (section, trade_group)
    headers: list[dict] = []
    for section, trade_groups in SECTION_TRADE_GROUPS.items():
        if section not in sections:
            continue
        for idx, tg in enumerate(trade_groups):
            if (section, tg) not in populated:
                continue   # empty trade group → no header
            hdr_sk = (idx + 1) * _TG_SCALE - 1
            headers.append(make_trade_group_header(section, tg, sort_key=hdr_sk))

    return items + headers
