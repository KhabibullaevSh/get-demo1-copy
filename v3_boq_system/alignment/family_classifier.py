"""
family_classifier.py — Reusable keyword/pattern classifier that maps item
descriptions to a canonical family name.

Design rules
------------
- No project-specific item numbers or codes.
- Longest/highest-priority match wins.
- Each rule is a tuple of (family_name, [keyword_fragments], priority).
  A keyword_fragment matches if it appears as a case-insensitive substring
  of the normalised description.
- Rules are sorted by priority (descending) at import time; within the same
  priority level the first match wins.
- Returns "unknown" when no rule matches — callers must handle this gracefully.
"""

from __future__ import annotations
import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# Rule table: (family_name, match_fragments, priority)
# Higher priority = evaluated first.  Use priority ≥ 200 for very specific
# items that would otherwise be caught by a broader rule.
# ---------------------------------------------------------------------------

_RULES: list[tuple[str, list[str], int]] = [

    # ── Substructure / Footings ─────────────────────────────────────────────
    ("footing_concrete",        ["footing", "concrete"],                    300),
    ("footing_formwork",        ["footing", "formwork"],                    300),
    ("footing_reinforcement",   ["footing", "reo bar"],                     300),
    ("footing_reinforcement",   ["footing", "reinforcement"],               300),
    ("footing_reinforcement",   ["reo bar"],                                200),
    ("footing_reinforcement",   ["reinforcement bar"],                      200),
    ("footing_reinforcement",   ["rebar"],                                  200),
    ("bar_chair",               ["bar chair", "reo spacer"],                250),
    ("termite_barrier",         ["termite"],                                200),
    # Single-keyword rules so "Bulk Earthworks" and "Site Preparation" classify correctly
    ("earthworks",              ["bulk earth"],                             215),
    ("earthworks",              ["earthwork"],                              210),
    ("earthworks",              ["excavat"],                                210),
    ("site_prep",               ["site prep"],                              215),
    ("site_prep",               ["site clear"],                             215),
    # Original AND-logic combined rules (backward compat):
    ("earthworks",              ["earthwork", "excavat", "bulk earth"],     200),
    ("site_prep",               ["site prep", "site clear"],                200),
    ("pad_footing",             ["pad footing", "pad foot"],                250),
    ("strip_footing",           ["strip footing"],                          250),
    ("concrete_supply",         ["concrete supply"],                        250),
    ("dpm",                     ["dpm", "polyethylene", "moisture barrier"], 200),

    # ── Structural Frame ────────────────────────────────────────────────────
    # Single-keyword rules at priority 215 (each keyword independently classifies)
    ("wall_frame",              ["wall frame"],                              215),
    ("wall_frame",              ["wall panel frame"],                        215),
    ("wall_frame",              ["framecad wall"],                           215),
    ("wall_frame",              ["lgs wall"],                                215),
    ("roof_truss",              ["roof truss"],                              215),
    ("roof_truss",              ["truss frame"],                             215),
    ("roof_truss",              ["framecad truss"],                          215),
    ("roof_panel_frame",        ["roof panel frame"],                        215),
    ("roof_panel_frame",        ["purlin"],                                  215),
    ("verandah_frame",          ["verandah frame"],                          215),
    ("verandah_frame",          ["verandah roof"],                           215),
    ("steel_post",              ["steel post"],                              215),
    ("steel_post",              ["shs post"],                                215),
    ("steel_post",              ["shs steel"],                               215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("wall_frame",              ["wall frame", "wall panel frame",
                                  "lgs c89", "lgs wall", "framecad wall"],  200),
    ("roof_truss",              ["roof truss", "truss frame",
                                  "framecad truss"],                         200),
    ("roof_panel_frame",        ["roof panel frame", "purlin"],              210),
    ("verandah_frame",          ["verandah frame", "verandah roof"],        200),
    ("steel_post",              ["steel post", "shs post", "shs steel",
                                  "steel stump", "adjustable steel stump"], 200),
    ("steel_beam",              ["steel beam", "shs beam", "rhs beam"],     200),
    ("angle_brace",             ["angle brac", "db |", "diagonal brac"],    200),
    ("strap_brace",             ["strap brace", "diagonal strap",
                                  "roof brac"],                             200),
    ("post_bracket",            ["ph (corner)", "ph (central)",
                                  "ph (perimeter)", "bracket"],              150),
    ("hold_down",               ["hold down", "fix washer", "hold-down"],   200),
    ("joist_hanger",            ["joist hanger"],                            215),
    ("joist_hanger",            ["joist hanger", "multi grip",
                                  "triple grip"],                            200),
    ("support_angle",           ["support angle", "hdg angle"],             180),

    # ── Floor System ────────────────────────────────────────────────────────
    # Single-keyword rules at priority 215
    ("floor_cassette",          ["floor cassette"],                          215),
    ("floor_cassette",          ["floor cassette panel"],                    215),
    ("joist",                   ["floor joist"],                             215),
    ("joist",                   ["joist"],                                   215),
    ("floor_edge_beam",         ["floor edge"],                              215),
    ("floor_edge_beam",         ["edge beam"],                               215),
    ("floor_stringer",          ["floor stringer"],                          215),
    ("floor_stringer",          ["stringer"],                                215),
    ("bearer",                  ["floor bearer"],                            215),
    ("bearer",                  ["bearer"],                                  215),
    ("support_post",            ["support post"],                            215),
    ("support_post",            ["sub-floor support"],                       215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("floor_cassette",          ["floor cassette", "floor panel",
                                  "floor cassette panel"],                   220),
    ("joist",                   ["floor joist", "joist", "c150"],            200),
    ("floor_edge_beam",         ["edge beam", "floor edge"],                210),
    ("floor_stringer",          ["stringer", "floor stringer"],             210),
    ("bearer",                  ["floor bearer", "bearer",
                                  "support beam"],                           200),
    ("support_post",            ["sub-floor support", "support post",
                                  "adjustable post"],                        200),
    # Single-keyword rules at priority 225 to beat floor_finish (215)
    # so "Floor Sheet X" rows route to Floor System, not Floor Finishes
    ("floor_substrate",         ["fc sheet floor"],                          225),
    ("floor_substrate",         ["floor sheet"],                             225),
    ("floor_substrate",         ["subfloor sheet"],                          220),
    ("floor_substrate",         ["floor board"],                             220),
    # Original AND-logic combined rule (backward compat):
    ("floor_substrate",         ["fc sheet floor", "floor sheet",
                                  "floor board", "subfloor sheet"],          220),

    # ── Roof Frame / Battens ─────────────────────────────────────────────────
    # Single-keyword rules (each keyword independently classifies)
    ("roof_batten",             ["top-hat batten"],                          215),
    ("roof_batten",             ["top hat batten"],                          215),
    ("roof_batten",             ["roof batten"],                             215),
    ("roof_batten",             ["roof top hat"],                            212),
    ("ceiling_batten",          ["ceiling/wall batten"],                     215),
    ("ceiling_batten",          ["ceiling batten"],                          215),
    ("ceiling_batten",          ["soffit batten"],                           215),
    ("ceiling_batten",          ["lgs batten"],                              212),
    # Original combined rule kept for backward compatibility (AND logic):
    ("roof_batten",             ["roof batten", "top hat batten",
                                  "top-hat batten", "top hat"],              210),
    ("ceiling_batten",          ["ceiling batten", "ceiling/wall batten",
                                  "lgs batten", "wall batten"],              210),

    # ── Roof Cladding ────────────────────────────────────────────────────────
    # Single-keyword rules at priority 215 (each keyword independently classifies)
    ("roof_cladding",           ["roof cladding"],                           215),
    ("roof_cladding",           ["custom orb"],                              215),
    ("roof_cladding",           ["corrugated iron"],                         215),
    ("roof_cladding",           ["corrugated"],                              215),
    ("roof_cladding",           ["colorbond"],                               215),
    ("roof_cladding",           ["zincalume"],                               215),
    ("roof_cladding",           ["panel rib"],                               215),
    ("hip_capping",             ["hip cap"],                                 215),
    ("ridge_capping",           ["ridge cap"],                               215),
    ("barge_capping",           ["barge cap"],                               215),
    ("birdproof_foam",          ["bird proof"],                              215),
    ("birdproof_foam",          ["birdproof"],                               215),
    ("birdproof_foam",          ["bird proofing"],                           215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("roof_cladding",           ["roof cladding", "custom orb",
                                  "corrugated iron", "colorbond",
                                  "zincalume", "panel rib"],                 200),
    ("hip_capping",             ["hip cap", "hip capping"],                  220),
    ("ridge_capping",           ["ridge cap", "ridge capping"],              220),
    ("barge_capping",           ["barge cap", "barge capping"],              220),
    ("apron_flashing",          ["apron flashing"],                          230),
    ("fascia",                  ["fascia"],                                   200),
    ("birdproof_foam",          ["bird proof", "birdproof", "bird proofing"], 200),

    # ── Roof Plumbing ────────────────────────────────────────────────────────
    # Single-keyword rules (each keyword independently classifies)
    ("gutter_accessory",        ["gutter joiner"],                           220),
    ("gutter_accessory",        ["gutter stop"],                             220),
    ("gutter_accessory",        ["gutter drop"],                             220),
    ("gutter_accessory",        ["gutter hanger"],                           220),
    ("gutter_accessory",        ["gutter clip"],                             220),
    ("gutter_accessory",        ["endcap"],                                  220),
    ("gutter_accessory",        ["end cap"],                                 220),
    ("gutter_accessory",        ["downspout connector"],                     220),
    ("gutter",                  ["gutter"],                                  215),
    ("gutter",                  ["rain gutter"],                             215),
    ("gutter",                  ["eave gutter"],                             215),
    ("downpipe",                ["downpipe"],                                215),
    ("downpipe",                ["downspout"],                               215),
    ("downpipe",                ["pvc pipe 50mm"],                           215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("gutter",                  ["gutter", "rain gutter", "eave gutter"],    200),
    ("gutter_accessory",        ["gutter joiner", "gutter stop",
                                  "gutter drop", "gutter hanger",
                                  "gutter clip", "endcap", "end cap",
                                  "downspout connector"],                     220),
    ("downpipe",                ["downpipe", "downspout", "pvc pipe 50mm"], 200),
    ("sisalation",              ["sisalation", "sarking", "reflective foil"], 200),
    ("sisalation_tape",         ["sisalation tape", "building wrap tape",
                                  "lap tape"],                               220),

    # ── External Cladding ────────────────────────────────────────────────────
    ("weatherboard",            ["weatherboard", "weather board",
                                  "fc weatherboard", "fc cladding"],         200),
    ("external_corner_flashing",["corner flashing", "corner trim",
                                  "external corner"],                         220),
    ("building_wrap",           ["building wrap", "sarking"],                200),
    ("soffit_flashing",         ["soffit flashing"],                         220),
    ("pvc_h_joiner",            ["pvc h joiner", "external h joiner",
                                  "pvc joiner"],                              220),
    ("reveal_trim",             ["reveal trim", "window reveal",
                                  "door reveal"],                             200),
    ("stud_clip",               ["stud clip", "stud fixing clip"],           220),
    ("expansion_sealant",       ["expansion joint", "sealant"],              200),

    # ── Openings — Doors ─────────────────────────────────────────────────────
    # Single-keyword rules at priority 215/225 (each keyword independently classifies)
    ("door_flashing",           ["door head flashing"],                      225),
    ("door_flashing",           ["door sill flashing"],                      225),
    ("door_flashing",           ["door flashing"],                           225),
    ("door_frame",              ["door frame"],                              215),
    ("door_frame",              ["timber door frame"],                       215),
    ("door_lockset",            ["lockset"],                                 215),
    ("door_lockset",            ["privacy set"],                             215),
    ("door_lockset",            ["entrance set"],                            215),
    ("door_lockset",            ["deadbolt"],                                215),
    ("door_lockset",            ["door lock"],                               215),
    ("door_stop",               ["door stop"],                               215),
    ("door_stop",               ["door stopper"],                            215),
    ("door_closer",             ["door closer"],                             215),
    ("door_closer",             ["hydraulic closer"],                        215),
    ("door",                    ["door —"],                                  215),
    ("door",                    ["door-"],                                   215),
    ("door",                    ["door |"],                                  215),
    ("door",                    ["solid core"],                              215),
    ("door",                    ["hollow core"],                             215),
    ("door",                    ["door leaf"],                               215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("door",                    ["door |", "door-", "door —",
                                  "solid core", "hollow core",
                                  "door leaf"],                               200),
    ("door_frame",              ["door frame", "timber door frame"],         210),
    ("door_hinge",              ["hinge", "fixed pin"],                      220),
    ("door_hinge",              ["door", "hinge"],                           180),
    ("door_hinge",              ["hinge (pair)"],                            220),
    ("door_hinge",              ["hinge pair"],                              220),
    ("door_lockset",            ["lockset", "privacy set", "entrance set",
                                  "deadbolt", "door lock"],                   200),
    ("door_stop",               ["door stop", "door stopper"],               210),
    ("door_closer",             ["door closer", "hydraulic closer"],         210),
    ("door_flashing",           ["door head flashing", "door sill flashing",
                                  "door flashing"],                           230),

    # ── Openings — Windows ───────────────────────────────────────────────────
    # Single-keyword rules at priority 215/225 (each keyword independently classifies)
    ("window_flashing",         ["window head flashing"],                    225),
    ("window_flashing",         ["window sill flashing"],                    225),
    ("window_flashing",         ["window flashing"],                         225),
    ("window_security",         ["security bar"],                            215),
    ("window_security",         ["window bar"],                              215),
    ("window_security",         ["window strip"],                            215),
    ("louvre_blade",            ["louvre blade"],                            215),
    ("louvre_blade",            ["louvre glass"],                            215),
    ("louvre_blade",            ["louvre bar"],                              215),
    ("louvre_blade",            ["louvre frame"],                            215),
    ("fly_screen",              ["fly screen"],                              215),
    ("fly_screen",              ["flyscreen"],                               215),
    ("fly_screen",              ["insect screen"],                           215),
    ("window",                  ["window —"],                                215),
    ("window",                  ["window—"],                                 215),
    ("window",                  ["window |"],                                215),
    ("window",                  ["timber window"],                           215),
    ("window",                  ["louvre window"],                           215),
    ("window",                  ["window frame"],                            215),
    # Original combined rules preserved for backward compatibility (AND logic):
    ("window",                  ["window |", "window—", "window —",
                                  "timber window", "louvre window",
                                  "window frame"],                            200),
    ("louvre_blade",            ["louvre blade", "louvre glass",
                                  "louvre bar", "louvre frame"],              210),
    ("fly_screen",              ["fly screen", "flyscreen", "insect screen"], 200),
    ("window_flashing",         ["window head flashing", "window sill flashing",
                                  "window flashing"],                         230),
    ("window_security",         ["security bar", "round bar", "window bar",
                                  "window strip"],                            220),
    ("glazing",                 ["glazing", "glass panel", "louvre glass",
                                  "clear glass", "frosted glass"],            200),

    # ── Architrave / Trim ────────────────────────────────────────────────────
    ("architrave",              ["architrave"],                              200),
    ("skirting",                ["skirting"],                                200),
    ("cornice",                 ["cornice", "quad cornice", "ceiling trim"], 200),
    ("decking",                 ["decking", "wpc", "timber decking"],        200),

    # ── Wall Lining / Internal Linings ───────────────────────────────────────
    # Single-keyword rules (each keyword independently classifies)
    ("internal_wall_lining",    ["internal wall lining"],                    215),
    ("internal_wall_lining",    ["internal lining"],                         215),
    ("internal_wall_lining",    ["fc wall sheet"],                           215),
    ("internal_wall_lining",    ["fc sheet wall"],                           215),
    ("internal_wall_lining",    ["internal fc sheet"],                       215),
    ("internal_wall_lining",    ["internal fc wall"],                        215),
    ("external_wall_lining",    ["external wall lining"],                    215),
    ("external_wall_lining",    ["external lining"],                         215),
    ("external_wall_lining",    ["external fc sheet"],                       215),
    ("ceiling_lining",          ["fc sheet ceiling"],                        215),
    ("ceiling_lining",          ["ceiling lining"],                          215),
    ("ceiling_lining",          ["ceiling fc sheet"],                        215),
    ("ceiling_lining",          ["soffit lining"],                           215),
    ("ceiling_lining",          ["fc ceiling"],                              215),
    # Original combined rules kept for backward compatibility:
    ("internal_wall_lining",    ["internal wall lining", "internal lining",
                                  "fc sheet wall", "fc wall sheet",
                                  "internal fc sheet"],                       210),
    ("external_wall_lining",    ["external wall lining", "external lining",
                                  "external fc sheet"],                       210),
    # Single-keyword rules so "Wet Area Wall Lining — FC Sheet Total Area" etc. resolve
    ("wet_area_lining",         ["wet area wall lining"],                    225),
    ("wet_area_lining",         ["wet area wall tiling"],                    225),
    ("wet_area_lining",         ["wet area board"],                          220),
    # Original AND-logic combined rule (backward compat):
    ("wet_area_lining",         ["wet area", "waterproof board",
                                  "wet area wall lining"],                    220),
    ("ceiling_lining",          ["ceiling lining", "fc ceiling",
                                  "fc sheet ceiling", "ceiling fc sheet",
                                  "soffit lining"],                           210),
    ("lining_joiner",           ["pvc h joiner", "lining joiner",
                                  "pvc joiner", "internal capping",
                                  "external capping"],                        220),
    ("lining_screw",            ["lining screw", "fc sheet screw",
                                  "ceiling fc sheet screw"],                  220),
    ("lining_adhesive",         ["lining adhesive", "fc sheet adhesive",
                                  "joint compound", "construction adhesive"], 220),

    # ── Floor Finish ─────────────────────────────────────────────────────────
    # Single-keyword rules at priority 215 (each keyword independently classifies)
    ("floor_finish",            ["floor finish"],                            215),
    ("floor_finish",            ["vinyl plank"],                             215),
    ("floor_finish",            ["vinyl floor"],                             215),
    ("floor_finish",            ["ceramic tile"],                            215),
    ("floor_finish",            ["floor tile"],                              215),
    ("floor_finish",            ["ceramic floor"],                           215),
    # Original combined rule preserved for backward compatibility (AND logic):
    ("floor_finish",            ["floor finish", "vinyl plank", "vinyl floor",
                                  "ceramic tile", "floor tile",
                                  "ceramic floor"],                           200),
    # Single-keyword rules at priority 225–230 to beat floor_finish (215)
    # so tile adhesive/grout rows route to Wet Area Linings, not Floor Finishes
    ("floor_tile_adhesive",     ["floor tile adhesive"],                     230),
    ("floor_tile_adhesive",     ["wet area tile adhesive"],                  230),
    ("floor_tile_adhesive",     ["tile adhesive"],                           225),
    ("floor_tile_grout",        ["floor tile grout"],                        230),
    ("floor_tile_grout",        ["wet area wall tile grout"],                230),
    ("floor_tile_grout",        ["wet area tile grout"],                     230),
    ("floor_tile_grout",        ["tile grout"],                              225),
    # Original AND-logic combined rules (backward compat):
    ("floor_tile_adhesive",     ["tile adhesive", "floor adhesive"],         220),
    ("floor_tile_grout",        ["tile grout", "floor grout"],               220),
    # Single-keyword waterproofing rules at 215–225
    ("wet_area_waterproofing",  ["waterproof membrane"],                     225),
    ("wet_area_waterproofing",  ["waterproofing"],                           215),
    ("wet_area_waterproofing",  ["wet area membrane"],                       215),
    # Original AND-logic combined rule (backward compat):
    ("wet_area_waterproofing",  ["waterproof membrane", "waterproofing",
                                  "wet area membrane"],                       210),
    ("floor_trim",              ["floor trim", "floor angle"],               200),

    # ── Painting ─────────────────────────────────────────────────────────────
    # Single-keyword rules at priority 215 (each keyword independently classifies)
    ("painting",                ["paint —"],                                 215),
    ("painting",                ["paint -"],                                 215),
    ("painting",                ["painting"],                                215),
    ("painting",                ["paint primer"],                            215),
    ("painting",                ["sealer coat"],                             215),
    ("painting",                ["primer"],                                  215),
    # Original combined rule preserved for backward compatibility (AND logic):
    ("painting",                ["paint —", "paint -", "painting",
                                  "paint primer", "sealer coat",
                                  "primer"],                                  200),

    # ── Insulation ───────────────────────────────────────────────────────────
    ("insulation_batts",        ["insulation batt", "glasswool batt",
                                  "wall batt", "roof batt",
                                  "ceiling batt"],                            200),
    ("dpm_membrane",            ["malthoid", "damp proof membrane",
                                  "vapour barrier"],                          200),

    # ── Structural Fixings ───────────────────────────────────────────────────
    ("screw_fixing",            ["screw", "self drilling", "self-drilling",
                                  "countersunk", "wafer head", "hex head"],   180),
    ("bolt_fixing",             ["bolt", "nut", "washer", "sleeve anchor",
                                  "dyna bolt", "cup head"],                   180),
    ("grommet",                 ["grommet"],                                  200),
    ("glue_special",            ["special glue", "pvc glue"],               200),

    # ── Hydraulics ───────────────────────────────────────────────────────────
    ("hot_water_system",        ["hot water", "hot water system"],           220),
    ("hydraulic_fixture",       ["water closet", "wc pan", "wc cistern",
                                  "toilet suite", "hand basin", "vanity basin",
                                  "laundry sink", "kitchen sink",
                                  "floor waste", "shower tray"],              200),
    ("tapware",                 ["tap", "tapware", "tap mixer"],             200),
    ("pex_pipe",                ["pex pipe", "pex tube"],                    210),
    ("pex_fitting",             ["pex", "coupling", "elbow", "tee piece",
                                  "adaptor", "pex straight"],                 180),
    ("hydraulic_allowance",     ["builder's works", "hydraulics",
                                  "plumbing allowance",
                                  "plumbing (consulting)", "plumbing (toilet)"], 200),

    # ── Electrical Services ───────────────────────────────────────────────────
    ("light_fitting",           ["led oyster", "led flood", "light fitting",
                                  "downlight"],                               200),
    ("ceiling_fan",             ["ceiling fan"],                              210),
    ("exhaust_fan",             ["exhaust fan"],                              210),
    ("smoke_detector",          ["smoke detector"],                           210),
    ("gpo_switch",              ["gpo", "light switch", "switch"],            200),
    ("switchboard",             ["load centre", "mcb", "rcbo", "switchboard",
                                  "mains entry"],                              200),
    ("cable",                   ["cable", "twin active", "building wire"],    200),
    ("conduit",                 ["conduit", "coupling", "saddle"],            200),
    ("electrical_allowance",    ["electrical allowance",
                                  "builder's works", "electrical ("],         200),
    ("air_conditioning",        ["air conditioning", "mechanical", "hvac",
                                  "split system"],                             200),

    # ── Stairs & Balustrades ─────────────────────────────────────────────────
    ("stair_stringer",          ["stair stringer"],                           220),
    ("stair_tread",             ["stair tread", "step tread", "step treads"], 220),
    ("stair_newel",             ["newel post"],                               220),
    ("stair_balustrade",        ["stair balustrade", "balustrade panel",
                                  "mesh panel"],                               220),
    ("handrail",                ["handrail", "handrail saddle",
                                  "galvanised pipe"],                          200),
    ("access_ramp",             ["ramp", "access ramp"],                     200),
    ("balustrade_fitting",      ["base flange", "cross type", "tee type",
                                  "swivel", "elbow", "end cap", "joint type",
                                  "fixing bracket"],                           180),

    # ── FFE ─────────────────────────────────────────────────────────────────
    ("ffe_toilet",              ["toilet", "wc pan", "wc cistern",
                                  "toilet roll", "toilet suite"],              200),
    ("ffe_basin",               ["hand basin", "vanity basin"],               200),
    ("ffe_mirror",              ["mirror", "bathroom mirror"],                 200),
    ("ffe_towel_rail",          ["towel rail", "hand towel"],                  200),
    ("ffe_soap_holder",         ["soap", "soap dispenser", "soap holder"],    200),
    ("ffe_shower",              ["shower tray", "shower curtain",
                                  "shower tap"],                               200),
    ("ffe_kitchen",             ["kitchen sink", "kitchen tap",
                                  "kitchen cabinet", "kitchen cupboard",
                                  "base cabinet", "wall cabinet",
                                  "drawer cabinet", "corner base",
                                  "bench top", "rangehood"],                   200),
    ("ffe_laundry",             ["laundry sink", "laundry tap",
                                  "laundry assembly", "washing machine tap"],  200),
    ("ffe_refrigeration",       ["cold room", "refrigeration"],               200),

    # ── Services Placeholders ────────────────────────────────────────────────
    ("fire_placeholder",        ["fire", "sprinkler", "hose reel",
                                  "fire hydrant"],                             180),
    ("communications_placeholder", ["data", "communications",
                                    "fibre", "antenna"],                       180),
    ("mechanical_allowance",    ["mechanical allowance", "hvac allowance"],   180),

    # ── Catch-alls (lowest priority) ─────────────────────────────────────────
    ("accessory",               ["accessory", "accessories",
                                  "fixing plate", "fix plate", "connector"],  100),
    ("unknown",                 [],                                             0),
]

# Pre-sort by priority descending (stable sort preserves insertion order within
# the same priority level).
_SORTED_RULES: list[tuple[str, list[str], int]] = sorted(
    _RULES, key=lambda r: r[2], reverse=True
)


@lru_cache(maxsize=2048)
def classify(description: str) -> str:
    """Return the canonical family name for *description*.

    The match is case-insensitive substring search.  The highest-priority
    rule whose *all* keyword fragments appear in the normalised description
    wins.  Returns ``"unknown"`` when nothing matches.
    """
    norm = description.lower()
    for family, fragments, _priority in _SORTED_RULES:
        if not fragments:
            continue
        if all(frag.lower() in norm for frag in fragments):
            return family
    return "unknown"


def classify_many(descriptions: list[str]) -> list[str]:
    """Classify a list of descriptions, returning a list of family names."""
    return [classify(d) for d in descriptions]


# Family groupings — used by the comparator and upgrade rules
FAMILY_GROUPS: dict[str, list[str]] = {
    "substructure": [
        "strip_footing", "pad_footing", "footing_concrete", "footing_formwork",
        "footing_reinforcement", "bar_chair", "termite_barrier", "earthworks",
        "site_prep", "concrete_supply", "dpm",
    ],
    "structural_frame": [
        "wall_frame", "roof_truss", "roof_panel_frame", "verandah_frame",
        "steel_post", "steel_beam", "angle_brace", "strap_brace",
        "post_bracket", "hold_down", "joist_hanger", "support_angle",
    ],
    "floor_system": [
        "floor_cassette", "joist", "floor_edge_beam", "floor_stringer",
        "bearer", "support_post", "floor_substrate",
    ],
    "roof_frame": [
        "roof_batten", "ceiling_batten",
    ],
    "roof_cladding": [
        "roof_cladding", "hip_capping", "ridge_capping", "barge_capping",
        "apron_flashing", "fascia", "birdproof_foam",
        "gutter", "gutter_accessory", "downpipe",
        "sisalation", "sisalation_tape",
    ],
    "external_cladding": [
        "weatherboard", "external_corner_flashing", "building_wrap",
        "soffit_flashing", "pvc_h_joiner", "reveal_trim",
        "stud_clip", "expansion_sealant",
    ],
    "openings": [
        "door", "door_frame", "door_hinge", "door_lockset", "door_stop",
        "door_closer", "door_flashing",
        "window", "louvre_blade", "fly_screen", "window_flashing",
        "window_security", "glazing",
    ],
    "trim": [
        "architrave", "skirting", "cornice", "decking",
    ],
    "linings": [
        "internal_wall_lining", "external_wall_lining", "wet_area_lining",
        "ceiling_lining", "lining_joiner", "lining_screw", "lining_adhesive",
    ],
    "floor_finish": [
        "floor_finish", "floor_tile_adhesive", "floor_tile_grout",
        "wet_area_waterproofing", "floor_trim",
    ],
    "painting": [
        "painting",
    ],
    "insulation": [
        "insulation_batts", "dpm_membrane",
    ],
    "fixings": [
        "screw_fixing", "bolt_fixing", "grommet", "glue_special",
    ],
    "hydraulics": [
        "hot_water_system", "hydraulic_fixture", "tapware",
        "pex_pipe", "pex_fitting", "hydraulic_allowance",
    ],
    "electrical": [
        "light_fitting", "ceiling_fan", "exhaust_fan", "smoke_detector",
        "gpo_switch", "switchboard", "cable", "conduit",
        "electrical_allowance", "air_conditioning",
    ],
    "stairs": [
        "stair_stringer", "stair_tread", "stair_newel", "stair_balustrade",
        "handrail", "access_ramp", "balustrade_fitting",
    ],
    "ffe": [
        "ffe_toilet", "ffe_basin", "ffe_mirror", "ffe_towel_rail",
        "ffe_soap_holder", "ffe_shower", "ffe_kitchen", "ffe_laundry",
        "ffe_refrigeration", "hydraulic_fixture",
    ],
    "placeholders": [
        "fire_placeholder", "communications_placeholder",
        "mechanical_allowance",
    ],
}

# Reverse map: family_name → group
FAMILY_TO_GROUP: dict[str, str] = {
    fam: grp
    for grp, fams in FAMILY_GROUPS.items()
    for fam in fams
}
