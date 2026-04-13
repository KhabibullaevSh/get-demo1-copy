"""
test_trade_group.py — Trade group layer regression tests.

Tests cover:
  1.  Trade group mapping (family → trade_group, section-aware)
  2.  Section-specific override resolution
  3.  Trade group header creation (structure, null quantity)
  4.  Header sort_key = (tg_idx + 1) × 10000 - 1
  5.  Item trade_group_sort_key = (tg_idx + 1) × 10000 + family_sort_key
  6.  Empty trade group → no header inserted
  7.  Correct ordering: section → trade_group → items
  8.  No quantity mutation by any trade-group operation
  9.  Export mode gating (estimator only)
  10. Trade group annotation in alignment_notes (via rule log)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from alignment.trade_group_mapper import (
    get_trade_group,
    make_trade_group_header,
    insert_trade_group_headers,
    FAMILY_TO_TRADE_GROUP,
    SECTION_TRADE_GROUPS,
    _TG_SCALE,
    _DEFAULT_TG_SK,
)
from alignment.upgrade_rules import (
    rule_insert_trade_group_headers,
    apply_upgrade_rules,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    *,
    name: str,
    pkg: str = "50107",
    sk: int = 100,
    qty: float = 1.0,
    unit: str = "nr",
    conf: str = "HIGH",
) -> dict:
    return {
        "item_name":               name,
        "item_display_name":       name,
        "commercial_package_code": pkg,
        "package_code":            pkg,
        "family_sort_key":         sk,
        "quantity":                qty,
        "unit":                    unit,
        "confidence":              conf,
        "manual_review":           False,
        "quantity_status":         "measured",
        "evidence_class":          "measured_source",
        "export_class":            "source_quantified",
    }


def _ctx(export_style: str = "estimator") -> dict:
    return {
        "export_style":      export_style,
        "baseline_profile":  {"sections": {}, "global_flags": {}},
        "ai_profile":        {"sections": {}, "global_flags": {}},
        "comparison_report": {"section_results": {}, "required_gaps": [],
                              "summary": {}, "global_flag_diffs": {}},
        "fixings_strategy":  None,
        "missing_schedules": set(),
    }


def _tg_headers(items: list[dict]) -> list[dict]:
    return [i for i in items
            if i.get("derivation_rule") == "insert_trade_group_headers"]


def _real_items(items: list[dict]) -> list[dict]:
    return [i for i in items
            if i.get("export_class") != "export_only_grouping"]


# ============================================================
# 1 — Family → trade group mapping
# ============================================================

class TestTradeGroupMapping:
    def test_wall_frame_maps_to_frame(self):
        assert get_trade_group("50107", "wall_frame") == "Frame"

    def test_roof_truss_maps_to_frame(self):
        assert get_trade_group("50107", "roof_truss") == "Frame"

    def test_footing_concrete_maps_to_footings(self):
        assert get_trade_group("50107", "footing_concrete") == "Footings"

    def test_dpm_maps_to_substructure(self):
        assert get_trade_group("50107", "dpm") == "Substructure"

    def test_joist_maps_to_floor_system(self):
        assert get_trade_group("50107", "joist") == "Floor System"

    def test_bearer_maps_to_floor_system(self):
        assert get_trade_group("50107", "bearer") == "Floor System"

    def test_floor_cassette_maps_to_floor_system(self):
        assert get_trade_group("50107", "floor_cassette") == "Floor System"

    def test_roof_batten_maps_to_roof_structure(self):
        assert get_trade_group("50112", "roof_batten") == "Roof Structure"

    def test_roof_cladding_maps_to_roof_covering(self):
        assert get_trade_group("50112", "roof_cladding") == "Roof Covering"

    def test_hip_capping_maps_to_roof_covering(self):
        assert get_trade_group("50112", "hip_capping") == "Roof Covering"

    def test_gutter_maps_to_roof_plumbing(self):
        assert get_trade_group("50112", "gutter") == "Roof Plumbing"

    def test_downpipe_maps_to_roof_plumbing(self):
        assert get_trade_group("50112", "downpipe") == "Roof Plumbing"

    def test_door_maps_to_doors(self):
        assert get_trade_group("50114", "door") == "Doors"

    def test_door_hinge_maps_to_door_hardware(self):
        assert get_trade_group("50114", "door_hinge") == "Door Hardware"

    def test_window_maps_to_windows(self):
        assert get_trade_group("50114", "window") == "Windows"

    def test_louvre_blade_maps_to_window_accessories(self):
        assert get_trade_group("50114", "louvre_blade") == "Window Accessories"

    def test_internal_wall_lining_maps_to_wall_finishes(self):
        assert get_trade_group("50115", "internal_wall_lining") == "Wall Finishes"

    def test_ceiling_lining_maps_to_ceiling_finishes(self):
        assert get_trade_group("50115", "ceiling_lining") == "Ceiling Finishes"

    def test_floor_finish_maps_to_floor_finishes(self):
        assert get_trade_group("50115", "floor_finish") == "Floor Finishes"

    def test_paint_maps_to_painting(self):
        assert get_trade_group("50115", "paint") == "Painting"

    def test_skirting_maps_to_trims(self):
        assert get_trade_group("50115", "skirting") == "Trims"

    def test_architrave_maps_to_trims(self):
        assert get_trade_group("50115", "architrave") == "Trims"

    def test_unknown_family_returns_none(self):
        assert get_trade_group("50107", "nonexistent_family") is None

    def test_family_wrong_section_returns_none(self):
        # roof_cladding is a 50112 family — not in 50107 trade groups
        assert get_trade_group("50107", "roof_cladding") is None

    def test_section_with_no_trade_groups_returns_none(self):
        # 50106 WPC has no trade group definitions
        assert get_trade_group("50106", "wall_frame") is None


# ============================================================
# 2 — Section-specific overrides
# ============================================================

class TestSectionSpecificOverrides:
    def test_floor_substrate_in_50107_is_floor_system(self):
        assert get_trade_group("50107", "floor_substrate") == "Floor System"

    def test_floor_substrate_in_50115_is_floor_finishes(self):
        assert get_trade_group("50115", "floor_substrate") == "Floor Finishes"

    def test_sisalation_in_50112_is_roof_covering(self):
        assert get_trade_group("50112", "sisalation") == "Roof Covering"

    def test_sisalation_in_50118_is_insulation(self):
        assert get_trade_group("50118", "sisalation") == "Insulation"


# ============================================================
# 3 — Trade group header structure
# ============================================================

class TestTradeGroupHeaderStructure:
    def test_header_export_class(self):
        hdr = make_trade_group_header("50107", "Frame", sort_key=39999)
        assert hdr["export_class"] == "export_only_grouping"

    def test_header_quantity_is_none(self):
        hdr = make_trade_group_header("50107", "Frame", sort_key=39999)
        assert hdr["quantity"] is None

    def test_header_unit_is_none(self):
        hdr = make_trade_group_header("50107", "Frame", sort_key=39999)
        assert hdr["unit"] is None

    def test_header_manual_review_false(self):
        hdr = make_trade_group_header("50107", "Frame", sort_key=39999)
        assert hdr["manual_review"] is False

    def test_header_display_name_matches_trade_group(self):
        hdr = make_trade_group_header("50112", "Roof Plumbing", sort_key=39999)
        assert hdr["item_display_name"] == "Roof Plumbing"

    def test_header_derivation_rule(self):
        hdr = make_trade_group_header("50107", "Footings", sort_key=9999)
        assert hdr["derivation_rule"] == "insert_trade_group_headers"

    def test_header_trade_group_field(self):
        hdr = make_trade_group_header("50114", "Doors", sort_key=9999)
        assert hdr["trade_group"] == "Doors"

    def test_header_source_evidence(self):
        hdr = make_trade_group_header("50107", "Frame", sort_key=39999)
        assert hdr["source_evidence"] == "export_layer"


# ============================================================
# 4 & 5 — Sort key arithmetic
# ============================================================

class TestSortKeyArithmetic:
    def test_header_sort_key_formula(self):
        # "Footings" is idx=0 in 50107 → header sk = (0+1)*10000 - 1 = 9999
        items = [_item(name="Strip Footing — External Perimeter", pkg="50107", sk=200)]
        result = insert_trade_group_headers(items, sections={"50107"})
        hdrs = _tg_headers(result)
        footings_hdr = next(
            (h for h in hdrs if h["trade_group"] == "Footings"), None
        )
        assert footings_hdr is not None
        assert footings_hdr["trade_group_sort_key"] == 1 * _TG_SCALE - 1

    def test_frame_header_sort_key(self):
        # "Frame" is idx=3 in 50107 → header sk = (3+1)*10000 - 1 = 39999
        items = [_item(name="FrameCAD Wall Frame type-A", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        hdrs = _tg_headers(result)
        frame_hdr = next(
            (h for h in hdrs if h["trade_group"] == "Frame"), None
        )
        assert frame_hdr is not None
        assert frame_hdr["trade_group_sort_key"] == 4 * _TG_SCALE - 1

    def test_item_trade_group_sort_key_formula(self):
        # Wall Frame (family=wall_frame, tg_idx=3 in 50107) with family_sk=100
        # Expected tg_sk = (3+1)*10000 + 100 = 40100
        items = [_item(name="FrameCAD Wall Frame type-A", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        real = _real_items(result)
        assert real[0]["trade_group_sort_key"] == 4 * _TG_SCALE + 100

    def test_ungrouped_item_gets_default_sk(self):
        # An item in 50107 whose family is unknown gets DEFAULT_TG_SK + family_sk
        items = [_item(name="Some Unrecognised Item", pkg="50107", sk=500)]
        result = insert_trade_group_headers(items, sections={"50107"})
        real = _real_items(result)
        assert real[0]["trade_group_sort_key"] == _DEFAULT_TG_SK + 500

    def test_header_sorts_before_its_items(self):
        items = [_item(name="FrameCAD Wall Frame type-A", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        frame_hdr = next(h for h in _tg_headers(result)
                         if h["trade_group"] == "Frame")
        real      = _real_items(result)[0]
        assert frame_hdr["trade_group_sort_key"] < real["trade_group_sort_key"]


# ============================================================
# 6 — Empty trade group → no header
# ============================================================

class TestEmptyTradeGroup:
    def test_no_header_for_empty_trade_group(self):
        # Only wall frame items → no Footings/Substructure/Floor System headers
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        hdrs = _tg_headers(result)
        tg_names = {h["trade_group"] for h in hdrs}
        assert "Footings" not in tg_names
        assert "Substructure" not in tg_names
        assert "Floor System" not in tg_names

    def test_header_only_for_populated_groups(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100),
            _item(name="Strip Footing — External Perimeter", pkg="50107", sk=200),
        ]
        result = insert_trade_group_headers(items, sections={"50107"})
        hdrs = _tg_headers(result)
        tg_names = {h["trade_group"] for h in hdrs}
        assert "Frame" in tg_names
        assert "Footings" in tg_names
        assert "Substructure" not in tg_names  # no DPM/termite items

    def test_section_not_in_mapping_produces_no_headers(self):
        items = [_item(name="Some Item", pkg="50106", sk=100)]
        result = insert_trade_group_headers(items, sections={"50106"})
        assert len(_tg_headers(result)) == 0


# ============================================================
# 7 — Correct ordering
# ============================================================

class TestOrdering:
    def test_footings_header_before_frame_header(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100),
            _item(name="Strip Footing — External Perimeter", pkg="50107", sk=200),
        ]
        result = insert_trade_group_headers(items, sections={"50107"})
        sorted_result = sorted(result,
                               key=lambda i: i.get("trade_group_sort_key",
                                                    i.get("family_sort_key", 500)))
        tg_order = [i.get("trade_group") for i in sorted_result
                    if i.get("derivation_rule") == "insert_trade_group_headers"]
        # Footings (idx=0) must appear before Frame (idx=3)
        assert tg_order.index("Footings") < tg_order.index("Frame")

    def test_header_before_its_items_when_sorted(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        sorted_result = sorted(result,
                               key=lambda i: i.get("trade_group_sort_key",
                                                    i.get("family_sort_key", 500)))
        first = sorted_result[0]
        # The header should come before the item
        assert first.get("derivation_rule") == "insert_trade_group_headers"

    def test_items_within_trade_group_ordered_by_family_sk(self):
        items = [
            _item(name="Strip Footing — Internal Bearing Lines", pkg="50107", sk=210),
            _item(name="Strip Footing — External Perimeter", pkg="50107", sk=200),
        ]
        result = insert_trade_group_headers(items, sections={"50107"})
        sorted_result = sorted(result,
                               key=lambda i: i.get("trade_group_sort_key",
                                                    i.get("family_sort_key", 500)))
        real = [i for i in sorted_result
                if i.get("export_class") != "export_only_grouping"]
        assert real[0]["family_sort_key"] < real[1]["family_sort_key"]

    def test_multi_section_items_stay_in_section_order(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100),
            _item(name="Top-Hat Batten G35 5800mm", pkg="50112", sk=140),
        ]
        result = insert_trade_group_headers(items, sections={"50107", "50112"})
        sorted_result = sorted(
            result,
            key=lambda i: (
                i.get("commercial_package_code", ""),
                i.get("trade_group_sort_key", i.get("family_sort_key", 500)),
            ),
        )
        # 50107 items should come before 50112 items
        codes = [i["commercial_package_code"] for i in sorted_result]
        last_50107 = max(idx for idx, c in enumerate(codes) if c == "50107")
        first_50112 = min(idx for idx, c in enumerate(codes) if c == "50112")
        assert last_50107 < first_50112


# ============================================================
# 8 — No quantity mutation
# ============================================================

class TestNoQuantityMutation:
    def test_item_quantity_unchanged(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100, qty=7.5)]
        result = insert_trade_group_headers(items, sections={"50107"})
        real = _real_items(result)
        assert real[0]["quantity"] == 7.5

    def test_headers_have_null_quantity(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        for hdr in _tg_headers(result):
            assert hdr["quantity"] is None

    def test_item_name_unchanged(self):
        original = "FrameCAD Wall Frame — LGS C-90"
        items = [_item(name=original, pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        real = _real_items(result)
        assert real[0]["item_name"] == original

    def test_engine_package_code_unchanged(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result = insert_trade_group_headers(items, sections={"50107"})
        real = _real_items(result)
        assert real[0]["package_code"] == "50107"

    def test_full_pipeline_quantity_unchanged(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100, qty=5.0),
            _item(name="Strip Footing — External Perimeter", pkg="50107", sk=200, qty=38.4),
        ]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        real = _real_items(result)
        qty_map = {i["item_name"]: i["quantity"] for i in real}
        assert qty_map["FrameCAD Wall Frame"] == 5.0
        assert qty_map["Strip Footing — External Perimeter"] == 38.4


# ============================================================
# 9 — Export mode gating
# ============================================================

class TestExportModeGating:
    def test_commercial_mode_no_trade_group_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_trade_group_headers(items, _ctx("commercial"))
        assert len(_tg_headers(result)) == 0
        assert len(log) == 0

    def test_engine_mode_no_trade_group_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_trade_group_headers(items, _ctx("engine"))
        assert len(_tg_headers(result)) == 0

    def test_estimator_mode_inserts_trade_group_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_trade_group_headers(items, _ctx("estimator"))
        assert len(_tg_headers(result)) >= 1
        assert len(log) >= 1

    def test_full_commercial_pipeline_no_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, _ = apply_upgrade_rules(items, _ctx("commercial"))
        assert len(_tg_headers(result)) == 0

    def test_full_estimator_pipeline_inserts_headers(self):
        # Phase 6: pipeline now produces commercial_block headers (not trade_group).
        # Any export_only_grouping header is sufficient for this test.
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        all_headers = [i for i in result
                       if i.get("export_class") == "export_only_grouping"]
        assert len(all_headers) >= 1

    def test_items_get_trade_group_sort_key_in_estimator(self):
        # Phase 6: pipeline sets commercial_block_sort_key (supersedes trade_group_sort_key).
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        real = _real_items(result)
        assert "commercial_block_sort_key" in real[0]

    def test_items_have_no_trade_group_sort_key_in_commercial(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, _ = apply_upgrade_rules(items, _ctx("commercial"))
        real = _real_items(result)
        assert "trade_group_sort_key" not in real[0]


# ============================================================
# 10 — Rule log annotation
# ============================================================

class TestRuleLog:
    def test_log_contains_one_entry_per_header(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100),
            _item(name="Strip Footing — External Perimeter", pkg="50107", sk=200),
        ]
        _, log = rule_insert_trade_group_headers(items, _ctx("estimator"))
        tg_log = [e for e in log if e.get("rule") == "insert_trade_group_headers"]
        tg_names = {e["trade_group"] for e in tg_log}
        assert "Frame" in tg_names
        assert "Footings" in tg_names

    def test_log_entry_has_section_code(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        _, log = rule_insert_trade_group_headers(items, _ctx("estimator"))
        entry = next(e for e in log if e.get("trade_group") == "Frame")
        assert entry["section"] == "50107"

    def test_log_entry_has_sort_key(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        _, log = rule_insert_trade_group_headers(items, _ctx("estimator"))
        entry = next(e for e in log if e.get("trade_group") == "Frame")
        assert isinstance(entry["sort_key"], int)
