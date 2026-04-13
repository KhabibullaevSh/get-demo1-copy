"""
subgroup_mapper.py — Family→subgroup classification and header-row insertion.

Called by upgrade_rules.rule_insert_subgroup_headers when export_style="estimator".

Subgroup header rows:
  export_class      = "export_only_grouping"
  quantity / unit   = None
  family_sort_key   = (minimum sort key of children) - 1
  manual_review     = False

Traceability is preserved:
  derivation_rule   = "insert_subgroup_headers"
  source_evidence   = "export_layer"
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Family → subgroup display name
# ---------------------------------------------------------------------------

FAMILY_TO_SUBGROUP: dict[str, str] = {
    # 50107 Structural & Footings
    "wall_frame":           "Structural Framing",
    "roof_truss":           "Structural Framing",
    "roof_panel_frame":     "Structural Framing",
    "ceiling_batten":       "Structural Framing",
    "roof_batten":          "Roof Battens",      # stays in 50107 per BOQ_FOR_AI reference
    "floor_cassette":       "Floor System",
    "joist":                "Floor System",
    "floor_edge_beam":      "Floor System",
    "floor_stringer":       "Floor System",
    "bearer":               "Floor System",
    "support_post":         "Floor System",
    "joist_hanger":         "Floor System",
    "footing_concrete":     "Footings & Substructure",
    "footing_formwork":     "Footings & Substructure",
    "termite_barrier":      "Footings & Substructure",
    "bulk_earthworks":      "Footings & Substructure",
    "dpm":                  "Footings & Substructure",
    # 50112 Roof & Roof Plumbing
    "roof_cladding":        "Roof Cladding",
    "ridge_capping":        "Ridge, Barge & Hip",
    "hip_capping":          "Ridge, Barge & Hip",
    "barge_capping":        "Ridge, Barge & Hip",
    "flashing":             "Flashings",
    "gutter":               "Eaves & Drainage",
    "downpipe":             "Eaves & Drainage",
    "fascia":               "Eaves & Drainage",
    "soffit":               "Eaves & Drainage",
    # 50113 External Cladding
    "weatherboard":         "Wall Cladding",
    "external_wall_lining": "Wall Lining (External Face)",
    # 50114 Openings
    "door":                 "Doors",
    "window":               "Windows",
    "glazing":              "Windows",
    "door_hinge":           "Door Hardware",
    "door_lockset":         "Door Hardware",
    "door_stop":            "Door Hardware",
    "door_closer":          "Door Hardware",
    # 50115 Internal Linings & Finishes
    "ceiling_lining":       "Ceiling Linings",
    "cornice":              "Ceiling Linings",
    "internal_wall_lining": "Wall Linings",
    "wet_area_lining":      "Wet Area Linings",
    "floor_substrate":      "Floor Substrate",
    "floor_finish":         "Floor Finishes",
    "paint":                "Paint & Coatings",
    "skirting":             "Paint & Coatings",
    "architrave":           "Paint & Coatings",
    # 50124 Stairs & Balustrades
    "stair":                "Stairs",
    "ramp":                 "Stairs",
    "balustrade":           "Balustrades",
    # 50129 FFE
    "sanitary_fixture":     "Sanitary Fixtures",
    "tapware":              "Tapware & Accessories",
    "mirror":               "Tapware & Accessories",
    "toilet":               "Sanitary Fixtures",
    "cabinet":              "Cabinetry",
}

# ---------------------------------------------------------------------------
# Ordered subgroups per commercial section
# Order = display/sort order — first listed appears first in the section.
# ---------------------------------------------------------------------------

SECTION_SUBGROUPS: dict[str, list[str]] = {
    "50107": [
        "Structural Framing",
        "Roof Battens",
        "Floor System",
        "Footings & Substructure",
    ],
    "50112": [
        "Roof Cladding",
        "Ridge, Barge & Hip",
        "Flashings",
        "Eaves & Drainage",
    ],
    "50113": [
        "Wall Cladding",
        "Wall Lining (External Face)",
    ],
    "50114": [
        "Doors",
        "Windows",
        "Door Hardware",
    ],
    "50115": [
        "Ceiling Linings",
        "Wall Linings",
        "Wet Area Linings",
        "Floor Substrate",
        "Floor Finishes",
        "Paint & Coatings",
    ],
    "50124": [
        "Stairs",
        "Balustrades",
    ],
    "50129": [
        "Sanitary Fixtures",
        "Tapware & Accessories",
        "Cabinetry",
    ],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_subgroup(section_code: str, family: str) -> str | None:
    """Return the subgroup name for *family* in *section_code*, or None."""
    sg = FAMILY_TO_SUBGROUP.get(family)
    if sg is None:
        return None
    subgroups = SECTION_SUBGROUPS.get(section_code, [])
    if sg in subgroups:
        return sg
    return None


def make_subgroup_header(
    section_code: str,
    subgroup_name: str,
    *,
    sort_key: int = 0,
) -> dict:
    """Create a display-only subgroup header row.

    *sort_key* should be set to (min child sort key - 1) by the caller
    so the header appears immediately before its first child.
    """
    return {
        "item_name":               f"[SUBGROUP] {subgroup_name}",
        "item_display_name":       subgroup_name,
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
        "quantity_basis":          "DISPLAY HEADER — no quantity.",
        "source_evidence":         "export_layer",
        "derivation_rule":         "insert_subgroup_headers",
        "family_sort_key":         sort_key,
    }


def insert_subgroup_headers(
    items: list[dict],
    *,
    sections: set[str] | None = None,
) -> list[dict]:
    """Insert subgroup header rows for all populated subgroups.

    A header is only inserted when at least one real item (non-header) in
    the section classifies to that subgroup.  Empty subgroups are skipped.

    The header's ``family_sort_key`` is set to (min child sort key - 1)
    so it always renders immediately before its first child.

    Parameters
    ----------
    items:
        Full BOQ items list (already passed through section remap rules).
    sections:
        Sections to process.  Defaults to all keys in SECTION_SUBGROUPS.
    """
    from .family_classifier import classify

    if sections is None:
        sections = set(SECTION_SUBGROUPS.keys())

    # Pass 1: find populated subgroups and their minimum child sort key
    subgroup_min_sk: dict[tuple[str, str], int] = {}

    for item in items:
        if item.get("export_class") == "export_only_grouping":
            continue
        code = item.get("commercial_package_code", "50199")
        if code not in sections:
            continue
        family = classify(item.get("item_name", ""))
        sg = get_subgroup(code, family)
        if not sg:
            continue
        key = (code, sg)
        sk = item.get("family_sort_key", 500)
        if key not in subgroup_min_sk or sk < subgroup_min_sk[key]:
            subgroup_min_sk[key] = sk

    if not subgroup_min_sk:
        return items

    # Pass 2: build one header per populated subgroup
    headers = []
    for section, subgroups in SECTION_SUBGROUPS.items():
        if section not in sections:
            continue
        for sg in subgroups:
            key = (section, sg)
            if key not in subgroup_min_sk:
                continue
            sk = max(0, subgroup_min_sk[key] - 1)
            headers.append(make_subgroup_header(section, sg, sort_key=sk))

    return items + headers
