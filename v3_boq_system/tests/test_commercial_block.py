"""
test_commercial_block.py — Regression tests for the commercial block layer.

Tests cover:
  1.  Trade-style block mapping (TRADE strategy)
  2.  Section-specific overrides
  3.  Keyword-based block mapping (KEYWORD strategy — services sections)
  4.  Assembly block mapping (ASSEMBLY strategy — stairs/ramps)
  5.  Room block mapping (ROOM strategy — FFE)
  6.  Commercial block header structure
  7.  Sort key arithmetic
  8.  Empty block suppression
  9.  Export mode gating (commercial/engine → no CB headers)
  10. No quantity mutation
  11. No traceability mutation
  12. Correct ordering (block headers before items, blocks in defined order)
  13. Rule log structure
  14. Full pipeline integration
"""
from __future__ import annotations

import copy
import pytest

from alignment.commercial_block_mapper import (
    BlockStrategy,
    SECTION_STRATEGY,
    SECTION_TRADE_BLOCKS,
    SECTION_KEYWORD_BLOCK_ORDER,
    SECTION_ASSEMBLY_BLOCK_ORDER,
    _CB_SCALE,
    _DEFAULT_CB_SK,
    get_commercial_block,
    make_commercial_block_header,
    insert_commercial_block_headers,
)
from alignment.upgrade_rules import (
    rule_insert_commercial_block_headers,
    apply_upgrade_rules,
    RULE_PIPELINE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    name: str,
    section: str,
    family_sort_key: int = 100,
    quantity: float = 5.0,
    unit: str = "nr",
    source_evidence: str = "measured_source",
    derivation_rule: str = "quantifier:test",
    evidence_class: str = "measured_source",
    confidence: str = "HIGH",
    package_code: str | None = None,
) -> dict:
    """Create a minimal BOQ item with commercial_package_code set."""
    return {
        "item_name":               name,
        "item_display_name":       name,
        "commercial_package_code": section,
        "package_code":            package_code or section,
        "unit":                    unit,
        "quantity":                quantity,
        "family_sort_key":         family_sort_key,
        "quantity_status":         "measured",
        "evidence_class":          evidence_class,
        "export_class":            "source_quantified",
        "confidence":              confidence,
        "manual_review":           False,
        "notes":                   None,
        "source_evidence":         source_evidence,
        "derivation_rule":         derivation_rule,
    }


def _estimator_ctx() -> dict:
    return {
        "export_style":      "estimator",
        "baseline_profile":  {},
        "ai_profile":        {},
        "comparison_report": {},
    }


def _commercial_ctx() -> dict:
    return {
        "export_style":      "commercial",
        "baseline_profile":  {},
        "ai_profile":        {},
        "comparison_report": {},
    }


# ===========================================================================
# 1. TRADE strategy — family → block
# ===========================================================================

