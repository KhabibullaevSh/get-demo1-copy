"""test_lining_logic.py — Tests for lining quantifier."""
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    CeilingElement, ProjectElementModel, RoomElement, WallElement
)
from v3_boq_system.quantify.lining_quantifier import quantify_linings

_CFG = {
    "structural": {"wall_height_m": 2.4, "ceiling_batten_spacing_mm": 400},
    "lining": {"fc_wall_sheet_area_m2": 3.24, "fc_ceiling_sheet_area_m2": 2.88,
               "waste_factor": 1.05, "int_wall_lm_ratio": 0.34},
}


def _model() -> ProjectElementModel:
    m = ProjectElementModel()
    m.walls.append(WallElement(element_id="ext", wall_type="external",
                                length_m=38.4, height_m=2.4,
                                source="dxf_geometry", confidence="HIGH"))
    m.walls.append(WallElement(element_id="int", wall_type="internal",
                                length_m=29.4, height_m=2.4,
                                source="derived_ratio", confidence="LOW"))
    m.ceilings.append(CeilingElement(element_id="c1", area_m2=64.8,
                                      source="derived", confidence="MEDIUM"))
    return m


class TestLiningQuantifier:

    def test_external_wall_sheets_correct(self):
        """Ext wall sheets = ceil(38.4 × 2.4 × 1.05 / 3.24) = 30"""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        ext_rows = [r for r in rows if "External Wall Lining" in r["item_name"]
                    and "Sheet" in r["item_name"] and "Screw" not in r["item_name"]]
        assert ext_rows, "Expected external wall lining row"
        assert ext_rows[0]["quantity"] == 30

    def test_internal_wall_sheets_correct(self):
        """Int wall sheets = ceil(29.4 × 2.4 × 1.05 / 3.24) = 23"""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        int_rows = [r for r in rows if "Internal Wall Lining" in r["item_name"]
                    and "Sheet" in r["item_name"] and "Screw" not in r["item_name"]]
        assert int_rows, "Expected internal wall lining row"
        assert int_rows[0]["quantity"] == 23

    def test_ceiling_sheets_correct(self):
        """Ceiling sheets = ceil(64.8 × 1.05 / 2.88) = 24"""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        ceil_rows = [r for r in rows if "Ceiling Lining" in r["item_name"]
                     and "Screw" not in r["item_name"] and "Batten" not in r["item_name"]]
        assert ceil_rows
        assert ceil_rows[0]["quantity"] == 24

    def test_cornice_equals_ext_wall_perimeter(self):
        """Cornice lm = ext_wall_perimeter = 38.4"""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        cornice = [r for r in rows if "Cornice" in r["item_name"]]
        assert cornice
        assert cornice[0]["quantity"] == pytest.approx(38.4, 0.1)

    def test_skirting_equals_ext_plus_int(self):
        """Skirting = ext(38.4) + int(29.4) × 2 faces = 97.2
        Internal partitions have skirting on both sides."""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        skirting = [r for r in rows if "Skirting" in r["item_name"]]
        assert skirting
        assert skirting[0]["quantity"] == pytest.approx(97.2, 0.1)

    def test_wet_area_lining_with_room_data(self):
        """Wet rooms should trigger wet area lining row."""
        m = _model()
        m.rooms.append(RoomElement(element_id="r1", room_name="Toilet",
                                    room_type="toilet", area_m2=4.0,
                                    is_wet_area=True, source="pdf_schedule",
                                    confidence="HIGH"))
        rows = quantify_linings(m, _CFG, {})
        wet_rows = [r for r in rows if "Wet Area" in r["item_name"]]
        assert wet_rows, "Expected wet area lining row when wet room detected"

    def test_internal_wall_lining_marked_manual_review_when_estimated(self):
        """Int wall from ratio estimate → manual_review = True"""
        m = _model()
        rows = quantify_linings(m, _CFG, {})
        int_sheet_rows = [r for r in rows if "Internal Wall Lining" in r["item_name"]
                          and "Sheet" in r["item_name"]]
        if int_sheet_rows:
            assert int_sheet_rows[0].get("manual_review") is True
