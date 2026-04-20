"""
test_relationship_reconciliation.py — Tests for canonical geometry relationship linking.

Covers:
  - Opening → WallFace links (classification-driven)
  - WallFace → Space links (perimeter/topology inference)
  - Space → WallFace + Opening links
  - Space enclosure_class classification
  - WallFace face_class classification
  - Opening exposure_class classification
  - Floor zone creation, linkage, and verandah exclusion
  - Unresolved object queries (GeometryIndex helpers)
  - Evidence lists on all canonical objects
  - Debug JSON includes floor_zones and relationship_summary
  - Conflict / ambiguity tests (no geometry, missing wall face, zero-width opening)
  - FinishZoneQuantifier canonical path
  - Backward compatibility when canonical geometry is absent

Fixtures mirror a realistic single-storey commercial building with:
  - 1 entrance door (DOOR_90, 920 mm)
  - 4 partition doors (DOOR_82 × 4)
  - 8 windows (WIN_1100 × 8, h=1.20)
  - 1 louvre window (WIN_LOUVRE_1800 × 1, h=0)
  - External wall + internal wall
  - 5 enclosed dry spaces + 1 wet + 1 verandah (all config-backed)
"""
from __future__ import annotations

import json

import pytest

from v3_boq_system.normalize.canonical_objects import (
    CanonicalGeometryModel,
    TruthClass,
)
from v3_boq_system.normalize.element_model import (
    OpeningElement,
    ProjectElementModel,
    SpaceElement,
    WallElement,
)
from v3_boq_system.normalize.geometry_index import GeometryIndex
from v3_boq_system.normalize.geometry_reconciler import (
    _derive_enclosure_class,
    _derive_exposure_class,
    _link_relationships,
    build_canonical_geometry,
    _build_canonical_openings,
    _build_canonical_wall_faces,
    _build_canonical_spaces,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════════════════

_CFG = {
    "lining": {"default_louvre_height_m": 0.75},
    "structural": {"wall_height_m": 2.4},
}


def _make_opening(
    mark, opening_type="door", width_m=0.92, height_m=2.04,
    quantity=1, is_external=True, source="dxf_geometry", element_id=""
) -> OpeningElement:
    return OpeningElement(
        element_id=element_id or mark.lower(),
        source=source,
        confidence="HIGH",
        opening_type=opening_type,
        mark=mark,
        width_m=width_m,
        height_m=height_m,
        quantity=quantity,
        is_external=is_external,
    )


def _make_wall(wall_type="external", length_m=38.4, height_m=2.4,
               faces=1, source="dxf_geometry") -> WallElement:
    return WallElement(
        element_id=f"wall_{wall_type}",
        source=source,
        confidence="HIGH",
        wall_type=wall_type,
        length_m=length_m,
        height_m=height_m,
        faces=faces,
    )


def _make_space(
    space_id, space_name, space_type="office", area_m2=10.0,
    perimeter_m=13.0, is_wet=False, is_verandah=False,
    is_enclosed=True, is_external=False,
    source_type="config", finish_floor_type="vinyl_plank",
) -> SpaceElement:
    return SpaceElement(
        element_id=space_id,
        source="config" if source_type == "config" else "dxf_geometry",
        confidence="LOW" if source_type == "config" else "HIGH",
        space_id=space_id,
        space_name=space_name,
        space_type=space_type,
        area_m2=area_m2,
        perimeter_m=perimeter_m,
        is_wet=is_wet,
        is_verandah=is_verandah,
        is_enclosed=is_enclosed,
        is_external=is_external,
        source_type=source_type,
        finish_floor_type=finish_floor_type,
    )


def _full_model() -> ProjectElementModel:
    """Full test fixture with openings, walls, and spaces."""
    return ProjectElementModel(
        openings=[
            _make_opening("DOOR_90",  "door",   width_m=0.92,  height_m=2.04, quantity=1),
            _make_opening("DOOR_82",  "door",   width_m=0.82,  height_m=2.04, quantity=4, is_external=False),
            _make_opening("WIN_1100", "window", width_m=1.08,  height_m=1.20, quantity=8),
            _make_opening("WIN_LOUVRE_1800", "window", width_m=1.847, height_m=0.0, quantity=1),
        ],
        walls=[
            _make_wall("external", length_m=38.4, height_m=2.4, faces=1),
            _make_wall("internal", length_m=29.4, height_m=2.4, faces=2),
        ],
        spaces=[
            _make_space("sp_01", "Dispensary",    area_m2=24.0, perimeter_m=20.0),
            _make_space("sp_02", "Waiting",       area_m2=14.0, perimeter_m=16.0),
            _make_space("sp_03", "Consultation",  area_m2=8.0,  perimeter_m=12.0),
            _make_space("sp_04", "Staff Room",    area_m2=6.5,  perimeter_m=11.0),
            _make_space("sp_05", "Toilet",        area_m2=4.5,  perimeter_m=9.5, is_wet=True,
                        finish_floor_type="ceramic_tile"),
            _make_space("sp_06", "Storage",       area_m2=7.8,  perimeter_m=12.0),
            _make_space("sp_07", "Verandah",      area_m2=21.6, perimeter_m=24.0,
                        is_verandah=True, is_enclosed=False, finish_floor_type="decking"),
        ],
    )


def _model_no_spaces() -> ProjectElementModel:
    m = _full_model()
    m.spaces = []
    return m


def _model_no_walls() -> ProjectElementModel:
    m = _full_model()
    m.walls = []
    return m


def _model_no_internal_wall() -> ProjectElementModel:
    m = _full_model()
    m.walls = [_make_wall("external", length_m=38.4, height_m=2.4, faces=1)]
    return m


# ═══════════════════════════════════════════════════════════════════════════════
# Classification helper tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeriveExposureClass:
    def test_entrance_door_is_external(self):
        assert _derive_exposure_class(True, False, "door", True) == "external"

    def test_partition_door_is_internal_partition(self):
        assert _derive_exposure_class(False, True, "door", False) == "internal_partition"

    def test_external_window_is_external(self):
        assert _derive_exposure_class(False, False, "window", True) == "external"

    def test_internal_window_is_unknown(self):
        assert _derive_exposure_class(False, False, "window", False) == "unknown"

    def test_neither_is_unknown(self):
        assert _derive_exposure_class(False, False, "fanlight", False) == "unknown"


class TestDeriveEnclosureClass:
    def test_verandah_space(self):
        from v3_boq_system.normalize.canonical_objects import CanonicalSpace
        s = CanonicalSpace(
            id="x", space_name="V", space_type="verandah", area_m2=10.0,
            perimeter_m=12.0, perimeter_source="estimated",
            is_verandah=True, is_enclosed=False,
        )
        assert _derive_enclosure_class(s) == "verandah"

    def test_enclosed_space(self):
        from v3_boq_system.normalize.canonical_objects import CanonicalSpace
        s = CanonicalSpace(
            id="x", space_name="Office", space_type="office", area_m2=10.0,
            perimeter_m=12.0, perimeter_source="config_specified",
            is_enclosed=True,
        )
        assert _derive_enclosure_class(s) == "enclosed"

    def test_external_open_space(self):
        from v3_boq_system.normalize.canonical_objects import CanonicalSpace
        s = CanonicalSpace(
            id="x", space_name="Yard", space_type="external", area_m2=10.0,
            perimeter_m=12.0, perimeter_source="estimated",
            is_external=True, is_enclosed=False,
        )
        assert _derive_enclosure_class(s) == "external"


# ═══════════════════════════════════════════════════════════════════════════════
# Opening → WallFace links
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpeningWallFaceLinks:
    def setup_method(self):
        self.geom = build_canonical_geometry(_full_model(), _CFG)

    def test_entrance_door_linked_to_external_wall_face(self):
        entrance = next(o for o in self.geom.openings if o.is_entrance)
        assert "wf_external" in entrance.linked_wall_face_ids

    def test_partition_door_linked_to_internal_wall_face(self):
        partition = next(o for o in self.geom.openings if o.is_partition)
        assert "wf_internal" in partition.linked_wall_face_ids

    def test_external_window_linked_to_external_wall_face(self):
        win = next(o for o in self.geom.openings
                   if o.opening_type == "window" and o.is_external)
        assert "wf_external" in win.linked_wall_face_ids

    def test_entrance_door_not_linked_to_internal(self):
        entrance = next(o for o in self.geom.openings if o.is_entrance)
        assert "wf_internal" not in entrance.linked_wall_face_ids

    def test_partition_door_not_linked_to_external(self):
        partition = next(o for o in self.geom.openings if o.is_partition)
        assert "wf_external" not in partition.linked_wall_face_ids

    def test_linked_evidence_recorded(self):
        entrance = next(o for o in self.geom.openings if o.is_entrance)
        # At least one evidence entry should mention the wall face link
        linked_ev = [e for e in entrance.evidence if "linked_to" in e]
        assert len(linked_ev) >= 1

    def test_opening_evidence_is_non_empty(self):
        for op in self.geom.openings:
            assert len(op.evidence) > 0, f"{op.mark} has empty evidence"


# ═══════════════════════════════════════════════════════════════════════════════
# WallFace → Space links
# ═══════════════════════════════════════════════════════════════════════════════

class TestWallFaceSpaceLinks:
    def setup_method(self):
        self.geom = build_canonical_geometry(_full_model(), _CFG)
        self.ext_wf = self.geom.external_wall_face()
        self.int_wf = self.geom.internal_wall_face()

    def test_external_wall_linked_to_enclosed_spaces(self):
        enclosed_ids = [s.id for s in self.geom.spaces if s.is_enclosed and not s.is_external]
        assert set(self.ext_wf.linked_space_ids) == set(enclosed_ids)

    def test_internal_wall_linked_to_enclosed_spaces(self):
        enclosed_ids = [s.id for s in self.geom.spaces if s.is_enclosed and not s.is_external]
        assert set(self.int_wf.linked_space_ids) == set(enclosed_ids)

    def test_verandah_not_in_internal_wall_linked_spaces(self):
        verandah_ids = {s.id for s in self.geom.spaces if s.is_verandah}
        assert not verandah_ids.intersection(set(self.int_wf.linked_space_ids))

    def test_wall_face_evidence_records_space_link(self):
        ev_with_linked = [e for e in self.ext_wf.evidence if "linked_spaces" in e]
        assert len(ev_with_linked) >= 1

    def test_wall_face_evidence_records_inference_limitation(self):
        # Must honestly record that this is inference, not precise topology
        ev_method = [e for e in self.ext_wf.evidence if "inference" in e or "unavailable" in e]
        assert len(ev_method) >= 1

    def test_face_class_external(self):
        assert self.ext_wf.face_class == "external"

    def test_face_class_internal(self):
        assert self.int_wf.face_class == "internal"


# ═══════════════════════════════════════════════════════════════════════════════
# Space → WallFace + Opening links + enclosure_class
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpaceLinks:
    def setup_method(self):
        self.geom = build_canonical_geometry(_full_model(), _CFG)

    def test_enclosed_space_linked_to_both_wall_faces(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed and not s.is_external)
        assert "wf_external" in enc.linked_wall_face_ids
        assert "wf_internal" in enc.linked_wall_face_ids

    def test_enclosed_space_linked_to_external_openings(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed and not s.is_external)
        assert len(enc.linked_opening_ids) > 0

    def test_verandah_linked_to_external_wall_only(self):
        ver = next(s for s in self.geom.spaces if s.is_verandah)
        assert "wf_external" in ver.linked_wall_face_ids
        assert "wf_internal" not in ver.linked_wall_face_ids

    def test_enclosed_enclosure_class(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed and not s.is_verandah)
        assert enc.enclosure_class == "enclosed"

    def test_verandah_enclosure_class(self):
        ver = next(s for s in self.geom.spaces if s.is_verandah)
        assert ver.enclosure_class == "verandah"

    def test_space_evidence_non_empty(self):
        for s in self.geom.spaces:
            assert len(s.evidence) > 0, f"{s.space_name} has empty evidence"

    def test_space_evidence_records_enclosure_class(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed)
        assert any("enclosure_class" in e for e in enc.evidence)

    def test_space_evidence_honest_about_topology_limitation(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed)
        # Must record that room-level topology is unavailable
        topo_ev = [e for e in enc.evidence
                   if "topology" in e or "unavailable" in e or "building-level" in e]
        assert len(topo_ev) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# GeometryIndex relationship helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeometryIndexRelationshipHelpers:
    def setup_method(self):
        self.geom  = build_canonical_geometry(_full_model(), _CFG)
        self.index = GeometryIndex(self.geom)

    def test_openings_for_wall_face_external(self):
        ops = self.index.openings_for_wall_face("wf_external")
        # External: entrance doors + external windows only (partition excluded)
        assert all(o.is_cladding_face for o in ops)
        assert not any(o.is_partition for o in ops)

    def test_openings_for_wall_face_internal(self):
        ops = self.index.openings_for_wall_face("wf_internal")
        assert all(o.is_partition for o in ops)

    def test_spaces_for_wall_face_returns_linked_spaces(self):
        spaces = self.index.spaces_for_wall_face("wf_external")
        # External wall face is linked to enclosed spaces + verandah (it adjoins both)
        assert len(spaces) > 0
        # All enclosed (non-verandah) spaces must be present
        enclosed_ids = {s.id for s in self.geom.spaces
                        if s.is_enclosed and not s.is_external}
        returned_ids = {s.id for s in spaces}
        assert enclosed_ids.issubset(returned_ids)

    def test_wall_faces_for_space_returns_two_faces(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed)
        faces = self.index.wall_faces_for_space(enc.id)
        assert len(faces) == 2
        types = {wf.wall_type for wf in faces}
        assert types == {"external", "internal"}

    def test_openings_for_space_returns_cladding_openings(self):
        enc = next(s for s in self.geom.spaces if s.is_enclosed)
        ops = self.index.openings_for_space(enc.id)
        assert len(ops) > 0
        assert all(o.is_cladding_face for o in ops)

    def test_floor_zones_for_space_dry(self):
        dry = next(s for s in self.geom.spaces
                   if s.is_enclosed and not s.is_wet)
        zones = self.index.floor_zones_for_space(dry.id)
        assert len(zones) >= 1
        assert any(fz.zone_type == "internal_dry" for fz in zones)

    def test_floor_zones_for_space_wet(self):
        wet = next(s for s in self.geom.spaces if s.is_wet)
        zones = self.index.floor_zones_for_space(wet.id)
        assert len(zones) == 1
        assert zones[0].zone_type == "internal_wet"

    def test_no_unresolved_openings_in_full_model(self):
        # All openings in full model have matching wall faces
        unres = self.index.unresolved_openings()
        assert len(unres) == 0

    def test_no_unresolved_spaces_in_full_model(self):
        unres = self.index.unresolved_spaces()
        assert len(unres) == 0

    def test_no_unresolved_wall_faces_in_full_model(self):
        unres = self.index.unresolved_wall_faces()
        assert len(unres) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Unresolved object queries (conflict / ambiguity tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnresolvedObjects:
    def test_unresolved_openings_when_no_wall_faces(self):
        """Openings with no matching wall face type → unresolved."""
        geom  = build_canonical_geometry(_model_no_walls(), _CFG)
        index = GeometryIndex(geom)
        # No walls → no wall faces → all openings unresolved
        assert len(index.unresolved_openings()) == len(geom.openings)

    def test_unresolved_spaces_when_no_walls(self):
        """Enclosed spaces with no wall faces → unresolved."""
        geom  = build_canonical_geometry(_model_no_walls(), _CFG)
        index = GeometryIndex(geom)
        # No wall faces → all enclosed spaces unresolved
        assert len(index.unresolved_spaces()) == len(
            [s for s in geom.spaces if s.is_enclosed and not s.is_external]
        )

    def test_unresolved_wall_faces_when_no_spaces(self):
        """Wall faces with no spaces → unresolved."""
        geom  = build_canonical_geometry(_model_no_spaces(), _CFG)
        index = GeometryIndex(geom)
        unres = index.unresolved_wall_faces()
        # Both external and internal wall faces have no linked spaces
        assert len(unres) == 2

    def test_partition_unresolved_when_no_internal_wall(self):
        """Partition doors unresolved when internal wall face is absent."""
        geom  = build_canonical_geometry(_model_no_internal_wall(), _CFG)
        index = GeometryIndex(geom)
        unres = index.unresolved_openings()
        partition_ids = {o.id for o in geom.openings if o.is_partition}
        unres_ids     = {o.id for o in unres}
        assert partition_ids == unres_ids

    def test_unresolved_evidence_recorded(self):
        """When a link fails, evidence records 'unresolved' with reason."""
        geom = build_canonical_geometry(_model_no_internal_wall(), _CFG)
        partitions = [o for o in geom.openings if o.is_partition]
        for p in partitions:
            assert any("unresolved" in e for e in p.evidence)

    def test_zero_width_opening_is_not_cladding_face(self):
        """An opening with width=0 should not deduct from cladding face (width check)."""
        model = ProjectElementModel(
            openings=[_make_opening("WIN_ZERO", "window", width_m=0.0, height_m=1.2)],
            walls=[_make_wall("external")],
        )
        geom = build_canonical_geometry(model, _CFG)
        zero_op = geom.openings[0]
        # is_cladding_face=False because width condition `op.width_m > 0` fails
        assert not zero_op.is_cladding_face
        # opening_area_m2=0 — contributes nothing to deductions
        assert zero_op.opening_area_m2 == 0.0
        # exposure_class is still "external" (the window is on an external wall);
        # cladding deduction is a separate concern from exposure orientation
        assert zero_op.exposure_class == "external"


# ═══════════════════════════════════════════════════════════════════════════════
# Floor zone linkage and verandah exclusion
# ═══════════════════════════════════════════════════════════════════════════════

class TestFloorZoneRelationships:
    def setup_method(self):
        self.geom  = build_canonical_geometry(_full_model(), _CFG)
        self.index = GeometryIndex(self.geom)

    def test_three_floor_zones_created(self):
        assert len(self.geom.floor_zones) == 3

    def test_wet_zone_contains_toilet(self):
        wet = self.index.floor_zones_by_type("internal_wet")
        assert len(wet) == 1
        toilet = next(s for s in self.geom.spaces if s.is_wet)
        assert toilet.id in wet[0].space_ids

    def test_dry_zone_excludes_verandah(self):
        dry = self.index.floor_zones_by_type("internal_dry")
        verandah_ids = {s.id for s in self.geom.spaces if s.is_verandah}
        for fz in dry:
            assert not verandah_ids.intersection(set(fz.space_ids))

    def test_verandah_zone_created(self):
        ver = self.index.floor_zones_by_type("verandah")
        assert len(ver) == 1

    def test_floor_zone_evidence_non_empty(self):
        for fz in self.geom.floor_zones:
            assert len(fz.evidence) > 0

    def test_floor_zone_evidence_records_verandah_exclusion(self):
        dry = self.index.floor_zones_by_type("internal_dry")
        for fz in dry:
            assert any("verandah_excluded" in e for e in fz.evidence)

    def test_floor_zone_evidence_records_contributing_spaces(self):
        for fz in self.geom.floor_zones:
            assert any("contributing_spaces" in e for e in fz.evidence)


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence-based truth assignment (not just source_type)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceBasedTruth:
    def test_louvre_evidence_records_fallback_downgrade(self):
        """WINDOW_LOUVRE_1800 (h=0) must record truth_class downgrade in evidence."""
        geom    = build_canonical_geometry(_full_model(), _CFG)
        louvre  = next(o for o in geom.openings if "LOUVRE_1800" in o.mark)
        # Evidence should mention truth_class downgrade from measured → calculated
        downgrade_ev = [e for e in louvre.evidence
                        if "downgraded" in e or "fallback" in e]
        assert len(downgrade_ev) >= 1

    def test_measured_window_evidence_no_downgrade(self):
        """WIN_1100 (h=1.2) — no fallback — evidence should not mention downgrade."""
        geom = build_canonical_geometry(_full_model(), _CFG)
        win  = next(o for o in geom.openings if "WIN_1100" in o.mark)
        assert win.truth_class == TruthClass.MEASURED
        # Evidence must confirm no downgrade
        assert not any("downgraded" in e for e in win.evidence)

    def test_config_space_evidence_records_config_fallback(self):
        """All config-backed spaces must have config_fallback in evidence."""
        geom = build_canonical_geometry(_full_model(), _CFG)
        for s in geom.spaces:
            if s.truth_class == TruthClass.CONFIG_FALLBACK:
                assert any("config_fallback" in e for e in s.evidence)

    def test_wall_face_evidence_records_source_entity(self):
        """Wall face with entity IDs must record them in evidence."""
        model = ProjectElementModel(
            walls=[WallElement(
                element_id="WALL_EXT_001",
                source="dxf_geometry",
                confidence="HIGH",
                wall_type="external",
                length_m=38.4,
                height_m=2.4,
                faces=1,
            )],
        )
        geom = build_canonical_geometry(model, _CFG)
        ext  = geom.external_wall_face()
        assert ext is not None
        # Entity ID should appear in evidence
        assert any("WALL_EXT_001" in e for e in ext.evidence)

    def test_relationship_summary_counts_by_class(self):
        """relationship_summary must provide non-empty class counts."""
        geom  = build_canonical_geometry(_full_model(), _CFG)
        index = GeometryIndex(geom)
        rs    = index.relationship_summary()
        assert rs["openings"]["by_exposure_class"]["external"] >= 1
        assert rs["openings"]["by_exposure_class"]["internal_partition"] >= 1
        assert rs["wall_faces"]["by_face_class"]["external"] == 1
        assert rs["wall_faces"]["by_face_class"]["internal"] == 1
        assert rs["spaces"]["by_enclosure_class"]["enclosed"] >= 1
        assert rs["spaces"]["by_enclosure_class"]["verandah"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Debug JSON — floor_zones and relationship_summary included
# ═══════════════════════════════════════════════════════════════════════════════

class TestDebugJSON:
    def setup_method(self):
        self.geom  = build_canonical_geometry(_full_model(), _CFG)
        self.index = GeometryIndex(self.geom)

    def test_full_debug_dict_has_floor_zones_key(self):
        d = self.index.full_debug_dict()
        assert "floor_zones" in d

    def test_full_debug_dict_has_relationship_summary_key(self):
        d = self.index.full_debug_dict()
        assert "relationship_summary" in d

    def test_full_debug_dict_has_unresolved_key(self):
        d = self.index.full_debug_dict()
        assert "unresolved" in d

    def test_floor_zones_in_debug_dict_correct_count(self):
        d = self.index.full_debug_dict()
        assert len(d["floor_zones"]) == len(self.geom.floor_zones)

    def test_floor_zone_debug_has_space_ids(self):
        d = self.index.full_debug_dict()
        for fz in d["floor_zones"]:
            assert "space_ids" in fz
            assert "zone_type" in fz
            assert "area_m2" in fz
            assert "evidence" in fz

    def test_opening_debug_has_exposure_class(self):
        d = self.index.full_debug_dict()
        for o in d["openings"]:
            assert "exposure_class" in o
            assert "evidence" in o
            assert "linked_wall_faces" in o

    def test_wall_face_debug_has_face_class(self):
        d = self.index.full_debug_dict()
        for wf in d["wall_faces"]:
            assert "face_class" in wf
            assert "evidence" in wf
            assert "linked_spaces" in wf

    def test_space_debug_has_enclosure_class(self):
        d = self.index.full_debug_dict()
        for s in d["spaces"]:
            assert "enclosure_class" in s
            assert "evidence" in s
            assert "linked_wall_faces" in s

    def test_full_debug_dict_is_json_serialisable(self):
        d = self.index.full_debug_dict()
        serialised = json.dumps(d)
        assert len(serialised) > 100

    def test_relationship_summary_linked_counts_correct(self):
        d = self.index.full_debug_dict()
        rs = d["relationship_summary"]
        # In full model all openings should be linked
        assert rs["openings"]["unlinked"] == 0
        assert rs["unresolved"]["openings"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# FinishZoneQuantifier — canonical path
# ═══════════════════════════════════════════════════════════════════════════════

class TestFinishZoneQuantifierCanonical:
    def setup_method(self):
        self.model  = _full_model()
        self.geom   = build_canonical_geometry(self.model, _CFG)

    def _run(self, canonical_geom=None):
        from v3_boq_system.quantify.finish_zone_quantifier import quantify_finish_zones
        return quantify_finish_zones(self.model, _CFG, canonical_geom=canonical_geom)

    def test_accepts_canonical_geom_param(self):
        rows = self._run(self.geom)
        assert isinstance(rows, list)

    def test_dry_zone_row_present(self):
        rows = self._run(self.geom)
        names = [r["item_name"] for r in rows]
        assert any("Dry Zone" in n or "vinyl plank" in n.lower() for n in names)

    def test_wet_zone_row_present(self):
        rows = self._run(self.geom)
        names = [r["item_name"] for r in rows]
        assert any("Wet Zone" in n or "ceramic tile" in n.lower() for n in names)

    def test_canonical_source_evidence_mentions_canonical(self):
        rows = self._run(self.geom)
        dry_row = next(r for r in rows if "Dry Zone" in r.get("item_name", ""))
        assert "canonical" in dry_row.get("source_evidence", "").lower()

    def test_dry_zone_quantity_matches_canonical_zone(self):
        dry_zone = next(
            fz for fz in self.geom.floor_zones if fz.zone_type == "internal_dry"
        )
        rows  = self._run(self.geom)
        dry_r = next(r for r in rows if "Floor Finish" in r.get("item_name", "")
                     and "Dry" in r.get("item_name", ""))
        assert abs(dry_r["quantity"] - dry_zone.area_m2) < 0.01

    def test_wet_zone_quantity_matches_canonical_zone(self):
        wet_zone = next(
            fz for fz in self.geom.floor_zones if fz.zone_type == "internal_wet"
        )
        rows  = self._run(self.geom)
        wet_r = next(r for r in rows if "Floor Finish" in r.get("item_name", "")
                     and "Wet" in r.get("item_name", ""))
        assert abs(wet_r["quantity"] - wet_zone.area_m2) < 0.01

    def test_verandah_zone_not_in_finish_rows(self):
        """Verandah (WPC decking) must not appear in Floor Finish rows."""
        rows  = self._run(self.geom)
        names = [r["item_name"] for r in rows]
        assert not any("verandah" in n.lower() for n in names)

    def test_fallback_path_works_without_canonical(self):
        """Existing space model path still works when canonical_geom is None."""
        rows = self._run(None)
        names = [r["item_name"] for r in rows]
        assert any("Dry" in n or "vinyl" in n.lower() for n in names)

    def test_same_quantities_canonical_vs_space_model(self):
        """Canonical and space model paths must produce identical quantities."""
        rows_canon  = self._run(self.geom)
        rows_spaces = self._run(None)

        def _qty(rows, keyword):
            r = next((r for r in rows if keyword in r.get("item_name", "")), None)
            return r["quantity"] if r else None

        assert _qty(rows_canon, "Floor Finish") == _qty(rows_spaces, "Floor Finish")


# ═══════════════════════════════════════════════════════════════════════════════
# Backward compatibility
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    def test_external_cladding_still_works_without_canonical(self):
        from v3_boq_system.quantify.external_cladding_quantifier import (
            quantify_external_cladding,
        )
        rows = quantify_external_cladding(_full_model(), _CFG)
        assert len(rows) > 0

    def test_lining_quantifier_still_works_without_canonical(self):
        from v3_boq_system.quantify.lining_quantifier import quantify_linings
        rows = quantify_linings(_full_model(), _CFG, {})
        assert len(rows) > 0

    def test_finish_zone_still_works_without_canonical(self):
        from v3_boq_system.quantify.finish_zone_quantifier import quantify_finish_zones
        rows = quantify_finish_zones(_full_model(), _CFG)
        assert len(rows) > 0

    def test_geometry_build_with_empty_model(self):
        """Empty model should return an empty canonical model without raising."""
        empty = ProjectElementModel()
        geom  = build_canonical_geometry(empty, _CFG)
        assert len(geom.openings) == 0
        assert len(geom.wall_faces) == 0
        assert len(geom.spaces) == 0
        assert len(geom.floor_zones) == 0

    def test_geometry_index_with_empty_model(self):
        """GeometryIndex on empty canonical model should not raise."""
        geom  = build_canonical_geometry(ProjectElementModel(), _CFG)
        index = GeometryIndex(geom)
        assert index.unresolved_openings() == []
        assert index.unresolved_wall_faces() == []
        assert index.unresolved_spaces() == []
