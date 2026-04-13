"""
commercial_block_mapper.py — SECTION → COMMERCIAL BLOCK classification.

Introduces a generalised *Commercial Block* layer under each section in
estimator export mode.  This supersedes the trade-group layer with a
section-aware strategy system:

    Strategy    Sections         Resolution method
    ─────────   ───────────────  ──────────────────────────────────────────
    TRADE       50107–50118      family → block  (building trades)
    KEYWORD     50117, 50119     item_name keyword matching (services)
    ASSEMBLY    50124            keyword + family  (stairs / ramps)
    ROOM        50129            room/area metadata (FFE)

Structure in estimator export:

    SECTION  (e.g. 50107 - Structural & Base Steel & Footings)
      COMMERCIAL BLOCK  (e.g. "Footings")      ← export_only_grouping header
        item
        item
      COMMERCIAL BLOCK  (e.g. "Frame")
        item

Commercial block header rows
────────────────────────────
  export_class               = "export_only_grouping"
  derivation_rule            = "insert_commercial_block_headers"
  quantity / unit            = None
  commercial_block_sort_key  = (block_idx + 1) × _CB_SCALE − 1
  manual_review              = False

Items gain two new fields
─────────────────────────
  commercial_block           = block name (None if ungrouped)
  commercial_block_sort_key  = (block_idx + 1) × _CB_SCALE + family_sort_key
  Ungrouped items            = _DEFAULT_CB_SK + family_sort_key  (sort last)

Sort key resolution in Excel (preferred → fallback):
  commercial_block_sort_key → trade_group_sort_key → family_sort_key

NON-NEGOTIABLE: quantities are never changed by this module.
"""
from __future__ import annotations

from enum import Enum

_CB_SCALE:      int = 10_000   # sort-key gap between consecutive commercial blocks
_DEFAULT_CB_SK: int = 90_000   # ungrouped items sort after all named blocks


class BlockStrategy(str, Enum):
    TRADE    = "trade"     # family → block  (building trades)
    KEYWORD  = "keyword"   # item_name keywords → block  (services)
    ASSEMBLY = "assembly"  # keyword + family → assembly package  (stairs/ramps)
    ROOM     = "room"      # room/area metadata → block  (FFE)


# ---------------------------------------------------------------------------
# TRADE strategy — building trade sections
# ---------------------------------------------------------------------------

# Global family → commercial block
_FAMILY_TO_BLOCK: dict[str, str] = {
    # ── 50107 Structural ─────────────────────────────────────────────────────
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
    "floor_substrate":           "Floor System",   # FC floor sheet stays in 50107
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
    "roof_batten":               "Frame",           # P3: battens grouped under Frame
    "ceiling_batten":            "Frame",           # P3: ceiling battens under Frame in 50107
    # ── 50112 Roof ───────────────────────────────────────────────────────────
    "roof_cladding":             "Roof Covering",
    "ridge_capping":             "Roof Covering",
    "hip_capping":               "Roof Covering",
    "barge_capping":             "Roof Covering",
    "apron_flashing":            "Roof Covering",
    "sisalation":                "Roof Covering",   # overridden for 50118
    "sisalation_tape":           "Roof Covering",   # overridden for 50118
    "flashing":                  "Roof Covering",
    "fascia":                    "Eaves",
    "soffit":                    "Eaves",
    "birdproof_foam":            "Eaves",
    "gutter":                    "Roof Plumbing",
    "gutter_accessory":          "Roof Plumbing",
    "downpipe":                  "Roof Plumbing",
    # ── 50113 External Cladding ──────────────────────────────────────────────
    "weatherboard":              "Wall Cladding",
    "external_wall_lining":      "Wall Cladding",
    "external_corner_flashing":  "Wall Cladding",
    "building_wrap":             "Wall Cladding",
    "pvc_h_joiner":              "Wall Cladding",
    "reveal_trim":               "Wall Cladding",
    "stud_clip":                 "Wall Cladding",
    "expansion_sealant":         "Wall Cladding",
    "soffit_flashing":           "Wall Cladding",
    # ── 50114 Openings ───────────────────────────────────────────────────────
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
    # ── 50115 Internal Linings & Finishes ────────────────────────────────────
    "ceiling_lining":            "Ceiling Finishes",
    "cornice":                   "Ceiling Finishes",
    "internal_wall_lining":      "Wall Finishes",
    "wet_area_lining":           "Wet Area Linings",   # P5
    "floor_finish":              "Floor Finishes",
    "floor_tile_adhesive":       "Wet Area Linings",   # P5
    "floor_tile_grout":          "Wet Area Linings",   # P5
    "wet_area_waterproofing":    "Wet Area Linings",   # P5
    "skirting":                  "Trims",
    "architrave":                "Trims",
    # ── 50116 Painting ───────────────────────────────────────────────────────
    "paint":                     "Paint & Coatings",   # P3: own section 50116
    "painting":                  "Paint & Coatings",   # P3
    # ── 50118 Insulation ─────────────────────────────────────────────────────
    "insulation":                "Insulation",
    "insulation_batts":          "Insulation",
    "dpm_membrane":              "Insulation",
}

