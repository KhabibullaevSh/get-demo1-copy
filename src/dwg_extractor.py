"""
dwg_extractor.py — Extract geometry from DWG/DXF files using ezdxf.

Rebuilt (Step 3) based on DXF audit of SDP 3-BEDROOM ARC.dxf:
  Verified layers: WALLS, ROOF, VERANDAH, DOORS, WINDOWS, STRUCTURE, STAIRS,
                   CEILING, FLOOR  (TEXT layer is defined but empty)

Extraction strategy per layer:
  WALLS     — closed LWPOLYLINE = building outline (floor area + ext perimeter);
               open LWPOLYLINE/LINE = internal walls (sum = int wall length)
  ROOF      — closed LWPOLYLINE = roof outline (area + perimeter)
  VERANDAH  — closed LWPOLYLINE = verandah outline (area + bounding box dims)
  DOORS     — INSERT blocks; block name DOOR_<N> where N×10 = leaf mm
               (e.g. DOOR_82 → 820mm, DOOR_90 → 900mm, DOOR_72 → 720mm)
  WINDOWS   — INSERT blocks (WINDOW_LOUVRE); x-scale encodes type:
               sx≈1.0 → Window A (1080mm), sx≈0.74 → Window B (800mm),
               sx≈1.71 → Window D (1850mm)
  STRUCTURE — CIRCLE entities = posts/piers (count only)
  STAIRS    — LINE entities = stair stringers/treads (count lines)
  CEILING   — HATCH entity (backup ceiling area)
  FLOOR     — HATCH entity (backup floor area)

Note: No TEXT or DIMENSION entities exist in this DXF file.
      All schedule data (room names, door/window schedule) must come from PDFs.
"""

from __future__ import annotations
import logging
import math
import re
from pathlib import Path
from typing import Any

from src.config import Confidence
from src.utils import safe_float

log = logging.getLogger("boq.dwg_extractor")

# ─── Exact layer names (verified from DXF audit) ─────────────────────────────
_L_WALLS     = "WALLS"
_L_ROOF      = "ROOF"
_L_VERANDAH  = "VERANDAH"
_L_DOORS     = "DOORS"
_L_WINDOWS   = "WINDOWS"
_L_STRUCTURE = "STRUCTURE"
_L_STAIRS    = "STAIRS"
_L_CEILING   = "CEILING"
_L_FLOOR     = "FLOOR"

# Keyword fallbacks for DXF files with different layer naming conventions
WALL_KW      = {"wall", "w-", "partition", "a-wall", "ext_wall", "int_wall"}
DOOR_KW      = {"door", "d-"}
WINDOW_KW    = {"window", "win-", "_w_", "wnd", "louvre"}
ROOF_KW      = {"roof", "rfg", "r-"}
FLOOR_KW     = {"floor", "flr", "slab", "ground", "f-"}
STAIR_KW     = {"stair", "step", "riser", "tread"}
POST_KW      = {"post", "pier", "column", "col", "struct"}
VERANDAH_KW  = {"verandah", "veranda", "porch", "deck", "balcony"}
CEILING_KW   = {"ceiling", "ceil", "clg"}
ROOM_KW      = {"room", "bed", "bath", "kitchen", "living", "laundry", "dining",
                "corridor", "hall", "store", "toilet", "wc"}

# ─── Door leaf width → BOQ type mapping ──────────────────────────────────────
# Based on PDF schedule A-017: Door B=920mm, Door C=820mm, Door D=720mm
DOOR_WIDTH_MAP = {
    (870, 950): "Door B",   # 900mm block → 920mm leaf
    (790, 870): "Door C",   # 820mm leaf
    (690, 790): "Door D",   # 720mm leaf
    (950, 1100): "Door A",  # wider doors / sliding
}

# ─── Window type by x-scale on WINDOW_LOUVRE block ───────────────────────────
# Verified from insert positions in DXF audit:
#   sx=1.000 → Window A (1080mm base)
#   sx=0.740 → Window B (1080×0.74 = 799mm ≈ 800mm)
#   sx=1.710 → Window D (1080×1.71 = 1847mm ≈ 1850mm)
_WINDOW_LOUVRE_BASE_MM = 1080

_WINDOW_SCALE_TYPES: list[tuple] = [
    # (sx_min, sx_max, type_char, width_mm, height_mm)
    (0.90, 1.10, "A", 1080, 1200),
    (0.60, 0.89, "B",  800,  620),
    (1.50, 2.00, "D", 1850, 1200),
]

WINDOW_WIDTH_MAP = {
    (1050, 1160): "Window A",
    (750,  870):  "Window B",
    (1750, 1950): "Window D",
}


# ─── Main entry point ─────────────────────────────────────────────────────────