class TestTradeStyleBlocks:
    """TRADE strategy: family-based resolution for building trade sections."""

    def test_wall_frame_maps_to_frame(self):
        assert get_commercial_block("50107", "wall_frame", "Wall Frame") == "Frame"

    def test_roof_truss_maps_to_frame(self):
        assert get_commercial_block("50107", "roof_truss", "Roof Truss") == "Frame"

    def test_joist_maps_to_floor_system(self):
        assert get_commercial_block("50107", "joist", "Floor Joist") == "Floor System"

    def test_bearer_maps_to_floor_system(self):
        assert get_commercial_block("50107", "bearer", "Floor Bearer") == "Floor System"

    def test_footing_concrete_maps_to_footings(self):
        assert get_commercial_block("50107", "footing_concrete", "Footing Concrete") == "Footings"

    def test_dpm_maps_to_substructure(self):
        assert get_commercial_block("50107", "dpm", "DPM") == "Substructure"

    def test_roof_batten_maps_to_battens_in_50107(self):
        # Battens stay in 50107 (Structural) per BOQ_FOR_AI reference — not remapped to 50112
        assert get_commercial_block("50107", "roof_batten", "Roof Batten") == "Battens"

    def test_ceiling_batten_maps_to_battens_in_50107(self):
        assert get_commercial_block("50107", "ceiling_batten", "Ceiling Batten") == "Battens"

    def test_roof_cladding_maps_to_roof_covering(self):
        assert get_commercial_block("50112", "roof_cladding", "Roof Cladding Sheet") == "Roof Covering"

    def test_gutter_maps_to_roof_plumbing(self):
        assert get_commercial_block("50112", "gutter", "Gutter") == "Roof Plumbing"

    def test_downpipe_maps_to_roof_plumbing(self):
        assert get_commercial_block("50112", "downpipe", "Downpipe") == "Roof Plumbing"

    def test_door_maps_to_doors(self):
        assert get_commercial_block("50114", "door", "Door — DOOR_90") == "Doors"

    def test_door_hinge_maps_to_door_hardware(self):
        assert get_commercial_block("50114", "door_hinge", "Door Hinge (pair)") == "Door Hardware"

    def test_window_maps_to_windows(self):
        assert get_commercial_block("50114", "window", "Window — W01") == "Windows"

    def test_louvre_blade_maps_to_window_accessories(self):
        assert get_commercial_block("50114", "louvre_blade", "Louvre Blade") == "Window Accessories"

    def test_ceiling_lining_maps_to_ceiling_finishes(self):
        assert get_commercial_block("50115", "ceiling_lining", "FC Sheet Ceiling") == "Ceiling Finishes"

    def test_internal_wall_lining_maps_to_wall_finishes(self):
        assert get_commercial_block("50115", "internal_wall_lining", "Internal Wall Lining") == "Wall Finishes"

    def test_skirting_maps_to_trims(self):
        assert get_commercial_block("50115", "skirting", "Skirting Board") == "Trims"

    def test_insulation_batts_maps_to_insulation(self):
        assert get_commercial_block("50118", "insulation_batts", "Insulation Batts") == "Insulation"

    def test_unknown_family_returns_none(self):
        assert get_commercial_block("50107", "unknown", "Some Item") is None

    def test_family_in_wrong_section_returns_none(self):
        # gutter is defined for 50112, not 50107
        assert get_commercial_block("50107", "gutter", "Gutter") is None

    def test_section_without_strategy_returns_none(self):
        assert get_commercial_block("50199", "wall_frame", "Wall Frame") is None


# ===========================================================================
# 2. Section-specific overrides
# ===========================================================================

class TestSectionSpecificOverrides:
    def test_floor_substrate_in_50107_is_floor_system(self):
        assert get_commercial_block("50107", "floor_substrate", "FC Sheet Floor") == "Floor System"

    def test_floor_substrate_in_50115_is_floor_finishes(self):
        assert get_commercial_block("50115", "floor_substrate", "Floor Sheet") == "Floor Finishes"

    def test_sisalation_in_50112_is_roof_covering(self):
        assert get_commercial_block("50112", "sisalation", "Sisalation / Sarking") == "Roof Covering"

    def test_sisalation_in_50118_is_insulation(self):
        assert get_commercial_block("50118", "sisalation", "Reflective Foil / Sisalation Underlay") == "Insulation"

    def test_sisalation_tape_in_50112_is_roof_covering(self):
        assert get_commercial_block("50112", "sisalation_tape", "Sisalation Lap Tape") == "Roof Covering"

    def test_sisalation_tape_in_50118_is_insulation(self):
        assert get_commercial_block("50118", "sisalation_tape", "Sisalation Lap Tape") == "Insulation"


# ===========================================================================
# 3. KEYWORD strategy — services sections (50117)
# ===========================================================================

