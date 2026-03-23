"""
pdf_extractor.py — V2 PDF extractor.

Delegates to the V1 pdf_extractor for AI-assisted extraction.
Returns structured schedule / notes data.

CRITICAL: This module NEVER invents quantities.  It only surfaces what the
AI finds explicitly stated in the PDF pages.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("boq.v2.pdf_extractor")


def extract_pdf(input_dir: Path) -> dict:
    """
    Delegate to V1 PDF extractor.

    Returns a dict with rooms, finishes, doors, windows, stairs, notes, warnings.
    If V1 extractor is unavailable or fails, returns an empty result with a warning.
    """
    # Insert the V1 boq-system root onto sys.path so V1 imports resolve
    v1_root = Path(__file__).resolve().parent.parent.parent.parent
    if str(v1_root) not in sys.path:
        sys.path.insert(0, str(v1_root))

    try:
        from src.pdf_extractor import extract_pdf as v1_extract  # type: ignore
        log.info("Delegating PDF extraction to V1 extractor for %s", input_dir)
        result = v1_extract(input_dir)
        return {
            "rooms":    result.get("rooms",    []),
            "finishes": result.get("finishes", []),
            "doors":    result.get("doors",    []),
            "windows":  result.get("windows",  []),
            "stairs":   result.get("stairs",   []),
            "notes":    result.get("notes",    []),
            "source":   "pdf_ai_extraction",
            "warnings": result.get("warnings", []),
        }
    except ImportError as exc:
        msg = f"V1 pdf_extractor not importable: {exc}"
        log.warning(msg)
        return _empty(msg)
    except Exception as exc:
        msg = f"PDF extraction failed: {exc}"
        log.warning(msg)
        return _empty(msg)


def _empty(warning_msg: str) -> dict:
    return {
        "rooms":    [],
        "finishes": [],
        "doors":    [],
        "windows":  [],
        "stairs":   [],
        "notes":    [],
        "source":   "pdf_ai_extraction",
        "warnings": [warning_msg],
    }