# Section-specific overrides — (section, family) → block
# Checked before _FAMILY_TO_BLOCK for ambiguous families.
_SECTION_FAMILY_OVERRIDES: dict[tuple[str, str], str] = {
    # floor_substrate: FC floor sheet + accessories → Floor System in 50107 (P2)
    ("50107", "floor_substrate"):  "Floor System",
    ("50115", "floor_substrate"):  "Floor Finishes",
    # external_wall_lining in 50115 → Wall Finishes (P4)
    # (global _FAMILY_TO_BLOCK maps it to "Wall Cladding" for 50113)
    ("50115", "external_wall_lining"): "Wall Finishes",
    # ceiling_batten in 50115 → Ceiling Finishes (soffit/ceiling substrate)
    # (global _FAMILY_TO_BLOCK maps it to "Frame" for 50107)
    ("50115", "ceiling_batten"):       "Ceiling Finishes",
    # sisalation section routing
    ("50112", "sisalation"):       "Roof Covering",
    ("50118", "sisalation"):       "Insulation",
    ("50112", "sisalation_tape"):  "Roof Covering",
    ("50118", "sisalation_tape"):  "Insulation",
}

# Ordered blocks per TRADE section — first = displayed first
SECTION_TRADE_BLOCKS: dict[str, list[str]] = {
    "50107": ["Footings", "Substructure", "Floor System", "Frame"],  # P3: battens merged into Frame
    "50112": ["Roof Covering", "Eaves", "Roof Plumbing"],                        # P8
    "50113": ["Wall Cladding"],
    "50114": ["Doors", "Door Hardware", "Windows", "Window Accessories"],
    "50115": ["Ceiling Finishes", "Wall Finishes", "Wet Area Linings",
              "Floor Finishes", "Trims"],                                         # P5
    "50116": ["Paint & Coatings"],                                                # P3
    "50118": ["Insulation"],
}


# ---------------------------------------------------------------------------
# KEYWORD strategy — services sections
# ---------------------------------------------------------------------------