def extract_geometry(drawing_path: str | Path) -> dict[str, Any]:
    """Extract geometry from DXF/DWG using audit-verified layer structure.

    Returns a dict with: source_file, summary, rooms, doors, windows,
    posts, stairs, dimensions, warnings.
    """
    path = Path(drawing_path)
    result: dict[str, Any] = {
        "source_file": path.name,
        "summary": {},
        "rooms": [],
        "doors": [],
        "windows": [],
        "posts": [],
        "stairs": [],
        "dimensions": [],
        "warnings": [],
    }

    dxf_path = path
    if path.suffix.lower() == ".dwg":
        dxf_path = _try_convert_dwg(path, result)
        if dxf_path is None:
            result["warnings"].append(f"DWG conversion failed for {path.name}.")
            return result

    try:
        import ezdxf
    except ImportError:
        result["warnings"].append("ezdxf not installed — DWG/DXF extraction skipped")
        return result

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        result["warnings"].append(f"Cannot read DXF {dxf_path.name}: {exc}")
        return result

    msp = doc.modelspace()
    entities = list(msp)

    _extract_walls(entities, result)
    _extract_roof(entities, result)
    _extract_rooms_areas(entities, result)
    _extract_doors(entities, doc, result)
    _extract_windows(entities, doc, result)
    _extract_posts(entities, result)
    _extract_stairs(entities, result)
    _extract_dimensions(entities, result)
    _extract_roof_pitch(entities, result)
    _build_summary(result)

    log.info(
        "DXF extract: ext_wall=%.1fm  int_wall=%.1fm  floor=%.1fm²  "
        "doors=%d  windows=%d  posts=%d",
        result["summary"].get("external_wall_length_m", 0),
        result["summary"].get("internal_wall_length_m", 0),
        result["summary"].get("total_floor_area_m2", 0),
        len(result["doors"]),
        len(result["windows"]),
        len(result["posts"]),
    )
    return result


# ─── Walls ────────────────────────────────────────────────────────────────────

def _extract_walls(entities: list, result: dict) -> None:
    """
    WALLS layer:
      - Closed LWPOLYLINE (flags & 1 OR first==last point) → building outline
        → floor area (shoelace), external perimeter, building bounding-box dims
      - Open LWPOLYLINE / LINE → internal wall segments → sum = int wall length
    Falls back to keyword matching if exact layer name finds nothing.
    """
    def _on_wall_layer(e) -> bool:
        layer = (e.dxf.layer or "").upper()
        return layer == _L_WALLS or _layer_has_kw(layer, WALL_KW)

    wall_ents = [e for e in entities if _on_wall_layer(e)
                 and e.dxftype() in ("LWPOLYLINE", "POLYLINE", "LINE")]

    if not wall_ents:
        result["warnings"].append("No wall entities found")
        return

    closed_polys = []
    open_segs_mm: list[float] = []  # lengths in mm

    for e in wall_ents:
        if e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            if _is_closed_poly(e):
                closed_polys.append(e)
            else:
                segs = _entity_to_segments(e)
                open_segs_mm.extend(s["length"] for s in segs)
        elif e.dxftype() == "LINE":
            segs = _entity_to_segments(e)
            open_segs_mm.extend(s["length"] for s in segs)

    # Closed polyline = building outline
    if closed_polys:
        # Pick largest area as the building footprint outline
        outline = max(closed_polys, key=lambda e: (_polygon_area(e) or 0.0))
        area_mm2 = _polygon_area(outline) or 0.0
        floor_area_m2 = area_mm2 / 1e6

        pts = _entity_to_points(outline)
        n = len(pts)
        perim_mm = sum(
            math.hypot(pts[(i + 1) % n][0] - pts[i][0],
                       pts[(i + 1) % n][1] - pts[i][1])
            for i in range(n)
        )
        ext_wall_m = perim_mm / 1000.0

        all_x = [p[0] for p in pts]
        all_y = [p[1] for p in pts]
        if all_x and all_y:
            span_x = (max(all_x) - min(all_x)) / 1000.0
            span_y = (max(all_y) - min(all_y)) / 1000.0
            result["summary"]["building_length_m"] = round(max(span_x, span_y), 2)
            result["summary"]["building_width_m"]  = round(min(span_x, span_y), 2)

        result["summary"]["total_floor_area_m2"]    = round(floor_area_m2, 2)
        result["summary"]["external_wall_length_m"] = round(ext_wall_m, 2)
        result["summary"]["wall_confidence"]        = Confidence.HIGH.value
    else:
        result["warnings"].append("No closed wall polyline — building outline not extracted")

    # Open polylines + lines = internal walls
    int_wall_m = sum(open_segs_mm) / 1000.0
    result["summary"]["internal_wall_length_m"] = round(int_wall_m, 2)
    result["summary"]["total_wall_length_m"] = round(
        result["summary"].get("external_wall_length_m", 0) + int_wall_m, 2
    )


# ─── Roof ─────────────────────────────────────────────────────────────────────

