"""test_traceability.py — Verify every generated row has full traceability metadata."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    CeilingElement, FloorElement, OpeningElement, ProjectElementModel,
    RoofElement, VerandahElement, WallElement,
)
from v3_boq_system.quantify.lining_quantifier    import quantify_linings
from v3_boq_system.quantify.opening_quantifier   import quantify_openings
from v3_boq_system.quantify.roof_quantifier      import quantify_roof
from v3_boq_system.quantify.stair_ramp_quantifier import quantify_stairs

_BASE_CONFIG = {
    "structural": {"wall_height_m": 2.4, "ceiling_batten_spacing_mm": 400,
                   "roof_batten_spacing_mm": 900},
    "lining": {"fc_wall_sheet_area_m2": 3.24, "fc_ceiling_sheet_area_m2": 2.88,
               "waste_factor": 1.05, "int_wall_lm_ratio": 0.34},
    "roof": {"min_downpipes": 2, "downpipe_spacing_m": 10.0, "sisalation_roll_m2": 73.0},
    "finishes": {"architrave_door_lm_each": 6.0, "architrave_window_lm_each": 4.8},
    "openings": {"default_door_height_m": 2.04,
                 "door_block_width_map": {"DOOR_90": 0.9, "DOOR_82": 0.82}},
}

_REQUIRED_FIELDS = [
    "item_name", "unit", "quantity", "package",
    "quantity_status", "quantity_basis", "source_evidence", "derivation_rule", "confidence",
]

_VALID_STATUSES = {"measured", "calculated", "inferred", "placeholder"}
_VALID_CONFIDENCES = {"HIGH", "MEDIUM", "LOW"}


def _check_rows(rows: list[dict]) -> None:
    for row in rows:
        for field in _REQUIRED_FIELDS:
            assert field in row, f"Missing field '{field}' on row: {row.get('item_name','?')}"
        assert row["quantity_status"] in _VALID_STATUSES, (
            f"Invalid quantity_status '{row['quantity_status']}' on '{row['item_name']}'"
        )
        assert row["confidence"] in _VALID_CONFIDENCES, (
            f"Invalid confidence '{row['confidence']}' on '{row['item_name']}'"
        )
        assert row["source_evidence"], f"Empty source_evidence on '{row['item_name']}'"
        assert row["derivation_rule"], f"Empty derivation_rule on '{row['item_name']}'"


def _standard_model() -> ProjectElementModel:
    m = ProjectElementModel()
    m.floors.append(FloorElement(element_id="gf", area_m2=86.4, perimeter_m=38.4,
                                  source="dxf_geometry", confidence="HIGH"))
    m.ceilings.append(CeilingElement(element_id="c1", area_m2=64.8,
                                      source="derived", confidence="MEDIUM"))
    m.roofs.append(RoofElement(
        element_id="r1", area_m2=106.6, perimeter_m=42.4,
        eaves_length_m=42.4, ridge_length_m=10.6, barge_length_m=8.5,
        roof_type="hip", source="dxf_geometry", confidence="HIGH",
    ))
    m.verandahs.append(VerandahElement(element_id="v1", area_m2=21.6,
                                        perimeter_m=20.4, source="dxf_geometry",
                                        confidence="HIGH"))
    m.walls.append(WallElement(element_id="ext", wall_type="external",
                                length_m=38.4, height_m=2.4,
                                source="dxf_geometry", confidence="HIGH"))
    m.walls.append(WallElement(element_id="int", wall_type="internal",
                                length_m=29.4, height_m=2.4,
                                source="derived_ratio", confidence="LOW"))
    m.openings.append(OpeningElement(element_id="d1", opening_type="door",
                                      mark="DOOR_90", width_m=0.9, quantity=6,
                                      swing_type="hinged", source="dxf_blocks",
                                      confidence="HIGH"))
    m.openings.append(OpeningElement(element_id="w1", opening_type="window",
                                      mark="WINDOW_LOUVRE", quantity=11,
                                      swing_type="louvre", source="dxf_blocks",
                                      confidence="HIGH"))
    return m


class TestTraceability:

    def test_lining_rows_have_traceability(self):
        m = _standard_model()
        rows = quantify_linings(m, _BASE_CONFIG, {})
        assert len(rows) > 0
        _check_rows(rows)

    def test_roof_rows_have_traceability(self):
        import yaml
        from pathlib import Path
        asm_path = Path(__file__).parent.parent / "config" / "assembly_rules.yaml"
        if asm_path.exists():
            with open(asm_path) as f:
                rules = yaml.safe_load(f) or {}
        else:
            rules = {}
        m = _standard_model()
        rows = quantify_roof(m, _BASE_CONFIG, rules)
        assert len(rows) > 0
        _check_rows(rows)

    def test_opening_rows_have_traceability(self):
        import yaml
        from pathlib import Path
        asm_path = Path(__file__).parent.parent / "config" / "assembly_rules.yaml"
        rules = {}
        if asm_path.exists():
            with open(asm_path) as f:
                rules = yaml.safe_load(f) or {}
        m = _standard_model()
        rows = quantify_openings(m, _BASE_CONFIG, rules)
        assert len(rows) > 0
        _check_rows(rows)

    def test_stair_rows_have_traceability(self):
        from v3_boq_system.normalize.element_model import StairElement
        m = _standard_model()
        m.stairs.append(StairElement(element_id="s1", stair_type="prefab",
                                      flights=1, risers_per_flight=5,
                                      source="dxf_geometry", confidence="MEDIUM"))
        rows = quantify_stairs(m, _BASE_CONFIG)
        assert len(rows) > 0
        _check_rows(rows)

    def test_manual_review_items_have_notes(self):
        """All manual_review=True items should have non-empty notes."""
        m = _standard_model()
        rows = quantify_linings(m, _BASE_CONFIG, {})
        mr_rows = [r for r in rows if r.get("manual_review")]
        for row in mr_rows:
            assert row.get("notes"), (
                f"manual_review item '{row['item_name']}' has empty notes"
            )
