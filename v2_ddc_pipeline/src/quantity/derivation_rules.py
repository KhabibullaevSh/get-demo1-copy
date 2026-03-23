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


def gutter_lm(ext_wall_perimeter_m: float) -> float:
    """Approximate eaves gutter total length (half the perimeter as default)."""
    return round(ext_wall_perimeter_m / 2, 1)


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