# Each entry: ([keyword_fragments], block_name)
# All fragments must appear (case-insensitive substring).  First match wins.
# MORE SPECIFIC patterns must come BEFORE broader patterns in the list.
_KEYWORD_BLOCKS: dict[str, list[tuple[list[str], str]]] = {
    "50117": [
        # ── Sanitary accessories (specific keywords first) ────────────────────
        (["toilet roll"],            "Sanitary Accessories"),
        (["hand towel"],             "Sanitary Accessories"),
        (["towel rail"],             "Sanitary Accessories"),
        (["soap dispenser"],         "Sanitary Accessories"),
        (["soap holder"],            "Sanitary Accessories"),
        (["mirror"],                 "Sanitary Accessories"),
        (["medicine cabinet"],       "Sanitary Accessories"),
        # ── Sanitary fixtures ─────────────────────────────────────────────────
        (["wc pan"],                 "Sanitary Fixtures"),
        (["wc cistern"],             "Sanitary Fixtures"),
        (["toilet suite"],           "Sanitary Fixtures"),
        # ── Wet area finishes ────────────────────────────────────────────────
        (["wet area wall"],          "Wet Area Finishes"),
        (["wall tiling"],            "Wet Area Finishes"),
        (["wet area tiling"],        "Wet Area Finishes"),
        (["waterproofing"],          "Wet Area Finishes"),
        (["wet area membrane"],      "Wet Area Finishes"),
        (["waterproof membrane"],    "Wet Area Finishes"),
        # ── Plumbing fixtures ────────────────────────────────────────────────
        (["hand basin"],             "Plumbing Fixtures"),
        (["basin"],                  "Plumbing Fixtures"),
        (["floor waste"],            "Plumbing Fixtures"),
        (["tapware"],                "Plumbing Fixtures"),
        (["tap mixer"],              "Plumbing Fixtures"),
        # ── Water services ───────────────────────────────────────────────────
        (["hot water system"],       "Water Services"),
        (["hot water"],              "Water Services"),
        (["water meter"],            "Water Services"),
        (["stopcock"],               "Water Services"),
        (["water main"],             "Water Services"),
        # ── Electrical works — BEFORE "builder's works" so that
        #    "Builder's Works — Electrical (…)" resolves to Electrical Works ──
        (["exhaust fan"],            "Electrical Works"),
        (["ceiling fan"],            "Electrical Works"),
        (["switchboard"],            "Electrical Works"),
        (["distribution board"],     "Electrical Works"),
        (["smoke detector"],         "Electrical Works"),
        (["fire alarm"],             "Electrical Works"),
        (["electrical"],             "Electrical Works"),
        # ── Mechanical ───────────────────────────────────────────────────────
        (["air conditioning"],       "Mechanical"),
        (["mechanical ventilation"], "Mechanical"),
        (["mechanical"],             "Mechanical"),
        (["hvac"],                   "Mechanical"),
        # ── Refrigeration ────────────────────────────────────────────────────
        (["cold room"],              "Refrigeration"),
        (["refrigeration"],          "Refrigeration"),
        # ── Builder's works — last; "Builder's Works — Plumbing (…)" lands here
        (["builder's works"],        "Builder's Works"),
        (["builders works"],         "Builder's Works"),
        (["plumbing allowance"],     "Builder's Works"),
        (["plumbing ("],             "Builder's Works"),
    ],
    "50119": [
        (["switchboard"],            "Switchboard & Protection"),
        (["distribution board"],     "Switchboard & Protection"),
        (["mcb"],                    "Switchboard & Protection"),
        (["rcbo"],                   "Switchboard & Protection"),
        (["load centre"],            "Switchboard & Protection"),
        (["cable"],                  "Cabling"),
        (["building wire"],          "Cabling"),
        (["twin active"],            "Cabling"),
        (["conduit"],                "Conduit & Containment"),
        (["saddle"],                 "Conduit & Containment"),
        (["gpo"],                    "Power Outlets"),
        (["power point"],            "Power Outlets"),
        (["light switch"],           "Switching"),
        (["light fitting"],          "Lighting"),
        (["downlight"],              "Lighting"),
        (["led"],                    "Lighting"),
        (["exhaust fan"],            "Ventilation"),
        (["ceiling fan"],            "Ventilation"),
        (["smoke detector"],         "Safety Devices"),
        (["fire alarm"],             "Safety Devices"),
        (["air conditioning"],       "Mechanical"),
        (["split system"],           "Mechanical"),
        (["hvac"],                   "Mechanical"),
        (["electrical allowance"],   "Builder's Works"),
        (["builder's works"],        "Builder's Works"),
        (["builders works"],         "Builder's Works"),
    ],
}