def _extract_roof(entities: list, result: dict) -> None:
    """
    ROOF layer:  closed LWPOLYLINE → roof outline area + perimeter
    VERANDAH layer: closed LWPOLYLINE → verandah area + bounding-box dimensions
    """
    def _on_layer(e, exact: str, kws: set) -> bool:
        layer = (e.dxf.layer or "").upper()
        return layer == exact or _layer_has_kw(layer, kws)

    # ── Roof outline ──────────────────────────────────────────────────────────
    roof_ents = [e for e in entities
                 if _on_layer(e, _L_ROOF, ROOF_KW)
                 and e.dxftype() == "LWPOLYLINE"]

    if roof_ents:
        largest = max(roof_ents, key=lambda e: (_polygon_area(e) or 0.0))
        roof_area = (_polygon_area(largest) or 0.0) / 1e6
        pts = _entity_to_points(largest)
        n = len(pts)
        roof_perim_mm = sum(
            math.hypot(pts[(i + 1) % n][0] - pts[i][0],
                       pts[(i + 1) % n][1] - pts[i][1])
            for i in range(n)
        )
        result["summary"]["roof_area_m2"]     = round(roof_area, 2)
        result["summary"]["roof_perimeter_m"] = round(roof_perim_mm / 1000.0, 2)
        result["summary"]["roof_confidence"]  = Confidence.HIGH.value

        # Building dims from roof bounding box (includes overhangs)
        all_x = [p[0] for p in pts]
        all_y = [p[1] for p in pts]
        if all_x and "building_length_m" not in result["summary"]:
            span_x = (max(all_x) - min(all_x)) / 1000.0
            span_y = (max(all_y) - min(all_y)) / 1000.0
            result["summary"]["building_length_m"] = round(max(span_x, span_y), 2)
            result["summary"]["building_width_m"]  = round(min(span_x, span_y), 2)
    else:
        floor = result["summary"].get("total_floor_area_m2", 0)
        if floor > 0:
            result["summary"]["roof_area_m2"]    = round(floor * 1.25, 2)
            result["summary"]["roof_confidence"] = Confidence.LOW.value
            result["warnings"].append("Roof area estimated from floor × 1.25 (no ROOF layer)")

    # ── Verandah outline ──────────────────────────────────────────────────────
    ver_ents = [e for e in entities
                if _on_layer(e, _L_VERANDAH, VERANDAH_KW)
                and e.dxftype() == "LWPOLYLINE"]

    if ver_ents:
        ver = max(ver_ents, key=lambda e: (_polygon_area(e) or 0.0))
        ver_area = (_polygon_area(ver) or 0.0) / 1e6
        result["summary"]["verandah_area_m2"] = round(ver_area, 2)

        ver_pts = _entity_to_points(ver)
        if ver_pts:
            vx = [p[0] for p in ver_pts]
            vy = [p[1] for p in ver_pts]
            vs_x = (max(vx) - min(vx)) / 1000.0
            vs_y = (max(vy) - min(vy)) / 1000.0
            result["summary"]["verandah_length_m"] = round(max(vs_x, vs_y), 2)
            result["summary"]["verandah_width_m"]  = round(min(vs_x, vs_y), 2)
    else:
        result["summary"]["verandah_area_m2"] = 0.0


# ─── Rooms / areas ────────────────────────────────────────────────────────────

def _extract_rooms_areas(entities: list, result: dict) -> None:
    """
    Attempt to extract individual room areas from polygon entities.
    In this DXF there is no TEXT layer, so room names will be 'Unknown'.
    FLOOR hatch used as backup floor area if walls didn't yield a closed poly.
    CEILING hatch used as backup ceiling area.
    """
    rooms: list[dict] = []
    hatch_floor_area = 0.0
    hatch_ceiling_area = 0.0

    # Collect TEXT entities for nearest-name lookup (none in this DXF, but support future files)
    texts: list[dict] = []
    for e in entities:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        try:
            text = (e.dxf.text if e.dxftype() == "TEXT"
                    else (e.text if hasattr(e, "text") else "")).strip()
            text = re.sub(r'\\[A-Za-z][^;]*;', '', text).strip()
            text = re.sub(r'\{[^}]*\}', '', text).strip()
            if text:
                ix = float(e.dxf.insert.x)
                iy = float(e.dxf.insert.y)
                texts.append({"text": text, "x": ix, "y": iy})
        except Exception:
            pass

    for e in entities:
        layer = (e.dxf.layer or "").upper()

        # HATCH on FLOOR layer → backup floor area
        if e.dxftype() == "HATCH" and (layer == _L_FLOOR or _layer_has_kw(layer, FLOOR_KW)):
            area = _polygon_area(e)
            if area and area > 1.0:
                hatch_floor_area += area / 1e6
            continue

        # HATCH on CEILING layer → backup ceiling area
        if e.dxftype() == "HATCH" and (layer == _L_CEILING or _layer_has_kw(layer, CEILING_KW)):
            area = _polygon_area(e)
            if area and area > 1.0:
                hatch_ceiling_area += area / 1e6
            continue

        # Room polygons on floor/room layers (for DXFs with per-room polylines)
        is_room_layer = (
            _layer_has_kw(layer, FLOOR_KW) or
            _layer_has_kw(layer, ROOM_KW) or
            _layer_has_kw(layer, CEILING_KW)
        )
        if not is_room_layer or e.dxftype() not in ("LWPOLYLINE", "HATCH"):
            continue

        area = _polygon_area(e)
        if area is None or area < 1e4:  # < 0.01 m² → skip noise
            continue

        area_m2 = area / 1e6
        pts = _entity_to_points(e)
        cx = sum(p[0] for p in pts) / len(pts) if pts else 0
        cy = sum(p[1] for p in pts) / len(pts) if pts else 0

        # Find nearest TEXT entity as room name
        room_name = _nearest_text(cx, cy, texts) or _guess_room_name(layer)

        rooms.append({
            "name": room_name,
            "area_m2": round(area_m2, 2),
            "source_note": f"layer={layer}",
            "confidence": Confidence.MEDIUM.value,
            "explicit_or_derived": "explicit",
        })

    result["rooms"] = rooms

    # Backup floor area from HATCH if not set by wall extraction
    if "total_floor_area_m2" not in result["summary"] or result["summary"].get("total_floor_area_m2", 0) == 0:
        if hatch_floor_area > 0:
            result["summary"]["total_floor_area_m2"] = round(hatch_floor_area, 2)

    if hatch_ceiling_area > 0:
        result["summary"]["ceiling_area_m2"] = round(hatch_ceiling_area, 2)
    else:
        floor = result["summary"].get("total_floor_area_m2", 0)
        ver   = result["summary"].get("verandah_area_m2", 0)
        result["summary"]["ceiling_area_m2"] = round(max(0, floor - ver), 2)


def _nearest_text(cx: float, cy: float, texts: list[dict]) -> str | None:
    """Return text of closest TEXT entity to (cx, cy), or None if none within 5000mm."""
    if not texts:
        return None
    best = min(texts, key=lambda t: math.hypot(t["x"] - cx, t["y"] - cy))
    dist = math.hypot(best["x"] - cx, best["y"] - cy)
    return best["text"] if dist < 5000 else None