class TestKeywordBlocks:
    """Keyword-based block resolution for services sections."""

    # Plumbing Fixtures
    def test_hand_basin_dispensary_is_plumbing_fixtures(self):
        assert get_commercial_block("50117", "unknown", "Hand Basin (dispensary)") == "Plumbing Fixtures"

    def test_hand_basin_consulting_is_plumbing_fixtures(self):
        assert get_commercial_block("50117", "unknown", "Hand Basin (consulting room)") == "Plumbing Fixtures"

    def test_floor_waste_is_plumbing_fixtures(self):
        assert get_commercial_block("50117", "unknown", "Floor Waste") == "Plumbing Fixtures"

    def test_tapware_is_plumbing_fixtures(self):
        assert get_commercial_block("50117", "unknown", "Tapware (basin)") == "Plumbing Fixtures"

    # Sanitary Fixtures
    def test_wc_pan_is_sanitary_fixtures(self):
        assert get_commercial_block("50117", "unknown", "WC Pan (close-coupled)") == "Sanitary Fixtures"

    def test_wc_cistern_is_sanitary_fixtures(self):
        assert get_commercial_block("50117", "unknown", "WC Cistern") == "Sanitary Fixtures"

    # Sanitary Accessories
    def test_mirror_is_sanitary_accessories(self):
        assert get_commercial_block("50117", "unknown", "Mirror / Medicine Cabinet") == "Sanitary Accessories"

    def test_toilet_roll_is_sanitary_accessories(self):
        assert get_commercial_block("50117", "unknown", "Toilet Roll Holder") == "Sanitary Accessories"

    def test_towel_rail_is_sanitary_accessories(self):
        assert get_commercial_block("50117", "unknown", "Hand Towel Rail / Dryer") == "Sanitary Accessories"

    # Wet Area Finishes
    def test_wet_area_tiling_is_wet_area_finishes(self):
        assert get_commercial_block("50117", "unknown", "Wet Area Wall Tiling — Toilet") == "Wet Area Finishes"

    def test_waterproofing_is_wet_area_finishes(self):
        assert get_commercial_block("50117", "unknown", "Wet Area Waterproofing — Membrane") == "Wet Area Finishes"

    # Water Services
    def test_hot_water_system_is_water_services(self):
        assert get_commercial_block("50117", "unknown", "Hot Water System (central)") == "Water Services"

    def test_water_meter_is_water_services(self):
        assert get_commercial_block("50117", "unknown", "Main Water Meter / Stopcock") == "Water Services"

    # Electrical Works — including "Builder's Works — Electrical"
    def test_switchboard_is_electrical_works(self):
        assert get_commercial_block("50117", "unknown", "Main Electrical Switchboard / Distribution Board") == "Electrical Works"

    def test_builders_works_electrical_is_electrical_works(self):
        # Contains "electrical" → Electrical Works before "builder's works"
        assert get_commercial_block("50117", "unknown", "Builder's Works — Electrical (pharmacy)") == "Electrical Works"

    def test_exhaust_fan_is_electrical_works(self):
        assert get_commercial_block("50117", "unknown", "Exhaust Fan — Wet Area (toilet / laundry)") == "Electrical Works"

    def test_smoke_detector_is_electrical_works(self):
        assert get_commercial_block("50117", "unknown", "Smoke Detectors (provisional)") == "Electrical Works"

    # Mechanical
    def test_air_conditioning_is_mechanical(self):
        assert get_commercial_block("50117", "unknown",
               "Air Conditioning / Mechanical Ventilation — PLACEHOLDER") == "Mechanical"

    # Refrigeration
    def test_cold_room_is_refrigeration(self):
        assert get_commercial_block("50117", "unknown", "Cold Room / Refrigeration Allowance") == "Refrigeration"

    # Builder's Works — "Plumbing" only, not "Electrical"
    def test_builders_works_plumbing_is_builders_works(self):
        assert get_commercial_block("50117", "unknown", "Builder's Works — Plumbing (consulting)") == "Builder's Works"

    def test_builders_works_plumbing_toilet_is_builders_works(self):
        assert get_commercial_block("50117", "unknown", "Builder's Works — Plumbing (toilet)") == "Builder's Works"

    # Unknown keyword → None
    def test_unknown_item_returns_none(self):
        assert get_commercial_block("50117", "unknown", "Some Unrecognised Service") is None


# ===========================================================================
# 4. ASSEMBLY strategy — stairs & ramps (50124)
# ===========================================================================

