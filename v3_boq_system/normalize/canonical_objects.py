"""
canonical_objects.py — Canonical geometry layer for the V3 BOQ pipeline.

The canonical layer is a post-reconciliation, multi-source-fused representation
of building geometry.  It is ADDITIVE to ProjectElementModel — it does not
replace it.  Quantifiers prefer canonical objects when available and fall back
to the element model when not.

Key design goals:
  1. Every classification decision is explicit in object metadata, not buried in
     notes strings (is_entrance, height_fallback_used, truth_class, etc.)
  2. TruthClass surfaces source quality as a typed field — not inferred by the reader.
  3. Net areas and opening deductions are pre-computed once, consistently.
  4. All objects carry full provenance (source_files, source_entity_ids, confidence).

INVARIANT: No quantities from BOQ reference files enter canonical objects.
           All values come from DXF / IFC / FrameCAD / PDF / config sources only.

Architecture:
  ProjectElementModel + config
          ↓
  geometry_reconciler.build_canonical_geometry()
          ↓
  CanonicalGeometryModel  ← this module defines the types
          ↓
  quantify_external_cladding / quantify_linings / quantify_finish_zones
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# PART D — Truth Class
# ═══════════════════════════════════════════════════════════════════════════════

class TruthClass:
    """
    Explicit data-quality classification for each canonical object.

    Rules:
      MEASURED             — directly taken from a source drawing with no
                             intermediate computation (DXF polyline area,
                             IFC IfcSpace polygon, DXF INSERT width).
      CALCULATED           — derived by a deterministic geometry calculation
                             from one or more MEASURED inputs (joist count from
                             DWG schedule × panel count, cladding area from
                             measured wall lm × height).
      INFERRED             — logically deduced but not directly measurable from
                             available sources (internal wall lm from floor area
                             ratio, room areas from config schedule).
      CONFIG_FALLBACK      — came from project_config.yaml; no drawing source.
                             This is the lowest-confidence class — always
                             surfaces manual_review=True in the BOQ.
      SOURCE_LIMITED       — a source is identified but cannot yield this
                             particular value (IFC gives total verandah lm but
                             no per-member breakdown).
    """
    MEASURED         = "measured"
    CALCULATED       = "calculated_from_geometry"
    INFERRED         = "inferred"
    CONFIG_FALLBACK  = "config_fallback"
    SOURCE_LIMITED   = "source_limited"

    _ORDER = {
        MEASURED:        5,
        CALCULATED:      4,
        INFERRED:        3,
        CONFIG_FALLBACK: 2,
        SOURCE_LIMITED:  1,
    }

    @classmethod
    def weaker(cls, a: str, b: str) -> str:
        """Return the weaker (lower confidence) of two TruthClass values."""
        return a if cls._ORDER.get(a, 0) <= cls._ORDER.get(b, 0) else b

    @classmethod
    def weakest(cls, values: list[str]) -> str:
        """Return the weakest TruthClass from a list."""
        if not values:
            return cls.CONFIG_FALLBACK
        return min(values, key=lambda v: cls._ORDER.get(v, 0))

    @classmethod
    def to_quantity_status(cls, tc: str) -> str:
        """
        Map TruthClass → BOQ quantity_status string.

        This ensures canonical objects drive BOQ status consistently.
        """
        return {
            cls.MEASURED:        "measured",
            cls.CALCULATED:      "calculated",
            cls.INFERRED:        "inferred",
            cls.CONFIG_FALLBACK: "inferred",
            cls.SOURCE_LIMITED:  "inferred",
        }.get(tc, "inferred")


# ═══════════════════════════════════════════════════════════════════════════════
# PART A — Canonical Object Dataclasses
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CanonicalOpening:
    """
    Opening (door / window) with all classification decisions resolved.

    Resolves ambiguities that caused bugs in OpeningElement:
      is_entrance:          True only for doors wide enough to be external entry
                            (width ≥ _EXT_DOOR_MIN_W = 0.85 m).
      is_cladding_face:     True if this opening cuts the external cladding surface.
      height_used:          The height value actually used in area calculations.
      height_fallback_used: True when height came from config/louvre default,
                            not from a direct source measurement.
      opening_area_m2:      width × height_used × quantity — always quantity-correct.

    The opening_area_m2 field is the canonical deduction value.  Quantifiers
    must NOT recompute it — just sum the canonical openings they need.
    """
    # Identity
    id:                   str
    mark:                 str
    opening_type:         str          # door | window | fanlight | skylight

    # Dimensions (as-extracted and resolved)
    width_m:              float
    height_m_raw:         float        # from extractor (may be 0.0 for louvres)
    height_used:          float        # value used in all area calculations
    height_fallback_used: bool         # True → height came from config default

    # Count
    quantity:             int

    # Classification flags (all explicit — not buried in notes)
    is_external:          bool
    is_entrance:          bool         # door AND width >= _EXT_DOOR_MIN_W
    is_cladding_face:     bool         # contributes to external cladding deduction
    is_partition:         bool         # door in internal wall, NOT on cladding face

    # Pre-computed (canonical deduction value — always width × height_used × qty)
    opening_area_m2:      float

    # Optional geometry context
    level:                str          = "GF"

    # Provenance
    source_files:         list[str]    = field(default_factory=list)
    source_entity_ids:    list[str]    = field(default_factory=list)
    confidence:           str          = "MEDIUM"
    truth_class:          str          = TruthClass.MEASURED
    fallback_used:        bool         = False
    notes:                str          = ""

    # Linked objects (populated by _link_relationships in geometry_reconciler)
    linked_wall_face_ids: list[str]    = field(default_factory=list)
    linked_space_ids:     list[str]    = field(default_factory=list)
    # Explicit exposure class — derived from classification, not buried in is_* flags
    exposure_class:       str          = "unknown"  # external | internal_partition | unknown
    # Evidence chain — each string records one piece of evidence for truth_class
    evidence:             list[str]    = field(default_factory=list)


@dataclass
class CanonicalWallFace:
    """
    A single wall face — one side of one wall aggregate — with pre-computed
    gross and net areas.

    For external walls: one face (the external cladding face).
    For internal partitions: both faces combined (WallElement.area_m2 already
    includes faces=2, so gross_area_m2 here is the combined both-face area).

    Net area is gross minus opening deductions, computed once with correct
    quantity multipliers.  Quantifiers read net_area_m2 directly.
    """
    id:                   str
    wall_type:            str          # external | internal

    # Geometry
    length_m:             float
    height_m:             float
    gross_area_m2:        float        # length × height (× faces for internal)
    net_area_m2:          float        # gross − opening_deduction_m2
    opening_deduction_m2: float        # total area deducted

    # Linked openings that contributed to the deduction
    opening_ids:          list[str]    = field(default_factory=list)

    # Optional geometry metadata
    orientation:          Optional[str] = None   # N | S | E | W
    is_cladding_face:     bool          = False
    level:                str           = "GF"

    # Provenance
    source_files:         list[str]    = field(default_factory=list)
    source_entity_ids:    list[str]    = field(default_factory=list)
    confidence:           str          = "HIGH"
    truth_class:          str          = TruthClass.MEASURED
    fallback_used:        bool         = False
    notes:                str          = ""

    # Linked spaces (populated by _link_relationships)
    linked_space_ids:     list[str]    = field(default_factory=list)
    # Explicit face classification — replaces implicit wall_type checks in quantifiers
    face_class:           str          = "unknown"  # external | internal | verandah_edge | unknown
    # Evidence chain
    evidence:             list[str]    = field(default_factory=list)


@dataclass
class CanonicalCladdingFace:
    """
    External cladding face: the external wall surface ready for quantification.

    Pre-computes:
      - Gross area from DXF-measured wall dimensions
      - Net area after correct opening deductions (entrance doors + windows,
        with louvre height fallback, with quantity multiplied in)
      - Separate breakdown of door vs window deductions for traceability

    external_cladding_quantifier consumes this directly — it does not re-derive
    opening classifications or net areas.
    """
    id:                      str
    wall_face_id:            str        # links to CanonicalWallFace

    # Areas
    gross_area_m2:           float
    net_area_m2:             float
    opening_deduction_m2:    float

    # All opening ids that contributed to the deduction
    opening_ids:             list[str]    = field(default_factory=list)

    # Wall dimensions (needed by board-count and accessory rows)
    ext_lm:                  float        = 0.0
    wall_height_m:           float        = 2.4

    # Deduction breakdown by type (for traceability)
    door_deduction_m2:       float        = 0.0
    window_deduction_m2:     float        = 0.0
    door_opening_ids:        list[str]    = field(default_factory=list)
    window_opening_ids:      list[str]    = field(default_factory=list)
    louvre_height_default_m: float        = 0.75

    # Provenance
    level:                   str          = "GF"
    source_files:            list[str]    = field(default_factory=list)
    source_entity_ids:       list[str]    = field(default_factory=list)
    confidence:              str          = "HIGH"
    truth_class:             str          = TruthClass.MEASURED
    fallback_used:           bool         = False
    notes:                   str          = ""


@dataclass
class CanonicalSpace:
    """
    Building space with explicit truth classification and finish zoning.

    Key improvement over SpaceElement: truth_class is a first-class typed field,
    not buried in notes strings.  Callers can branch on it without string parsing.

    perimeter_source distinguishes:
      "measured"               — DXF polygon boundary derived
      "calculated_from_geometry" — wall-network zone, DXF-backed perimeter
      "config_specified"       — perimeter listed in project_config (not drawn)
      "estimated"              — 4×√area rectangle estimate (no source)

    All config-sourced spaces produce truth_class=CONFIG_FALLBACK and
    fallback_used=True.  This surfaces automatically in BOQ quantity_status.
    """
    id:                   str
    space_name:           str
    space_type:           str          # toilet | pharmacy | waiting | verandah | unknown

    # Geometry
    area_m2:              float
    perimeter_m:          float
    perimeter_source:     str          # measured | calculated_from_geometry | config_specified | estimated
    ceiling_area_m2:      float        = 0.0

    # Classification flags
    is_wet:               bool         = False
    is_external:          bool         = False
    is_verandah:          bool         = False
    is_enclosed:          bool         = True

    # Finish assignments
    finish_floor:         str          = ""   # vinyl_plank | ceramic_tile | decking | …
    finish_wall:          str          = ""   # paint | ceramic_tile | none | …
    finish_ceiling:       str          = ""   # paint | none | …
    finish_source:        str          = ""   # schedule | inferred_from_type | config

    # Optional geometry
    level:                str          = "GF"
    polygon:              list         = field(default_factory=list)   # [[x,y], …]

    # Provenance
    source_files:         list[str]    = field(default_factory=list)
    source_entity_ids:    list[str]    = field(default_factory=list)
    confidence:           str          = "LOW"
    truth_class:          str          = TruthClass.CONFIG_FALLBACK
    fallback_used:        bool         = True
    notes:                str          = ""

    # Linked objects (populated by _link_relationships)
    linked_opening_ids:   list[str]    = field(default_factory=list)
    linked_wall_face_ids: list[str]    = field(default_factory=list)
    # Explicit enclosure class — replaces is_enclosed / is_verandah flag checks
    enclosure_class:      str          = "unknown"  # enclosed | semi_external | verandah | external | unknown
    # Evidence chain
    evidence:             list[str]    = field(default_factory=list)


@dataclass
class CanonicalFloorZone:
    """
    Aggregated floor finish zone for procurement.

    Groups spaces by finish type and zone character (wet/dry/verandah).
    Carries the minimum TruthClass of contributing spaces so that config-backed
    rooms propagate LOW confidence to the finish BOQ row.
    """
    id:                   str
    zone_name:            str
    zone_type:            str          # internal_dry | internal_wet | verandah | external
    finish_type:          str          # vinyl_plank | ceramic_tile | decking | screed

    # Aggregated geometry
    area_m2:              float
    perimeter_m:          float        = 0.0

    # Contributing spaces
    space_ids:            list[str]    = field(default_factory=list)
    # Evidence chain — why this zone has the truth_class it has
    evidence:             list[str]    = field(default_factory=list)

    # Provenance
    level:                str          = "GF"
    source_files:         list[str]    = field(default_factory=list)
    source_entity_ids:    list[str]    = field(default_factory=list)
    confidence:           str          = "LOW"
    truth_class:          str          = TruthClass.CONFIG_FALLBACK
    fallback_used:        bool         = True
    notes:                str          = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical Geometry Model — container
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CanonicalGeometryModel:
    """
    Post-reconciliation geometry truth model for the V3 BOQ pipeline.

    Built by geometry_reconciler.build_canonical_geometry() after all extractors
    and the graphical reconciler have run.  Quantifiers consume this when available;
    they fall back to ProjectElementModel when it is None.

    INVARIANT: Only geometry-derived quantities here.  No BOQ reference values.
    """
    openings:       list[CanonicalOpening]      = field(default_factory=list)
    wall_faces:     list[CanonicalWallFace]      = field(default_factory=list)
    cladding_faces: list[CanonicalCladdingFace]  = field(default_factory=list)
    spaces:         list[CanonicalSpace]         = field(default_factory=list)
    floor_zones:    list[CanonicalFloorZone]     = field(default_factory=list)

    # ── Filtered accessors ────────────────────────────────────────────────────

    def entrance_doors(self) -> list[CanonicalOpening]:
        return [o for o in self.openings if o.is_entrance]

    def cladding_face_openings(self) -> list[CanonicalOpening]:
        return [o for o in self.openings if o.is_cladding_face]

    def partition_doors(self) -> list[CanonicalOpening]:
        return [o for o in self.openings if o.is_partition]

    def external_windows(self) -> list[CanonicalOpening]:
        return [o for o in self.openings
                if o.opening_type == "window" and o.is_external]

    def primary_cladding_face(self) -> Optional[CanonicalCladdingFace]:
        """Primary external cladding face (first / only for single-storey)."""
        return self.cladding_faces[0] if self.cladding_faces else None

    def external_wall_face(self) -> Optional[CanonicalWallFace]:
        return next((wf for wf in self.wall_faces if wf.wall_type == "external"), None)

    def internal_wall_face(self) -> Optional[CanonicalWallFace]:
        return next((wf for wf in self.wall_faces if wf.wall_type == "internal"), None)

    def wet_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self.spaces if s.is_wet]

    def enclosed_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self.spaces if s.is_enclosed and not s.is_external]

    def verandah_spaces(self) -> list[CanonicalSpace]:
        return [s for s in self.spaces if s.is_verandah]

    def spaces_by_truth_class(self, tc: str) -> list[CanonicalSpace]:
        return [s for s in self.spaces if s.truth_class == tc]

    def floor_zones_by_type(self, zone_type: str) -> list[CanonicalFloorZone]:
        return [fz for fz in self.floor_zones if fz.zone_type == zone_type]

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary_dict(self) -> dict:
        """Compact summary for logging and JSON output."""
        return {
            "openings":              len(self.openings),
            "entrance_doors":        len(self.entrance_doors()),
            "partition_doors":       len(self.partition_doors()),
            "cladding_face_openings": len(self.cladding_face_openings()),
            "wall_faces":            len(self.wall_faces),
            "cladding_faces":        len(self.cladding_faces),
            "spaces":                len(self.spaces),
            "wet_spaces":            len(self.wet_spaces()),
            "verandah_spaces":       len(self.verandah_spaces()),
            "floor_zones":           len(self.floor_zones),
            "config_fallback_spaces": len(self.spaces_by_truth_class(TruthClass.CONFIG_FALLBACK)),
            "measured_spaces":       len(self.spaces_by_truth_class(TruthClass.MEASURED)),
        }