def _guess_room_name(layer: str) -> str:
    l = layer.lower()
    for kw in ("bedroom", "bed", "bath", "kitchen", "living", "dining",
               "laundry", "corridor", "hall", "store", "toilet", "verandah"):
        if kw in l:
            return kw.title()
    return "Room"


# ─── Doors ────────────────────────────────────────────────────────────────────

def _extract_doors(entities: list, doc, result: dict) -> None:
    """
    DOORS layer INSERT blocks.
    Block name format: DOOR_<N>
      N is 2-digit shorthand → ×10 = leaf mm (DOOR_82 → 820mm)
      N is 3-4 digit direct  → leaf mm directly (DOOR_820 → 820mm)
    """
    doors: list[dict] = []
    for e in entities:
        if e.dxftype() != "INSERT":
            continue
        layer = (e.dxf.layer or "").upper()
        block_name = (e.dxf.name or "").upper()

        on_door_layer = (layer == _L_DOORS or _layer_has_kw(layer, DOOR_KW))
        looks_door    = block_name.startswith("DOOR") or _looks_like_door(block_name, layer)

        if not (on_door_layer or looks_door):
            continue
        if not (on_door_layer and looks_door) and not looks_door:
            continue

        leaf_mm = _door_leaf_mm_from_block(block_name, e, doc)
        door_type = _map_door_type(leaf_mm)

        try:
            pos = (round(e.dxf.insert.x, 1), round(e.dxf.insert.y, 1))
        except Exception:
            pos = (0, 0)

        doors.append({
            "block_name": block_name,
            "layer": layer,
            "width_mm": leaf_mm,
            "type_mapped": door_type,
            "position": pos,
            "source_type": "plan",
            "confidence": Confidence.HIGH.value if leaf_mm else Confidence.MEDIUM.value,
            "explicit_or_derived": "explicit",
        })

    result["doors"] = doors
    from collections import Counter
    type_counts = Counter(d["type_mapped"] or "Unknown" for d in doors)
    result["summary"]["door_count"]  = len(doors)
    result["summary"]["door_types"]  = dict(type_counts)


def _door_leaf_mm_from_block(block_name: str, entity, doc) -> int | None:
    """Parse door leaf width from block name.

    DOOR_82  → 82 < 100 → 82 × 10 = 820mm
    DOOR_90  → 90 < 100 → 90 × 10 = 900mm
    DOOR_72  → 72 < 100 → 72 × 10 = 720mm
    DOOR_820 → 820 ≥ 100 → 820mm directly
    """
    m = re.search(r'DOOR[_\-\s](\d+)', block_name.upper())
    if m:
        n = int(m.group(1))
        if n < 100:
            return n * 10
        if 400 < n < 1500:
            return n
    # Fallback: measure from block geometry × scale
    try:
        x_scale = safe_float(getattr(entity.dxf, "xscale", 1.0)) or 1.0
        if block_name in doc.blocks:
            blk = doc.blocks[block_name]
            max_len = 0.0
            for ent in blk:
                if ent.dxftype() == "LINE":
                    dx = ent.dxf.end.x - ent.dxf.start.x
                    dy = ent.dxf.end.y - ent.dxf.start.y
                    max_len = max(max_len, math.hypot(dx, dy))
                elif ent.dxftype() == "ARC":
                    max_len = max(max_len, ent.dxf.radius * 2)
            if max_len > 0:
                v = round(max_len * x_scale)
                if 400 < v < 1500:
                    return v
    except Exception:
        pass
    return None


def _looks_like_door(block_name: str, layer: str) -> bool:
    combined = (block_name + " " + layer).lower()
    return any(k in combined for k in ("door", "d-", "_d_", "dr-"))


def _map_door_type(width_mm: int | None) -> str | None:
    if width_mm is None:
        return None
    for (lo, hi), name in DOOR_WIDTH_MAP.items():
        if lo <= width_mm <= hi:
            return name
    return f"Door ~{width_mm}mm"


# ─── Windows ──────────────────────────────────────────────────────────────────

def _extract_windows(entities: list, doc, result: dict) -> None:
    """
    WINDOWS layer INSERT blocks (WINDOW_LOUVRE).
    Window type is determined from x-scale factor:
      sx ≈ 1.0 → Window A (1080mm)
      sx ≈ 0.74 → Window B (~800mm)
      sx ≈ 1.71 → Window D (~1850mm)
    """
    windows: list[dict] = []
    for e in entities:
        if e.dxftype() != "INSERT":
            continue
        layer = (e.dxf.layer or "").upper()
        block_name = (e.dxf.name or "").upper()

        on_win_layer = (layer == _L_WINDOWS or _layer_has_kw(layer, WINDOW_KW))
        looks_win    = _looks_like_window(block_name, layer)

        if not (on_win_layer or looks_win):
            continue

        sx  = float(getattr(e.dxf, "xscale", 1.0) or 1.0)
        sy  = float(getattr(e.dxf, "yscale", 1.0) or 1.0)
        rot = float(getattr(e.dxf, "rotation", 0.0) or 0.0)

        win_type, width_mm, height_mm = _classify_window_insert(block_name, sx, sy, rot, e, doc)

        try:
            pos = (round(e.dxf.insert.x, 1), round(e.dxf.insert.y, 1))
        except Exception:
            pos = (0, 0)

        windows.append({
            "block_name": block_name,
            "layer": layer,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "xscale": round(sx, 3),
            "yscale": round(sy, 3),
            "rotation": round(rot, 1),
            "type_mapped": win_type,
            "position": pos,
            "source_type": "plan",
            "confidence": Confidence.HIGH.value if win_type else Confidence.MEDIUM.value,
            "explicit_or_derived": "explicit",
        })

    result["windows"] = windows
    from collections import Counter
    type_counts = Counter(w["type_mapped"] or "Unknown" for w in windows)
    result["summary"]["window_count"]  = len(windows)
    result["summary"]["window_types"]  = dict(type_counts)