class TestAssemblyBlocks:
    """Keyword-first, then family-based resolution for stairs/ramps section."""

    def test_stair_stringer_is_stairs(self):
        # "stair" keyword
        assert get_commercial_block("50124", "stair_stringer",
               "Stair Stringer (Prefabricated Set) — 3 RISER") == "Stairs"

    def test_stair_tread_is_stairs(self):
        assert get_commercial_block("50124", "stair_tread", "Stair Tread") == "Stairs"

    def test_stair_newel_is_stairs(self):
        assert get_commercial_block("50124", "stair_newel", "Stair Newel Post") == "Stairs"

    def test_stair_balustrade_top_rail_is_stairs(self):
        # "stair" keyword before "balustrade"
        assert get_commercial_block("50124", "stair_balustrade",
               "Stair Balustrade — Top Rail") == "Stairs"

    def test_stair_balustrade_post_is_stairs(self):
        assert get_commercial_block("50124", "stair_balustrade",
               "Stair Balustrade Post") == "Stairs"

    def test_stair_handrail_is_stairs(self):
        # "stair" keyword beats family fallback
        assert get_commercial_block("50124", "handrail", "Stair Handrail") == "Stairs"

    def test_access_ramp_surface_is_steel_ramp(self):
        # "ramp" keyword
        assert get_commercial_block("50124", "access_ramp",
               "Access Ramp — Surface (concrete / non-slip)") == "Steel Ramp"

    def test_access_ramp_handrail_is_steel_ramp(self):
        # "ramp" keyword beats "handrail" keyword
        assert get_commercial_block("50124", "handrail",
               "Access Ramp — Handrail (both sides)") == "Steel Ramp"

    def test_access_ramp_kerb_is_steel_ramp(self):
        assert get_commercial_block("50124", "access_ramp",
               "Access Ramp — Edge Kerb / Guard") == "Steel Ramp"

    def test_verandah_balustrade_is_verandah_balustrade(self):
        assert get_commercial_block("50124", "stair_balustrade",
               "Verandah Balustrade") == "Verandah Balustrade"

    def test_verandah_handrail_is_verandah_balustrade(self):
        assert get_commercial_block("50124", "handrail",
               "Verandah Handrail") == "Verandah Balustrade"

    def test_standalone_balustrade_post_is_balustrades(self):
        # No stair/ramp/verandah → "balustrade" catch-all
        assert get_commercial_block("50124", "balustrade_fitting",
               "Balustrade Post") == "Balustrades"

    def test_stair_wins_over_balustrade_keyword(self):
        # "stair" rule comes after "ramp"/"verandah" but before "balustrade"
        assert get_commercial_block("50124", "stair_balustrade",
               "Stair Balustrade Infill (glass / mesh / picket)") == "Stairs"


# ===========================================================================
# 5. Commercial block header structure
# ===========================================================================

class TestCommercialBlockHeaderStructure:
    """Header row produced by make_commercial_block_header."""

    def _header(self, block="Footings", section="50107", sort_key=9999):
        return make_commercial_block_header(section, block, sort_key=sort_key)

    def test_export_class_is_export_only_grouping(self):
        assert self._header()["export_class"] == "export_only_grouping"

    def test_quantity_is_none(self):
        assert self._header()["quantity"] is None

    def test_unit_is_none(self):
        assert self._header()["unit"] is None

    def test_manual_review_is_false(self):
        assert self._header()["manual_review"] is False

    def test_derivation_rule(self):
        assert self._header()["derivation_rule"] == "insert_commercial_block_headers"

    def test_display_name_matches_block(self):
        h = self._header(block="Floor System")
        assert h["item_display_name"] == "Floor System"

    def test_commercial_block_field(self):
        h = self._header(block="Frame")
        assert h["commercial_block"] == "Frame"

    def test_source_evidence_is_export_layer(self):
        assert self._header()["source_evidence"] == "export_layer"

    def test_sort_key_stored(self):
        h = self._header(sort_key=12345)
        assert h["commercial_block_sort_key"] == 12345
        assert h["family_sort_key"] == 12345  # backward-compat copy

    def test_section_code_stored(self):
        h = self._header(section="50112")
        assert h["commercial_package_code"] == "50112"


# ===========================================================================
# 6. Sort key arithmetic
# ===========================================================================

