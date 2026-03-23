"""
derivation_rules.py — Pure functions for derived quantity calculations.

All functions are stateless.  They take measured geometry / lm values and
return derived quantities using documented rules.  No data is read from any
BOQ template.
"""
from __future__ import annotations

import math

# ── Constants ──────────────────────────────────────────────────────────────────
FC_WALL_SHEET_AREA_M2    = 3.24    # 1.2 m × 2.7 m
FC_CEILING_SHEET_AREA_M2 = 2.88   # 1.2 m × 2.4 m
FC_WASTE_FACTOR          = 1.05
SISALATION_ROLL_M2       = 73.0
BATTEN_ROOF_SPACING_MM   = 900
BATTEN_CEIL_SPACING_MM   = 400
DEFAULT_WALL_HEIGHT_M    = 2.4

# Trim / finishes constants (empirical from surveyed projects)
ARCHITRAVE_DOOR_LM_EACH    = 6.0   # lm per door (one side, 3-leg set: 2×2.1m + 0.9m head)
ARCHITRAVE_WINDOW_LM_EACH  = 4.8   # lm per window (2×H + 2×W ≈ 4.8m typical 1080×1200)
INT_WALL_LM_RATIO           = 0.34  # provisional ratio: int_wall_lm / floor_area_m2
                                     # derived from surveyed data (29.4 lm / 86.4 m²)


def fc_wall_sheets(wall_area_m2: float) -> int:
    """Number of FC sheet panels for a given wall area."""
    return math.ceil(wall_area_m2 * FC_WASTE_FACTOR / FC_WALL_SHEET_AREA_M2)


def fc_ceiling_sheets(ceiling_area_m2: float) -> int:
    """Number of FC sheet panels for a given ceiling area."""
    return math.ceil(ceiling_area_m2 * FC_WASTE_FACTOR / FC_CEILING_SHEET_AREA_M2)


def sisalation_rolls(roof_area_m2: float) -> int:
    """Number of sisalation rolls required for roof."""
    return math.ceil(roof_area_m2 / SISALATION_ROLL_M2)


def roof_batten_lm(
    roof_area_m2: float,
    building_width_m: float = 0,
    building_length_m: float = 0,
) -> float:
    """
    Total linear metres of roof battens.
    Prefer width×length method if both dimensions available.
    Falls back to area / spacing.
    """
    if building_width_m > 0 and building_length_m > 0:
        spacing_m = BATTEN_ROOF_SPACING_MM / 1000
        runs = math.ceil(building_width_m / 2 / spacing_m) + 1
        return round(runs * building_length_m * 2, 1)
    return round(roof_area_m2 / (BATTEN_ROOF_SPACING_MM / 1000), 1)


def ceiling_batten_lm(
    ceiling_area_m2: float,
    building_width_m: float = 0,
    building_length_m: float = 0,
) -> float:
    """
    Total linear metres of ceiling battens.
    Prefer width×length method if both dimensions available.
    """
    spacing_m = BATTEN_CEIL_SPACING_MM / 1000
    if building_width_m > 0 and building_length_m > 0:
        return round(math.ceil(building_width_m / spacing_m) * building_length_m, 1)
    return round(ceiling_area_m2 / spacing_m, 1)


