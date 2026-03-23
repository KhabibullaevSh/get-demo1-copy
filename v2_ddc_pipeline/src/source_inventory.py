"""V2 source_inventory.py — scan input directory, classify each file, return inventory records."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("boq.v2.source_inventory")


def _classify_file(p: Path) -> dict:
    """Return a source-inventory record for a single file."""
    ext  = p.suffix.lower()
    name = p.name.lower()
    size_kb = round(p.stat().st_size / 1024, 1) if p.exists() else 0.0

    # Defaults
    file_type        = "other"
    discipline       = "unknown"
    source_category  = "other"
    priority         = 3
    notes            = ""

    if ext == ".ifc":
        file_type       = "ifc"
        discipline      = "structural"
        source_category = "ifc_model"
        priority        = 1

    elif ext == ".dxf":
        file_type       = "dxf"
        discipline      = "architectural"
        source_category = "dxf_architectural"
        priority        = 1

    elif ext == ".dwg":
        file_type = "dwg"
        if any(k in name for k in ("framecad", "frameclad", "structural")):
            source_category = "dwg_framecad"
            discipline      = "structural"
            priority        = 1
        elif "arch" in name:
            source_category = "dwg_architectural"
            discipline      = "architectural"
            priority        = 2
        else:
            source_category = "dwg_structural"
            discipline      = "structural"
            priority        = 2

    elif ext == ".pdf":
        file_type = "pdf"
        if any(k in name for k in ("layout", "arch", "marketing")):
            source_category = "pdf_architectural"
            discipline      = "architectural"
            priority        = 2
        elif any(k in name for k in ("summary", "structural")):
            source_category = "pdf_structural"
            discipline      = "structural"
            priority        = 2
        else:
            source_category = "pdf_schedule"
            discipline      = "unknown"
            priority        = 2

    elif ext == ".xlsx":
        file_type = "xlsx"
        if any(k in name for k in ("approved_boq", "boq")):
            source_category = "boq_reference"
            discipline      = "reference"
            priority        = 3
            notes           = "REFERENCE ONLY — no quantities"
        else:
            source_category = "rate_library"
            discipline      = "reference"
            priority        = 3

    elif ext == ".csv":
        file_type = "csv"
        if any(k in name for k in ("bom", "manufacturing", "framecad", "frameclad")):
            source_category = "bom_framecad"
            discipline      = "structural"
            priority        = 1
        else:
            source_category = "rate_library"
            discipline      = "reference"
            priority        = 3

    elif ext == ".txt":
        file_type = "txt"
        source_category = "other"
        priority        = 3

    return {
        "filename":           p.name,
        "path":               str(p),
        "extension":          ext,
        "size_kb":            size_kb,
        "type":               file_type,
        "discipline":         discipline,
        "source_category":    source_category,
        "parsed_successfully": False,     # updated by extractors
        "parse_error":        None,
        "used_in_reasoning":  False,      # updated by extractors
        "priority":           priority,
        "notes":              notes,
    }


def build_source_inventory(input_dir: Path) -> list[dict]:
    """
    Scan *input_dir* recursively, classify every file, return list of records.
    Directories and __pycache__ entries are ignored.
    """
    records: list[dict] = []
    if not input_dir.exists():
        log.warning("Input directory does not exist: %s", input_dir)
        return records

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in str(p):
            continue
        rec = _classify_file(p)
        records.append(rec)
        log.debug("Inventoried: %s  [%s / %s]", p.name, rec["source_category"], rec["discipline"])

    log.info("Source inventory: %d files in %s", len(records), input_dir)
    return records


def save_source_inventory(records: list, output_dir: Path) -> Path:
    """Write source_inventory.json to *output_dir*. Returns the path written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "source_inventory.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, default=str)
    log.info("Saved source_inventory.json (%d records) → %s", len(records), out_path)
    return out_path