class TestSortKeyArithmetic:
    """Verify the (idx+1)×CB_SCALE formula for headers and items."""

    def test_header_sort_key_for_first_block(self):
        # Footings is idx=0 in 50107  → header sk = (0+1)*10000 - 1 = 9999
        items = [_item("Footing Concrete", "50107", family_sort_key=100)]
        result = insert_commercial_block_headers(items)
        hdrs = [i for i in result if i.get("derivation_rule") == "insert_commercial_block_headers"
                and i.get("commercial_block") == "Footings"]
        assert hdrs, "Footings header not inserted"
        assert hdrs[0]["commercial_block_sort_key"] == _CB_SCALE - 1

    def test_header_sort_key_for_later_block(self):
        # Frame is idx=3 in 50107 → header sk = (3+1)*10000 - 1 = 39999
        items = [_item("Wall Frame — all members", "50107", family_sort_key=200)]
        result = insert_commercial_block_headers(items)
        hdrs = [i for i in result if i.get("commercial_block") == "Frame"
                and i.get("export_class") == "export_only_grouping"]
        assert hdrs
        assert hdrs[0]["commercial_block_sort_key"] == 4 * _CB_SCALE - 1

    def test_item_sort_key_formula(self):
        # wall_frame → Frame (idx=3) in 50107; fam_sk=200
        # → item cb_sk = (3+1)*10000 + 200 = 40200
        items = [_item("Wall Frame — all members", "50107", family_sort_key=200)]
        insert_commercial_block_headers(items)   # mutates in-place
        assert items[0]["commercial_block_sort_key"] == 4 * _CB_SCALE + 200

    def test_ungrouped_item_gets_default_sk(self):
        # "some_unknown" family → None block → _DEFAULT_CB_SK + fam_sk
        items = [_item("Totally Unknown Thing", "50107", family_sort_key=100)]
        insert_commercial_block_headers(items)
        assert items[0]["commercial_block_sort_key"] == _DEFAULT_CB_SK + 100

    def test_header_sorts_before_item(self):
        items = [_item("Footing Concrete", "50107", family_sort_key=100)]
        result = insert_commercial_block_headers(items)
        hdr  = next(i for i in result if i.get("commercial_block") == "Footings"
                    and i.get("export_class") == "export_only_grouping")
        item = next(i for i in result if i.get("commercial_block") == "Footings"
                    and i.get("export_class") != "export_only_grouping")
        assert hdr["commercial_block_sort_key"] < item["commercial_block_sort_key"]

    def test_first_block_header_before_second_block_header(self):
        items = [
            _item("Footing Concrete", "50107", family_sort_key=100),
            _item("Wall Frame — all members", "50107", family_sort_key=200),
        ]
        result = insert_commercial_block_headers(items)
        footings_hdr = next(i for i in result if i.get("commercial_block") == "Footings"
                            and i.get("export_class") == "export_only_grouping")
        frame_hdr    = next(i for i in result if i.get("commercial_block") == "Frame"
                            and i.get("export_class") == "export_only_grouping")
        assert footings_hdr["commercial_block_sort_key"] < frame_hdr["commercial_block_sort_key"]


# ===========================================================================
# 7. Empty block suppression
# ===========================================================================

class TestEmptyBlockSuppression:
    """No header inserted for blocks with no items."""

    def test_no_header_for_unpopulated_block(self):
        # Only Footings item; Frame block should have no header
        items = [_item("Footing Concrete", "50107", family_sort_key=100)]
        result = insert_commercial_block_headers(items)
        frame_hdrs = [i for i in result if i.get("commercial_block") == "Frame"
                      and i.get("export_class") == "export_only_grouping"]
        assert not frame_hdrs

    def test_only_populated_blocks_get_headers(self):
        items = [
            _item("Footing Concrete", "50107", family_sort_key=100),
            _item("Wall Frame — all members", "50107", family_sort_key=200),
        ]
        result = insert_commercial_block_headers(items)
        hdrs = {i["commercial_block"] for i in result
                if i.get("export_class") == "export_only_grouping"}
        assert hdrs == {"Footings", "Frame"}

    def test_no_headers_for_section_without_strategy(self):
        items = [_item("Some Item", "50199", family_sort_key=100)]
        result = insert_commercial_block_headers(items)
        assert result == items  # unchanged

    def test_no_headers_when_no_items(self):
        result = insert_commercial_block_headers([])
        assert result == []


# ===========================================================================
# 8. Export mode gating
# ===========================================================================

