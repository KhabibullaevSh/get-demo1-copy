"""
dxf_extractor.py — Extract geometry from DXF using named layers.

All coordinates in the DXF are in mm.  All returned values are in metres / m².

Layer mapping (ANGAU PHARMACY 01_arch.dxf):
  WALLS      LWPOLYLINE  → floor area polygon + ext wall perimeter
  ROOF       LWPOLYLINE  → roof plan area + roof perimeter
  VERANDAH   LWPOLYLINE  → verandah area + perimeter
  FLOOR      HATCH       → floor area (cross-check)
  CEILING    HATCH       → ceiling area
  DOORS      INSERT      → door count
  WINDOWS    INSERT      → window count
  STRUCTURE  CIRCLE      → post/column count
  STAIRS     LINE        → stair line count / evidence
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

log = logging.getLogger("boq.v2.dxf_extractor")

MM_TO_M   = 1 / 1_000
MM2_TO_M2 = 1 / 1_000_000


# ─── helpers ──────────────────────────────────────────────────────────────────

def _try_shapely_polygon(pts_m: list[tuple[float, float]]):
    """Return (area_m2, perimeter_m) or (None, None) if invalid."""
    try:
        from shapely.geometry import Polygon
        poly = Polygon(pts_m)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_valid and poly.area > 0:
            return abs(poly.area), poly.length
    except Exception as exc:
        log.debug("Shapely polygon failed: %s", exc)
    return None, None


def _lwpoly_pts_m(entity) -> list[tuple[float, float]]:
    """Extract (x, y) in metres from an LWPOLYLINE entity."""
    return [(p[0] * MM_TO_M, p[1] * MM_TO_M) for p in entity.get_points("xy")]


def _hatch_areas_m2(msp, layer_name: str) -> list[float]:
    """Sum areas from HATCH boundary paths on *layer_name*."""
    areas: list[float] = []
    try:
        from shapely.geometry import Polygon
        for hatch in msp.query(f'HATCH[layer=="{layer_name}"]'):
            for path in hatch.paths:
                # LwPolylinePath
                if hasattr(path, "vertices") and path.vertices:
                    pts = [(v[0] * MM_TO_M, v[1] * MM_TO_M) for v in path.vertices]
                    if len(pts) >= 3:
                        try:
                            poly = Polygon(pts)
                            if not poly.is_valid:
                                poly = poly.buffer(0)
                            if poly.is_valid and poly.area > 0:
                                areas.append(abs(poly.area))
                        except Exception:
                            pass
                # EdgePath — approximate from edge bounding box
                elif hasattr(path, "edges") and path.edges:
                    xs, ys = [], []
                    for edge in path.edges:
                        if hasattr(edge, "start"):
                            xs.append(edge.start[0] * MM_TO_M)
                            ys.append(edge.start[1] * MM_TO_M)
                        if hasattr(edge, "end"):
                            xs.append(edge.end[0] * MM_TO_M)
                            ys.append(edge.end[1] * MM_TO_M)
                    if xs and ys:
                        w = max(xs) - min(xs)
                        h = max(ys) - min(ys)
                        if w > 0 and h > 0:
                            areas.append(w * h)
    except ImportError:
        log.warning("shapely not installed — HATCH areas unavailable")
    return areas


# ─── main extractor ────────────────────────────────────────────────────────────

def _block_base_widths(doc) -> dict[str, float]:
    """
    Pre-scan block definitions for the longest LINE entity (= nominal opening width in mm).
    Returns {block_name: width_mm} for door/window blocks.
    """
    widths: dict[str, float] = {}
    for blk in doc.blocks:
        bn = blk.name
        if not any(k in bn.upper() for k in ("DOOR", "WINDOW", "WIN_")):
            continue
        max_len = 0.0
        for ent in blk:
            try:
                if ent.dxftype() == "LINE":
                    s = ent.dxf.start
                    e = ent.dxf.end
                    ln = math.sqrt((e[0] - s[0]) ** 2 + (e[1] - s[1]) ** 2)
                    if ln > max_len:
                        max_len = ln
            except Exception:
                pass
        if max_len > 0:
            widths[bn] = round(max_len, 1)
    return widths


def extract_dxf(dxf_path: Path) -> dict:
    """
    Extract geometry from *dxf_path* using named layers.

    Returns a dict with all geometry values in metres / m².
    On failure returns a minimal dict with a warning.
    """
    warnings: list[str] = []
    result: dict = {
        "floor_area_m2":        0.0,
        "ext_wall_perimeter_m": 0.0,
        "roof_area_m2":         0.0,
        "roof_perimeter_m":     0.0,
        "verandah_area_m2":     0.0,
        "verandah_perimeter_m": 0.0,
        "ceiling_area_m2":      0.0,
        "door_count":           0,
        "window_count":         0,
        "post_count":           0,
        "stair_evidence":       False,
        "stair_line_count":     0,
        "source":               "dxf_geometry",
        "source_file":          str(dxf_path),
        "warnings":             warnings,
    }

    try:
        import ezdxf
    except ImportError:
        warnings.append("ezdxf not installed — DXF extraction skipped")
        log.error("ezdxf not installed")
        return result

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        warnings.append(f"Failed to open DXF: {exc}")
        log.error("Failed to open DXF %s: %s", dxf_path, exc)
        return result

    msp = doc.modelspace()

    # ── List available layers for diagnostics ────────────────────────────────
    layer_names = {layer.dxf.name.upper() for layer in doc.layers}
    log.info("DXF layers: %s", sorted(layer_names))

    # Pre-scan block definitions for door/window widths
    block_widths = _block_base_widths(doc)

    # Helper: find entities by layer (case-insensitive prefix matching)
    def query_layer(entity_type: str, target: str) -> list:
        matches = []
        for e in msp:
            try:
                if e.dxftype() == entity_type:
                    layer = e.dxf.get("layer", "").upper()
                    if target.upper() in layer:
                        matches.append(e)
            except Exception:
                pass
        return matches

    # ── WALLS LWPOLYLINE → floor area + ext perimeter + int wall lm ──────
    walls_polys = query_layer("LWPOLYLINE", "WALLS")
    if walls_polys:
        # First pass: find the largest closed polygon (= external wall outline)
        best_area, best_perim, best_ent = 0.0, 0.0, None
        poly_data: list[tuple] = []   # (area, perim, lm_sum, entity)
        for ent in walls_polys:
            pts = _lwpoly_pts_m(ent)
            if len(pts) >= 2:
                area, perim = _try_shapely_polygon(pts) if len(pts) >= 3 else (None, None)
                area  = area  or 0.0
                perim = perim or 0.0
                # Sum vertex-to-vertex distances (open polyline length)
                seg_lm = sum(
                    math.sqrt((pts[i+1][0]-pts[i][0])**2 + (pts[i+1][1]-pts[i][1])**2)
                    for i in range(len(pts)-1)
                )
                poly_data.append((area, perim, round(seg_lm, 3), ent))
                if area > best_area:
                    best_area  = area
                    best_perim = perim
                    best_ent   = ent

        if best_area > 0:
            result["floor_area_m2"]        = round(best_area, 3)
            result["ext_wall_perimeter_m"] = round(best_perim, 3)
            log.info("WALLS → floor_area=%.2f m², perimeter=%.2f m", best_area, best_perim)

            # Second pass: sum remaining polylines as internal wall runs
            int_lm_total = 0.0
            int_segments: list[float] = []
            for area, perim, seg_lm, ent in poly_data:
                if ent is best_ent:
                    continue   # skip external envelope
                if seg_lm > 0:
                    int_lm_total += seg_lm
                    int_segments.append(seg_lm)

            if int_lm_total > 0:
                result["int_wall_lm"]       = round(int_lm_total, 3)
                result["int_wall_segments"] = int_segments
                log.info("WALLS internal → int_wall_lm=%.2f m (%d polylines)",
                         int_lm_total, len(int_segments))
        else:
            warnings.append("WALLS layer found but no valid polygon extracted")
    else:
        warnings.append("No LWPOLYLINE entities on WALLS layer")

    # ── ROOF LWPOLYLINE ────────────────────────────────────────────────────
    roof_polys = query_layer("LWPOLYLINE", "ROOF")
    if roof_polys:
        best_area, best_perim = 0.0, 0.0
        for ent in roof_polys:
            pts = _lwpoly_pts_m(ent)
            if len(pts) >= 3:
                area, perim = _try_shapely_polygon(pts)
                if area and area > best_area:
                    best_area  = area
                    best_perim = perim or 0.0
        if best_area > 0:
            result["roof_area_m2"]    = round(best_area, 3)
            result["roof_perimeter_m"] = round(best_perim, 3)
            log.info("ROOF → area=%.2f m², perimeter=%.2f m", best_area, best_perim)
        else:
            warnings.append("ROOF layer found but no valid polygon extracted")
    else:
        warnings.append("No LWPOLYLINE entities on ROOF layer")

    # ── VERANDAH LWPOLYLINE ────────────────────────────────────────────────
    verandah_polys = query_layer("LWPOLYLINE", "VERANDAH")
    if verandah_polys:
        best_area, best_perim = 0.0, 0.0
        for ent in verandah_polys:
            pts = _lwpoly_pts_m(ent)
            if len(pts) >= 3:
                area, perim = _try_shapely_polygon(pts)
                if area and area > best_area:
                    best_area  = area
                    best_perim = perim or 0.0
        if best_area > 0:
            result["verandah_area_m2"]    = round(best_area, 3)
            result["verandah_perimeter_m"] = round(best_perim, 3)
            log.info("VERANDAH → area=%.2f m², perimeter=%.2f m", best_area, best_perim)
        else:
            warnings.append("VERANDAH layer found but no valid polygon extracted")
    else:
        warnings.append("No LWPOLYLINE entities on VERANDAH layer")

    # ── FLOOR HATCH → floor area cross-check ──────────────────────────────
    floor_areas = _hatch_areas_m2(msp, "FLOOR")
    if floor_areas:
        result["floor_hatch_area_m2"] = round(sum(floor_areas), 3)
        log.info("FLOOR HATCH → %.2f m² (%d patches)", sum(floor_areas), len(floor_areas))

    # ── CEILING HATCH ──────────────────────────────────────────────────────
    ceiling_areas = _hatch_areas_m2(msp, "CEILING")
    if ceiling_areas:
        result["ceiling_area_m2"] = round(sum(ceiling_areas), 3)
        log.info("CEILING HATCH → %.2f m²", sum(ceiling_areas))
    else:
        # Fallback: ceiling ≈ floor area
        if result["floor_area_m2"] > 0:
            result["ceiling_area_m2"] = result["floor_area_m2"]
            warnings.append("No CEILING hatch found — ceiling_area_m2 set equal to floor_area_m2")

    # ── DOORS INSERT ───────────────────────────────────────────────────────
    door_inserts_raw = query_layer("INSERT", "DOORS")
    if door_inserts_raw:
        result["door_count"] = len(door_inserts_raw)
        inserts = []
        for e in door_inserts_raw:
            bn = e.dxf.get("name", "")
            xs = e.dxf.get("xscale", 1.0)
            base_w = block_widths.get(bn, 0.0)
            width_m = round(base_w * xs / 1000, 3) if base_w > 0 else 0.0
            inserts.append({
                "block_name":    bn,
                "insert_x_m":   round(e.dxf.insert[0] * MM_TO_M, 3),
                "insert_y_m":   round(e.dxf.insert[1] * MM_TO_M, 3),
                "rotation_deg": round(e.dxf.get("rotation", 0.0), 1),
                "width_m":      width_m,
                "xscale":       round(xs, 4),
            })
        result["door_inserts"] = inserts
        log.info("DOORS INSERT → %d doors (widths: %s)",
                 result["door_count"],
                 ", ".join(f"{i['block_name']}={i['width_m']:.3f}m" for i in inserts))
    else:
        warnings.append("No INSERT entities on DOORS layer")

    # ── WINDOWS INSERT ─────────────────────────────────────────────────────
    window_inserts_raw = query_layer("INSERT", "WINDOWS")
    if window_inserts_raw:
        result["window_count"] = len(window_inserts_raw)
        inserts = []
        for e in window_inserts_raw:
            bn = e.dxf.get("name", "")
            xs = e.dxf.get("xscale", 1.0)
            base_w = block_widths.get(bn, 0.0)
            width_m = round(base_w * xs / 1000, 3) if base_w > 0 else 0.0
            inserts.append({
                "block_name":    bn,
                "insert_x_m":   round(e.dxf.insert[0] * MM_TO_M, 3),
                "insert_y_m":   round(e.dxf.insert[1] * MM_TO_M, 3),
                "rotation_deg": round(e.dxf.get("rotation", 0.0), 1),
                "width_m":      width_m,
                "xscale":       round(xs, 4),
            })
        result["window_inserts"] = inserts
        # Log unique widths
        widths_seen = sorted(set(i["width_m"] for i in inserts if i["width_m"] > 0))
        log.info("WINDOWS INSERT → %d windows (unique widths: %s)",
                 result["window_count"],
                 ", ".join(f"{w:.3f}m" for w in widths_seen))
    else:
        warnings.append("No INSERT entities on WINDOWS layer")

    # ── STRUCTURE CIRCLE → post count + positions ─────────────────────────
    struct_circles = query_layer("CIRCLE", "STRUCTURE")
    if struct_circles:
        result["post_count"] = len(struct_circles)
        result["post_positions"] = [
            {
                "x_m": round(e.dxf.center[0] * MM_TO_M, 3),
                "y_m": round(e.dxf.center[1] * MM_TO_M, 3),
            }
            for e in struct_circles
        ]
        log.info("STRUCTURE CIRCLE → %d posts", result["post_count"])
    else:
        warnings.append("No CIRCLE entities on STRUCTURE layer")

    # ── STAIRS LINE ────────────────────────────────────────────────────────
    stair_lines = query_layer("LINE", "STAIRS")
    if stair_lines:
        result["stair_evidence"]   = True
        result["stair_line_count"] = len(stair_lines)
        log.info("STAIRS LINE → %d lines (stair evidence)", len(stair_lines))
    else:
        warnings.append("No LINE entities on STAIRS layer")

    return result
