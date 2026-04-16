"""
geometry_index.py — Fast lookup index for the CanonicalGeometryModel.

Provides O(1) by-ID lookups, filtered accessors, and serialisable summaries
for logging and JSON debug output.

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
    Dictionary-backed lookup index for a CanonicalGeometryModel.

    Build once, query many times.
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

    def openings_for_wall_face(self, wall_face_id: str) -> list[CanonicalOpening]:
        wf = self._wall_faces.get(wall_face_id)
        if wf is None:
            return []
        return [o for oid, o in self._openings.items()
                if oid in wf.opening_ids]

    def spaces_by_type(self, space_type: str) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values()
                if s.space_type == space_type]

    def floor_zones_by_type(self, zone_type: str) -> list[CanonicalFloorZone]:
        return [fz for fz in self._floor_zones.values()
                if fz.zone_type == zone_type]

    def spaces_by_truth_class(self, tc: str) -> list[CanonicalSpace]:
        return [s for s in self._spaces.values() if s.truth_class == tc]

    # ── Serialisable summaries for logging and JSON debug output ──────────────

    def opening_summary(self) -> list[dict]:
        """Compact opening classification table for JSON debug export."""
        return [
            {
                "id":                o.id,
                "mark":              o.mark,
                "type":              o.opening_type,
                "width_m":           o.width_m,
                "height_m_raw":      o.height_m_raw,
                "height_used":       o.height_used,
                "height_fallback":   o.height_fallback_used,
                "quantity":          o.quantity,
                "area_m2":           o.opening_area_m2,
                "is_entrance":       o.is_entrance,
                "is_partition":      o.is_partition,
                "is_cladding_face":  o.is_cladding_face,
                "truth_class":       o.truth_class,
                "confidence":        o.confidence,
                "notes":             o.notes,
            }
            for o in self._openings.values()
        ]

    def wall_face_summary(self) -> list[dict]:
        """Compact wall face net-area summary for JSON debug export."""
        return [
            {
                "id":              wf.id,
                "wall_type":       wf.wall_type,
                "length_m":        wf.length_m,
                "height_m":        wf.height_m,
                "gross_area_m2":   wf.gross_area_m2,
                "opening_ded_m2":  wf.opening_deduction_m2,
                "net_area_m2":     wf.net_area_m2,
                "opening_count":   len(wf.opening_ids),
                "truth_class":     wf.truth_class,
                "confidence":      wf.confidence,
            }
            for wf in self._wall_faces.values()
        ]

    def space_summary(self) -> list[dict]:
        """Compact space classification table for JSON debug export."""
        return [
            {
                "id":               s.id,
                "name":             s.space_name,
                "type":             s.space_type,
                "area_m2":          s.area_m2,
                "perimeter_m":      s.perimeter_m,
                "perimeter_source": s.perimeter_source,
                "is_wet":           s.is_wet,
                "is_verandah":      s.is_verandah,
                "is_enclosed":      s.is_enclosed,
                "finish_floor":     s.finish_floor,
                "truth_class":      s.truth_class,
                "confidence":       s.confidence,
                "fallback_used":    s.fallback_used,
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

    def full_debug_dict(self) -> dict:
        """Full debug export for writing to JSON output file."""
        return {
            "summary":       self._geom.summary_dict(),
            "openings":      self.opening_summary(),
            "wall_faces":    self.wall_face_summary(),
            "cladding_faces": self.cladding_face_summary(),
            "spaces":        self.space_summary(),
        }