class TestExportModeGating:
    """Commercial block headers only appear in estimator mode."""

    def test_commercial_mode_no_cb_headers(self):
        items = [_item("Footing Concrete", "50107")]
        new_items, _ = rule_insert_commercial_block_headers(items, _commercial_ctx())
        hdrs = [i for i in new_items
                if i.get("derivation_rule") == "insert_commercial_block_headers"]
        assert not hdrs

    def test_engine_mode_no_cb_headers(self):
        items = [_item("Footing Concrete", "50107")]
        new_items, _ = rule_insert_commercial_block_headers(
            items, {"export_style": "engine"}
        )
        hdrs = [i for i in new_items
                if i.get("derivation_rule") == "insert_commercial_block_headers"]
        assert not hdrs

    def test_estimator_mode_inserts_cb_headers(self):
        items = [_item("Footing Concrete", "50107")]
        new_items, _ = rule_insert_commercial_block_headers(items, _estimator_ctx())
        hdrs = [i for i in new_items
                if i.get("derivation_rule") == "insert_commercial_block_headers"]
        assert len(hdrs) >= 1

    def test_items_get_commercial_block_sort_key_in_estimator(self):
        items = [_item("Wall Frame — all members", "50107", family_sort_key=200)]
        rule_insert_commercial_block_headers(items, _estimator_ctx())
        assert "commercial_block_sort_key" in items[0]

    def test_items_have_no_commercial_block_sort_key_in_commercial(self):
        items = [_item("Wall Frame — all members", "50107", family_sort_key=200)]
        rule_insert_commercial_block_headers(items, _commercial_ctx())
        assert "commercial_block_sort_key" not in items[0]

    def test_services_section_gets_keyword_blocks_in_estimator(self):
        items = [_item("Hand Basin (dispensary)", "50117")]
        new_items, _ = rule_insert_commercial_block_headers(items, _estimator_ctx())
        hdrs = [i for i in new_items
                if i.get("commercial_block") == "Plumbing Fixtures"
                and i.get("export_class") == "export_only_grouping"]
        assert hdrs

    def test_assembly_section_gets_assembly_blocks_in_estimator(self):
        items = [_item("Stair Stringer — 3 RISER", "50124")]
        new_items, _ = rule_insert_commercial_block_headers(items, _estimator_ctx())
        hdrs = [i for i in new_items
                if i.get("commercial_block") == "Stairs"
                and i.get("export_class") == "export_only_grouping"]
        assert hdrs


# ===========================================================================
# 9. No quantity mutation
# ===========================================================================

class TestNoQuantityMutation:
    """Quantities on engine items must never change."""

    def test_item_quantity_unchanged(self):
        items = [_item("Footing Concrete", "50107", quantity=42.0)]
        result = insert_commercial_block_headers(items)
        real_items = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real_items[0]["quantity"] == 42.0

    def test_headers_have_null_quantity(self):
        items = [_item("Footing Concrete", "50107")]
        result = insert_commercial_block_headers(items)
        hdrs = [i for i in result if i.get("export_class") == "export_only_grouping"]
        for h in hdrs:
            assert h["quantity"] is None

    def test_item_name_unchanged(self):
        original = "Footing Concrete — 25MPa"
        items = [_item(original, "50107")]
        result = insert_commercial_block_headers(items)
        real_items = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real_items[0]["item_name"] == original

    def test_engine_package_code_unchanged(self):
        items = [_item("Footing Concrete", "50107", package_code="50107")]
        result = insert_commercial_block_headers(items)
        real_items = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real_items[0]["package_code"] == "50107"

    def test_unit_unchanged(self):
        items = [_item("Footing Concrete", "50107", unit="m3")]
        result = insert_commercial_block_headers(items)
        real_items = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real_items[0]["unit"] == "m3"

    def test_full_pipeline_quantity_unchanged(self):
        items = [_item("Footing Concrete", "50107", quantity=12.5)]
        new_items, _ = apply_upgrade_rules(
            items,
            _estimator_ctx(),
            rules=[rule_insert_commercial_block_headers],
        )
        real = [i for i in new_items if i.get("export_class") != "export_only_grouping"]
        assert real[0]["quantity"] == 12.5


# ===========================================================================
# 10. No traceability mutation
# ===========================================================================

class TestNoTraceabilityMutation:
    """source_evidence, derivation_rule, confidence unchanged on real items."""

    def test_source_evidence_unchanged(self):
        items = [_item("Wall Frame", "50107",
                       source_evidence="ifc: FrameCAD BOM")]
        result = insert_commercial_block_headers(items)
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["source_evidence"] == "ifc: FrameCAD BOM"

    def test_derivation_rule_unchanged_on_real_items(self):
        items = [_item("Wall Frame", "50107",
                       derivation_rule="framecad_bom:wall_frame")]
        result = insert_commercial_block_headers(items)
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["derivation_rule"] == "framecad_bom:wall_frame"

    def test_evidence_class_unchanged(self):
        items = [_item("Wall Frame", "50107",
                       evidence_class="measured_source")]
        result = insert_commercial_block_headers(items)
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["evidence_class"] == "measured_source"

    def test_confidence_unchanged(self):
        items = [_item("Wall Frame", "50107", confidence="MEDIUM")]
        result = insert_commercial_block_headers(items)
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["confidence"] == "MEDIUM"


