"""
titleblock_detector.py — Extract project metadata from title blocks using AI vision.
"""

from __future__ import annotations
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from src import ai_client
from src.config import OUTPUT_LOGS, STANDARD_MODELS_MAP
from src.utils import normalise_text, save_json, timestamp_str

log = logging.getLogger("boq.titleblock")

_PROMPT_TITLEBLOCK = """Review this construction drawing page as a quantity surveyor.
Extract only title block / drawing metadata that is explicitly visible.

Return JSON only with this schema:
{
  "project_name": "",
  "drawing_number": "",
  "drawing_title": "",
  "revision": "",
  "date": "",
  "scale": "",
  "house_type_detected": "",
  "house_type_confidence": "HIGH|MEDIUM|LOW|UNKNOWN",
  "highset_detected": null,
  "laundry_location_detected": "",
  "discipline": "architectural|structural|services|unknown",
  "page_scope_summary": "",
  "warnings": []
}

If a field is not visible, use "" or null.
Do not guess — only extract what is explicitly readable.
For house_type_detected: look for G-range codes like G303, G403E, G404, G504E, G302, G202, G201.
For highset_detected: true if "highset", "raised", "stumped" visible; false if "slab" or "on-ground" visible; null if unclear.
"""


def detect_titleblock(pdf_files: list[dict]) -> dict[str, Any]:
    """Scan first 1-2 pages of each PDF to detect project metadata.

    Returns combined detection result with best-confidence values.
    """
    result: dict[str, Any] = {
        "project_name": "",
        "house_type_detected": "",
        "house_type_confidence": "UNKNOWN",
        "highset_detected": None,
        "laundry_location_detected": "",
        "drawings": [],
        "warnings": [],
    }

    if not ai_client.is_available():
        result["warnings"].append("AI not available — title block detection skipped")
        return result

    if not pdf_files:
        result["warnings"].append("No PDF files for title block detection")
        return result

    for pdf_entry in pdf_files[:3]:  # Limit to first 3 PDFs
        path = Path(pdf_entry["path"])
        pages = pdf_entry.get("pages", 0)
        if pages == 0:
            continue
        _scan_pdf_titleblock(path, min(pages, 3), result)

    _resolve_best(result)
    log.info(
        "Title block: project=%r  type=%r  highset=%s  confidence=%s",
        result["project_name"], result["house_type_detected"],
        result["highset_detected"], result["house_type_confidence"],
    )
    return result


def _scan_pdf_titleblock(pdf_path: Path, max_pages: int, result: dict) -> None:
    """Render pages to PNG and send to AI for extraction."""
    try:
        import fitz
    except ImportError:
        result["warnings"].append("pymupdf not installed — titleblock vision skipped")
        return

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        result["warnings"].append(f"Cannot open {pdf_path.name}: {exc}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        for page_no in range(min(max_pages, doc.page_count)):
            try:
                page = doc[page_no]
                pix = page.get_pixmap(dpi=120)
                img_path = Path(tmp) / f"page_{page_no}.png"
                pix.save(str(img_path))
                raw = ai_client.call_json(
                    user_prompt=_PROMPT_TITLEBLOCK,
                    images=[img_path],
                    label=f"titleblock_{pdf_path.stem}_p{page_no}",
                    max_tokens=1024,
                )
                if raw and isinstance(raw, dict):
                    _merge_titleblock_result(raw, result, pdf_path.name, page_no + 1)
            except Exception as exc:
                result["warnings"].append(
                    f"{pdf_path.name} page {page_no + 1}: {exc}"
                )
    doc.close()


def _merge_titleblock_result(raw: dict, result: dict, source: str, page: int) -> None:
    """Merge one page's detection into the running result."""
    drawing: dict = {
        "source": source,
        "page": page,
        "drawing_number": raw.get("drawing_number", ""),
        "drawing_title": raw.get("drawing_title", ""),
        "revision": raw.get("revision", ""),
        "discipline": raw.get("discipline", "unknown"),
        "page_scope_summary": raw.get("page_scope_summary", ""),
    }
    result["drawings"].append(drawing)

    # Best project name: first non-empty
    if not result["project_name"] and raw.get("project_name"):
        result["project_name"] = raw["project_name"]

    # Best house type: prefer HIGH confidence
    new_type = raw.get("house_type_detected", "")
    new_conf = raw.get("house_type_confidence", "UNKNOWN")
    if new_type and _conf_rank(new_conf) > _conf_rank(result["house_type_confidence"]):
        result["house_type_detected"] = new_type
        result["house_type_confidence"] = new_conf

    # Highset: first explicit answer wins
    if result["highset_detected"] is None and raw.get("highset_detected") is not None:
        result["highset_detected"] = raw["highset_detected"]

    if not result["laundry_location_detected"] and raw.get("laundry_location_detected"):
        result["laundry_location_detected"] = raw["laundry_location_detected"]

    for w in raw.get("warnings", []):
        result["warnings"].append(f"{source} p{page}: {w}")


def _resolve_best(result: dict) -> None:
    """Normalise house type to a known G-range code if possible."""
    ht = normalise_text(result.get("house_type_detected", ""))
    for code in STANDARD_MODELS_MAP:
        if code.lower() in ht:
            result["house_type_detected"] = code
            return
    # Fuzzy: look for G + digits pattern
    m = re.search(r"g[\s\-]?(\d{3}[a-z]?)", ht)
    if m:
        candidate = "G" + m.group(1).upper()
        if candidate in STANDARD_MODELS_MAP:
            result["house_type_detected"] = candidate


def _conf_rank(c: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(c.upper(), 0)