def _classify_window_insert(block_name: str, sx: float, sy: float, rot: float,
                             entity, doc) -> tuple:
    """Return (type_char, width_mm, height_mm) for a window INSERT."""
    is_louvre = "LOUVRE" in block_name or "WINDOW" in block_name

    if is_louvre:
        for sx_lo, sx_hi, wtype, w_mm, h_mm in _WINDOW_SCALE_TYPES:
            if sx_lo <= abs(sx) <= sx_hi:
                return wtype, w_mm, h_mm
        # Unknown scale — compute from base
        return None, round(_WINDOW_LOUVRE_BASE_MM * abs(sx)), round(1200 * abs(sy))

    # Fallback: width from block name digits
    m = re.search(r"(\d{3,4})", block_name)
    if m:
        v = int(m.group(1))
        if 400 < v < 2500:
            for (lo, hi), name in WINDOW_WIDTH_MAP.items():
                if lo <= v <= hi:
                    return name.replace("Window ", ""), v, None
            return None, v, None

    # Fallback: measure block geometry
    try:
        if block_name in doc.blocks:
            blk = doc.blocks[block_name]
            max_len = 0.0
            for ent in blk:
                if ent.dxftype() == "LINE":
                    dx = ent.dxf.end.x - ent.dxf.start.x
                    dy = ent.dxf.end.y - ent.dxf.start.y
                    max_len = max(max_len, math.hypot(dx, dy))
            if max_len > 0:
                w_mm = round(max_len * abs(sx))
                for (lo, hi), name in WINDOW_WIDTH_MAP.items():
                    if lo <= w_mm <= hi:
                        return name.replace("Window ", ""), w_mm, None
                return None, w_mm, None
    except Exception:
        pass

    return None, None, None


def _looks_like_window(block_name: str, layer: str) -> bool:
    combined = (block_name + " " + layer).lower()
    return any(k in combined for k in ("window", "win-", "_w_", "wnd", "louvre"))


def _map_window_type(width_mm: int | None) -> str | None:
    if width_mm is None:
        return None
    for (lo, hi), name in WINDOW_WIDTH_MAP.items():
        if lo <= width_mm <= hi:
            return name
    return f"Window ~{width_mm}mm"


# ─── Posts / columns ──────────────────────────────────────────────────────────

def _extract_posts(entities: list, result: dict) -> None:
    """
    STRUCTURE layer CIRCLE entities = posts/piers.
    Also accepts INSERT blocks on post-like layers.
    """
    posts = []
    for e in entities:
        layer = (e.dxf.layer or "").upper()
        t = e.dxftype()
        block_name = (e.dxf.name or "").upper() if t == "INSERT" else ""

        is_structure_layer = (layer == _L_STRUCTURE or _layer_has_kw(layer, POST_KW))
        is_post_block = _layer_has_kw(block_name, POST_KW)

        if not (is_structure_layer or is_post_block):
            continue
        if t not in ("CIRCLE", "INSERT"):
            continue

        try:
            if t == "CIRCLE":
                pos = (round(e.dxf.center.x, 0), round(e.dxf.center.y, 0))
                radius_mm = round(e.dxf.radius, 1)
            else:
                pos = (round(e.dxf.insert.x, 0), round(e.dxf.insert.y, 0))
                radius_mm = None
        except Exception:
            pos = (0, 0)
            radius_mm = None

        posts.append({
            "type": "column" if t == "CIRCLE" else block_name,
            "layer": layer,
            "position": pos,
            "radius_mm": radius_mm,
            "confidence": Confidence.HIGH.value,
        })

    result["posts"] = posts
    result["summary"]["post_count"] = len(posts)


# ─── Stairs ───────────────────────────────────────────────────────────────────

def _extract_stairs(entities: list, result: dict) -> None:
    """
    STAIRS layer LINE entities = stair stringers/treads.
    Counts lines; exact stair dimensions come from PDF details.
    """
    stair_lines = [e for e in entities
                   if e.dxftype() == "LINE"
                   and ((e.dxf.layer or "").upper() == _L_STAIRS
                        or _layer_has_kw((e.dxf.layer or "").upper(), STAIR_KW))]

    if stair_lines:
        result["stairs"].append({
            "source_type": "plan",
            "entity_count": len(stair_lines),
            "confidence": Confidence.LOW.value,
            "explicit_or_derived": "derived",
            "note": f"{len(stair_lines)} stair lines found — dimensions from PDF section/detail",
        })
        result["summary"]["stair_flight_count"] = 1
    else:
        result["summary"]["stair_flight_count"] = 0


# ─── Dimensions ───────────────────────────────────────────────────────────────

def _extract_dimensions(entities: list, result: dict) -> None:
    """Extract DIMENSION entity values (none in SDP DXF, kept for completeness)."""
    dims = []
    for e in entities:
        if e.dxftype() not in ("DIMENSION", "ROTATED_DIMENSION", "ALIGNED_DIMENSION"):
            continue
        try:
            val = safe_float(e.dxf.actual_measurement)
            if val and val > 0:
                dims.append({
                    "value_mm": round(val),
                    "layer": (e.dxf.layer or ""),
                    "confidence": Confidence.HIGH.value,
                })
        except Exception:
            pass
    result["dimensions"] = dims


