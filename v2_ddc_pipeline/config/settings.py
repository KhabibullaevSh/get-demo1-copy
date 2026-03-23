"""V2 configuration — paths, constants, source-priority rules."""
from __future__ import annotations
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT.parent / "input"    # shared with V1
DATA_DIR  = ROOT.parent / "data"     # shared with V1 (item library, approved BOQ)
OUTPUT_DIR = ROOT / "outputs"

# Unit conversions
MM_TO_M   = 1 / 1000
MM2_TO_M2 = 1 / 1_000_000

# Derived quantity rules (same as V1 for comparability)
FC_WALL_SHEET_AREA_M2    = 3.24    # 1.2 × 2.7 m
FC_CEILING_SHEET_AREA_M2 = 2.88   # 1.2 × 2.4 m
FC_WASTE_FACTOR          = 1.05
SISALATION_ROLL_M2       = 73.0
BATTEN_ROOF_SPACING_MM   = 900
BATTEN_CEIL_SPACING_MM   = 400

# Source priority (higher index = higher priority)
STRUCTURAL_PRIORITY = ["fallback_rules", "pdf_notes", "dxf_geometry", "ifc_geometry", "framecad_bom"]
OPENINGS_PRIORITY   = ["fallback_rules", "dxf_blocks", "ifc_doors_windows", "pdf_schedule"]
ROOF_PRIORITY       = ["fallback_rules", "pdf_notes", "dxf_geometry", "ifc_geometry"]
FINISHES_PRIORITY   = ["fallback_rules", "dxf_rooms", "ifc_spaces", "pdf_schedule"]
