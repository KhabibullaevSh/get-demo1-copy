"""
config.py — Centralised constants, paths, enums, and rule loading.
"""

from __future__ import annotations
import os
import json
from enum import Enum
from pathlib import Path

# ── Root paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR        = ROOT / "data"
STANDARD_MODELS = DATA_DIR / "standard_models"
RULES_DIR       = DATA_DIR / "rules"
INPUT_DIR       = ROOT / "input"
OUTPUT_DIR      = ROOT / "output"
OUTPUT_BOQ      = OUTPUT_DIR / "boq"
OUTPUT_REPORTS  = OUTPUT_DIR / "reports"
OUTPUT_LOGS     = OUTPUT_DIR / "logs"

# ── Supported extensions ──────────────────────────────────────────────────────
DWG_EXTS  = {".dwg", ".dxf"}
PDF_EXTS  = {".pdf"}
IFC_EXTS  = {".ifc"}
BOM_EXTS  = {".xlsx", ".xls", ".csv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

# ── Sheet / material constants ─────────────────────────────────────────────────
# NOTE: Spacing values are sourced from Rules Library (data/standard_models/G303.xlsx).
# These constants are used as fallback only — loader.load_rules_library() overrides them.
SHEET_AREA_FC        = 2.88   # m²  (1.2 × 2.4 ceiling sheet)
SHEET_AREA_FC_WALL   = 3.24   # m²  (1.2 × 2.7 wall sheet)
SHEET_AREA_PLASTER   = 2.88   # m²
DEFAULT_WALL_HEIGHT  = 2.4    # m
DEFAULT_CONFLICT_TOLERANCE = 0.10   # 10 %
BATTEN_ROOF_SPACING_MM      = 900   # R-001: 900mm centres per methodology section 6.3
BATTEN_CEILING_SPACING_MM   = 400   # R-010: 400mm centres
BATTEN_VERANDAH_SPACING_MM  = 800   # soffit battens: 800mm centres
BATTEN_LENGTH_MM            = 5800  # standard stock length
DEFAULT_ROOF_PITCH_DEG      = 18.0  # fallback when DWG pitch not found
DEFAULT_EAVE_OVERHANG_MM    = 300   # fallback eave overhang
WEATHERBOARD_COVER_MM       = 200   # R-018: 200mm effective cover per row (230mm face − 30mm lap)
WEATHERBOARD_LENGTH_MM      = 4200  # R-018: 4200mm standard board length
SISALATION_ROLL_M2          = 73.0  # R-002/R-014: effective roll coverage (1.35×60 less laps)
FC_WASTE_FACTOR             = 1.05  # grid method already handles edge cuts; reduced from 1.10
PLASTER_WASTE_FACTOR        = 1.10

# ── Project modes ──────────────────────────────────────────────────────────────
class ProjectMode(str, Enum):
    STANDARD = "standard"
    GENERIC  = "generic"

# ── Confidence levels ──────────────────────────────────────────────────────────
class Confidence(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"
    UNKNOWN = "UNKNOWN"

# ── Source priority order (lower index = higher priority) ─────────────────────
SOURCE_PRIORITY: dict[str, list[str]] = {
    "structural": ["bom", "ifc", "structural_schedule", "dwg_geometry",
                   "arch_evidence", "standard_fallback"],
    "room_areas": ["dwg_polygon", "pdf_explicit", "ifc_slab", "standard_fallback"],
    "doors":      ["schedule", "elevation_detail", "plan_count", "standard_fallback"],
    "windows":    ["schedule", "elevation_detail", "plan_count", "standard_fallback"],
    "stairs":     ["structural_detail", "arch_detail", "plan_evidence", "standard_fallback"],
    "finishes":   ["finish_schedule", "room_note", "general_note", "standard_fallback"],
    "battens":    ["bom_explicit", "structural_note", "area_rule", "standard_fallback"],
    "fc_sheets":  ["lining_schedule", "finish_note", "wall_area_rule", "standard_fallback"],
    "roof":       ["dwg_polygon", "pdf_explicit", "standard_fallback"],
}

# ── G-Range standard models ────────────────────────────────────────────────────
STANDARD_MODELS_MAP: dict[str, str] = {
    "G201":  "G201.xlsx",
    "G202":  "G202.xlsx",
    "G302":  "G302.xlsx",
    "G303":  "G303.xlsx",
    "G403E": "G403E.xlsx",
    "G404":  "G404.xlsx",
    "G504E": "G504E.xlsx",
}

ITEM_RULES: dict = {}
_SOURCE_PRIORITY_OVERRIDE: dict = {}

def load_rules() -> None:
    """Load JSON rule files into module-level dicts."""
    global ITEM_RULES, _SOURCE_PRIORITY_OVERRIDE, SOURCE_PRIORITY

    rules_path = RULES_DIR / "item_rules.json"
    if rules_path.exists():
        try:
            with open(rules_path, encoding="utf-8") as f:
                ITEM_RULES = json.load(f)
        except Exception:
            pass

    sp_path = RULES_DIR / "source_priority.json"
    if sp_path.exists():
        try:
            with open(sp_path, encoding="utf-8") as f:
                _SOURCE_PRIORITY_OVERRIDE = json.load(f)
                SOURCE_PRIORITY.update(_SOURCE_PRIORITY_OVERRIDE)
        except Exception:
            pass


def ensure_output_dirs() -> None:
    """Create output directories if they do not exist."""
    for d in [OUTPUT_BOQ, OUTPUT_REPORTS, OUTPUT_LOGS]:
        d.mkdir(parents=True, exist_ok=True)


# Auto-load on import
load_rules()
ensure_output_dirs()
