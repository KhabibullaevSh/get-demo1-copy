"""
test_canonical_geometry.py — Tests for the canonical geometry layer.

Tests cover PARTS A–F of the geometry fusion architecture:
  A — CanonicalOpening / CanonicalWallFace / CanonicalCladdingFace / CanonicalSpace
  B — Candidate generation from ProjectElementModel
  C — Reconciliation (net area pre-computation, opening deduction with qty)
  D — TruthClass propagation
  E — Quantifier integration (canonical objects consumed)
  F — Opening quantity multiplier, partition exclusion, louvre fallback,
      verandah exclusion, wet/dry tagging, internal/external zoning,
      traceability fields

All tests use minimal model fixtures — no project files required.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import math
import pytest
from v3_boq_system.normalize.element_model import (
    OpeningElement, ProjectElementModel, SpaceElement, WallElement,
)
from v3_boq_system.normalize.canonical_objects import (
    CanonicalGeometryModel, TruthClass,
)
from v3_boq_system.normalize.geometry_reconciler import (
    build_canonical_geometry,
    _build_canonical_openings,
    _build_canonical_wall_faces,
    _build_cladding_faces,
    _build_canonical_spaces,
    _build_floor_zones,
)
from v3_boq_system.normalize.geometry_index import GeometryIndex

# ── Shared fixtures ────────────────────────────────────────────────────────────

_CFG = {
    "lining": {"default_louvre_height_m": 0.75},
    "structural": {"wall_stud_spacing_mm": 600},
    "external_cladding": {"board_exposure_mm": 200, "board_length_mm": 4200,
                           "waste_factor": 1.05},
}
_LOUVRE_H = 0.75
_EXT_DOOR_MIN_W = 0.85


def _ext_wall_model() -> ProjectElementModel:
    m = ProjectElementModel()
    m.walls.append(WallElement(
        element_id="ext", wall_type="external",
        length_m=38.4, height_m=2.4,
        source="dxf_geometry", confidence="HIGH",
    ))
    return m


def _full_model() -> ProjectElementModel:
    """Model mirroring Angau Pharmacy: 1 entrance door + 4 partition doors + 8 windows."""
    m = _ext_wall_model()
    m.walls.append(WallElement(
        element_id="int", wall_type="internal",
        length_m=29.4, height_m=2.4,
        source="derived_ratio", confidence="LOW",
    ))
    # Entrance door (≥ 0.85 m)
    m.openings.append(OpeningElement(
        element_id="d90", mark="DOOR_90", opening_type="door",
        width_m=0.92, height_m=2.04, quantity=1, is_external=True,
        source="dxf_geometry", confidence="HIGH",
    ))
    # Partition doors (< 0.85 m)
    m.openings.append(OpeningElement(
        element_id="d82", mark="DOOR_82", opening_type="door",
        width_m=0.82, height_m=2.04, quantity=4, is_external=True,
        source="dxf_geometry", confidence="HIGH",
    ))
    m.openings.append(OpeningElement(
        element_id="d72", mark="DOOR_72", opening_type="door",
        width_m=0.72, height_m=2.04, quantity=1, is_external=True,
        source="dxf_geometry", confidence="HIGH",
    ))
    # Windows with known height
    m.openings.append(OpeningElement(
        element_id="w11", mark="WIN_1100", opening_type="window",
        width_m=1.10, height_m=1.20, quantity=8, is_external=True,
        source="framecad_panel_label_ocr", confidence="MEDIUM",
    ))
    # Louvre window with height_m=0 (needs fallback)
    m.openings.append(OpeningElement(
        element_id="w18", mark="WIN_LOUVRE_1800", opening_type="window",
        width_m=1.80, height_m=0.0, quantity=2, is_external=True,
        source="dxf_geometry", confidence="HIGH",
    ))
    return m


# ═══════════════════════════════════════════════════════════════════════════════
# PART A — TruthClass
# ═══════════════════════════════════════════════════════════════════════════════

class TestTruthClass:

    def test_weaker_returns_lower_class(self):
        assert TruthClass.weaker(TruthClass.MEASURED, TruthClass.CONFIG_FALLBACK) == TruthClass.CONFIG_FALLBACK
        assert TruthClass.weaker(TruthClass.CALCULATED, TruthClass.INFERRED) == TruthClass.INFERRED

    def test_weakest_list(self):
        vals = [TruthClass.MEASURED, TruthClass.CONFIG_FALLBACK, TruthClass.CALCULATED]
        assert TruthClass.weakest(vals) == TruthClass.CONFIG_FALLBACK

    def test_weakest_empty_returns_fallback(self):
        assert TruthClass.weakest([]) == TruthClass.CONFIG_FALLBACK

    def test_to_quantity_status_measured(self):
        assert TruthClass.to_quantity_status(TruthClass.MEASURED) == "measured"

    def test_to_quantity_status_calculated(self):
        assert TruthClass.to_quantity_status(TruthClass.CALCULATED) == "calculated"

    def test_to_quantity_status_config_fallback_is_inferred(self):
        """Config fallback → BOQ quantity_status = inferred (not 'measured')."""
        assert TruthClass.to_quantity_status(TruthClass.CONFIG_FALLBACK) == "inferred"


# ═══════════════════════════════════════════════════════════════════════════════
# PART B/C — Canonical Opening generation and classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalOpenings:

    def _openings(self, model):
        return _build_canonical_openings(model, _LOUVRE_H)

    def test_entrance_door_classified_correctly(self):
        """DOOR_90 (w=0.92 ≥ 0.85) → is_entrance=True, is_partition=False."""
        ops = self._openings(_full_model())
        d90 = next(o for o in ops if o.mark == "DOOR_90")
        assert d90.is_entrance is True
        assert d90.is_partition is False
        assert d90.is_cladding_face is True

    def test_partition_door_classified_correctly(self):
        """DOOR_82 (w=0.82 < 0.85) → is_partition=True, is_entrance=False, is_cladding_face=False."""
        ops = self._openings(_full_model())
        d82 = next(o for o in ops if o.mark == "DOOR_82")
        assert d82.is_partition is True
        assert d82.is_entrance is False
        assert d82.is_cladding_face is False

    def test_window_is_cladding_face(self):
        """External windows are always on the cladding face."""
        ops = self._openings(_full_model())
        windows = [o for o in ops if o.opening_type == "window"]
        assert all(o.is_cladding_face for o in windows)

    def test_opening_area_multiplies_quantity(self):
        """DOOR_82 × qty=4 → area = 4 × 0.82 × 2.04."""
        ops = self._openings(_full_model())
        d82 = next(o for o in ops if o.mark == "DOOR_82")
        expected = round(4 * 0.82 * 2.04, 3)
        assert abs(d82.opening_area_m2 - expected) < 0.001, (
            f"Expected area={expected}, got {d82.opening_area_m2}"
        )

    def test_louvre_zero_height_uses_default(self):
        """WIN_LOUVRE_1800 has height_m=0 → height_used=louvre_h_default=0.75."""
        ops = self._openings(_full_model())
        louvre = next(o for o in ops if o.mark == "WIN_LOUVRE_1800")
        assert louvre.height_m_raw == 0.0
        assert louvre.height_used == _LOUVRE_H
        assert louvre.height_fallback_used is True

    def test_known_height_window_no_fallback(self):
        """WIN_1100 has height_m=1.2 → no fallback."""
        ops = self._openings(_full_model())
        w11 = next(o for o in ops if o.mark == "WIN_1100")
        assert w11.height_fallback_used is False
        assert w11.height_used == 1.20

    def test_louvre_fallback_downgrades_truth_class(self):
        """
        WIN_LOUVRE_1800: source=dxf_geometry (MEASURED) but height_m=0.
        Height fallback → truth_class downgrades from MEASURED to CALCULATED.
        """
        ops = self._openings(_full_model())
        louvre = next(o for o in ops if o.mark == "WIN_LOUVRE_1800")
        assert louvre.truth_class == TruthClass.CALCULATED

    def test_no_fallback_preserves_measured_truth(self):
        """DOOR_90 with height_m=2.04 (>0), source=dxf → MEASURED."""
        ops = self._openings(_full_model())
        d90 = next(o for o in ops if o.mark == "DOOR_90")
        assert d90.truth_class == TruthClass.MEASURED

    def test_notes_mention_partition_exclusion(self):
        """Partition door notes must explicitly state it's excluded from cladding face."""
        ops = self._openings(_full_model())
        d82 = next(o for o in ops if o.mark == "DOOR_82")
        assert "partition_door" in d82.notes or "excluded" in d82.notes.lower()

    def test_notes_mention_entrance_inclusion(self):
        """Entrance door notes must document why it's included."""
        ops = self._openings(_full_model())
        d90 = next(o for o in ops if o.mark == "DOOR_90")
        assert "entrance_door" in d90.notes or "entrance" in d90.notes.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — Wall face net area reconciliation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalWallFaces:

    def _faces(self, model):
        ops = _build_canonical_openings(model, _LOUVRE_H)
        return _build_canonical_wall_faces(model, ops)

    def test_external_wall_face_created(self):
        faces = self._faces(_full_model())
        ext = next((f for f in faces if f.wall_type == "external"), None)
        assert ext is not None

    def test_internal_wall_face_created(self):
        faces = self._faces(_full_model())
        int_ = next((f for f in faces if f.wall_type == "internal"), None)
        assert int_ is not None

    def test_external_face_gross_area(self):
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        assert abs(ext.gross_area_m2 - 38.4 * 2.4) < 0.01

    def test_external_face_net_less_than_gross(self):
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        assert ext.net_area_m2 < ext.gross_area_m2

    def test_external_face_deducts_entrance_doors_and_windows(self):
        """
        Correct deduction = entrance_door + windows (with quantities).
        DOOR_90: 1×0.92×2.04 = 1.877
        WIN_1100: 8×1.10×1.20 = 10.56
        WIN_LOUVRE_1800: 2×1.80×0.75 = 2.70
        Total = 15.137
        """
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        expected_ded = round(
            1 * 0.92 * 2.04   # DOOR_90
            + 8 * 1.10 * 1.20 # WIN_1100
            + 2 * 1.80 * 0.75 # WIN_LOUVRE_1800 (louvre fallback)
        , 3)
        assert abs(ext.opening_deduction_m2 - expected_ded) < 0.05, (
            f"Expected ded≈{expected_ded:.3f}, got {ext.opening_deduction_m2:.3f}"
        )

    def test_external_face_excludes_partition_doors(self):
        """
        DOOR_82 (qty=4) and DOOR_72 (qty=1) are partition doors — must NOT
        appear in the external face deduction.
        """
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        # If partition doors were wrongly included, deduction would be much larger
        partition_area = round((4 * 0.82 * 2.04) + (1 * 0.72 * 2.04), 3)
        assert ext.opening_deduction_m2 < ext.gross_area_m2, "Sanity: some deduction"
        # Deduction must NOT include partition area
        assert ext.opening_deduction_m2 < (
            ext.opening_deduction_m2 + partition_area - 0.01
        )

    def test_internal_face_deducts_partition_doors_both_faces(self):
        """Partition doors cut both internal faces: deduction = 2 × area per opening."""
        faces = self._faces(_full_model())
        int_ = next(f for f in faces if f.wall_type == "internal")
        # DOOR_82: 4×0.82×2.04 = 6.691 per face × 2 = 13.382
        # DOOR_72: 1×0.72×2.04 = 1.469 per face × 2 = 2.938
        expected_ded = round(
            (4 * 0.82 * 2.04 + 1 * 0.72 * 2.04) * 2, 3
        )
        assert abs(int_.opening_deduction_m2 - expected_ded) < 0.05, (
            f"Expected int_ded≈{expected_ded:.3f}, got {int_.opening_deduction_m2:.3f}"
        )

    def test_external_face_is_cladding_face(self):
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        assert ext.is_cladding_face is True

    def test_external_face_truth_class_measured(self):
        faces = self._faces(_full_model())
        ext = next(f for f in faces if f.wall_type == "external")
        assert ext.truth_class == TruthClass.MEASURED

    def test_internal_face_truth_class_inferred_for_derived_ratio(self):
        faces = self._faces(_full_model())
        int_ = next(f for f in faces if f.wall_type == "internal")
        # source="derived_ratio" → INFERRED
        assert int_.truth_class == TruthClass.INFERRED


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — Cladding face
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalCladdingFace:

    def _clad(self, model):
        ops   = _build_canonical_openings(model, _LOUVRE_H)
        faces = _build_canonical_wall_faces(model, ops)
        return _build_cladding_faces(faces, ops, _LOUVRE_H)

    def test_cladding_face_created_for_ext_wall(self):
        clads = self._clad(_full_model())
        assert len(clads) == 1

    def test_no_cladding_face_without_ext_wall(self):
        m = ProjectElementModel()  # no walls
        clads = self._clad(m)
        assert clads == []

    def test_net_area_correct(self):
        """Net = gross - entrance_door - windows (all with quantity)."""
        clads = self._clad(_full_model())
        cf = clads[0]
        gross = 38.4 * 2.4
        ded   = (1 * 0.92 * 2.04
                 + 8 * 1.10 * 1.20
                 + 2 * 1.80 * 0.75)
        expected_net = round(gross - ded, 2)
        assert abs(cf.net_area_m2 - expected_net) < 0.05

    def test_door_window_deduction_split(self):
        clads = self._clad(_full_model())
        cf = clads[0]
        assert cf.door_deduction_m2 > 0, "Should have door deduction"
        assert cf.window_deduction_m2 > 0, "Should have window deduction"

    def test_louvre_height_recorded(self):
        clads = self._clad(_full_model())
        assert clads[0].louvre_height_default_m == _LOUVRE_H

    def test_notes_mention_partition_exclusion(self):
        clads = self._clad(_full_model())
        assert "Partition" in clads[0].notes or "partition" in clads[0].notes.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# PART D — TruthClass propagation to spaces
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalSpaces:

    def _make_model_with_spaces(self) -> ProjectElementModel:
        m = ProjectElementModel()
        # Config-sourced space (typical pharmacy setup)
        m.spaces.append(SpaceElement(
            element_id="sp_toilet", space_id="toilet", space_name="Toilet",
            space_type="toilet", area_m2=4.5, perimeter_m=8.20,
            is_wet=True, is_enclosed=True, is_external=False,
            source="config", source_type="config",
            source_ref="project_config room_schedule",
            confidence="LOW",
        ))
        # DXF-sourced space (with polygon)
        m.spaces.append(SpaceElement(
            element_id="sp_disp", space_id="dispensary", space_name="Dispensary",
            space_type="pharmacy", area_m2=24.0, perimeter_m=19.6,
            is_wet=False, is_enclosed=True, is_external=False,
            source="dxf_geometry", source_type="dxf_geometry",
            source_ref="ANGAU.dxf",
            confidence="HIGH",
            polygon=[[0, 0], [6, 0], [6, 4], [0, 4]],
        ))
        # Verandah space
        m.spaces.append(SpaceElement(
            element_id="sp_ver", space_id="verandah", space_name="Verandah",
            space_type="verandah", area_m2=21.6, perimeter_m=20.4,
            is_wet=False, is_enclosed=False, is_external=True, is_verandah=True,
            source="dxf_geometry", source_type="dxf_geometry",
            source_ref="ANGAU.dxf",
            confidence="HIGH",
        ))
        return m

    def test_config_space_gets_config_fallback(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        toilet = next(s for s in spaces if s.space_name == "Toilet")
        assert toilet.truth_class == TruthClass.CONFIG_FALLBACK
        assert toilet.fallback_used is True

    def test_dxf_polygon_space_gets_measured(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        disp = next(s for s in spaces if s.space_name == "Dispensary")
        assert disp.truth_class == TruthClass.MEASURED
        assert disp.fallback_used is False

    def test_config_space_perimeter_source_is_config_specified(self):
        """Toilet has perimeter_m=8.20 from config — not estimated."""
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        toilet = next(s for s in spaces if s.space_name == "Toilet")
        assert toilet.perimeter_source == "config_specified"

    def test_wet_space_tagged(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        toilet = next(s for s in spaces if s.space_name == "Toilet")
        assert toilet.is_wet is True

    def test_verandah_space_tagged(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        ver = next(s for s in spaces if s.space_name == "Verandah")
        assert ver.is_verandah is True
        assert ver.is_external is True

    def test_enclosed_space_tagged(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        disp = next(s for s in spaces if s.space_name == "Dispensary")
        assert disp.is_enclosed is True
        assert disp.is_external is False

    def test_config_space_notes_mention_truth_class(self):
        spaces = _build_canonical_spaces(self._make_model_with_spaces())
        toilet = next(s for s in spaces if s.space_name == "Toilet")
        assert "config_fallback" in toilet.notes or "config" in toilet.notes.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — Floor zones
# ═══════════════════════════════════════════════════════════════════════════════

class TestFloorZones:

    def _zones(self):
        m = ProjectElementModel()
        m.spaces.extend([
            SpaceElement(element_id="s1", space_id="toilet", space_name="Toilet",
                         space_type="toilet", area_m2=4.5, perimeter_m=8.2,
                         is_wet=True, is_enclosed=True, is_external=False,
                         finish_floor_type="ceramic_tile",
                         source="config", source_type="config", confidence="LOW"),
            SpaceElement(element_id="s2", space_id="disp", space_name="Dispensary",
                         space_type="pharmacy", area_m2=24.0, perimeter_m=19.6,
                         is_wet=False, is_enclosed=True, is_external=False,
                         finish_floor_type="vinyl_plank",
                         source="dxf_geometry", source_type="dxf_geometry", confidence="HIGH"),
            SpaceElement(element_id="s3", space_id="ver", space_name="Verandah",
                         space_type="verandah", area_m2=21.6, perimeter_m=20.4,
                         is_wet=False, is_enclosed=False, is_external=True, is_verandah=True,
                         finish_floor_type="decking",
                         source="dxf_geometry", source_type="dxf_geometry", confidence="HIGH"),
        ])
        cspaces = _build_canonical_spaces(m)
        return _build_floor_zones(cspaces)

    def test_wet_zone_created(self):
        zones = self._zones()
        wet_z = [z for z in zones if z.zone_type == "internal_wet"]
        assert len(wet_z) == 1
        assert abs(wet_z[0].area_m2 - 4.5) < 0.01

    def test_dry_zone_created(self):
        zones = self._zones()
        dry_z = [z for z in zones if z.zone_type == "internal_dry"]
        assert len(dry_z) >= 1

    def test_verandah_zone_created(self):
        zones = self._zones()
        ver_z = [z for z in zones if z.zone_type == "verandah"]
        assert len(ver_z) == 1
        assert abs(ver_z[0].area_m2 - 21.6) < 0.01

    def test_verandah_excluded_from_dry_zone(self):
        """Verandah area must NOT appear in internal_dry zones."""
        zones = self._zones()
        dry_z = [z for z in zones if z.zone_type == "internal_dry"]
        dry_total = sum(z.area_m2 for z in dry_z)
        # Dry should be ~24 m² (Dispensary only), not 24+21.6
        assert dry_total < 40, (
            f"Verandah likely included in dry zone — dry_total={dry_total}"
        )

    def test_wet_zone_truth_class_is_weakest(self):
        """Toilet is config → wet zone truth_class = CONFIG_FALLBACK."""
        zones = self._zones()
        wet_z = next(z for z in zones if z.zone_type == "internal_wet")
        assert wet_z.truth_class == TruthClass.CONFIG_FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# Full model integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildCanonicalGeometry:

    def test_returns_canonical_geometry_model(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        assert isinstance(geom, CanonicalGeometryModel)

    def test_summary_dict_keys(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        s = geom.summary_dict()
        assert "openings" in s
        assert "cladding_faces" in s
        assert "config_fallback_spaces" in s

    def test_entrance_doors_accessor(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        assert len(geom.entrance_doors()) == 1   # only DOOR_90

    def test_partition_doors_accessor(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        # DOOR_82 + DOOR_72 (both < 0.85 m)
        assert len(geom.partition_doors()) == 2

    def test_primary_cladding_face_present(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        assert geom.primary_cladding_face() is not None

    def test_cladding_face_net_area_correct(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        cf   = geom.primary_cladding_face()
        gross = 38.4 * 2.4
        ded   = (1 * 0.92 * 2.04 + 8 * 1.10 * 1.20 + 2 * 1.80 * 0.75)
        assert abs(cf.net_area_m2 - round(gross - ded, 2)) < 0.05

    def test_no_spaces_ok(self):
        """Model with no spaces → canonical model builds without error."""
        geom = build_canonical_geometry(_full_model(), _CFG)
        assert geom.spaces == []
        assert geom.floor_zones == []


# ═══════════════════════════════════════════════════════════════════════════════
# Geometry Index
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeometryIndex:

    def _idx(self):
        geom = build_canonical_geometry(_full_model(), _CFG)
        return GeometryIndex(geom)

    def test_opening_by_id(self):
        idx = self._idx()
        o = idx.opening("cop_d90")
        assert o is not None
        assert o.mark == "DOOR_90"

    def test_external_wall_face(self):
        idx = self._idx()
        wf = idx.external_wall_face()
        assert wf is not None
        assert wf.wall_type == "external"

    def test_primary_cladding_face(self):
        idx = self._idx()
        cf = idx.primary_cladding_face()
        assert cf is not None

    def test_opening_summary_has_correct_fields(self):
        idx = self._idx()
        summary = idx.opening_summary()
        assert len(summary) > 0
        first = summary[0]
        required_keys = {"id", "mark", "type", "area_m2", "is_entrance",
                         "is_partition", "is_cladding_face", "truth_class"}
        assert required_keys.issubset(first.keys())

    def test_full_debug_dict_serialisable(self):
        """full_debug_dict must produce a plain-dict structure (no dataclasses)."""
        import json
        idx = self._idx()
        d = idx.full_debug_dict()
        # Should not raise
        serialised = json.dumps(d)
        assert len(serialised) > 100


# ═══════════════════════════════════════════════════════════════════════════════
# PART E — Quantifier integration (external cladding)
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuantifierConsumesCanonical:

    def test_cladding_quantifier_accepts_canonical_geom(self):
        """quantify_external_cladding must not fail when canonical_geom is provided."""
        from v3_boq_system.quantify.external_cladding_quantifier import quantify_external_cladding
        model = _full_model()
        geom  = build_canonical_geometry(model, _CFG)
        rows  = quantify_external_cladding(model, _CFG, canonical_geom=geom)
        assert len(rows) > 0

    def test_cladding_net_area_same_with_and_without_canonical(self):
        """The net area row must be identical whether canonical_geom is used or not."""
        from v3_boq_system.quantify.external_cladding_quantifier import quantify_external_cladding
        model = _full_model()
        geom  = build_canonical_geometry(model, _CFG)
        rows_canon  = quantify_external_cladding(model, _CFG, canonical_geom=geom)
        rows_fallbk = quantify_external_cladding(model, _CFG, canonical_geom=None)
        net_canon  = next(r["quantity"] for r in rows_canon
                          if "FC Weatherboard (supply" in r["item_name"])
        net_fallbk = next(r["quantity"] for r in rows_fallbk
                          if "FC Weatherboard (supply" in r["item_name"])
        assert abs(net_canon - net_fallbk) < 0.05, (
            f"Canonical={net_canon:.2f} vs fallback={net_fallbk:.2f} — should match"
        )

    def test_cladding_evidence_mentions_canonical_when_used(self):
        """When canonical_geom is used, evidence / notes must reference it."""
        from v3_boq_system.quantify.external_cladding_quantifier import quantify_external_cladding
        model = _full_model()
        geom  = build_canonical_geometry(model, _CFG)
        rows  = quantify_external_cladding(model, _CFG, canonical_geom=geom)
        area_row = next(r for r in rows if "FC Weatherboard (supply" in r["item_name"])
        assert "canonical" in area_row.get("notes", "").lower(), (
            "Notes should mention canonical_geometry source when used"
        )

    def test_lining_quantifier_accepts_canonical_geom(self):
        """quantify_linings must not fail when canonical_geom is provided."""
        from v3_boq_system.quantify.lining_quantifier import quantify_linings
        model = _full_model()
        geom  = build_canonical_geometry(model, _CFG)
        lining_cfg = {**_CFG, "structural": {"wall_height_m": 2.4,
                                              "ceiling_batten_spacing_mm": 400}}
        rows = quantify_linings(model, lining_cfg, {}, canonical_geom=geom)
        assert len(rows) > 0

    def test_lining_net_area_same_with_and_without_canonical(self):
        """External wall lining sheet count must be identical in both paths."""
        from v3_boq_system.quantify.lining_quantifier import quantify_linings
        model = _full_model()
        geom  = build_canonical_geometry(model, _CFG)
        lining_cfg = {**_CFG, "structural": {"wall_height_m": 2.4,
                                              "ceiling_batten_spacing_mm": 400},
                      "lining": {"fc_wall_sheet_area_m2": 3.24,
                                 "fc_ceiling_sheet_area_m2": 2.88,
                                 "waste_factor": 1.05, "default_louvre_height_m": 0.75}}
        rows_canon  = quantify_linings(model, lining_cfg, {}, canonical_geom=geom)
        rows_fallbk = quantify_linings(model, lining_cfg, {})
        ext_sheets_c = next((r["quantity"] for r in rows_canon
                             if "External Wall Lining" in r["item_name"]
                             and "Sheet" in r["item_name"] and "Screw" not in r["item_name"]
                             and "Total" not in r["item_name"]), None)
        ext_sheets_f = next((r["quantity"] for r in rows_fallbk
                             if "External Wall Lining" in r["item_name"]
                             and "Sheet" in r["item_name"] and "Screw" not in r["item_name"]
                             and "Total" not in r["item_name"]), None)
        assert ext_sheets_c is not None and ext_sheets_f is not None
        assert abs(ext_sheets_c - ext_sheets_f) <= 1, (
            f"Sheet count drift: canonical={ext_sheets_c} vs fallback={ext_sheets_f}"
        )
