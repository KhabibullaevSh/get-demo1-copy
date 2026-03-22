"""
file_detector.py — Recursive scan of input/ directory.

Returns a structured dict classifying every file found.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

from src.config import INPUT_DIR, DWG_EXTS, PDF_EXTS, IFC_EXTS, BOM_EXTS

log = logging.getLogger("boq.file_detector")


def detect_files(base_dir: Path | None = None) -> dict[str, Any]:
    """Recursively scan *base_dir* (defaults to INPUT_DIR) and classify files.

    Returns::

        {
          "dwg":   [{"path": ..., "size_kb": ...}],
          "dxf":   [...],
          "pdf":   [{"path": ..., "type": "vector|raster|mixed", "pages": N}],
          "ifc":   [...],
          "bom":   [...],
          "other": [...],
          "warnings": [...]
        }
    """
    base = Path(base_dir) if base_dir else INPUT_DIR
    result: dict[str, Any] = {
        "dwg": [], "dxf": [], "pdf": [], "ifc": [], "bom": [], "other": [],
        "warnings": [],
    }

    if not base.exists():
        result["warnings"].append(f"Input directory does not exist: {base}")
        return result

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$") or path.name.startswith("."):
            continue

        ext = path.suffix.lower()
        size_kb = round(path.stat().st_size / 1024, 1)
        entry: dict[str, Any] = {"path": str(path), "size_kb": size_kb}

        if ext == ".dwg":
            result["dwg"].append(entry)
        elif ext == ".dxf":
            result["dxf"].append(entry)
        elif ext in PDF_EXTS:
            result["pdf"].append(_classify_pdf(path, entry))
        elif ext in IFC_EXTS:
            result["ifc"].append(entry)
        elif ext in BOM_EXTS:
            # Only flag as BOM if in bom/ subfolder or name hints at BOM
            if "bom" in str(path).lower() or _looks_like_bom(path.name):
                result["bom"].append(entry)
            else:
                result["other"].append(entry)
        else:
            result["other"].append(entry)

    _log_summary(result)
    return result


def _classify_pdf(path: Path, entry: dict) -> dict:
    """Classify PDF as vector / raster / mixed using pymupdf."""
    entry["type"] = "unknown"
    entry["pages"] = 0
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        entry["pages"] = doc.page_count
        vector_pages = 0
        raster_pages = 0
        sample = min(doc.page_count, 6)
        for i in range(sample):
            page = doc[i]
            paths = page.get_drawings()
            images = page.get_images()
            text = page.get_text().strip()
            if paths and len(paths) > 10:
                vector_pages += 1
            elif images and not paths:
                raster_pages += 1
            else:
                vector_pages += 1  # treat text-heavy as vector-like
        doc.close()
        if raster_pages == 0:
            entry["type"] = "vector"
        elif vector_pages == 0:
            entry["type"] = "raster"
        else:
            entry["type"] = "mixed"
    except Exception as exc:
        entry["type"] = "unknown"
        entry["warning"] = f"PDF classification failed: {exc}"
        log.warning("PDF classification failed for %s: %s", path.name, exc)
    return entry


def _looks_like_bom(filename: str) -> bool:
    kw = ["bom", "bill", "material", "framecad", "schedule", "takeoff", "take-off"]
    fn = filename.lower()
    return any(k in fn for k in kw)


def _log_summary(result: dict) -> None:
    log.info(
        "Files found — DWG:%d  DXF:%d  PDF:%d  IFC:%d  BOM:%d  other:%d",
        len(result["dwg"]), len(result["dxf"]), len(result["pdf"]),
        len(result["ifc"]), len(result["bom"]), len(result["other"]),
    )
    for w in result["warnings"]:
        log.warning(w)
