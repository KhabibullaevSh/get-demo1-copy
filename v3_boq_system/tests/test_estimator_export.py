"""
test_estimator_export.py — Phase 4: Commercial Polish regression tests.

Tests cover:
  1.  Subgroup header insertion (populate correctly, skip empty subgroups)
  2.  Subgroup ordering (header sort_key < first child sort_key)
  3.  Estimator display name rename rules
  4.  Roof batten section remap 50107 → 50112
  5.  Service placeholder naming (Provisional Sum)
  6.  Export mode gating (engine / commercial / estimator)
  7.  Quantity unchanged by all estimator transforms
  8.  Traceability preserved (item_name unchanged, derivation_rule set)
  9.  Subgroup header has export_class="export_only_grouping"
  10. Commercial mode does NOT insert subgroup headers
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from alignment.subgroup_mapper import (
    get_subgroup,
    make_subgroup_header,
    insert_subgroup_headers,
    FAMILY_TO_SUBGROUP,
    SECTION_SUBGROUPS,
)
from alignment.export_style_rules import (
    apply_estimator_section_remaps,
    apply_estimator_names,
    apply_placeholder_renames,
)
from alignment.upgrade_rules import (
    rule_estimator_transforms,
    rule_insert_subgroup_headers,
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
        "package_code":            pkg,   # engine code — must NEVER change
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


# ============================================================
# 1 & 4 — get_subgroup mapping
# ============================================================

class TestSubgroupMapping:
    def test_wall_frame_maps_to_structural_framing(self):
        assert get_subgroup("50107", "wall_frame") == "Structural Framing"

    def test_roof_batten_maps_to_roof_battens_in_50107(self):
        # Battens stay in 50107 — no remap to 50112 per BOQ_FOR_AI reference
        assert get_subgroup("50107", "roof_batten") == "Roof Battens"

    def test_roof_batten_not_in_50112_subgroups(self):
        # 50112 no longer has a Roof Battens subgroup (battens stay in 50107)
        assert get_subgroup("50112", "roof_batten") is None

    def test_joist_maps_to_floor_system(self):
        assert get_subgroup("50107", "joist") == "Floor System"

    def test_family_not_in_section_returns_none(self):
        # roof_cladding is a 50112 family — not in 50107 subgroups
        assert get_subgroup("50107", "roof_cladding") is None

    def test_unknown_family_returns_none(self):
        assert get_subgroup("50107", "nonexistent_family") is None

    def test_door_hinge_maps_to_door_hardware(self):
        assert get_subgroup("50114", "door_hinge") == "Door Hardware"

    def test_ceiling_lining_maps_correct_section(self):
        assert get_subgroup("50115", "ceiling_lining") == "Ceiling Linings"


# ============================================================
# 2 — Subgroup header sort key
# ============================================================

class TestSubgroupHeaderSortKey:
    def test_header_sort_key_is_below_min_child_key(self):
        items = [
            _item(name="FrameCAD Wall Frame type-A", pkg="50107", sk=100),
            _item(name="FrameCAD Wall Frame type-B", pkg="50107", sk=102),
        ]
        result = insert_subgroup_headers(items, sections={"50107"})
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        # "Structural Framing" header for wall frames
        sg_framing = next(
            (h for h in headers if h["item_display_name"] == "Structural Framing"),
            None,
        )
        assert sg_framing is not None, "Structural Framing header not inserted"
        assert sg_framing["family_sort_key"] < 100, (
            f"Header sort key {sg_framing['family_sort_key']} should be < 100"
        )

    def test_header_sort_key_is_exactly_min_minus_one(self):
        items = [_item(name="Roof Joist (J1) 150mm", pkg="50107", sk=122)]
        result = insert_subgroup_headers(items, sections={"50107"})
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        floor_hdr = next(
            (h for h in headers if h["item_display_name"] == "Floor System"),
            None,
        )
        assert floor_hdr is not None
        assert floor_hdr["family_sort_key"] == 121  # 122 - 1

    def test_empty_subgroup_not_inserted(self):
        # Only wall frame items → no Floor System or Footings headers
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result = insert_subgroup_headers(items, sections={"50107"})
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        header_names = {h["item_display_name"] for h in headers}
        assert "Floor System" not in header_names
        assert "Footings & Substructure" not in header_names

    def test_no_headers_when_no_matching_families(self):
        # Fixings section — not in SECTION_SUBGROUPS
        items = [_item(name="Hex Bolt M12", pkg="50111", sk=200)]
        result = insert_subgroup_headers(items, sections={"50111"})
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        assert len(headers) == 0


# ============================================================
# 3 — Estimator name rules
# ============================================================

class TestEstimatorNames:
    def test_framecad_wall_frame_renamed(self):
        items = [_item(name="FrameCAD Wall Frame | LGS 89mm C-section")]
        result, log = apply_estimator_names(items)
        assert result[0]["item_display_name"] == "Wall Framing System — LGS"
        assert len(log) == 1

    def test_wall_frame_generic_renamed(self):
        items = [_item(name="Wall Frame — perimeter type 1")]
        result, log = apply_estimator_names(items)
        assert result[0]["item_display_name"] == "Wall Framing System — LGS"

    def test_roof_truss_renamed(self):
        items = [_item(name="FrameCAD Roof Truss — 3.5kPa")]
        result, log = apply_estimator_names(items)
        assert result[0]["item_display_name"] == "Roof Truss System — LGS"

    def test_hydraulics_placeholder_renamed(self):
        items = [_item(name="Hydraulics | Builder's Works (Allowance)", pkg="50117")]
        result, log = apply_estimator_names(items)
        assert "Provisional Sum" in result[0]["item_display_name"]

    def test_item_name_never_changed(self):
        original = "FrameCAD Wall Frame | LGS 89mm C-section"
        items = [_item(name=original)]
        result, _ = apply_estimator_names(items)
        assert result[0]["item_name"] == original

    def test_subgroup_headers_not_renamed(self):
        hdr = make_subgroup_header("50107", "Structural Framing", sort_key=99)
        result, log = apply_estimator_names([hdr])
        assert result[0]["item_display_name"] == "Structural Framing"
        assert len(log) == 0

    def test_no_match_display_name_unchanged(self):
        items = [_item(name="Termite Barrier — chemical", pkg="50107")]
        result, log = apply_estimator_names(items)
        assert result[0]["item_display_name"] == "Termite Barrier — chemical"
        assert len(log) == 0


# ============================================================
# 4 — Section remap (roof battens 50107 → 50112)
# ============================================================

class TestEstimatorSectionRemaps:
    """apply_estimator_section_remaps is now a no-op.

    Battens stay in 50107 (Structural) per BOQ_FOR_AI reference — not remapped
    to 50112.  Tests verify the no-op contract and that engine codes are preserved.
    """

    def test_roof_batten_stays_in_50107(self):
        # Remap reverted: battens remain in 50107 under the Battens commercial block
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107", sk=140)]
        result, log = apply_estimator_section_remaps(items)
        assert result[0]["commercial_package_code"] == "50107"
        assert len(log) == 0   # no-op produces empty log

    def test_engine_package_code_unchanged(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107", sk=140)]
        result, _ = apply_estimator_section_remaps(items)
        assert result[0]["package_code"] == "50107"  # engine code preserved

    def test_ceiling_batten_stays_in_50107(self):
        items = [_item(name="Ceiling/Wall Batten G22 3600mm", pkg="50107")]
        result, log = apply_estimator_section_remaps(items)
        assert result[0]["commercial_package_code"] == "50107"
        assert len(log) == 0

    def test_wall_frame_stays_in_50107(self):
        items = [_item(name="FrameCAD Wall Frame type-1", pkg="50107")]
        result, log = apply_estimator_section_remaps(items)
        assert result[0]["commercial_package_code"] == "50107"
        assert len(log) == 0

    def test_non_50107_items_unchanged(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50112")]
        result, log = apply_estimator_section_remaps(items)
        assert result[0]["commercial_package_code"] == "50112"
        assert len(log) == 0


# ============================================================
# 5 — Service placeholder naming
# ============================================================

class TestPlaceholderRenames:
    def test_hydraulics_renamed_to_provisional_sum(self):
        items = [{
            **_item(name="Hydraulics | Builder's Works (Allowance)", pkg="50117"),
            "item_display_name": "Hydraulics | Builder's Works (Allowance)",
        }]
        result, log = apply_placeholder_renames(items)
        assert result[0]["item_display_name"] == (
            "Hydraulics — Builder's Works (Provisional Sum)"
        )
        assert len(log) == 1

    def test_electrical_renamed_to_provisional_sum(self):
        items = [{
            **_item(name="Electrical | Builder's Works (Allowance)", pkg="50119"),
            "item_display_name": "Electrical | Builder's Works (Allowance)",
        }]
        result, log = apply_placeholder_renames(items)
        assert result[0]["item_display_name"] == (
            "Electrical Services — Builder's Works (Provisional Sum)"
        )

    def test_other_placeholder_unchanged(self):
        items = [{
            **_item(name="Barge Capping (aluminium)", pkg="50112"),
            "item_display_name": "Barge Capping (aluminium)",
        }]
        result, log = apply_placeholder_renames(items)
        assert result[0]["item_display_name"] == "Barge Capping (aluminium)"
        assert len(log) == 0

    def test_item_name_preserved(self):
        original = "Hydraulics | Builder's Works (Allowance)"
        items = [{
            **_item(name=original, pkg="50117"),
            "item_display_name": original,
        }]
        result, _ = apply_placeholder_renames(items)
        assert result[0]["item_name"] == original


# ============================================================
# 6 — Export mode gating
# ============================================================

class TestExportModeGating:
    def test_commercial_mode_no_subgroup_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_subgroup_headers(items, _ctx("commercial"))
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        assert len(headers) == 0

    def test_engine_mode_no_subgroup_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_subgroup_headers(items, _ctx("engine"))
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        assert len(headers) == 0

    def test_estimator_mode_inserts_subgroup_headers(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, log = rule_insert_subgroup_headers(items, _ctx("estimator"))
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"]
        assert len(headers) >= 1

    def test_commercial_mode_no_estimator_transforms(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107")]
        result, log = rule_estimator_transforms(items, _ctx("commercial"))
        # No remap in commercial mode
        assert result[0]["commercial_package_code"] == "50107"
        assert len(log) == 0

    def test_estimator_mode_runs_transforms(self):
        # Section remap is now a no-op; batten stays in 50107
        # The transform pipeline still runs (names + placeholder renames may produce log entries)
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107")]
        result, log = rule_estimator_transforms(items, _ctx("estimator"))
        assert result[0]["commercial_package_code"] == "50107"


# ============================================================
# 7 — Quantity unchanged
# ============================================================

class TestQuantityPreservation:
    def test_section_remap_does_not_change_quantity(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107", qty=42.5)]
        result, _ = apply_estimator_section_remaps(items)
        assert result[0]["quantity"] == 42.5

    def test_estimator_names_do_not_change_quantity(self):
        items = [_item(name="FrameCAD Wall Frame", qty=3.0)]
        result, _ = apply_estimator_names(items)
        assert result[0]["quantity"] == 3.0

    def test_subgroup_headers_have_null_quantity(self):
        items = [_item(name="FrameCAD Wall Frame", pkg="50107", sk=100)]
        result, _ = rule_insert_subgroup_headers(items, _ctx("estimator"))
        for h in result:
            if h.get("export_class") == "export_only_grouping":
                assert h["quantity"] is None

    def test_full_pipeline_quantity_unchanged(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100, qty=5.0),
            _item(name="Top-Hat Batten G35 5800mm", pkg="50107", sk=140, qty=42.0),
        ]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        real_items = [i for i in result
                      if i.get("export_class") != "export_only_grouping"]
        qty_map = {i["item_name"]: i["quantity"] for i in real_items}
        assert qty_map["FrameCAD Wall Frame"] == 5.0
        assert qty_map["Top-Hat Batten G35 5800mm"] == 42.0


# ============================================================
# 8 — Traceability preserved
# ============================================================

class TestTraceability:
    def test_subgroup_header_has_derivation_rule(self):
        hdr = make_subgroup_header("50107", "Structural Framing", sort_key=99)
        assert hdr["derivation_rule"] == "insert_subgroup_headers"
        assert hdr["source_evidence"] == "export_layer"

    def test_subgroup_header_has_export_class(self):
        hdr = make_subgroup_header("50107", "Structural Framing", sort_key=99)
        assert hdr["export_class"] == "export_only_grouping"

    def test_remap_annotates_alignment_notes(self):
        # apply_estimator_section_remaps is now a no-op; no alignment notes added
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107")]
        result, _ = apply_estimator_section_remaps(items)
        notes = result[0].get("alignment_notes", [])
        # no remap notes — batten stays in 50107 with no annotation
        assert not any("50112" in n for n in notes)

    def test_item_name_unchanged_after_full_estimator_pipeline(self):
        original_name = "FrameCAD Wall Frame | LGS C-90 type-A"
        items = [_item(name=original_name, pkg="50107", sk=100)]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["item_name"] == original_name

    def test_engine_package_code_never_changes(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107")]
        result, _ = apply_upgrade_rules(items, _ctx("estimator"))
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["package_code"] == "50107"


# ============================================================
# 9 — Subgroup header structure
# ============================================================

class TestSubgroupHeaderStructure:
    def test_header_export_class(self):
        hdr = make_subgroup_header("50112", "Roof Cladding", sort_key=9)
        assert hdr["export_class"] == "export_only_grouping"
        assert hdr["quantity_status"] == "export_only_grouping"

    def test_header_has_no_unit(self):
        hdr = make_subgroup_header("50112", "Roof Cladding", sort_key=9)
        assert hdr["unit"] is None

    def test_header_has_no_quantity(self):
        hdr = make_subgroup_header("50112", "Roof Cladding", sort_key=9)
        assert hdr["quantity"] is None

    def test_header_manual_review_false(self):
        hdr = make_subgroup_header("50112", "Roof Cladding", sort_key=9)
        assert hdr["manual_review"] is False

    def test_header_display_name_matches_subgroup(self):
        hdr = make_subgroup_header("50115", "Wall Linings", sort_key=49)
        assert hdr["item_display_name"] == "Wall Linings"

    def test_header_item_name_has_subgroup_prefix(self):
        hdr = make_subgroup_header("50115", "Wall Linings", sort_key=49)
        assert hdr["item_name"].startswith("[SUBGROUP]")


# ============================================================
# 10 — No headers in commercial mode
# ============================================================

class TestNoHeadersInCommercialMode:
    def test_full_commercial_pipeline_no_subgroup_headers(self):
        items = [
            _item(name="FrameCAD Wall Frame", pkg="50107", sk=100),
            _item(name="Strip Footing Concrete", pkg="50107", sk=200),
        ]
        result, _ = apply_upgrade_rules(items, _ctx("commercial"))
        headers = [i for i in result if i.get("export_class") == "export_only_grouping"
                   and i.get("derivation_rule") == "insert_subgroup_headers"]
        assert len(headers) == 0

    def test_batten_stays_in_50107_in_commercial_mode(self):
        items = [_item(name="Top-Hat Batten G35 5800mm", pkg="50107")]
        result, _ = apply_upgrade_rules(items, _ctx("commercial"))
        real = [i for i in result if i.get("export_class") != "export_only_grouping"]
        assert real[0]["commercial_package_code"] == "50107"
