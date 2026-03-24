"""
element_model.py — Normalized element model for the V3 BOQ pipeline.

This module defines all element types that flow between the extraction layer
and the quantification/assembly layer.  Every element carries full provenance.

Architecture:
  extractors  →  element_builder  →  ElementModel  →  quantifiers  →  assemblies  →  BOQ

CRITICAL: No quantities from BOQ reference files are ever stored here.
All values come from DXF / IFC / FrameCAD / PDF extractions only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Base ──────────────────────────────────────────────────────────────────────

@dataclass
class BaseElement:
    """Shared provenance fields on every element."""
    element_id:       str   = ""
    source:           str   = "unknown"   # dxf_geometry | ifc_model | framecad_bom | pdf_schedule | derived
    source_reference: str   = ""          # filename, tab, layer, entity reference
    confidence:       str   = "LOW"       # HIGH | MEDIUM | LOW
    notes:            str   = ""


# ── Geometry ─────────────────────────────────────────────────────────────────

@dataclass
class FloorElement(BaseElement):
    category:     str   = "floor"
    area_m2:      float = 0.0
    perimeter_m:  float = 0.0
    level:        str   = "GF"        # GF | L1 | L2 …


@dataclass
class CeilingElement(BaseElement):
    category:    str   = "ceiling"
    area_m2:     float = 0.0
    level:       str   = "GF"


@dataclass
class RoofElement(BaseElement):
    category:         str   = "roof"
    area_m2:          float = 0.0
    perimeter_m:      float = 0.0
    eaves_length_m:   float = 0.0   # full eaves perimeter (all sides with gutters)
    ridge_length_m:   float = 0.0
    valley_length_m:  float = 0.0
    barge_length_m:   float = 0.0
    apron_length_m:   float = 0.0
    roof_type:        str   = "hip"   # hip | gable | shed | dutch_hip
    pitch_deg:        float = 0.0


@dataclass
class VerandahElement(BaseElement):
    category:    str   = "verandah"
    area_m2:     float = 0.0
    perimeter_m: float = 0.0


# ── Walls ─────────────────────────────────────────────────────────────────────

@dataclass
class WallElement(BaseElement):
    category:      str   = "wall"
    wall_type:     str   = "external"   # external | internal | partition
    length_m:      float = 0.0
    height_m:      float = 2.4
    faces:         int   = 1            # 1 = one face (external); 2 = both faces (partition)
    area_m2:       float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.area_m2 = round(self.length_m * self.height_m * self.faces, 2)


@dataclass
class WallFaceElement(BaseElement):
    """Individual wall face for lining calculations."""
    category:      str   = "wall_face"
    parent_id:     str   = ""
    wall_type:     str   = "external"
    area_m2:       float = 0.0
    lining_type:   str   = "standard"   # standard | wet_area | fire_rated | acoustic


# ── Structural ────────────────────────────────────────────────────────────────

@dataclass
class StructuralFrameElement(BaseElement):
    """LGS / timber framing totals from FrameCAD or IFC."""
    category:       str   = "structural_frame"
    frame_type:     str   = ""   # roof_panel | roof_truss | wall_frame | floor_joist | verandah_frame | roof_batten
    total_lm:       float = 0.0
    member_spec:    str   = ""   # e.g. "89S41-075-500"
    member_entries: list  = field(default_factory=list)  # per stock-length [{grade_mm, qty, length_mm, total_lm}]


@dataclass
class FloorSystemElement(BaseElement):
    """
    Floor panel / joist / bearer assembly from FrameCAD or structural schedule.
    One instance = one panel type or one floor zone.
    """
    category:          str   = "floor_panel"
    assembly_type:     str   = "unknown"  # floor_panel | floor_joist | floor_bearer | slab
    panel_length_mm:   int   = 0
    panel_width_mm:    int   = 0
    load_class:        str   = ""         # e.g. "1.8kPa" | "4kPa"
    panel_count:       int   = 0
    bearer_length_mm:  int   = 0
    bearer_count_pairs: int  = 0
    joist_length_mm:   int   = 0
    joist_count:       int   = 0
    total_joist_lm:    float = 0.0
    floor_area_m2:     float = 0.0

    def compute_floor_area(self) -> float:
        if self.panel_length_mm > 0 and self.panel_width_mm > 0 and self.panel_count > 0:
            return round(self.panel_length_mm / 1000 * self.panel_width_mm / 1000 * self.panel_count, 2)
        return self.floor_area_m2


# ── Openings ─────────────────────────────────────────────────────────────────

@dataclass
class OpeningElement(BaseElement):
    category:      str   = "opening"
    opening_type:  str   = "door"    # door | window | fanlight | skylight
    mark:          str   = ""
    width_m:       float = 0.0
    height_m:      float = 0.0
    quantity:      int   = 1
    location:      str   = ""       # room / wall reference
    swing_type:    str   = "hinged" # hinged | sliding | bifold | fixed | louvre | casement
    material:      str   = ""       # timber | aluminium | uPVC | steel
    frame_type:    str   = ""
    is_external:   bool  = True
    has_flyscreen: bool  = False


# ── Rooms ─────────────────────────────────────────────────────────────────────

@dataclass
class RoomElement(BaseElement):
    category:       str   = "room"
    room_name:      str   = ""
    room_type:      str   = "unknown"   # toilet | bathroom | kitchen | office | …
    area_m2:        float = 0.0
    perimeter_m:    float = 0.0
    level:          str   = "GF"
    is_wet_area:    bool  = False
    finish_type:    str   = ""


# ── Footings / Substructure ───────────────────────────────────────────────────

@dataclass
class FootingElement(BaseElement):
    category:        str   = "footing"
    footing_type:    str   = "slab"   # slab | pad | strip | pile
    count:           int   = 0
    area_m2:         float = 0.0      # slab footprint
    perimeter_m:     float = 0.0      # edge beam / formwork
    thickness_mm:    int   = 100
    concrete_m3:     float = 0.0
    reinforcement:   str   = ""       # mesh type or bar spec
    notes:           str   = ""


# ── Stairs ────────────────────────────────────────────────────────────────────

@dataclass
class StairElement(BaseElement):
    category:         str   = "stair"
    stair_type:       str   = "unknown"  # prefab | in_situ | timber | steel
    flights:          int   = 1
    risers_per_flight: int  = 0
    tread_depth_mm:   int   = 250
    riser_height_mm:  int   = 175
    width_m:          float = 0.0
    landing_area_m2:  float = 0.0
    balustrade_lm:    float = 0.0
    handrail_lm:      float = 0.0


# ── Finish Zones ─────────────────────────────────────────────────────────────

@dataclass
class FinishZoneElement(BaseElement):
    category:      str   = "finish_zone"
    finish_type:   str   = ""    # floor | wall | ceiling
    material:      str   = ""    # tiles | vinyl | paint | screed | carpet
    area_m2:       float = 0.0
    room_ref:      str   = ""


# ── Normalized Project Element Model ─────────────────────────────────────────

@dataclass
class ProjectElementModel:
    """
    Single container for all normalized elements extracted from project documents.
    This is the mandatory intermediate layer between extractors and quantifiers.

    INVARIANT: No quantities are ever copied from BOQ reference files into this model.
    """
    project_name:  str = ""
    project_type:  str = "unknown"

    # Geometry
    floors:          list[FloorElement]         = field(default_factory=list)
    ceilings:        list[CeilingElement]        = field(default_factory=list)
    roofs:           list[RoofElement]           = field(default_factory=list)
    verandahs:       list[VerandahElement]       = field(default_factory=list)

    # Walls
    walls:           list[WallElement]           = field(default_factory=list)
    wall_faces:      list[WallFaceElement]       = field(default_factory=list)

    # Structural
    structural_frames: list[StructuralFrameElement]  = field(default_factory=list)
    floor_systems:     list[FloorSystemElement]      = field(default_factory=list)

    # Openings
    openings:        list[OpeningElement]        = field(default_factory=list)

    # Rooms
    rooms:           list[RoomElement]           = field(default_factory=list)

    # Substructure
    footings:        list[FootingElement]        = field(default_factory=list)

    # Stairs
    stairs:          list[StairElement]          = field(default_factory=list)

    # Finishes
    finish_zones:    list[FinishZoneElement]     = field(default_factory=list)

    # Metadata
    source_files:    list[str]                   = field(default_factory=list)
    warnings:        list[str]                   = field(default_factory=list)
    extraction_notes: list[str]                  = field(default_factory=list)

    # ── Convenience accessors ─────────────────────────────────────────────────

    def total_floor_area_m2(self) -> float:
        return sum(f.area_m2 for f in self.floors)

    def total_roof_area_m2(self) -> float:
        return sum(r.area_m2 for r in self.roofs)

    def total_ext_wall_lm(self) -> float:
        return sum(w.length_m for w in self.walls if w.wall_type == "external")

    def total_int_wall_lm(self) -> float:
        return sum(w.length_m for w in self.walls if w.wall_type == "internal")

    def total_ceiling_area_m2(self) -> float:
        return sum(c.area_m2 for c in self.ceilings)

    def total_verandah_area_m2(self) -> float:
        return sum(v.area_m2 for v in self.verandahs)

    def total_verandah_perimeter_m(self) -> float:
        return sum(v.perimeter_m for v in self.verandahs)

    def door_count(self) -> int:
        return sum(o.quantity for o in self.openings if o.opening_type == "door")

    def window_count(self) -> int:
        return sum(o.quantity for o in self.openings if o.opening_type == "window")

    def openings_by_type(self, opening_type: str) -> list[OpeningElement]:
        return [o for o in self.openings if o.opening_type == opening_type]

    def has_floor_joists(self) -> bool:
        return any(fs.assembly_type in ("floor_joist", "floor_panel") for fs in self.floor_systems)

    def has_slab(self) -> bool:
        return any(f.footing_type == "slab" for f in self.footings)

    def rooms_by_type(self, room_type: str) -> list[RoomElement]:
        return [r for r in self.rooms if r.room_type == room_type]

    def wet_rooms(self) -> list[RoomElement]:
        return [r for r in self.rooms if r.is_wet_area]

    def has_stair_evidence(self) -> bool:
        return len(self.stairs) > 0

    def primary_roof(self) -> Optional[RoofElement]:
        if not self.roofs:
            return None
        return max(self.roofs, key=lambda r: r.area_m2)

    def summary(self) -> dict:
        """Return a flat summary of key geometry for logging / QA."""
        return {
            "floor_area_m2":       round(self.total_floor_area_m2(), 2),
            "roof_area_m2":        round(self.total_roof_area_m2(), 2),
            "ext_wall_lm":         round(self.total_ext_wall_lm(), 2),
            "int_wall_lm":         round(self.total_int_wall_lm(), 2),
            "ceiling_area_m2":     round(self.total_ceiling_area_m2(), 2),
            "verandah_area_m2":    round(self.total_verandah_area_m2(), 2),
            "door_count":          self.door_count(),
            "window_count":        self.window_count(),
            "room_count":          len(self.rooms),
            "has_floor_joists":    self.has_floor_joists(),
            "has_slab":            self.has_slab(),
            "has_stair_evidence":  self.has_stair_evidence(),
            "source_files":        len(self.source_files),
            "warnings":            len(self.warnings),
        }
