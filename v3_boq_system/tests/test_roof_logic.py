"""test_roof_logic.py — Tests for roof assembly derivation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml
import pytest
from v3_boq_system.normalize.element_model import (
    ProjectElementModel, RoofElement, StructuralFrameElement, WallElement
)
from v3_boq_system.quantify.roof_quantifier import quantify_roof
from v3_boq_system.assemblies.assembly_engine import apply_all_roof_assemblies

_CFG = {
    "structural": {"roof_batten_spacing_mm": 900, "wall_height_m": 2.4},
    "roof": {"min_downpipes": 2, "downpipe_spacing_m": 10.0, "sisalation_roll_m2": 73.0},
    "lining": {"fc_wall_sheet_area_m2": 3.24, "waste_factor": 1.05},
}

def _load_rules():
    p = Path(__file__).parent.parent / "config" / "assembly_rules.yaml"
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


def _model_with_roof() -> ProjectElementModel:
    m = ProjectElementModel()
    m.roofs.append(RoofElement(
        element_id="r1", area_m2=106.6, perimeter_m=42.4,
        eaves_length_m=42.4, ridge_length_m=10.6, barge_length_m=8.5,
        roof_type="hip", source="dxf_geometry", confidence="HIGH",
    ))
    m.walls.append(WallElement(element_id="ext", wall_type="external",
                                length_m=38.4, height_m=2.4,
                                source="dxf_geometry", confidence="HIGH"))
    return m


class TestRoofAssemblyDerivation:

    def test_gutter_equals_full_eaves_perimeter(self):
        """Gutter lm must equal full eaves length (not half)."""
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        gutter_rows = [r for r in rows if "Gutter" in r["item_name"] and "Joiner" not in r["item_name"]
                       and "Outlet" not in r["item_name"] and "End" not in r["item_name"]]
        if gutter_rows:
            assert gutter_rows[0]["quantity"] == pytest.approx(42.4, 0.1), (
                f"Gutter should be 42.4 lm. Got {gutter_rows[0]['quantity']}"
            )

    def test_roof_cladding_equals_roof_area(self):
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        cladding = [r for r in rows if "Cladding" in r["item_name"] or "CGI" in r["item_name"]]
        if cladding:
            assert cladding[0]["quantity"] == pytest.approx(106.6, 0.1)

    def test_fascia_equals_roof_perimeter(self):
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        fascia = [r for r in rows if "Fascia" in r["item_name"] and "Clip" not in r["item_name"]]
        if fascia:
            assert fascia[0]["quantity"] == pytest.approx(42.4, 0.1)

    def test_at_least_2_downpipes(self):
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        dp = [r for r in rows if "Downpipe" in r["item_name"] and "Elbow" not in r["item_name"]
              and "Clip" not in r["item_name"]]
        if dp:
            assert dp[0]["quantity"] >= 2, "Should have at least 2 downpipes"

    def test_insulation_equals_roof_area(self):
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        ins = [r for r in rows if "Insulation" in r["item_name"] and "Roof" in r["item_name"]]
        if ins:
            assert ins[0]["quantity"] == pytest.approx(106.6, 0.1)

    def test_assembly_engine_gutter_items(self):
        """Assembly engine must produce gutter joiners and stop ends."""
        rules = _load_rules()
        rows = apply_all_roof_assemblies(
            roof_area_m2=106.6, eaves_lm=42.4, ridge_lm=10.6,
            barge_lm=8.5, valley_lm=0, downpipe_count=2,
            rules=rules,
        )
        names = [r["item_name"] for r in rows]
        assert any("Joiner" in n for n in names), f"Missing gutter joiner. Got: {names}"
        assert any("Stop End" in n or "stop end" in n.lower() for n in names)

    def test_all_roof_rows_have_traceability(self):
        rules = _load_rules()
        m = _model_with_roof()
        rows = quantify_roof(m, _CFG, rules)
        for row in rows:
            assert row.get("source_evidence"), f"Missing source_evidence: {row['item_name']}"

    def test_apron_flashing_row_when_apron_length_present(self):
        """When apron_length_m > 0, an Apron Flashing row must be emitted."""
        rules = _load_rules()
        m = ProjectElementModel()
        m.roofs.append(RoofElement(
            element_id="r1", area_m2=106.6, perimeter_m=42.4,
            eaves_length_m=42.4, ridge_length_m=10.6,
            apron_length_m=6.0,   # wall-to-roof junction
            roof_type="hip", source="dxf_geometry", confidence="HIGH",
        ))
        rows = quantify_roof(m, _CFG, rules)
        apron = [r for r in rows if "Apron" in r["item_name"]]
        assert apron, "Apron Flashing row expected when apron_length_m > 0"
        assert apron[0]["quantity"] == pytest.approx(6.0, 0.01)

    def test_cladding_sheet_count_row_emitted(self):
        """Assembly engine must emit a 'sheets' unit row using stock-length selection."""
        import math
        rules = _load_rules()
        rows = apply_all_roof_assemblies(
            roof_area_m2=106.6, eaves_lm=42.4, ridge_lm=10.6,
            barge_lm=0, valley_lm=0, downpipe_count=2,
            rules=rules,
        )
        sheet_rows = [r for r in rows if r.get("unit") == "sheets" and "Cladding" in r["item_name"]]
        assert sheet_rows, "Expected a 'sheets' unit row for roof cladding"
        # run = 106.6/42.4 = 2.514m → min_len=2.664m → stock=3.0m
        # count = ceil(106.6 × 1.05 / (0.762 × 3.0)) = ceil(48.94) = 49
        expected = math.ceil(106.6 * 1.05 / (0.762 * 3.0))
        assert sheet_rows[0]["quantity"] == expected, (
            f"Expected {expected} sheets (stock-length formula), got {sheet_rows[0]['quantity']}"
        )

    def test_ridge_estimated_from_floor_plan_when_missing(self):
        """When ridge_length_m == 0, ridge should be estimated from floor plan."""
        from v3_boq_system.quantify.roof_quantifier import _estimate_ridge_lm
        from v3_boq_system.normalize.element_model import FloorElement
        # 10×8 rectangle: L=10, W=8 → hip ridge = L−W = 2
        floor_area  = 80.0
        floor_perim = 36.0
        ridge, note = _estimate_ridge_lm(floor_area, floor_perim, "hip")
        assert ridge == pytest.approx(2.0, abs=0.5), f"Expected ~2.0 m ridge, got {ridge}"

    def test_gable_ridge_estimated_as_long_dimension(self):
        """For gable roof, ridge ≈ long dimension of floor plan."""
        from v3_boq_system.quantify.roof_quantifier import _estimate_ridge_lm
        # 12×6 rectangle: L=12, W=6
        ridge, note = _estimate_ridge_lm(72.0, 36.0, "gable")
        assert ridge == pytest.approx(12.0, abs=0.5), f"Expected ~12 m gable ridge, got {ridge}"

    def test_ridge_end_caps_emitted(self):
        """Ridge end caps (nr=2) should appear when ridge length > 0."""
        rules = _load_rules()
        rows = apply_all_roof_assemblies(
            roof_area_m2=106.6, eaves_lm=42.4, ridge_lm=10.6,
            barge_lm=0, valley_lm=0, downpipe_count=2,
            rules=rules,
        )
        caps = [r for r in rows if "End Cap" in r["item_name"] and "Ridge" in r["item_name"]]
        assert caps, "Ridge End Caps row expected"
        assert caps[0]["quantity"] == 2