def ext_wall_area_m2(
    ext_wall_perimeter_m: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """Gross external wall area (perimeter × height)."""
    return round(ext_wall_perimeter_m * wall_height_m, 2)


def int_wall_area_m2(
    int_wall_lm: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """Gross internal wall area (both faces × height)."""
    return round(int_wall_lm * wall_height_m * 2, 2)


def downpipe_count(ext_wall_perimeter_m: float) -> int:
    """Approximate number of downpipes based on roof perimeter."""
    return max(2, math.ceil(ext_wall_perimeter_m / 10) // 2)


def roof_fixings_boxes(roof_area_m2: float) -> int:
    """Boxes of roofing fixings (1 box per 10 m²)."""
    return math.ceil(roof_area_m2 / 10)


def gutter_lm(roof_perimeter_m: float) -> float:
    """
    Eaves gutter total length — full roof perimeter.

    Assumes hip roof (gutters on all sides).  For gable roof with no
    gutter on the gable ends, reduce by ~40%.  Verify from roof plan.
    """
    return round(roof_perimeter_m, 1)


def ridgecap_lm(roof_perimeter_m: float) -> float:
    """Ridge cap length (estimated from roof perimeter)."""
    return round(roof_perimeter_m / 4, 1)


def fascia_lm(roof_perimeter_m: float) -> float:
    """Fascia board total length (full roof perimeter)."""
    return round(roof_perimeter_m, 1)


def barge_lm(roof_perimeter_m: float, gable_count: int = 2) -> float:
    """Barge board total length (estimated)."""
    # Approximate: 20% of roof perimeter attributable to gable ends
    return round(roof_perimeter_m * 0.2, 1)


# ── Insulation ─────────────────────────────────────────────────────────────────

def insulation_wall_m2(
    ext_wall_perimeter_m: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """Insulation batt area for external walls (perimeter × height, gross)."""
    return round(ext_wall_perimeter_m * wall_height_m, 2)


def insulation_roof_m2(roof_area_m2: float) -> float:
    """Insulation batt area for roof/ceiling (same as roof area)."""
    return round(roof_area_m2, 2)


# ── Ceiling trim ───────────────────────────────────────────────────────────────

def cornice_lm(ext_wall_perimeter_m: float) -> float:
    """Cornice / ceiling trim length (runs along all internal faces of ext walls)."""
    return round(ext_wall_perimeter_m, 1)


# ── Internal wall estimate ─────────────────────────────────────────────────────

def int_wall_lm_estimate(floor_area_m2: float) -> float:
    """
    Provisional estimate of internal wall total length.

    Uses empirical ratio INT_WALL_LM_RATIO (0.34 lm/m²).
    Confidence: LOW.  Should be replaced by measured value if DXF or
    PDF room schedule provides internal wall geometry.
    """
    return round(floor_area_m2 * INT_WALL_LM_RATIO, 1)


def int_wall_one_face_m2(
    int_wall_lm: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """Gross internal wall area — one face only (for lining sheets and paint)."""
    return round(int_wall_lm * wall_height_m, 2)


# ── Finishes trim ──────────────────────────────────────────────────────────────

def skirting_lm(
    ext_wall_perimeter_m: float,
    int_wall_lm: float = 0.0,
) -> float:
    """
    Total skirting board length.

    Runs along all walls at floor level: ext_wall_perimeter + int_wall_lm.
    Internal walls counted once (one side of each wall run).
    """
    return round(ext_wall_perimeter_m + int_wall_lm, 1)


def architrave_door_lm(door_count: int) -> float:
    """Total architrave for doors — one side per opening (3-leg set)."""
    return round(door_count * ARCHITRAVE_DOOR_LM_EACH, 1)


def architrave_window_lm(window_count: int) -> float:
    """Total architrave for windows — perimeter of each opening."""
    return round(window_count * ARCHITRAVE_WINDOW_LM_EACH, 1)


def paint_external_m2(
    ext_wall_perimeter_m: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """External paint area — gross external wall face (perimeter × height)."""
    return round(ext_wall_perimeter_m * wall_height_m, 2)


def paint_internal_m2(
    ceiling_area_m2: float,
    int_wall_lm: float,
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M,
) -> float:
    """
    Internal paint area — ceiling + internal wall faces (one face per wall).

    Rule: ceiling_area + (int_wall_lm × wall_height).
    Note: ext wall internal face is excluded here as it is captured by the
    external paint item (both faces typically priced together on site).
    """
    int_wall_area = int_wall_one_face_m2(int_wall_lm, wall_height_m)
    return round(ceiling_area_m2 + int_wall_area, 2)
