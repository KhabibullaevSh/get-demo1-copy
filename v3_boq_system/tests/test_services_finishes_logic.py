"""test_services_finishes_logic.py — Tests for services and finishes quantifiers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    CeilingElement, FloorElement, OpeningElement, ProjectElementModel,
    RoomElement, WallElement,
)
from v3_boq_system.quantify.services_quantifier import quantify_finishes, quantify_services

_CFG = {
    "structural": {"wall_height_m": 2.4},
    "finishes": {
        "floor_finish_type": "tiles",
        "architrave_door_lm_each": 6.0,
        "architrave_window_lm_each": 4.8,
    },
    "services": {
        "building_type_service_profile": "pharmacy",
        "smoke_detector_coverage_m2": 40,
    },
}


def _base_model() -> ProjectElementModel:
    m = ProjectElementModel()
    m.floors.append(FloorElement(element_id="f1", area_m2=64.8, perimeter_m=32.4,
                                  source="dxf_geometry", confidence="HIGH"))
    m.ceilings.append(CeilingElement(element_id="c1", area_m2=64.8,
                                      source="derived", confidence="MEDIUM"))
    m.walls.append(WallElement(element_id="ext", wall_type="external",
                                length_m=38.4, height_m=2.4,
                                source="dxf_geometry", confidence="HIGH"))
    m.walls.append(WallElement(element_id="int", wall_type="internal",
                                length_m=29.4, height_m=2.4,
                                source="dxf_geometry", confidence="HIGH"))
    return m


class TestFinishesInternalPaintBothFaces:

    def test_internal_paint_uses_wall_geometry(self):
        """
        PASS B: Internal paint = ceiling_area + (ext_wall_lm + int_wall_lm) × wall_height.
        ceiling_area = 64.8 (from CeilingElement)
        ext_lm = 38.4, int_lm = 29.4, wall_h = 2.4
        int_wall_area_geom = (38.4 + 29.4) × 2.4 = 162.72
        paint_int = 64.8 + 162.72 = 227.52
        """
        m = _base_model()
        rows = quantify_finishes(m, _CFG)
        paint = [r for r in rows if r["item_name"] == "Paint — Internal"]
        assert paint, "Expected Paint — Internal row"
        # PB formula: basis mentions ext_lm and int_lm
        basis = paint[0]["quantity_basis"]
        assert "ext_lm" in basis and "int_lm" in basis, \
            f"Basis should mention ext_lm and int_lm; got: {basis}"

    def test_internal_paint_basis_mentions_geometry(self):
        """Basis string must document the wall geometry used."""
        m = _base_model()
        rows = quantify_finishes(m, _CFG)
        paint = [r for r in rows if r["item_name"] == "Paint — Internal"]
        assert paint
        basis = paint[0]["quantity_basis"]
        assert "ceiling" in basis.lower() or "ceil" in basis.lower(), \
            f"Basis should mention ceiling; got: {basis}"


class TestFinishesArchitrave:

    def test_no_duplicate_architrave_in_finishes(self):
        """quantify_finishes must NOT emit Architrave rows (opening_quantifier owns these)."""
        m = _base_model()
        m.openings.append(OpeningElement(
            element_id="D1", opening_type="door", mark="D1",
            quantity=3, source="dxf_geometry", confidence="HIGH",
        ))
        rows = quantify_finishes(m, _CFG)
        arch = [r for r in rows if "Architrave" in r["item_name"]]
        assert not arch, (
            "quantify_finishes must not produce Architrave rows — "
            "these are owned by opening_quantifier. Got: " + str([r["item_name"] for r in arch])
        )

    def test_floor_finish_row_emitted(self):
        m = _base_model()
        rows = quantify_finishes(m, _CFG)
        floor = [r for r in rows if "Floor Finish" in r["item_name"]]
        assert floor
        assert floor[0]["quantity"] == pytest.approx(64.8, 0.01)


class TestServicesWetAreaWaterproofing:

    def _templates(self):
        import yaml
        p = Path(__file__).parent.parent / "config" / "room_templates.yaml"
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    def test_wet_area_waterproofing_uses_room_area(self):
        """
        Waterproofing area = room_area + perimeter × 0.15 m upstand.
        Toilet 4 m²: PASS B perim_est = 2×(√4+1) = 2×3 = 6.0 m
        wpf = 4.0 + 6.0×0.15 = 4.9 m²
        """
        m = _base_model()
        m.rooms.append(RoomElement(
            element_id="r1", room_name="Toilet", room_type="toilet",
            area_m2=4.0, is_wet_area=True,
            source="pdf_schedule", confidence="HIGH",
        ))
        templates = self._templates()
        if not templates:
            pytest.skip("room_templates.yaml not found")
        rows = quantify_services(m, _CFG, templates)
        wfp = [r for r in rows if "Waterproofing" in r["item_name"]]
        assert wfp, "Expected Wet Area Waterproofing row"
        # PB: perim_est = 2×(√4+1) = 6.0; wpf = 4.0 + 6.0×0.15 = 4.9
        assert wfp[0]["quantity"] == pytest.approx(4.9, abs=0.05), (
            f"Expected ~4.9 m² waterproofing (PB formula), got {wfp[0]['quantity']}"
        )

    def test_waterproofing_confidence_medium_when_area_known(self):
        m = _base_model()
        m.rooms.append(RoomElement(
            element_id="r1", room_name="Bathroom", room_type="bathroom",
            area_m2=6.0, is_wet_area=True,
            source="pdf_schedule", confidence="HIGH",
        ))
        templates = self._templates()
        if not templates:
            pytest.skip("room_templates.yaml not found")
        rows = quantify_services(m, _CFG, templates)
        wfp = [r for r in rows if "Waterproofing" in r["item_name"]]
        assert wfp
        assert wfp[0]["confidence"] == "MEDIUM"