# ===========================================================================
# 11. Correct ordering
# ===========================================================================

class TestOrdering:
    """After sorting by commercial_block_sort_key, structure is correct."""

    def _sorted(self, items):
        return sorted(items, key=lambda i: (
            i.get("commercial_block_sort_key", 99999),
            i.get("family_sort_key", 500),
        ))

    def test_footings_block_before_frame_block(self):
        items = [
            _item("Footing Concrete", "50107", family_sort_key=100),
            _item("Wall Frame — all members", "50107", family_sort_key=200),
        ]
        result = self._sorted(insert_commercial_block_headers(items))
        blocks_seen = [i.get("commercial_block") for i in result
                       if i.get("export_class") == "export_only_grouping"]
        assert blocks_seen.index("Footings") < blocks_seen.index("Frame")

    def test_header_before_its_items_when_sorted(self):
        items = [_item("Footing Concrete", "50107", family_sort_key=100)]
        result = self._sorted(insert_commercial_block_headers(items))
        order = [(i.get("commercial_block"), i.get("export_class")) for i in result
                 if i.get("commercial_block") == "Footings"]
        # header appears first (export_only_grouping), then real item
        assert order[0][1] == "export_only_grouping"
        assert order[1][1] != "export_only_grouping"

    def test_services_plumbing_before_electrical_in_50117(self):
        items = [
            _item("Hand Basin (dispensary)", "50117", family_sort_key=100),
            _item("Main Electrical Switchboard", "50117", family_sort_key=200),
        ]
        result = self._sorted(insert_commercial_block_headers(items))
        blocks = [i.get("commercial_block") for i in result
                  if i.get("export_class") == "export_only_grouping"]
        assert blocks.index("Plumbing Fixtures") < blocks.index("Electrical Works")

    def test_stairs_before_steel_ramp_in_50124(self):
        items = [
            _item("Stair Stringer — 3 RISER", "50124", family_sort_key=100),
            _item("Access Ramp — Surface", "50124", family_sort_key=200),
        ]
        result = self._sorted(insert_commercial_block_headers(items))
        blocks = [i.get("commercial_block") for i in result
                  if i.get("export_class") == "export_only_grouping"]
        assert blocks.index("Stairs") < blocks.index("Steel Ramp")

    def test_multi_section_items_stay_in_section_block_order(self):
        items = [
            _item("Footing Concrete", "50107", family_sort_key=100),
            _item("Roof Cladding Sheet", "50112", family_sort_key=100),
        ]
        result = insert_commercial_block_headers(items)
        # Each section's blocks should be independent
        s107_blocks = {i.get("commercial_block") for i in result
                       if i.get("commercial_package_code") == "50107"
                       and i.get("export_class") == "export_only_grouping"}
        s112_blocks = {i.get("commercial_block") for i in result
                       if i.get("commercial_package_code") == "50112"
                       and i.get("export_class") == "export_only_grouping"}
        assert "Footings" in s107_blocks
        assert "Roof Covering" in s112_blocks


# ===========================================================================
# 12. Rule log structure
# ===========================================================================

