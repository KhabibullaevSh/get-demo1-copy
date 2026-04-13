"""
test_commercial_mapping.py — Tests for the commercial BOQ remapping layer.

Verifies that:
  1. Commercial package code overrides work correctly for key item families
  2. Engine package_code is never changed by commercial remapping
  3. Family sort keys place main items before accessories and accessories before MR
  4. FC floor sheet moves to 50115 in commercial view
  5. Vinyl/tile finishes move to 50115 in commercial view
  6. FFE items (WC, basin, mirror) move to 50129 in commercial view
  7. Electrical services items move to 50119 in commercial view
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from v3_boq_system.mapping.boq_mapper import (
    _compute_commercial_package_code,
    _compute_family_sort_key,
    map_to_boq,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(item_name: str, package: str, qty_status: str = "calculated",
         manual_review: bool = False) -> dict:
    return {
        "item_name":       item_name,
        "package":         package,
        "unit":            "nr",
        "quantity":        1,
        "quantity_status": qty_status,
        "quantity_basis":  "test",
        "source_evidence": "test_source",
        "derivation_rule": "test_rule",
        "confidence":      "MEDIUM",
        "manual_review":   manual_review,
        "notes":           "",
    }


def _mapped(items: list[dict]) -> list[dict]:
    return map_to_boq(items, {})


# ── Commercial package code overrides ────────────────────────────────────────

class TestCommercialPackageOverrides:

    def test_fc_floor_sheet_stays_in_50107(self):
        """FC floor sheet stays in 50107 (Structural) per BOQ_FOR_AI reference structure."""
        row = _row("Floor Sheet (FC / plywood)", "floor_system")
        result = _mapped([row])
        item = result[0]
        assert item["package_code"] == "50107", "Engine package_code must not change"
        assert item["commercial_package_code"] == "50107", (
            f"FC floor sheet must stay in 50107, got {item['commercial_package_code']}"
        )

    def test_vinyl_plank_moves_to_50115(self):
        """Vinyl plank finishes must appear in 50115 not 50106 (WPC)."""
        row = _row("Vinyl Plank — Supply Total (10% cut waste)", "finishes")
        result = _mapped([row])
        item = result[0]
        assert item["package_code"] == "50106"
        assert item["commercial_package_code"] == "50115", (
            f"Vinyl plank must be 50115 commercial, got {item['commercial_package_code']}"
        )

    def test_ceramic_tile_moves_to_50115(self):
        """Ceramic floor tile must appear in 50115 not 50106."""
        row = _row("Ceramic Floor Tile — Supply Total (15% cut waste)", "finishes")
        result = _mapped([row])
        item = result[0]
        assert item["commercial_package_code"] == "50115"

    def test_floor_finish_zones_move_to_50115(self):
        """Floor Finish — Dry Zone and Wet Zone must move to 50115."""
        dry = _row("Floor Finish — Dry Zone (vinyl plank)", "finishes")
        wet = _row("Floor Finish — Wet Zone (ceramic tile)", "finishes")
        result = _mapped([dry, wet])
        for item in result:
            assert item["commercial_package_code"] == "50115", (
                f"'{item['item_name']}' should be 50115, got {item['commercial_package_code']}"
            )

    def test_paint_moves_to_50116(self):
        """Paint items must appear in 50116 (Painting) not 50106 (WPC) or 50115."""
        rows = [
            _row("Paint — External", "finishes"),
            _row("Paint — Internal", "finishes"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["commercial_package_code"] == "50116", (
                f"'{item['item_name']}' paint must be 50116, got {item['commercial_package_code']}"
            )

    def test_wc_and_basin_move_to_50129(self):
        """WC Pan and Hand Basin must appear in 50129 (FFE) not 50117 (Services)."""
        rows = [
            _row("WC Pan (close-coupled)", "services"),
            _row("WC Cistern", "services"),
            _row("Hand Basin", "services"),
            _row("Tapware (basin)", "services"),
            _row("Mirror / Medicine Cabinet", "services"),
            _row("Toilet Roll Holder", "services"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["package_code"] == "50117", "Engine package must not change"
            assert item["commercial_package_code"] == "50129", (
                f"'{item['item_name']}' must be 50129 FFE, got {item['commercial_package_code']}"
            )

    def test_electrical_items_move_to_50119(self):
        """Electrical services items must appear in 50119."""
        rows = [
            _row("Main Electrical Switchboard / Distribution Board", "services"),
            _row("Exhaust Fan — Wet Area (toilet / laundry)", "services"),
            _row("Air Conditioning / Mechanical Ventilation — PLACEHOLDER", "services"),
            _row("Smoke Detectors (provisional)", "services"),
            _row("Builder's Works — Electrical (pharmacy)", "services"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["commercial_package_code"] == "50119", (
                f"'{item['item_name']}' must be 50119, got {item['commercial_package_code']}"
            )

    def test_skirting_stays_in_50106(self):
        """Skirting board stays in 50106 (WPC) — no override for trim items."""
        row = _row("Skirting Board", "finishes_trim")
        result = _mapped([row])
        item = result[0]
        assert item["commercial_package_code"] == "50106"

    def test_plumbing_services_stay_in_50117(self):
        """Builder's Works Plumbing, Hot Water System stay in 50117 (Hydraulics)."""
        rows = [
            _row("Builder's Works — Plumbing (toilet)", "services"),
            _row("Hot Water System (central)", "services"),
            _row("Main Water Meter / Stopcock", "services"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["commercial_package_code"] == "50117", (
                f"'{item['item_name']}' must stay in 50117, got {item['commercial_package_code']}"
            )

    def test_sisalation_sarking_moves_to_50118(self):
        """Roof sarking must appear in 50118 (Insulation) not 50112 (Roof)."""
        row = _row("Sisalation / Sarking", "roof_cladding")
        result = _mapped([row])
        item = result[0]
        assert item["package_code"] == "50112", "Engine package must not change"
        assert item["commercial_package_code"] == "50118", (
            f"Sisalation/Sarking must be 50118 commercial, got {item['commercial_package_code']}"
        )

    def test_cornice_moves_to_50106(self):
        """Cornice / Ceiling Trim must appear in 50106 (WPC) not 50115."""
        row = _row("Cornice / Ceiling Trim", "ceiling_trim")
        result = _mapped([row])
        assert result[0]["commercial_package_code"] == "50106", (
            f"Cornice must be 50106, got {result[0]['commercial_package_code']}"
        )

    def test_architrave_moves_to_50106(self):
        """Architrave must appear in 50106 (WPC) not 50114 (Openings)."""
        rows = [
            _row("Architrave — Door", "openings_finishes"),
            _row("Architrave — Window", "openings_finishes"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["package_code"] == "50114", "Engine package must not change"
            assert item["commercial_package_code"] == "50106", (
                f"'{item['item_name']}' must be 50106, got {item['commercial_package_code']}"
            )

    def test_verandah_decking_moves_to_50106(self):
        """Verandah Decking must appear in 50106 (WPC) not 50113 (External Cladding)."""
        rows = [
            _row("Verandah Decking / Slab", "external_verandah"),
            _row("Verandah Decking — WPC Supply Area (5% cut waste)", "external_verandah"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item["commercial_package_code"] == "50106", (
                f"'{item['item_name']}' must be 50106, got {item['commercial_package_code']}"
            )

    def test_site_preparation_moves_to_50107(self):
        """Site Preparation must appear in 50107 (Structural) not 50113 (External Cladding)."""
        row = _row("Site Preparation (Provisional)", "external_works")
        result = _mapped([row])
        assert result[0]["package_code"] == "50113", "Engine package must not change"
        assert result[0]["commercial_package_code"] == "50107", (
            f"Site Preparation must be 50107, got {result[0]['commercial_package_code']}"
        )

    def test_engine_package_code_never_changes(self):
        """commercial_package_code override must NEVER alter package_code (engine field)."""
        rows = [
            _row("Floor Sheet (FC / plywood)", "floor_system"),        # 50107 engine → 50115 commercial
            _row("WC Pan (close-coupled)", "services"),                  # 50117 engine → 50129 commercial
            _row("Vinyl Plank — Supply Total (10% cut waste)", "finishes"),  # 50106 engine → 50115 commercial
        ]
        result = _mapped(rows)
        expected_engine = {"50107", "50117", "50106"}
        actual_engine   = {i["package_code"] for i in result}
        assert actual_engine == expected_engine, (
            f"Engine package_codes changed: {actual_engine} vs {expected_engine}"
        )


# ── Family sort keys ──────────────────────────────────────────────────────────

class TestFamilySortKeys:

    def test_main_items_sort_before_accessories(self):
        """Floor sheet count must have lower family_sort_key than its fixing screws."""
        sheet_key   = _compute_family_sort_key("Floor Sheet (FC / plywood)", False, False)
        screws_key  = _compute_family_sort_key("Floor Sheet Fixing Screws", False, False)
        adhesive_key = _compute_family_sort_key("Floor Sheet Adhesive (construction adhesive)", False, False)
        assert sheet_key < screws_key, (
            f"Floor sheet ({sheet_key}) must sort before fixing screws ({screws_key})"
        )
        assert sheet_key < adhesive_key, (
            f"Floor sheet ({sheet_key}) must sort before adhesive ({adhesive_key})"
        )

    def test_wall_frame_before_battens(self):
        """Wall frame must sort before battens in structural section."""
        wall_key   = _compute_family_sort_key("Wall Frame — all wall members 89S41", False, False)
        batten_key = _compute_family_sort_key("Roof Top-Hat Batten G40 × 6000mm", False, False)
        assert wall_key < batten_key, (
            f"Wall frame ({wall_key}) must sort before roof battens ({batten_key})"
        )

    def test_mr_items_sort_after_non_mr(self):
        """Manual review items must have a family_sort_key at least 1000 higher than non-MR."""
        non_mr_key = _compute_family_sort_key("Internal Wall Lining — FC Sheet (6mm", False, False)
        mr_key     = _compute_family_sort_key("Internal Wall Lining — FC Sheet (6mm", True, False)
        assert mr_key >= non_mr_key + 1000, (
            f"MR sort key ({mr_key}) must be >= non-MR ({non_mr_key}) + 1000"
        )

    def test_placeholders_sort_last(self):
        """Placeholder items must have the highest family_sort_key."""
        mr_key  = _compute_family_sort_key("Bulk Earthworks / Level (provisional)", True, False)
        ph_key  = _compute_family_sort_key("Bulk Earthworks / Level (provisional)", True, True)
        assert ph_key > mr_key, (
            f"Placeholder ({ph_key}) must sort after MR ({mr_key})"
        )

    def test_door_hardware_after_door_main(self):
        """Door Leaf/Frame/Hinge must sort after the Door — DOOR_XX main items."""
        door_main_key = _compute_family_sort_key("Door — DOOR_82 (820mm)", False, False)
        door_leaf_key = _compute_family_sort_key("Door Leaf", False, False)
        assert door_main_key < door_leaf_key, (
            f"Main door ({door_main_key}) must sort before hardware ({door_leaf_key})"
        )

    def test_roof_cladding_before_flashings(self):
        """Roof cladding must sort before flashings in roof section."""
        cladding_key  = _compute_family_sort_key("Roof Cladding Sheet — Total Supply Area (4.5m stock)", False, False)
        flashing_key  = _compute_family_sort_key("Hip Capping (metal, pre-formed)", False, False)
        assert cladding_key <= flashing_key, (
            f"Roof cladding ({cladding_key}) must sort at or before capping ({flashing_key})"
        )

    def test_fc_sheets_before_paint_in_linings(self):
        """FC sheets must sort before paint in the linings & finishes section."""
        fc_key    = _compute_family_sort_key("Internal Wall Lining — FC Sheet (6mm", False, False)
        paint_key = _compute_family_sort_key("Paint — External", False, False)
        assert fc_key < paint_key, (
            f"FC lining ({fc_key}) must sort before paint ({paint_key})"
        )


# ── Commercial section labels ─────────────────────────────────────────────────

class TestCommercialSectionLabels:

    def test_commercial_section_label_populated(self):
        """Every mapped item must have a commercial_section_label."""
        rows = [
            _row("Floor Sheet (FC / plywood)", "floor_system"),
            _row("WC Pan (close-coupled)", "services"),
            _row("Paint — External", "finishes"),
            _row("Skirting Board", "finishes_trim"),
        ]
        result = _mapped(rows)
        for item in result:
            assert item.get("commercial_section_label"), (
                f"'{item['item_name']}' missing commercial_section_label"
            )

    def test_baseline_aligned_labels(self):
        """FC floor sheet stays in 50107 — label should reference structural."""
        row = _row("Floor Sheet (FC / plywood)", "floor_system")
        result = _mapped([row])
        label = result[0]["commercial_section_label"]
        # FC floor sheet stays in 50107 Structural per BOQ_FOR_AI reference
        assert "50107" in label
        assert "Structural" in label or "Footings" in label

    def test_ffe_label_correct(self):
        """FFE commercial label must reference furniture/fittings."""
        row = _row("WC Pan (close-coupled)", "services")
        result = _mapped([row])
        label = result[0]["commercial_section_label"]
        assert "50129" in label
        assert "FFE" in label or "Furniture" in label


# ── Classifier regression tests ───────────────────────────────────────────────

class TestFamilyClassifierRegression:
    """Regression tests for family_classifier single-keyword rules added in P2–P5."""

    def test_floor_sheet_classifies_as_floor_substrate(self):
        """'Floor Sheet' keyword must classify as floor_substrate (not unknown)."""
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Floor Sheet (FC / plywood)") == "floor_substrate"

    def test_floor_sheet_supply_area_classifies_as_floor_substrate(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Floor Sheet — Total Supply Area (1200x2400mm)") == "floor_substrate"

    def test_floor_sheet_screws_classifies_as_floor_substrate(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Floor Sheet Fixing Screws") == "floor_substrate"

    def test_floor_sheet_adhesive_classifies_as_floor_substrate(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Floor Sheet Adhesive (construction adhesive, 300mL tube)") == "floor_substrate"

    def test_wet_area_wall_lining_total_area_classifies_correctly(self):
        """'Wet Area Wall Lining' rows without 'Waterproof Board' must still classify."""
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Wet Area Wall Lining — FC Sheet Total Area (1200x2700mm)") == "wet_area_lining"

    def test_wet_area_tile_adhesive_classifies_as_floor_tile_adhesive(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Wet Area Tile Adhesive (20kg bag)") == "floor_tile_adhesive"

    def test_wet_area_wall_tile_grout_classifies_as_floor_tile_grout(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Wet Area Wall Tile Grout (3kg bag)") == "floor_tile_grout"

    def test_wet_area_waterproof_membrane_classifies_correctly(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Wet Area Waterproof Membrane (floor + upstand)") == "wet_area_waterproofing"

    def test_floor_tile_adhesive_beats_floor_finish(self):
        """tile adhesive rows must classify as floor_tile_adhesive, not floor_finish."""
        from v3_boq_system.alignment.family_classifier import classify
        result = classify("Floor Tile Adhesive — Wet Area (20kg bag)")
        assert result == "floor_tile_adhesive", f"Expected floor_tile_adhesive, got {result!r}"

    def test_floor_tile_grout_beats_floor_finish(self):
        from v3_boq_system.alignment.family_classifier import classify
        result = classify("Floor Tile Grout — Wet Area (3kg bag)")
        assert result == "floor_tile_grout", f"Expected floor_tile_grout, got {result!r}"

    def test_bulk_earthworks_classifies_as_earthworks(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Bulk Earthworks / Level (provisional)") == "earthworks"

    def test_site_preparation_classifies_as_site_prep(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Site Preparation (Provisional)") == "site_prep"

    def test_soffit_batten_classifies_as_ceiling_batten(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Verandah Soffit Batten (LGS / timber)") == "ceiling_batten"

    def test_wet_area_wall_tiling_classifies_as_wet_area_lining(self):
        from v3_boq_system.alignment.family_classifier import classify
        assert classify("Wet Area Wall Tiling — Toilet") == "wet_area_lining"
