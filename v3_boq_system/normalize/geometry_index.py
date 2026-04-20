"""
geometry_index.py — Relationship index for the CanonicalGeometryModel.

Provides:
  - O(1) by-ID lookups for all canonical object types
  - Relationship helpers: openings_for_wall_face, spaces_for_wall_face,
    wall_faces_for_space, openings_for_space, floor_zones_for_space
  - Unresolved object queries: unresolved_openings, unresolved_wall_faces,
    unresolved_spaces
  - Serialisable summaries for JSON debug output including floor_zones,
    relationship summary, and unresolved object counts

This module is stateless — the index is built fresh from a CanonicalGeometryModel
and never mutated.  There is no caching or side-effects.
"""
from __future__ import annotations

from v3_boq_system.normalize.canonical_objects import (
    CanonicalCladdingFace,
    CanonicalFloorZone,
    CanonicalGeometryModel,
    CanonicalOpening,
    CanonicalSpace,
    CanonicalWallFace,
    TruthClass,
)


class GeometryIndex:
    """
    Relationship index for a CanonicalGeometryModel.

    Build once after geometry_reconciler.build_canonical_geometry() returns.
    Query many times.  All methods are read-only.
    """

    def __init__(self, geom: CanonicalGeometryModel) -> None:
        self._openings:    dict[str, CanonicalOpening]      = {o.id: o for o in geom.openings}
        self._wall_faces:  dict[str, CanonicalWallFace]     = {wf.id: wf for wf in geom.wall_faces}
        self._clad_faces:  dict[str, CanonicalCladdingFace] = {cf.id: cf for cf in geom.cladding_faces}
        self._spaces:      dict[str, CanonicalSpace]        = {s.id: s for s in geom.spaces}
        self._floor_zones: dict[str, CanonicalFloorZone]    = {fz.id: fz for fz in geom.floor_zones}
        self._geom = geom   # keep reference for summary helpers

    # ── By-ID lookups (O(1)) ──────────────────────────────────────────────────

    def opening(self, id_: str) -> CanonicalOpening | None:
        return self._openings.get(id_)

    def wall_face(self, id_: str) -> CanonicalWallFace | None:
        return self._wall_faces.get(id_)

    def cladding_face(self, id_: str) -> CanonicalCladdingFace | None:
        return self._clad_faces.get(id_)

    def space(self, id_: str) -> CanonicalSpace | None:
        return self._spaces.get(id_)

    def floor_zone(self, id_: str) -> CanonicalFloorZone | None:
        return self._floor_zones.get(id_)

    # ── Convenience accessors ─────────────────────────────────────────────────

    def primary_cladding_face(self) -> CanonicalCladdingFace | None:
        return next(iter(self._clad_faces.values()), None)

    def external_wall_face(self) -> CanonicalWallFace | None:
        return next((wf for wf in self._wall_faces.values()
                     if wf.wall_type == "external"), None)

    def internal_wall_face(self) -> CanonicalWallFace | None:
        return next((wf for wf in self._wall_faces.values()
                     if wf.wall_type == "internal"), None)

    def wet_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values() if s.is_wet]

    def verandah_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values() if s.is_verandah]

    def enclosed_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values()
                if s.is_enclosed and not s.is_external]

    def spaces_by_type(self, space_type: str) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values()
                if s.space_type == space_type]

    def floor_zones_by_type(self, zone_type: str) -> list[CanonicalFloorZone]:
        return [fz for fz in self._floor_zones.values()
                if fz.zone_type == zone_type]

    def spaces_by_truth_class(self, tc: str) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values() if s.truth_class == tc]

    # ── Relationship helpers ───────────────────────────────────────────────────

    def openings_for_wall_face(self, wall_face_id: str) -> list[CanonicalOpening]:
        """Return openings that are linked to the given wall face."""
        wf = self._wall_faces.get(wall_face_id)
        if wf is None:
            return []
        return [o for oid, o in self._openings.items()
                if oid in wf.opening_ids]

    def spaces_for_wall_face(self, wall_face_id: str) -> list[CanonicalSpace]:
        """Return spaces that are linked to the given wall face."""
        wf = self._wall_faces.get(wall_face_id)
        if wf is None:
            return []
        return [s for s in self._spaces.values()
                if wall_face_id in s.linked_wall_face_ids]

    def wall_faces_for_space(self, space_id: str) -> list[CanonicalWallFace]:
        """Return wall faces linked to the given space."""
        sp = self._spaces.get(space_id)
        if sp is None:
            return []
        return [wf for wf in self._wall_faces.values()
                if space_id in wf.linked_space_ids]

    def openings_for_space(self, space_id: str) -> list[CanonicalOpening]:
        """Return openings linked to the given space (via linked_opening_ids)."""
        sp = self._spaces.get(space_id)
        if sp is None:
            return []
        return [o for oid, o in self._openings.items()
                if oid in sp.linked_opening_ids]

    def floor_zones_for_space(self, space_id: str) -> list[CanonicalFloorZone]:
        """Return floor zones that contain the given space."""
        return [fz for fz in self._floor_zones.values()
                if space_id in fz.space_ids]

    # ── Unresolved object queries ──────────────────────────────────────────────

    def unresolved_openings(self) -> list[CanonicalOpening]:
        """
        Openings with no linked wall face.

        These are openings where the classification-driven link could not be
        established (e.g. no external wall face in model for an entrance door).
        Callers should treat these as SOURCE_LIMITED.
        """
        return [o for o in self._openings.values()
                if not o.linked_wall_face_ids]

    def unresolved_wall_faces(self) -> list[CanonicalWallFace]:
        """
        Wall faces with no linked spaces.

        For most single-storey buildings this indicates missing space model data
        (config-only spaces with no is_enclosed classification, or empty spaces
        list).
        """
        return [wf for wf in self._wall_faces.values()
                if not wf.linked_space_ids]

    def unresolved_spaces(self) -> list[CanonicalSpace]:
        """
        Enclosed spaces with no linked wall faces.

        Verandah and external spaces are excluded (they may intentionally have
        no internal wall face links).  A truly unresolved space is one that is
        enclosed but has no wall face relationship established.
        """
        return [s for s in self._spaces.values()
                if s.is_enclosed
                and not s.is_external
                and not s.linked_wall_face_ids]

    # ── Serialisable summaries for logging and JSON debug output ──────────────

    def opening_summary(self) -> list[dict]:
        """Compact opening classification table for JSON debug export."""
        return [
            {
                "id":                 o.id,
                "mark":               o.mark,
                "type":               o.opening_type,
                "width_m":            o.width_m,
                "height_m_raw":       o.height_m_raw,
                "height_used":        o.height_used,
                "height_fallback":    o.height_fallback_used,
                "quantity":           o.quantity,
                "area_m2":            o.opening_area_m2,
                "is_entrance":        o.is_entrance,
                "is_partition":       o.is_partition,
                "is_cladding_face":   o.is_cladding_face,
                "exposure_class":     o.exposure_class,
                "truth_class":        o.truth_class,
                "confidence":         o.confidence,
                "linked_wall_faces":  o.linked_wall_face_ids,
                "linked_spaces":      o.linked_space_ids,
                "evidence":           o.evidence,
                "notes":              o.notes,
            }
            for o in self._openings.values()
        ]

    def wall_face_summary(self) -> list[dict]:
        """Compact wall face net-area summary for JSON debug export."""
        return [
            {
                "id":               wf.id,
                "wall_type":        wf.wall_type,
                "face_class":       wf.face_class,
                "length_m":         wf.length_m,
                "height_m":         wf.height_m,
                "gross_area_m2":    wf.gross_area_m2,
                "opening_ded_m2":   wf.opening_deduction_m2,
                "net_area_m2":      wf.net_area_m2,
                "opening_count":    len(wf.opening_ids),
                "linked_spaces":    wf.linked_space_ids,
                "truth_class":      wf.truth_class,
                "confidence":       wf.confidence,
                "evidence":         wf.evidence,
            }
            for wf in self._wall_faces.values()
        ]

    def space_summary(self) -> list[dict]:
        """Compact space classification table for JSON debug export."""
        return [
            {
                "id":                s.id,
                "name":              s.space_name,
                "type":              s.space_type,
                "area_m2":           s.area_m2,
                "perimeter_m":       s.perimeter_m,
                "perimeter_source":  s.perimeter_source,
                "is_wet":            s.is_wet,
                "is_verandah":       s.is_verandah,
                "is_enclosed":       s.is_enclosed,
                "enclosure_class":   s.enclosure_class,
                "finish_floor":      s.finish_floor,
                "truth_class":       s.truth_class,
                "confidence":        s.confidence,
                "fallback_used":     s.fallback_used,
                "linked_wall_faces": s.linked_wall_face_ids,
                "linked_openings":   s.linked_opening_ids,
                "evidence":          s.evidence,
            }
            for s in self._spaces.values()
        ]

    def cladding_face_summary(self) -> list[dict]:
        """Cladding face deduction breakdown for JSON debug export."""
        return [
            {
                "id":                  cf.id,
                "gross_area_m2":       cf.gross_area_m2,
                "door_deduction_m2":   cf.door_deduction_m2,
                "window_deduction_m2": cf.window_deduction_m2,
                "total_deduction_m2":  cf.opening_deduction_m2,
                "net_area_m2":         cf.net_area_m2,
                "louvre_h_default":    cf.louvre_height_default_m,
                "truth_class":         cf.truth_class,
                "confidence":          cf.confidence,
                "notes":               cf.notes,
            }
            for cf in self._clad_faces.values()
        ]

    def floor_zone_summary(self) -> list[dict]:
        """Floor zone area and truth breakdown for JSON debug export."""
        return [
            {
                "id":           fz.id,
                "zone_name":    fz.zone_name,
                "zone_type":    fz.zone_type,
                "finish_type":  fz.finish_type,
                "area_m2":      fz.area_m2,
                "perimeter_m":  fz.perimeter_m,
                "space_ids":    fz.space_ids,
                "space_count":  len(fz.space_ids),
                "truth_class":  fz.truth_class,
                "confidence":   fz.confidence,
                "fallback_used": fz.fallback_used,
                "evidence":     fz.evidence,
                "notes":        fz.notes,
            }
            for fz in self._floor_zones.values()
        ]

    def relationship_summary(self) -> dict:
        """
        Counts-based summary of relationship resolution status.

        Useful for logging and the debug JSON to give a quick view of what
        is linked, what is unresolved, and what truth_class breakdown looks like.
        """
        openings    = list(self._openings.values())
        wall_faces  = list(self._wall_faces.values())
        spaces      = list(self._spaces.values())
        floor_zones = list(self._floor_zones.values())

        def _tc_counts(items):
            counts: dict[str, int] = {}
            for item in items:
                tc = getattr(item, "truth_class", "unknown")
                counts[tc] = counts.get(tc, 0) + 1
            return counts

        def _class_counts(items, attr):
            counts: dict[str, int] = {}
            for item in items:
                v = getattr(item, attr, "unknown")
                counts[v] = counts.get(v, 0) + 1
            return counts

        return {
            "openings": {
                "total":      len(openings),
                "linked":     sum(1 for o in openings if o.linked_wall_face_ids),
                "unlinked":   sum(1 for o in openings if not o.linked_wall_face_ids),
                "by_exposure_class": _class_counts(openings, "exposure_class"),
                "by_truth_class":    _tc_counts(openings),
            },
            "wall_faces": {
                "total":      len(wall_faces),
                "linked":     sum(1 for wf in wall_faces if wf.linked_space_ids),
                "unlinked":   sum(1 for wf in wall_faces if not wf.linked_space_ids),
                "by_face_class":  _class_counts(wall_faces, "face_class"),
                "by_truth_class": _tc_counts(wall_faces),
            },
            "spaces": {
                "total":      len(spaces),
                "linked":     sum(1 for s in spaces if s.linked_wall_face_ids),
                "unlinked":   sum(1 for s in spaces
                                   if s.is_enclosed and not s.is_external
                                   and not s.linked_wall_face_ids),
                "by_enclosure_class": _class_counts(spaces, "enclosure_class"),
                "by_truth_class":     _tc_counts(spaces),
            },
            "floor_zones": {
                "total":      len(floor_zones),
                "by_zone_type":   _class_counts(floor_zones, "zone_type"),
                "by_truth_class": _tc_counts(floor_zones),
                "total_area_m2":  round(sum(fz.area_m2 for fz in floor_zones), 2),
            },
            "unresolved": {
                "openings":   len(self.unresolved_openings()),
                "wall_faces": len(self.unresolved_wall_faces()),
                "spaces":     len(self.unresolved_spaces()),
            },
        }

    def full_debug_dict(self) -> dict:
        """Full debug export for writing to JSON output file."""
        return {
            "summary":              self._geom.summary_dict(),
            "relationship_summary": self.relationship_summary(),
            "openings":             self.opening_summary(),
            "wall_faces":           self.wall_face_summary(),
            "cladding_faces":       self.cladding_face_summary(),
            "spaces":               self.space_summary(),
            "floor_zones":          self.floor_zone_summary(),
            "unresolved": {
                "openings":   [o.id for o in self.unresolved_openings()],
                "wall_faces": [wf.id for wf in self.unresolved_wall_faces()],
                "spaces":     [s.id for s in self.unresolved_spaces()],
            },
        }