class TestRuleLog:
    """rule_insert_commercial_block_headers produces a correctly-structured log."""

    def test_log_has_one_entry_per_header(self):
        items = [
            _item("Footing Concrete", "50107"),
            _item("Wall Frame — all members", "50107"),
        ]
        _, log = rule_insert_commercial_block_headers(items, _estimator_ctx())
        header_log = [e for e in log if e.get("rule") == "insert_commercial_block_headers"]
        # 2 blocks populated (Footings, Frame) → 2 log entries
        assert len(header_log) == 2

    def test_log_entry_has_section(self):
        items = [_item("Footing Concrete", "50107")]
        _, log = rule_insert_commercial_block_headers(items, _estimator_ctx())
        assert any(e.get("section") == "50107" for e in log)

    def test_log_entry_has_commercial_block(self):
        items = [_item("Footing Concrete", "50107")]
        _, log = rule_insert_commercial_block_headers(items, _estimator_ctx())
        assert any(e.get("commercial_block") == "Footings" for e in log)

    def test_log_entry_has_sort_key(self):
        items = [_item("Footing Concrete", "50107")]
        _, log = rule_insert_commercial_block_headers(items, _estimator_ctx())
        for e in log:
            assert "sort_key" in e

    def test_no_log_in_commercial_mode(self):
        items = [_item("Footing Concrete", "50107")]
        _, log = rule_insert_commercial_block_headers(items, _commercial_ctx())
        assert log == []


# ===========================================================================
# 13. Full pipeline integration
# ===========================================================================

class TestFullPipelineIntegration:
    """Verify commercial_block_headers in the full RULE_PIPELINE (estimator)."""

    def test_pipeline_includes_commercial_block_rule(self):
        from alignment.upgrade_rules import rule_insert_commercial_block_headers
        assert rule_insert_commercial_block_headers in RULE_PIPELINE

    def test_pipeline_does_not_include_trade_group_rule(self):
        from alignment.upgrade_rules import rule_insert_trade_group_headers
        assert rule_insert_trade_group_headers not in RULE_PIPELINE

    def test_full_pipeline_estimator_inserts_cb_headers(self):
        items = [
            _item("Footing Concrete", "50107", quantity=12.0),
            _item("Wall Frame — all members", "50107", quantity=8.0),
            _item("Hand Basin (dispensary)", "50117", quantity=1.0),
        ]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _estimator_ctx(),
        )
        hdrs = [i for i in new_items
                if i.get("derivation_rule") == "insert_commercial_block_headers"]
        assert len(hdrs) >= 2  # at least Footings (50107) + Frame (50107)

    def test_full_pipeline_commercial_no_cb_headers(self):
        items = [
            _item("Footing Concrete", "50107", quantity=12.0),
            _item("Wall Frame — all members", "50107", quantity=8.0),
        ]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _commercial_ctx(),
        )
        hdrs = [i for i in new_items
                if i.get("derivation_rule") == "insert_commercial_block_headers"]
        assert not hdrs

    def test_quantity_invariant_through_full_pipeline(self):
        items = [_item("Footing Concrete", "50107", quantity=7.25)]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _estimator_ctx(),
        )
        real = [i for i in new_items if i.get("export_class") != "export_only_grouping"
                and i.get("derivation_rule") != "insert_commercial_block_headers"]
        assert any(abs(i["quantity"] - 7.25) < 0.001 for i in real)

    def test_package_code_invariant_through_full_pipeline(self):
        items = [_item("Footing Concrete", "50107", package_code="50107")]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _estimator_ctx(),
        )
        real = [i for i in new_items if i.get("export_class") != "export_only_grouping"
                and i.get("derivation_rule") != "placeholder_rule"]
        for r in real:
            assert r["package_code"] == "50107"

    def test_50117_gets_keyword_based_blocks_in_pipeline(self):
        items = [
            _item("Hand Basin (dispensary)", "50117", quantity=1.0),
            _item("WC Pan (close-coupled)", "50117", quantity=1.0),
        ]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _estimator_ctx(),
        )
        blocks_with_hdrs = {
            i["commercial_block"]
            for i in new_items
            if i.get("derivation_rule") == "insert_commercial_block_headers"
            and i.get("commercial_package_code") == "50117"
        }
        assert "Plumbing Fixtures" in blocks_with_hdrs
        assert "Sanitary Fixtures" in blocks_with_hdrs

    def test_50124_gets_assembly_blocks_in_pipeline(self):
        items = [
            _item("Stair Stringer — 3 RISER", "50124", quantity=1.0),
            _item("Access Ramp — Surface (concrete / non-slip)", "50124", quantity=1.0),
        ]
        new_items, _ = apply_upgrade_rules(
            copy.deepcopy(items),
            _estimator_ctx(),
        )
        blocks = {
            i["commercial_block"]
            for i in new_items
            if i.get("derivation_rule") == "insert_commercial_block_headers"
        }
        assert "Stairs" in blocks
        assert "Steel Ramp" in blocks