# Ordered blocks per KEYWORD section — drives sort key assignment
SECTION_KEYWORD_BLOCK_ORDER: dict[str, list[str]] = {
    "50117": [
        "Plumbing Fixtures",
        "Sanitary Fixtures",
        "Sanitary Accessories",
        "Wet Area Finishes",
        "Water Services",
        "Electrical Works",
        "Mechanical",
        "Refrigeration",
        "Builder's Works",
    ],
    "50119": [
        "Switchboard & Protection",
        "Cabling",
        "Conduit & Containment",
        "Power Outlets",
        "Switching",
        "Lighting",
        "Ventilation",
        "Safety Devices",
        "Mechanical",
        "Builder's Works",
    ],
}


# ---------------------------------------------------------------------------
# ASSEMBLY strategy — stairs & ramps section (50124)
# ---------------------------------------------------------------------------

# Ordered keyword rules: ([fragments], block).  All fragments must appear.
# Evaluated BEFORE family fallback.  More specific rules first.
_ASSEMBLY_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["ramp"],                 "Steel Ramp"),         # catches all ramp items
    (["verandah balustrade"],  "Verandah Balustrade"),
    (["verandah handrail"],    "Verandah Balustrade"),
    (["verandah"],             "Verandah Balustrade"),
    (["stair"],                "Stairs"),              # catches stair balustrade too
]

# Family fallback — used only when no keyword rule matches
_ASSEMBLY_FAMILY_BLOCKS: dict[str, str] = {
    "stair_stringer":    "Stairs",
    "stair_tread":       "Stairs",
    "stair_newel":       "Stairs",
    "stair_balustrade":  "Balustrades",
    "handrail":          "Stairs",
    "access_ramp":       "Steel Ramp",
    "balustrade_fitting": "Balustrades",
}

# Ordered blocks per ASSEMBLY section
SECTION_ASSEMBLY_BLOCK_ORDER: dict[str, list[str]] = {
    "50124": ["Stairs", "Steel Ramp", "Balustrades", "Verandah Balustrade"],
}


# ---------------------------------------------------------------------------
# ROOM strategy — FFE section (50129)
# ---------------------------------------------------------------------------

# Keyword rules against combined (item_name + source_evidence + notes)
_ROOM_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["dispensary", "prep"],      "Dispensary / Prep Area"),
    (["dispensary"],              "Dispensary / Prep Area"),
    (["pharmacy prep"],           "Dispensary / Prep Area"),
    (["consulting room"],         "Consulting Room"),
    (["consulting"],              "Consulting Room"),
    (["staff room"],              "Staff Room"),
    (["staff"],                   "Staff Room"),
    (["store room"],              "Store Room"),
    (["storeroom"],               "Store Room"),
    (["storage"],                 "Store Room"),
    (["toilet"],                  "Toilets / Amenities"),
    (["amenities"],               "Toilets / Amenities"),
    (["office"],                  "Office / Reception"),
    (["reception"],               "Office / Reception"),
    (["kitchen"],                 "Kitchen"),
    (["laundry"],                 "Laundry"),
]

SECTION_ROOM_BLOCK_ORDER: dict[str, list[str]] = {
    "50129": [
        "Dispensary / Prep Area",
        "Consulting Room",
        "Staff Room",
        "Store Room",
        "Toilets / Amenities",
        "Office / Reception",
        "Kitchen",          # P7: explicit — renders when kitchen FFE items exist
        "Laundry",
    ],
}


# ---------------------------------------------------------------------------
# Strategy routing — section → strategy
# ---------------------------------------------------------------------------

