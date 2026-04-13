"""
pdf_graphics_analyzer.py — Detect schedule/table regions from vector PDF drawings.

For each page, analyzes the PDF drawing commands to find clusters of horizontal
and vertical lines that form table grids. These regions are candidates for:
  - Window/door schedule tables
  - Room finish schedules
  - Detail/note tables
  - Title blocks with project data

Does NOT perform OCR — just detects WHERE schedule regions are.
Returns structured region descriptors for downstream rasterization + OCR.

Strategy:
  1. For each page: extract all path/line drawing objects via PyMuPDF (fitz)
  2. Classify segments as H-line, V-line, or diagonal
  3. Build a spatial grid: divide page into cells, count H/V lines per cell
  4. Find cells with both H and V line density above threshold → table candidates
  5. Merge adjacent high-density cells into bounding boxes
  6. Return regions sorted by size and density (largest/densest = most likely schedules)
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("boq.v3.pdf_graphics_analyzer")

# Minimum segment length to count (filters out tiny fill artifacts)
_MIN_SEGMENT_MM = 5.0
# Threshold: minimum H and V line count in a grid cell to flag as potential table
_H_DENSITY_THRESHOLD = 3
_V_DENSITY_THRESHOLD = 2
# Grid cell size for density analysis (in PDF points; 72pt = 1 inch ≈ 25.4mm)
_GRID_CELL_PT = 72   # 1-inch grid cells


def analyze_pdf_graphics(pdf_path: str | Path) -> dict:
    """
    Analyze vector drawing content of *pdf_path* and detect table/schedule regions.

    Returns:
    {
      "pdf_file":    str,
      "pages":       list[dict]  — per-page analysis
      "all_regions": list[dict]  — all table candidates across all pages, sorted by confidence
      "notes":       list[str]
    }
    """
    result: dict = {
        "pdf_file":    str(pdf_path),
        "pages":       [],
        "all_regions": [],
        "notes":       [],
    }

    try:
        import fitz  # PyMuPDF
    except ImportError:
        result["notes"].append("PyMuPDF (fitz) not installed — PDF graphics analysis skipped")
        log.warning("PyMuPDF not installed — PDF graphics analysis skipped")
        return result

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        result["notes"].append(f"Failed to open PDF: {exc}")
        log.error("Failed to open PDF %s: %s", pdf_path, exc)
        return result

    for page_num, page in enumerate(doc):
        page_result = _analyze_page(page, page_num)
        result["pages"].append(page_result)
        for region in page_result.get("table_candidates", []):
            region["page_num"]    = page_num
            region["page_label"]  = page_result.get("page_label", f"page_{page_num+1}")
            result["all_regions"].append(region)

    doc.close()

    # Sort all regions: HIGH confidence first, then by line density
    result["all_regions"].sort(
        key=lambda r: (0 if r["confidence"] == "HIGH" else
                       1 if r["confidence"] == "MEDIUM" else 2,
                       -(r.get("h_line_count", 0) + r.get("v_line_count", 0)))
    )

    log.info(
        "PDF graphics analysis: %s | %d pages | %d table candidates",
        Path(pdf_path).name, len(result["pages"]), len(result["all_regions"]),
    )
    return result


def _analyze_page(page, page_num: int) -> dict:
    """Analyze a single PDF page for line clusters."""
    import fitz

    rect = page.rect
    page_w = rect.width
    page_h = rect.height
    scale_pt = 72 / 72.0   # points per point (identity, but explicit)

    page_result: dict = {
        "page_num":          page_num,
        "page_label":        f"page_{page_num + 1}",
        "width_pt":          round(page_w, 1),
        "height_pt":         round(page_h, 1),
        "has_text":          False,
        "text_char_count":   0,
        "h_segments":        0,
        "v_segments":        0,
        "total_segments":    0,
        "table_candidates":  [],
    }

    # ── Check if page has embedded text ────────────────────────────────────
    text = page.get_text("text")
    text_stripped = text.strip()
    page_result["text_char_count"] = len(text_stripped)
    page_result["has_text"]        = len(text_stripped) > 10
    # Store raw text for pages with substantial embedded text (used by
    # reconciler to extract FrameCAD panel dimension labels etc.)
    page_result["page_text"] = text_stripped if len(text_stripped) > 50 else ""

    # ── Extract drawing paths ──────────────────────────────────────────────
    h_segments: list[tuple] = []  # (x0, y0, x1, y1)
    v_segments: list[tuple] = []

    try:
        drawings = page.get_drawings()
    except AttributeError:
        # Older fitz versions
        drawings = []

    for draw in drawings:
        for item in draw.get("items", []):
            if item[0] == "l":       # line segment
                x0, y0 = item[1].x, item[1].y
                x1, y1 = item[2].x, item[2].y
                dx = abs(x1 - x0)
                dy = abs(y1 - y0)
                length = (dx**2 + dy**2)**0.5
                # Convert mm threshold to points (1pt = 25.4/72 mm ≈ 0.353mm)
                min_len_pt = _MIN_SEGMENT_MM / 0.353
                if length < min_len_pt:
                    continue
                # Classify
                if dy < dx * 0.1:    # nearly horizontal
                    h_segments.append((x0, y0, x1, y1))
                elif dx < dy * 0.1:  # nearly vertical
                    v_segments.append((x0, y0, x1, y1))
            elif item[0] == "re":    # rectangle → 4 implicit lines
                rect_r = item[1]
                x0, y0 = rect_r.x0, rect_r.y0
                x1, y1 = rect_r.x1, rect_r.y1
                w = abs(x1 - x0)
                h = abs(y1 - y0)
                min_len_pt = _MIN_SEGMENT_MM / 0.353
                if w >= min_len_pt:
                    h_segments.append((x0, y0, x1, y0))  # top
                    h_segments.append((x0, y1, x1, y1))  # bottom
                if h >= min_len_pt:
                    v_segments.append((x0, y0, x0, y1))  # left
                    v_segments.append((x1, y0, x1, y1))  # right

    page_result["h_segments"] = len(h_segments)
    page_result["v_segments"] = len(v_segments)
    page_result["total_segments"] = len(h_segments) + len(v_segments)

    if not h_segments and not v_segments:
        return page_result

    # ── Grid density analysis ──────────────────────────────────────────────
    cell_size = float(_GRID_CELL_PT)
    cols = max(1, int(page_w / cell_size) + 1)
    rows = max(1, int(page_h / cell_size) + 1)

    h_grid = [[0] * cols for _ in range(rows)]
    v_grid = [[0] * cols for _ in range(rows)]

    for x0, y0, x1, y1 in h_segments:
        # Mark all cells the segment crosses
        cx0 = int(min(x0, x1) / cell_size)
        cx1 = int(max(x0, x1) / cell_size)
        cy  = int((y0 + y1) / 2 / cell_size)
        if 0 <= cy < rows:
            for cx in range(max(0, cx0), min(cx1 + 1, cols)):
                h_grid[cy][cx] += 1

    for x0, y0, x1, y1 in v_segments:
        cx  = int((x0 + x1) / 2 / cell_size)
        cy0 = int(min(y0, y1) / cell_size)
        cy1 = int(max(y0, y1) / cell_size)
        if 0 <= cx < cols:
            for cy in range(max(0, cy0), min(cy1 + 1, rows)):
                v_grid[cy][cx] += 1

    # Find cells above threshold
    hot_cells: list[tuple] = []  # (row, col)
    for r in range(rows):
        for c in range(cols):
            if (h_grid[r][c] >= _H_DENSITY_THRESHOLD and
                    v_grid[r][c] >= _V_DENSITY_THRESHOLD):
                hot_cells.append((r, c))

    if not hot_cells:
        return page_result

    # ── Merge adjacent hot cells into regions ──────────────────────────────
    regions = _merge_hot_cells(hot_cells, h_grid, v_grid, cell_size, page_h)
    page_result["table_candidates"] = regions
    return page_result


def _merge_hot_cells(
    hot_cells: list[tuple],
    h_grid: list[list[int]],
    v_grid: list[list[int]],
    cell_size: float,
    page_h: float,
) -> list[dict]:
    """Merge adjacent hot cells into bounding-box regions."""
    if not hot_cells:
        return []

    hot_set = set(hot_cells)
    visited = set()
    regions: list[dict] = []

    def _flood(r0: int, c0: int) -> list[tuple]:
        """BFS flood-fill from (r0,c0) through adjacent hot cells."""
        queue = [(r0, c0)]
        component = []
        while queue:
            r, c = queue.pop()
            if (r, c) in visited or (r, c) not in hot_set:
                continue
            visited.add((r, c))
            component.append((r, c))
            for dr, dc in ((-1,0),(1,0),(0,-1),(0,1)):
                nb = (r + dr, c + dc)
                if nb in hot_set and nb not in visited:
                    queue.append(nb)
        return component

    for cell in hot_cells:
        if cell in visited:
            continue
        component = _flood(cell[0], cell[1])
        if not component:
            continue

        min_r = min(rc[0] for rc in component)
        max_r = max(rc[0] for rc in component)
        min_c = min(rc[1] for rc in component)
        max_c = max(rc[1] for rc in component)

        # Bounding box in PDF points (y is top-down in fitz)
        x0 = min_c * cell_size
        x1 = (max_c + 1) * cell_size
        y0 = min_r * cell_size
        y1 = (max_r + 1) * cell_size

        total_h = sum(h_grid[r][c] for r, c in component)
        total_v = sum(v_grid[r][c] for r, c in component)

        # Estimate row/col count from line counts
        row_span = (max_r - min_r + 1)
        col_span = (max_c - min_c + 1)
        est_rows = max(1, total_h // max(1, col_span))
        est_cols = max(1, total_v // max(1, row_span))

        # Size in mm (1 pt = 25.4/72 mm)
        pt_to_mm = 25.4 / 72
        w_mm = round((x1 - x0) * pt_to_mm, 1)
        h_mm = round((y1 - y0) * pt_to_mm, 1)
        area_mm2 = w_mm * h_mm

        # Confidence based on line density and region size
        if total_h >= 10 and total_v >= 6 and area_mm2 >= 10000:
            confidence = "HIGH"
            region_type = "schedule_table"
        elif total_h >= 5 and total_v >= 3:
            confidence = "MEDIUM"
            region_type = "possible_table"
        else:
            confidence = "LOW"
            region_type = "line_cluster"

        regions.append({
            "bbox_pt":       [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            "width_mm":      w_mm,
            "height_mm":     h_mm,
            "h_line_count":  total_h,
            "v_line_count":  total_v,
            "est_rows":      est_rows,
            "est_cols":      est_cols,
            "region_type":   region_type,
            "confidence":    confidence,
        })

    # Sort by area (largest first)
    regions.sort(key=lambda r: r["width_mm"] * r["height_mm"], reverse=True)
    return regions
