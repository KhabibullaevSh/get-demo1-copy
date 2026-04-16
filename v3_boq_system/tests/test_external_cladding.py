"""test_external_cladding.py — Regression tests for external cladding quantifier.

Key bug fixed: opening area deduction must multiply by o.quantity AND must
exclude internal partition doors (width < 0.85 m) from the external cladding face.
"""
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    OpeningElement, ProjectElementModel, WallElement,
)
from v3_boq_system.quantify.external_cladding_quantifier import quantify_external_cladding

_CFG = {
    "structural": {"wall_stud_spacing_mm": 600},
    "external_cladding": {
        "board_exposure_mm": 200,
        "board_length_mm": 4200,
        "waste_factor": 1.05,
    },
    "lining": {"default_louvre_height_m": 0.75},
}


def _ext_wall_model() -> ProjectElementModel:
    m = ProjectElementModel()
    m.walls.append(WallElement(
        element_id="ext", wall_type="external",
        length_m=38.4, height_m=2.4,
        source="dxf_geometry", confidence="HIGH",
    ))
    return m


class TestOpeningAreaDeductionBugFix:
    """
    Guard against the regression where quantity was not multiplied.

    Scenario (mirrors Angau Pharmacy project2):
      - 1 × DOOR_90   (entrance, w=0.92 m, h=2.04 m, qty=1)  — external entrance
      - 4 × DOOR_82   (partition, w=0.82 m, h=2.04 m, qty=4) — internal (< 0.85 m)
      - 8 × WIN_1100  (louvre,    w=1.10 m, h=0.00 m, qty=8) — louvre_h_default=0.75
    """

    def _make_model(self) -> ProjectElementModel:
        m = _ext_wall_model()
        m.openings.append(OpeningElement(
            element_id="d90", mark="DOOR_90", opening_type="door",
            width_m=0.92, height_m=2.04, quantity=1, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        m.openings.append(OpeningElement(
            element_id="d82", mark="DOOR_82", opening_type="door",
            width_m=0.82, height_m=2.04, quantity=4, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        m.openings.append(OpeningElement(
            element_id="w11", mark="WIN_1100", opening_type="window",
            width_m=1.10, height_m=0.0, quantity=8, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        return m

    def _cladding_row(self, model):
        rows = quantify_external_cladding(model, _CFG)
        hits = [r for r in rows if "FC Weatherboard (supply" in r["item_name"]]
        assert hits, f"No cladding area row. Got: {[r['item_name'] for r in rows]}"
        return hits[0]

    def test_partition_doors_excluded_from_cladding_deduction(self):
        """DOOR_82 (w=0.82 < 0.85 m) must NOT appear in external cladding deduction."""
        row = self._cladding_row(self._make_model())
        notes = row.get("notes", "")
        # Partition doors must be mentioned as excluded
        assert "Partition" in notes or "partition" in notes, (
            f"Expected partition-door exclusion note. Got: {notes}"
        )
        # DOOR_82 must not be in the deduction detail
        assert "DOOR_82" not in notes or "excluded" in notes.lower() or "Partition" in notes, (
            "DOOR_82 must be excluded from cladding deduction"
        )

    def test_opening_deduction_multiplies_by_quantity(self):
        """
        Correct deduction:
          entrance door: 1 × 0.92 × 2.04 = 1.877 m²
          windows:       8 × 1.10 × 0.75 = 6.600 m²
          total:         8.477 m²
        Gross: 38.4 × 2.4 = 92.16 m²  →  net ≈ 83.68 m²

        Old buggy result (no ×qty, all is_external doors counted once each):
          DOOR_90×1 + DOOR_82×1 (wrongly included) + window×1 = ~4.2 m²
        """
        row = self._cladding_row(self._make_model())
        gross = 38.4 * 2.4          # 92.16
        door_ded = 1 * 0.92 * 2.04  # 1.877
        win_ded  = 8 * 1.10 * 0.75  # 6.600
        expected_net = round(gross - door_ded - win_ded, 2)  # 83.68

        assert abs(row["quantity"] - expected_net) < 0.05, (
            f"Expected net cladding ≈ {expected_net} m², got {row['quantity']} m². "
            "Check that o.quantity is multiplied and partition doors are excluded."
        )

    def test_net_area_less_than_gross(self):
        """Net area must be strictly less than gross area when openings exist."""
        model = self._make_model()
        gross = 38.4 * 2.4
        row = self._cladding_row(model)
        assert row["quantity"] < gross, (
            f"Net area ({row['quantity']}) should be < gross ({gross})"
        )

    def test_no_openings_returns_gross(self):
        """When no openings, net area equals gross area."""
        model = _ext_wall_model()
        row = self._cladding_row(model)
        gross = 38.4 * 2.4
        assert abs(row["quantity"] - gross) < 0.01, (
            f"Expected gross {gross}, got {row['quantity']}"
        )

    def test_louvre_uses_config_height_not_wall_height(self):
        """
        A louvre with height_m=0 must use _louvre_h_default (0.75), not wall height (2.4).
        """
        model = _ext_wall_model()
        model.openings.append(OpeningElement(
            element_id="w_louvre", mark="WIN_LOUVRE", opening_type="window",
            width_m=1.10, height_m=0.0, quantity=1, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        row = self._cladding_row(model)
        # With louvre_h=0.75: deduction = 1×1.10×0.75 = 0.825 m²
        # With wall_h=2.4:    deduction = 1×1.10×2.40 = 2.640 m² (wrong)
        gross = 38.4 * 2.4
        expected_net_correct = round(gross - 1.10 * 0.75, 2)
        expected_net_wrong   = round(gross - 1.10 * 2.40, 2)
        assert abs(row["quantity"] - expected_net_correct) < 0.05, (
            f"Expected net ≈ {expected_net_correct} (louvre_h=0.75), "
            f"got {row['quantity']} (wrong would be {expected_net_wrong})"
        )


class TestRevealTrimClassification:
    """Reveal trim must use same entrance-door threshold as area deduction."""

    def test_reveal_trim_excludes_partition_doors(self):
        """Partition doors (< 0.85 m) must not contribute to reveal trim lm."""
        model = _ext_wall_model()
        # Add only a partition door
        model.openings.append(OpeningElement(
            element_id="d82", mark="DOOR_82", opening_type="door",
            width_m=0.82, height_m=2.04, quantity=4, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        rows = quantify_external_cladding(model, _CFG)
        trim_rows = [r for r in rows if "Reveal Trim" in r["item_name"]]
        # No external entrance openings → no reveal trim row expected
        assert len(trim_rows) == 0, (
            "Reveal trim row must not be emitted for partition doors only. "
            f"Got: {[r['item_name'] for r in trim_rows]}"
        )

    def test_reveal_trim_includes_entrance_door_with_quantity(self):
        """
        Entrance door (≥ 0.85 m) × qty=2 contributes to reveal trim.
        Trim per door = 2×h + w = 2×2.04 + 0.92 = 5.0 lm
        Total = 2 × 5.0 = 10.0 lm
        """
        model = _ext_wall_model()
        model.openings.append(OpeningElement(
            element_id="d90x2", mark="DOOR_90", opening_type="door",
            width_m=0.92, height_m=2.04, quantity=2, is_external=True,
            source="dxf_geometry", confidence="HIGH",
        ))
        rows = quantify_external_cladding(model, _CFG)
        trim_rows = [r for r in rows if "Reveal Trim" in r["item_name"]]
        assert trim_rows, "Expected reveal trim row for entrance door"
        expected_lm = round((2 * 2.04 + 0.92) * 2, 2)  # 10.0 lm
        assert abs(trim_rows[0]["quantity"] - expected_lm) < 0.05, (
            f"Expected {expected_lm} lm, got {trim_rows[0]['quantity']}"
        )