SECTION_STRATEGY: dict[str, BlockStrategy] = {
    "50107": BlockStrategy.TRADE,
    "50112": BlockStrategy.TRADE,
    "50113": BlockStrategy.TRADE,
    "50114": BlockStrategy.TRADE,
    "50115": BlockStrategy.TRADE,
    "50116": BlockStrategy.TRADE,   # P3: Painting section
    "50118": BlockStrategy.TRADE,
    "50117": BlockStrategy.KEYWORD,
    "50119": BlockStrategy.KEYWORD,
    "50124": BlockStrategy.ASSEMBLY,
    "50129": BlockStrategy.ROOM,
}


# ---------------------------------------------------------------------------
# Internal resolution helpers
# ---------------------------------------------------------------------------

def _resolve_trade(section: str, family: str) -> str | None:
    override = _SECTION_FAMILY_OVERRIDES.get((section, family))
    if override is not None:
        blocks = SECTION_TRADE_BLOCKS.get(section, [])
        return override if override in blocks else None
    block = _FAMILY_TO_BLOCK.get(family)
    if block is None:
        return None
    blocks = SECTION_TRADE_BLOCKS.get(section, [])
    return block if block in blocks else None


def _resolve_keyword(section: str, item_name: str) -> str | None:
    norm = item_name.lower()
    for fragments, block in _KEYWORD_BLOCKS.get(section, []):
        if all(f.lower() in norm for f in fragments):
            return block
    return None


def _resolve_assembly(section: str, family: str, item_name: str) -> str | None:
    norm = item_name.lower()
    # Keyword rules first (most specific)
    for fragments, block in _ASSEMBLY_KEYWORD_RULES:
        if all(f.lower() in norm for f in fragments):
            return block
    # "balustrade" keyword catch-all — catches standalone balustrade items
    # that are not stair/ramp/verandah (those were caught above)
    if "balustrade" in norm:
        return "Balustrades"
    # Family fallback
    return _ASSEMBLY_FAMILY_BLOCKS.get(family)


def _resolve_room(
    section: str,
    family: str,
    item_name: str,
    item: dict | None = None,
) -> str | None:
    # Build combined text from item_name + source_evidence + notes
    combined = item_name.lower()
    if item:
        ev    = (item.get("source_evidence") or "").lower()
        notes = (item.get("notes") or "").lower()
        combined = f"{combined} {ev} {notes}"
    for fragments, block in _ROOM_KEYWORD_RULES:
        if all(f.lower() in combined for f in fragments):
            return block
    return None


def _get_block_order(section: str) -> list[str]:
    """Return the ordered block list for *section*."""
    strategy = SECTION_STRATEGY.get(section)
    if strategy == BlockStrategy.TRADE:
        return SECTION_TRADE_BLOCKS.get(section, [])
    elif strategy == BlockStrategy.KEYWORD:
        return SECTION_KEYWORD_BLOCK_ORDER.get(section, [])
    elif strategy == BlockStrategy.ASSEMBLY:
        return SECTION_ASSEMBLY_BLOCK_ORDER.get(section, [])
    elif strategy == BlockStrategy.ROOM:
        return SECTION_ROOM_BLOCK_ORDER.get(section, [])
    return []


def _block_idx(section: str, block: str) -> int:
    """0-based index of *block* in its section's ordered list.
    Unknown blocks sort after all defined blocks."""
    order = _get_block_order(section)
    try:
        return order.index(block)
    except ValueError:
        return len(order)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_commercial_block(
    section_code: str,
    family: str,
    item_name: str,
    *,
    item: dict | None = None,
) -> str | None:
    """Return the commercial block for an item, or None if unresolved.

    Resolution is layered:
    1. Strategy routing by section (TRADE / KEYWORD / ASSEMBLY / ROOM).
    2. Within TRADE: section-specific override first, then global family map.
    3. Within KEYWORD/ASSEMBLY/ROOM: keyword rules, then family fallback.
    4. Returns None → item sorts into the ungrouped bucket (_DEFAULT_CB_SK).
    """
    strategy = SECTION_STRATEGY.get(section_code)
    if strategy is None:
        return None
    if strategy == BlockStrategy.TRADE:
        return _resolve_trade(section_code, family)
    elif strategy == BlockStrategy.KEYWORD:
        return _resolve_keyword(section_code, item_name)
    elif strategy == BlockStrategy.ASSEMBLY:
        return _resolve_assembly(section_code, family, item_name)
    elif strategy == BlockStrategy.ROOM:
        return _resolve_room(section_code, family, item_name, item=item)
    return None


