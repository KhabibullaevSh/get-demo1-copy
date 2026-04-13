"""
pdf_region_detector.py — Rasterize and crop detected schedule regions from PDFs.

Takes the table/schedule region candidates from pdf_graphics_analyzer and
produces cropped raster images at high DPI for downstream OCR.

Strategy:
  - Only rasterize HIGH and MEDIUM confidence regions (skip LOW to save compute)
  - Rasterize at 300 DPI
  - Add a margin around each detected region (captures header rows / borders)
  - Return image bytes (PNG) + region metadata for each crop

For pages with no text and low/no graphics structure (pure vector CAD without
schedule tables), documents the situation without attempting OCR.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("boq.v3.pdf_region_detector")

# Rasterization resolution
_DPI = 300
# Margin to add around each detected region (points)
_MARGIN_PT = 20.0
# Skip regions smaller than this (likely just border lines, not schedules)
_MIN_AREA_MM2 = 5000.0   # 50mm × 100mm minimum


def detect_and_crop_regions(
    pdf_path: str | Path,
    graphics_result: dict,
    min_confidence: str = "MEDIUM",
) -> dict:
    """
    Rasterize and crop all detected schedule regions from *pdf_path*.

    Args:
        pdf_path:        Path to the PDF file.
        graphics_result: Output from pdf_graphics_analyzer.analyze_pdf_graphics().
        min_confidence:  Only process regions at or above this confidence level
                         ("HIGH", "MEDIUM", "LOW").

    Returns:
    {
      "pdf_file":       str,
      "crops":          list[dict]  — {page_num, region_type, bbox_pt, image_bytes, ...}
      "skipped_pages":  list[dict]  — pages that had no viable regions
      "notes":          list[str]
    }
    """
    result: dict = {
        "pdf_file":      str(pdf_path),
        "crops":         [],
        "skipped_pages": [],
        "notes":         [],
    }

    conf_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    min_rank  = conf_rank.get(min_confidence, 1)

    try:
        import fitz
    except ImportError:
        result["notes"].append("PyMuPDF (fitz) not installed — region rasterization skipped")
        return result

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        result["notes"].append(f"Failed to open PDF for rasterization: {exc}")
        return result

    page_regions: dict[int, list[dict]] = {}
    for region in graphics_result.get("all_regions", []):
        conf = region.get("confidence", "LOW")
        if conf_rank.get(conf, 2) > min_rank:
            continue
        area_mm2 = region.get("width_mm", 0) * region.get("height_mm", 0)
        if area_mm2 < _MIN_AREA_MM2:
            continue
        pn = region.get("page_num", 0)
        page_regions.setdefault(pn, []).append(region)

    if not page_regions:
        result["notes"].append(
            f"No regions met the minimum confidence ({min_confidence}) / size threshold "
            f"for rasterization in {Path(pdf_path).name}."
        )
        for pg_result in graphics_result.get("pages", []):
            result["skipped_pages"].append({
                "page_num":   pg_result["page_num"],
                "reason":     "no_viable_regions",
                "has_text":   pg_result.get("has_text", False),
                "h_segments": pg_result.get("h_segments", 0),
                "v_segments": pg_result.get("v_segments", 0),
            })
        doc.close()
        return result

    # DPI → fitz zoom matrix
    zoom  = _DPI / 72.0
    mat   = fitz.Matrix(zoom, zoom)

    for page_num, regions in page_regions.items():
        if page_num >= len(doc):
            continue
        page = doc[page_num]
        page_rect = page.rect

        for region in regions:
            bbox = region.get("bbox_pt", [])
            if len(bbox) != 4:
                continue
            x0, y0, x1, y1 = bbox

            # Add margin (clipped to page bounds)
            x0c = max(0,            x0 - _MARGIN_PT)
            y0c = max(0,            y0 - _MARGIN_PT)
            x1c = min(page_rect.width,  x1 + _MARGIN_PT)
            y1c = min(page_rect.height, y1 + _MARGIN_PT)

            clip_rect = fitz.Rect(x0c, y0c, x1c, y1c)
            try:
                pix = page.get_pixmap(matrix=mat, clip=clip_rect, colorspace=fitz.csGRAY)
                image_bytes = pix.tobytes("png")
            except Exception as exc:
                log.warning("Region rasterization failed on page %d: %s", page_num, exc)
                continue

            crop_info: dict = {
                "page_num":      page_num,
                "page_label":    region.get("page_label", f"page_{page_num+1}"),
                "region_type":   region.get("region_type", "unknown"),
                "confidence":    region.get("confidence", "LOW"),
                "bbox_pt":       bbox,
                "width_mm":      region.get("width_mm"),
                "height_mm":     region.get("height_mm"),
                "est_rows":      region.get("est_rows"),
                "est_cols":      region.get("est_cols"),
                "dpi":           _DPI,
                "image_bytes":   image_bytes,    # bytes — not serialized to JSON
                "image_size_px": (pix.width, pix.height),
            }
            result["crops"].append(crop_info)
            log.info(
                "Cropped region: page=%d type=%s conf=%s size=%dx%d px",
                page_num, region["region_type"], region["confidence"],
                pix.width, pix.height,
            )

    doc.close()

    # Document pages that were skipped
    processed_pages = set(page_regions.keys())
    for pg_result in graphics_result.get("pages", []):
        pn = pg_result["page_num"]
        if pn not in processed_pages:
            result["skipped_pages"].append({
                "page_num":   pn,
                "reason":     "no_qualifying_regions",
                "has_text":   pg_result.get("has_text", False),
                "h_segments": pg_result.get("h_segments", 0),
                "v_segments": pg_result.get("v_segments", 0),
            })

    result["notes"].append(
        f"{len(result['crops'])} region crops generated from {len(page_regions)} pages."
    )
    log.info(
        "Region detection complete: %s | %d crops from %d pages",
        Path(pdf_path).name, len(result["crops"]), len(page_regions),
    )
    return result
