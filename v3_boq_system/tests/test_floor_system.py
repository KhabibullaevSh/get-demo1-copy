"""test_floor_system.py — Tests for floor system quantifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from v3_boq_system.normalize.element_model import (
    FloorElement, FloorSystemElement, FootingElement, ProjectElementModel,
    VerandahElement, SpaceElement,
)
from v3_boq_system.quantify.floor_system_quantifier import quantify_floor_system
from v3_boq_system.quantify.footing_quantifier import quantify_footings


_BASE_CONFIG = {
    "structural": {"floor_joist_spacing_mm": 450, "floor_bearer_spacing_mm": 1800,
                   "floor_panel_width_m": 0.6, "floor_panel_length_m": 3.6,
                   "floor_panel_height_mm": 200},
    "lining": {"fc_ceiling_sheet_area_m2": 2.88, "waste_factor": 1.05},
    "footings": {"slab_thickness_mm": 100, "mesh_type": "SL72",
                 "strip_footing_depth_m": 0.5, "strip_footing_width_m": 0.4},
}


def _model_with_floor(area=86.4, perim=38.4) -> ProjectElementModel:
    m = ProjectElementModel()
    m.floors.append(FloorElement(element_id="gf", area_m2=area, perimeter_m=perim,
                                  source="dxf_geometry", confidence="HIGH"))
    return m


def _model_with_slab(area=86.4, perim=38.4) -> ProjectElementModel:
    m = _model_with_floor(area, perim)
    m.footings.append(FootingElement(
        element_id="slab_gf", footing_type="slab",
        area_m2=area, perimeter_m=perim,
        thickness_mm=100, concrete_m3=round(area * 0.1, 2),
        reinforcement="SL72", source="derived", confidence="LOW",
        notes="Slab on ground assumed."
    ))
    return m


class TestFloorSystemQuantifier:

    def test_slab_on_ground_when_no_joists(self):
        """With no joist/panel/floor-type evidence, floor_system emits a slab placeholder."""
        model = _model_with_slab(86.4)
        rows = quantify_floor_system(model, _BASE_CONFIG)
        names = [r["item_name"] for r in rows]
        assert any("Slab" in n or "slab" in n for n in names), (
            f"Expected slab placeholder row. Got: {names}"
        )

    def test_slab_mesh_and_vapour_barrier_in_footings(self):
        """Slab mesh and vapour barrier should be in footing_quantifier, NOT floor_system."""
        model = _model_with_slab(86.4)
        # floor_system should NOT have mesh/vapour (they belong in footings)
        fs_rows = quantify_floor_system(model, _BASE_CONFIG)
        fs_names = [r["item_name"] for r in fs_rows]
        assert not any("Mesh" in n or "mesh" in n for n in fs_names), (
            "Mesh should not be in floor_system rows — it belongs in footings"
        )
        # footing_quantifier SHOULD have mesh and vapour barrier
        ft_rows = quantify_footings(model, _BASE_CONFIG)
        ft_names = [r["item_name"] for r in ft_rows]
        assert any("Mesh" in n or "mesh" in n for n in ft_names), (
            f"Expected mesh in footings. Got: {ft_names}"
        )
        assert any("Vapour" in n or "vapour" in n for n in ft_names), (
            f"Expected vapour barrier in footings. Got: {ft_names}"
        )

    def test_ifc_joist_data_used_when_present(self):
        model = _model_with_floor(86.4)
        model.floor_systems.append(FloorSystemElement(
            element_id="floor_joist_ifc",
            assembly_type="floor_joist",
            total_joist_lm=250.0,
            source="ifc_model",
            confidence="HIGH",
        ))
        rows = quantify_floor_system(model, _BASE_CONFIG)
        names = [r["item_name"] for r in rows]
        assert any("Joist" in n or "joist" in n for n in names), (
            "IFC joist data should produce joist BOQ row"
        )

    def test_bom_floor_panel_highest_priority(self):
        model = _model_with_floor(86.4)
        # Add both IFC joist and BOM panel — BOM should win
        model.floor_systems.append(FloorSystemElement(
            element_id="floor_panel_bom",
            assembly_type="floor_panel",
            total_joist_lm=300.0,
            source="framecad_bom",
            confidence="HIGH",
        ))
        model.floor_systems.append(FloorSystemElement(
            element_id="floor_joist_ifc",
            assembly_type="floor_joist",
            total_joist_lm=250.0,
            source="ifc_model",
            confidence="HIGH",
        ))
        rows = quantify_floor_system(model, _BASE_CONFIG)
        # All rows should use framecad_bom evidence
        bom_rows = [r for r in rows if "framecad_bom" in r.get("source_evidence", "")]
        assert len(bom_rows) > 0, "BOM source should take priority over IFC"

    def test_steel_floor_frame_produces_joist_and_bearer(self):
        """When floor_type=steel is detected (no schedule), derive joist + bearer rows."""
        model = _model_with_floor(86.4)
        model.floor_systems.append(FloorSystemElement(
            element_id="floor_steel_derived",
            assembly_type="steel_floor_frame",
            floor_area_m2=86.4,
            source="pdf_layout",
            source_reference="FrameCAD layout Design Summary: Floor Type Steel",
            confidence="MEDIUM",
        ))
        rows = quantify_floor_system(model, _BASE_CONFIG)
        names = [r["item_name"] for r in rows]
        assert any("Joist" in n or "joist" in n or "Cassette" in n for n in names), (
            f"Steel floor frame should produce joist/cassette rows. Got: {names}"
        )
        assert any("Bearer" in n or "bearer" in n for n in names), (
            f"Steel floor frame should produce bearer rows. Got: {names}"
        )
        # All items should be manual_review since no schedule
        for r in rows:
            if r.get("quantity_status") in ("inferred", "calculated"):
                assert r.get("manual_review") is True or r.get("confidence") in ("LOW", "MEDIUM")

    def test_all_rows_have_traceability(self):
        model = _model_with_slab(86.4)
        rows = quantify_floor_system(model, _BASE_CONFIG)
        for row in rows:
            assert row.get("source_evidence"), f"Row '{row['item_name']}' missing source_evidence"
            assert row.get("derivation_rule"), f"Row '{row['item_name']}' missing derivation_rule"
            assert row.get("quantity_basis"),  f"Row '{row['item_name']}' missing quantity_basis"
            assert row.get("confidence") in ("HIGH","MEDIUM","LOW")

    def test_no_quantities_from_boq_template(self):
        model = _model_with_slab(86.4)
        rows = quantify_floor_system(model, _BASE_CONFIG)
        for row in rows:
            ev = row.get("source_evidence","").lower()
            assert "boq_template" not in ev
            assert "benchmark" not in ev


class TestFloorSheetZoneExclusion:
    """Tests that verandah area is excluded from FC floor sheet regardless of source case."""

    def _model_with_verandah(self, building_area=86.4, building_perim=38.4,
                              ver_area=21.6, ver_perim=20.4) -> ProjectElementModel:
        m = ProjectElementModel()
        m.floors.append(FloorElement(
            element_id="gf", area_m2=building_area, perimeter_m=building_perim,
            source="dxf_geometry", confidence="HIGH",
        ))
        m.verandahs.append(VerandahElement(
            element_id="ver", area_m2=ver_area, perimeter_m=ver_perim,
            source="dxf_geometry", confidence="HIGH",
        ))
        return m

    def test_bom_case_excludes_verandah_from_floor_sheet(self):
        """CASE 1 (BOM): floor sheet area must not include verandah area."""
        m = self._model_with_verandah()
        m.floor_systems.append(FloorSystemElement(
            element_id="fp_bom", assembly_type="floor_panel",
            total_joist_lm=300.0, source="framecad_bom", confidence="HIGH",
        ))
        rows = quantify_floor_system(m, _BASE_CONFIG)
        sheet_rows = [r for r in rows if "Floor Sheet" in r["item_name"]
                      and r["unit"] in ("sheets", "m2")]
        assert sheet_rows, "BOM case must produce floor sheet rows"
        for r in sheet_rows:
            # Evidence must NOT reference the full 86.4 m² (which would include verandah)
            ev = r.get("source_evidence", "")
            # The correct main_floor_area is 86.4 - 21.6 = 64.8 m²
            assert "64.8" in ev or "64.80" in ev, (
                f"BOM floor sheet evidence should reference main_floor_area=64.8m², not full 86.4m². "
                f"Got: {ev}"
            )

    def test_ifc_case_excludes_verandah_from_floor_sheet(self):
        """CASE 2 (IFC): floor sheet area must not include verandah area."""
        m = self._model_with_verandah()
        m.floor_systems.append(FloorSystemElement(
            element_id="fl_ifc", assembly_type="floor_joist",
            total_joist_lm=250.0, source="ifc_model", confidence="HIGH",
        ))
        rows = quantify_floor_system(m, _BASE_CONFIG)
        sheet_rows = [r for r in rows if "Floor Sheet" in r["item_name"]
                      and r["unit"] in ("sheets", "m2")]
        assert sheet_rows, "IFC case must produce floor sheet rows"
        for r in sheet_rows:
            ev = r.get("source_evidence", "")
            assert "64.8" in ev or "64.80" in ev, (
                f"IFC floor sheet evidence should reference main_floor_area=64.8m², not full 86.4m². "
                f"Got: {ev}"
            )

    def test_floor_sheet_zone_notes_show_dry_wet_breakdown(self):
        """When space model has dry and wet zones, floor sheet notes document the split."""
        m = self._model_with_verandah()
        m.floor_systems.append(FloorSystemElement(
            element_id="fp_bom", assembly_type="floor_panel",
            total_joist_lm=300.0, source="framecad_bom", confidence="HIGH",
        ))
        # Add dry and wet spaces to the model
        m.spaces.append(SpaceElement(
            element_id="s_dry", space_id="space_01", space_name="Office",
            space_type="office", area_m2=60.3, is_enclosed=True, is_wet=False,
            source_type="config", confidence="LOW",
        ))
        m.spaces.append(SpaceElement(
            element_id="s_wet", space_id="space_02", space_name="Toilet",
            space_type="toilet", area_m2=4.5, is_enclosed=True, is_wet=True,
            source_type="config", confidence="LOW",
        ))
        rows = quantify_floor_system(m, _BASE_CONFIG)
        sheet_rows = [r for r in rows if "Floor Sheet" in r["item_name"]
                      and r["unit"] == "sheets"]
        assert sheet_rows, "Should produce a sheet-count row"
        notes = sheet_rows[0].get("notes", "")
        assert "dry_internal" in notes or "internal_dry" in notes, (
            f"Sheet notes should document dry zone. Got: {notes[:200]}"
        )
        assert "wet_internal" in notes or "internal_wet" in notes, (
            f"Sheet notes should document wet zone. Got: {notes[:200]}"
        )

    def test_verandah_explicitly_excluded_from_substrate_notes(self):
        """Floor sheet notes must explicitly state verandah is excluded."""
        m = self._model_with_verandah()
        m.floor_systems.append(FloorSystemElement(
            element_id="fp_bom", assembly_type="floor_panel",
            total_joist_lm=300.0, source="framecad_bom", confidence="HIGH",
        ))
        m.spaces.append(SpaceElement(
            element_id="s_dry", space_id="space_01", space_name="Dispensary",
            space_type="pharmacy", area_m2=64.8, is_enclosed=True, is_wet=False,
            source_type="config", confidence="LOW",
        ))
        rows = quantify_floor_system(m, _BASE_CONFIG)
        sheet_rows = [r for r in rows if "Floor Sheet" in r["item_name"]
                      and r["unit"] == "sheets"]
        notes = sheet_rows[0].get("notes", "") if sheet_rows else ""
        assert "verandah" in notes.lower() or "K-package" in notes, (
            f"Floor sheet notes must mention verandah exclusion. Got: {notes[:200]}"
        )