def make_commercial_block_header(
    section_code: str,
    block: str,
    *,
    sort_key: int,
) -> dict:
    """Create a display-only commercial block header row.

    Has ``export_class="export_only_grouping"`` and ``quantity=None``.
    Never carries a quantity.
    """
    return {
        "item_name":               f"[COMMERCIAL BLOCK] {block}",
        "item_display_name":       block,
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
        "quantity_basis":          "COMMERCIAL BLOCK HEADER — no quantity.",
        "source_evidence":         "export_layer",
        "derivation_rule":         "insert_commercial_block_headers",
        "family_sort_key":         sort_key,    # backward-compat fallback
        "commercial_block_sort_key": sort_key,
        "commercial_block":        block,
    }


def insert_commercial_block_headers(
    items:    list[dict],
    *,
    sections: set[str] | None = None,
) -> list[dict]:
    """Classify items into commercial blocks, tag sort keys, insert headers.

    Steps
    -----
    1. For each real item whose section has a strategy:
       - Resolve → commercial block using that section's strategy.
       - Assign ``commercial_block_sort_key``:
           grouped   = (idx + 1) × _CB_SCALE + family_sort_key
           ungrouped = _DEFAULT_CB_SK + family_sort_key
    2. For each populated (section, block) pair:
       - Create a header at (idx + 1) × _CB_SCALE − 1
         (sorts immediately before its first child item).
    3. Return augmented items list + header rows (unsorted — caller sorts).

    The function uses ``commercial_package_code or package_code`` so it works
    correctly whether items carry the commercial code already or only the
    engine code.

    Parameters
    ----------
    items:
        Full BOQ items list. Section remaps (rule_estimator_transforms) must
        already have run so battens, etc., are in their commercial section.
    sections:
        Sections to process. Defaults to all keys in SECTION_STRATEGY.
    """
    from .family_classifier import classify

    if sections is None:
        sections = set(SECTION_STRATEGY.keys())

    # Pass 1: tag items with commercial_block and commercial_block_sort_key
    populated: dict[tuple[str, str], int] = {}

    for item in items:
        if item.get("export_class") == "export_only_grouping":
            continue
        # Prefer commercial_package_code; fall back to package_code
        code = item.get("commercial_package_code") or item.get("package_code", "50199")
        if code not in sections:
            continue

        family  = classify(item.get("item_name", ""))
        block   = get_commercial_block(code, family, item.get("item_name", ""), item=item)
        fam_sk  = item.get("family_sort_key", 500)

        if block:
            idx   = _block_idx(code, block)
            cb_sk = (idx + 1) * _CB_SCALE + fam_sk
        else:
            cb_sk = _DEFAULT_CB_SK + fam_sk

        item["commercial_block"]           = block
        item["commercial_block_sort_key"]  = cb_sk

        if block:
            key = (code, block)
            if key not in populated or cb_sk < populated[key]:
                populated[key] = cb_sk

    if not populated:
        return items

    # Pass 2: one header per populated (section, block)
    headers: list[dict] = []
    for section in sorted(SECTION_STRATEGY.keys()):
        if section not in sections:
            continue
        for idx, block in enumerate(_get_block_order(section)):
            if (section, block) not in populated:
                continue
            hdr_sk = (idx + 1) * _CB_SCALE - 1
            headers.append(
                make_commercial_block_header(section, block, sort_key=hdr_sk)
            )

    return items + headers