# ─── Summary assembly ─────────────────────────────────────────────────────────

def _build_summary(result: dict) -> None:
    s = result["summary"]
    # Derived wall areas
    if "external_wall_area_m2" not in s:
        s["external_wall_area_m2"] = round(s.get("external_wall_length_m", 0) * 2.4, 2)
    if "internal_wall_area_m2" not in s:
        s["internal_wall_area_m2"] = round(s.get("internal_wall_length_m", 0) * 2.4, 2)


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _is_closed_poly(e) -> bool:
    """Return True if LWPOLYLINE/POLYLINE is closed."""
    try:
        if e.dxftype() == "LWPOLYLINE":
            if int(getattr(e.dxf, "flags", 0)) & 1:
                return True
            pts = list(e.get_points())
            if len(pts) >= 2:
                p0, pn = pts[0], pts[-1]
                return math.hypot(p0[0] - pn[0], p0[1] - pn[1]) < 10.0
        elif e.dxftype() == "POLYLINE":
            flags = int(getattr(e.dxf, "flags", 0))
            return bool(flags & 1)
    except Exception:
        pass
    return False


def _layer_has_kw(layer: str, keywords: set) -> bool:
    l = layer.lower()
    return any(k in l for k in keywords)


def _entity_to_segments(e) -> list[dict]:
    t = e.dxftype()
    segs = []
    try:
        if t == "LINE":
            segs.append(_seg(e.dxf.start, e.dxf.end))
        elif t == "LWPOLYLINE":
            pts = list(e.get_points())
            for i in range(len(pts) - 1):
                segs.append(_seg(pts[i], pts[i + 1]))
        elif t == "POLYLINE":
            verts = list(e.vertices)
            for i in range(len(verts) - 1):
                segs.append(_seg(verts[i].dxf.location, verts[i + 1].dxf.location))
    except Exception:
        pass
    return segs


def _seg(p1, p2) -> dict:
    try:
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        return {"start": (x1, y1), "end": (x2, y2),
                "length": math.hypot(x2 - x1, y2 - y1), "external": False}
    except Exception:
        return {"start": (0, 0), "end": (0, 0), "length": 0, "external": False}


def _polygon_area(e) -> float | None:
    """Shoelace area (mm²) for closed polygon entities."""
    t = e.dxftype()
    try:
        if t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
        elif t == "HATCH":
            paths = e.paths
            if not paths:
                return None
            pts = ([(v.x, v.y) for v in paths[0].vertices]
                   if hasattr(paths[0], "vertices") else [])
        else:
            return None
        if len(pts) < 3:
            return None
        n = len(pts)
        area = abs(sum(
            pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
            for i in range(n)
        ) / 2.0)
        return area
    except Exception:
        return None


def _entity_to_points(e) -> list[tuple[float, float]]:
    """Return (x, y) vertex list for polygon-like entities."""
    try:
        if e.dxftype() == "LWPOLYLINE":
            return [(float(p[0]), float(p[1])) for p in e.get_points()]
        if e.dxftype() == "HATCH":
            paths = e.paths
            if paths and hasattr(paths[0], "vertices"):
                return [(float(v.x), float(v.y)) for v in paths[0].vertices]
    except Exception:
        pass
    return []


def _extract_roof_pitch(entities: list, result: dict) -> None:
    """Find roof pitch from TEXT/MTEXT entities (none in SDP DXF — uses PDF default)."""
    pitch_values: list[float] = []
    for e in entities:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        try:
            text = (e.dxf.text or "").strip()
        except Exception:
            continue
        m = re.search(r'(?:pitch|slope|angle)?\s*(\d+(?:\.\d+)?)\s*[°d]', text, re.I)
        if m:
            v = float(m.group(1))
            if 5 <= v <= 45:
                pitch_values.append(v)
        m = re.search(r'1\s*:\s*(\d+(?:\.\d+)?)', text)
        if m:
            ratio = float(m.group(1))
            if ratio > 0:
                angle = math.degrees(math.atan(1.0 / ratio))
                if 5 <= angle <= 45:
                    pitch_values.append(round(angle, 1))

    from src.config import DEFAULT_ROOF_PITCH_DEG
    if pitch_values:
        from collections import Counter
        result["summary"]["roof_pitch_degrees"]     = Counter(pitch_values).most_common(1)[0][0]
        result["summary"]["roof_pitch_confidence"]  = Confidence.HIGH.value
    else:
        result["summary"]["roof_pitch_degrees"]     = DEFAULT_ROOF_PITCH_DEG
        result["summary"]["roof_pitch_confidence"]  = Confidence.LOW.value
        result["warnings"].append(
            f"Roof pitch not found in DWG text — defaulting to {DEFAULT_ROOF_PITCH_DEG}°"
        )


def audit_dwg(dxf_path: str | Path) -> str:
    """Audit everything in a DXF file and return a formatted report string."""
    path = Path(dxf_path)
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    w(f"DWG AUDIT REPORT")
    w(f"File: {path.name}")
    w(f"{'='*80}")

    try:
        import ezdxf
    except ImportError:
        w("ERROR: ezdxf not installed")
        return "\n".join(lines)

    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        w(f"ERROR reading file: {exc}")
        return "\n".join(lines)

    msp = doc.modelspace()
    entities = list(msp)

    # ── Entity type counts ────────────────────────────────────────────────────
    from collections import Counter, defaultdict
    type_counts: Counter = Counter(e.dxftype() for e in entities)
    w()
    w("ENTITY TYPE COUNTS")
    w("-" * 40)
    for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        w(f"  {etype:<30} {cnt:>6}")
    w(f"  {'TOTAL':<30} {len(entities):>6}")

    # ── Layer inventory ───────────────────────────────────────────────────────
    layer_entity_counts: Counter = Counter(
        (e.dxf.layer or "0").upper() for e in entities
    )
    w()
    w(f"LAYERS  ({len(layer_entity_counts)} total)")
    w("-" * 40)
    for layer, cnt in sorted(layer_entity_counts.items(), key=lambda x: x[0]):
        w(f"  {layer:<50} {cnt:>6} entities")

    # ── Layer table (includes empty layers) ───────────────────────────────────
    try:
        all_layers = sorted(lt.dxf.name.upper() for lt in doc.layers)
        w()
        w(f"LAYER TABLE  ({len(all_layers)} defined)")
        w("-" * 40)
        for lname in all_layers:
            cnt = layer_entity_counts.get(lname, 0)
            w(f"  {lname:<50} {cnt:>6} entities")
    except Exception:
        pass

    # ── Block definitions ─────────────────────────────────────────────────────
    block_insert_counts: Counter = Counter()
    for e in entities:
        if e.dxftype() == "INSERT":
            block_insert_counts[(e.dxf.name or "").upper()] += 1

    w()
    w(f"BLOCK INSERTS IN MODELSPACE  ({sum(block_insert_counts.values())} total inserts)")
    w("-" * 40)
    for bname, cnt in sorted(block_insert_counts.items(), key=lambda x: -x[1]):
        w(f"  {bname:<50} {cnt:>6} inserts")

    # Block definitions (all defined, not just inserted)
    try:
        all_blocks = [b.name for b in doc.blocks if not b.name.startswith("*")]
        w()
        w(f"BLOCK DEFINITIONS  ({len(all_blocks)} total)")
        w("-" * 40)
        for bname in sorted(all_blocks):
            ent_count = sum(1 for _ in doc.blocks[bname])
            ins_count = block_insert_counts.get(bname.upper(), 0)
            w(f"  {bname:<50} def_entities={ent_count:<6} inserts={ins_count}")
    except Exception:
        pass

    # ── INSERT positions (all) ─────────────────────────────────────────────────
    inserts = [e for e in entities if e.dxftype() == "INSERT"]
    w()
    w(f"INSERT POSITIONS  ({len(inserts)} total)")
    w("-" * 40)
    for e in inserts:
        try:
            bname = (e.dxf.name or "").upper()
            layer = (e.dxf.layer or "0").upper()
            x = round(e.dxf.insert.x, 1)
            y = round(e.dxf.insert.y, 1)
            sx = round(getattr(e.dxf, "xscale", 1.0), 3)
            sy = round(getattr(e.dxf, "yscale", 1.0), 3)
            rot = round(getattr(e.dxf, "rotation", 0.0), 1)
            w(f"  BLOCK={bname:<40} LAYER={layer:<30} x={x:<10} y={y:<10} sx={sx} sy={sy} rot={rot}")
        except Exception:
            pass

    # ── LWPOLYLINE summary ────────────────────────────────────────────────────
    lwpoly = [e for e in entities if e.dxftype() == "LWPOLYLINE"]
    w()
    w(f"LWPOLYLINE ENTITIES  ({len(lwpoly)} total)")
    w("-" * 40)
    layer_poly: dict = defaultdict(list)
    for e in lwpoly:
        layer = (e.dxf.layer or "0").upper()
        try:
            pts = list(e.get_points())
            # Perimeter
            perim = 0.0
            for i in range(len(pts)):
                p1, p2 = pts[i], pts[(i + 1) % len(pts)]
                perim += math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            is_closed = bool(getattr(e.dxf, "flags", 0) & 1) or (
                len(pts) > 1 and
                abs(pts[0][0] - pts[-1][0]) < 1 and
                abs(pts[0][1] - pts[-1][1]) < 1
            )
            area = _polygon_area(e) or 0.0
            layer_poly[layer].append({
                "pts": len(pts),
                "perim_mm": round(perim, 1),
                "area_mm2": round(area, 0),
                "closed": is_closed,
            })
        except Exception:
            layer_poly[layer].append({"pts": "?", "perim_mm": 0, "area_mm2": 0, "closed": False})

    for layer in sorted(layer_poly.keys()):
        items = layer_poly[layer]
        w(f"  LAYER: {layer}  ({len(items)} polylines)")
        for item in items:
            closed_str = "CLOSED" if item["closed"] else "open  "
            perim_m = round(item["perim_mm"] / 1000.0, 3) if isinstance(item["perim_mm"], float) else "?"
            area_m2 = round(item["area_mm2"] / 1e6, 4) if isinstance(item["area_mm2"], float) else "?"
            w(f"    {closed_str}  pts={item['pts']:<5} perim={perim_m}m   area={area_m2}m2")

    # ── LINE entities by layer ────────────────────────────────────────────────
    lines_ents = [e for e in entities if e.dxftype() == "LINE"]
    w()
    w(f"LINE ENTITIES  ({len(lines_ents)} total)")
    w("-" * 40)
    line_by_layer: Counter = Counter((e.dxf.layer or "0").upper() for e in lines_ents)
    line_len_by_layer: dict = defaultdict(float)
    for e in lines_ents:
        layer = (e.dxf.layer or "0").upper()
        try:
            length = math.hypot(
                e.dxf.end.x - e.dxf.start.x,
                e.dxf.end.y - e.dxf.start.y
            )
            line_len_by_layer[layer] += length
        except Exception:
            pass
    for layer in sorted(line_by_layer.keys()):
        cnt = line_by_layer[layer]
        total_m = round(line_len_by_layer[layer] / 1000.0, 2)
        w(f"  {layer:<50} {cnt:>5} lines  total={total_m}m")

    # ── TEXT entities ─────────────────────────────────────────────────────────
    texts = [e for e in entities if e.dxftype() in ("TEXT", "MTEXT")]
    w()
    w(f"TEXT / MTEXT ENTITIES  ({len(texts)} total)")
    w("-" * 40)
    for e in texts:
        try:
            layer = (e.dxf.layer or "0").upper()
            if e.dxftype() == "TEXT":
                text = (e.dxf.text or "").strip()
                try:
                    x, y = round(e.dxf.insert.x, 0), round(e.dxf.insert.y, 0)
                except Exception:
                    x, y = 0, 0
            else:  # MTEXT
                text = (e.text if hasattr(e, "text") else getattr(e.dxf, "text", "")).strip()
                # Strip MTEXT format codes
                text = re.sub(r'\{[^}]*\}', '', text)
                text = re.sub(r'\\[A-Za-z][^;]*;', '', text)
                text = text.strip()
                try:
                    x, y = round(e.dxf.insert.x, 0), round(e.dxf.insert.y, 0)
                except Exception:
                    x, y = 0, 0
            if text:
                w(f"  LAYER={layer:<35} x={x:<10} y={y:<10} TEXT={text[:80]!r}")
        except Exception:
            pass

    # ── DIMENSION entities ────────────────────────────────────────────────────
    dims = [e for e in entities if e.dxftype() == "DIMENSION"]
    w()
    w(f"DIMENSION ENTITIES  ({len(dims)} total)")
    w("-" * 40)
    for e in dims:
        try:
            layer = (e.dxf.layer or "0").upper()
            val = safe_float(e.dxf.actual_measurement)
            val_m = round(val / 1000.0, 3) if val else None
            try:
                text_override = e.dxf.text or ""
            except Exception:
                text_override = ""
            w(f"  LAYER={layer:<35} measured={val_m}m  override={text_override!r}")
        except Exception:
            pass

    # ── HATCH entities ────────────────────────────────────────────────────────
    hatches = [e for e in entities if e.dxftype() == "HATCH"]
    w()
    w(f"HATCH ENTITIES  ({len(hatches)} total)")
    w("-" * 40)
    hatch_by_layer: Counter = Counter((e.dxf.layer or "0").upper() for e in hatches)
    for layer, cnt in sorted(hatch_by_layer.items(), key=lambda x: x[0]):
        w(f"  {layer:<50} {cnt:>5}")

    # ── CIRCLE entities ───────────────────────────────────────────────────────
    circles = [e for e in entities if e.dxftype() == "CIRCLE"]
    w()
    w(f"CIRCLE ENTITIES  ({len(circles)} total)")
    w("-" * 40)
    circle_by_layer: Counter = Counter((e.dxf.layer or "0").upper() for e in circles)
    for layer, cnt in sorted(circle_by_layer.items(), key=lambda x: x[0]):
        w(f"  {layer:<50} {cnt:>5}")

    # ── ARC entities ──────────────────────────────────────────────────────────
    arcs = [e for e in entities if e.dxftype() == "ARC"]
    w()
    w(f"ARC ENTITIES  ({len(arcs)} total)")
    w("-" * 40)
    arc_by_layer: Counter = Counter((e.dxf.layer or "0").upper() for e in arcs)
    for layer, cnt in sorted(arc_by_layer.items(), key=lambda x: x[0]):
        w(f"  {layer:<50} {cnt:>5}")

    # ── Bounding box ──────────────────────────────────────────────────────────
    all_x: list[float] = []
    all_y: list[float] = []
    for e in entities:
        try:
            if e.dxftype() == "LINE":
                all_x += [e.dxf.start.x, e.dxf.end.x]
                all_y += [e.dxf.start.y, e.dxf.end.y]
            elif e.dxftype() == "LWPOLYLINE":
                for p in e.get_points():
                    all_x.append(p[0]); all_y.append(p[1])
            elif e.dxftype() in ("INSERT", "TEXT"):
                all_x.append(e.dxf.insert.x); all_y.append(e.dxf.insert.y)
        except Exception:
            pass
    if all_x:
        w()
        w("BOUNDING BOX")
        w("-" * 40)
        w(f"  X: {min(all_x):.1f} to {max(all_x):.1f}  span={(max(all_x)-min(all_x))/1000:.2f}m")
        w(f"  Y: {min(all_y):.1f} to {max(all_y):.1f}  span={(max(all_y)-min(all_y))/1000:.2f}m")

    w()
    w("=" * 80)
    w("END OF AUDIT")
    return "\n".join(lines)


def _try_convert_dwg(dwg_path: Path, result: dict) -> Path | None:
    """Attempt DWG→DXF conversion. Return DXF path or None."""
    dxf_path = dwg_path.with_suffix(".dxf")
    if dxf_path.exists():
        log.info("Using existing DXF: %s", dxf_path.name)
        return dxf_path
    try:
        from src.dwg_converter import convert_dwg_to_dxf
        out = convert_dwg_to_dxf(str(dwg_path), str(dwg_path.parent))
        if out and Path(out).exists():
            return Path(out)
    except Exception as exc:
        result["warnings"].append(f"DWG conversion error: {exc}")
    return None
