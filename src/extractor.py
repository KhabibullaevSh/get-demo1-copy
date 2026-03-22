"""
extractor.py — Extracts geometry from DXF files using ezdxf.

Extracts:
  - Room polygons and areas (m²)
  - Wall polylines and lengths (lm) — split into external vs internal geometrically
  - Door/window block insert counts, mapped to standard model names (Door A/B/C)
  - Roof outline and area (m²)
  - Post/column positions and count
  - Stair elements

Returns: project_geometry dict matching standard_geometry structure.
"""

import math
import ezdxf
from ezdxf.math import Vec3


# Layer name patterns for classification
LAYER_PATTERNS = {
    "walls": ["WALL", "WALLS", "A-WALL", "S-WALL", "EXT-WALL", "INT-WALL",
              "EXTERIOR", "INTERIOR", "PARTITION", "BLDG"],
    "doors": ["DOOR", "DOORS", "A-DOOR", "A-DOOR-FRAME"],
    "windows": ["WINDOW", "WINDOWS", "WIN", "A-GLAZ", "A-WINDOW", "GLAZ"],
    "roof": ["ROOF", "ROOFING", "A-ROOF", "ROOF-LINE", "ROOF-EDGE"],
    "floor": ["FLOOR", "FLOORS", "A-FLOOR", "SLAB", "A-FLOR", "FINISHES"],
    "structure": ["STRUCT", "STRUCTURE", "S-COLS", "S-BEAM", "COLUMN", "POST",
                  "FOOTING", "PIER", "BEARER", "JOIST", "S-FRAMING"],
    "stairs": ["STAIR", "STAIRS", "A-STAIR", "STEP", "STEPS"],
    "ceiling": ["CEILING", "CEIL", "A-CEIL", "A-CLNG"],
    "verandah": ["VERANDAH", "VERANDA", "DECK", "PORCH", "BALCONY"],
    "annotation": ["TEXT", "DIM", "ANNO", "ANNOT", "HATCH", "TITLE", "BORDER",
                   "TITLEBLOCK", "SHEET"],
}

# Door width → standard model name mapping (width in mm)
DOOR_WIDTH_MAP = {
    (900, 950): "Door A",    # 920mm solid core front door
    (800, 860): "Door B",    # 820mm hollow core
    (700, 760): "Door C",    # 720mm hollow core
    (600, 700): "Door D",
}

# Window type mapping by width (mm)
WINDOW_WIDTH_MAP = {
    (1050, 1150): "Window A",   # 1080mm louvre
    (780, 830): "Window B",     # 800mm louvre
    (1150, 1250): "Window C",   # 1200mm louvre
    (1800, 1900): "Window D",   # 1850mm louvre
}


def extract_geometry(doc: ezdxf.document.Drawing) -> dict:
    """Extract all construction geometry from a normalised DXF document."""
    msp = doc.modelspace()

    # Collect all layers for debugging
    all_layers = [layer.dxf.name for layer in doc.layers]

    # Classify entities by layer
    classified = _classify_entities(msp, doc)

    # Extract each geometry category
    walls = _extract_walls(classified.get("walls", []))
    rooms = _extract_rooms(classified.get("floor", []), classified.get("walls", []))
    doors = _extract_doors(classified.get("doors", []), msp, doc)
    windows = _extract_windows(classified.get("windows", []), msp, doc)
    roof = _extract_roof(classified.get("roof", []))
    posts = _extract_posts(classified.get("structure", []), msp)
    stairs = _extract_stairs(classified.get("stairs", []))
    verandah = _extract_verandah(classified.get("verandah", []))
    ceiling = _extract_ceiling(classified.get("ceiling", []))

    # Derive ceiling area from floor/room area when not explicitly drawn
    ceiling_area = ceiling.get("area", 0.0)
    if ceiling_area == 0.0 and rooms.get("total_area", 0) > 0:
        # Ceiling ≈ internal floor area (habitable rooms, excluding verandah)
        verandah_area = verandah.get("area", 0.0)
        ceiling_area = max(0.0, rooms.get("total_area", 0.0) - verandah_area)

    geometry = {
        "total_floor_area": rooms.get("total_area", 0.0),
        "total_wall_length": walls.get("total_length", 0.0),
        "external_wall_length": walls.get("external_length", 0.0),
        "internal_wall_length": walls.get("internal_length", 0.0),
        "roof_area": roof.get("area", 0.0),
        "roof_perimeter": roof.get("perimeter", 0.0),
        "verandah_area": verandah.get("area", 0.0),
        "ceiling_area": round(ceiling_area, 2),
        "door_count": doors.get("total_count", 0),
        "window_count": windows.get("total_count", 0),
        "post_count": posts.get("count", 0),
        "stair_count": stairs.get("count", 0),
        "room_count": rooms.get("count", 0),
        # Detailed breakdowns
        "rooms": rooms.get("rooms", []),
        "door_types": doors.get("types", {}),
        "window_types": windows.get("types", {}),
        "wall_segments": walls.get("segments", []),
        "_layers": all_layers,
    }

    return geometry


def _classify_entities(msp, doc) -> dict:
    """Classify entities by their layer into construction categories."""
    classified = {k: [] for k in LAYER_PATTERNS}
    unclassified = []

    for entity in msp:
        layer_name = entity.dxf.layer.upper() if hasattr(entity.dxf, "layer") else "0"
        matched = False

        for category, patterns in LAYER_PATTERNS.items():
            if any(p in layer_name for p in patterns):
                classified[category].append(entity)
                matched = True
                break

        if not matched:
            unclassified.append(entity)

    # If no layers matched at all, try to classify by entity type as fallback
    wall_count = len(classified.get("walls", []))
    if wall_count == 0 and unclassified:
        fallback = _classify_by_type(unclassified)
        for k, v in fallback.items():
            classified[k].extend(v)

    return classified


def _classify_by_type(entities: list) -> dict:
    """Fallback classification when layer names don't match patterns."""
    classified = {k: [] for k in LAYER_PATTERNS}

    for entity in entities:
        dxftype = entity.dxftype()
        if dxftype == "INSERT":
            block_name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
            if any(d in block_name for d in ["DOOR", "DR", "D-"]):
                classified["doors"].append(entity)
            elif any(w in block_name for w in ["WINDOW", "WIN", "W-"]):
                classified["windows"].append(entity)
            elif any(p in block_name for p in ["POST", "COL", "COLUMN", "PIER"]):
                classified["structure"].append(entity)
        elif dxftype in ("LINE", "LWPOLYLINE", "POLYLINE"):
            classified["walls"].append(entity)
        elif dxftype in ("CIRCLE", "ARC"):
            classified["structure"].append(entity)
        elif dxftype == "HATCH":
            classified["floor"].append(entity)

    return classified


def _extract_walls(entities: list) -> dict:
    """Extract wall segments and lengths.

    External walls are detected geometrically: after collecting all segments,
    find the bounding box of all wall geometry. Segments that lie on or near
    the bounding box perimeter are classified as external; the rest internal.
    Also honours explicit layer naming (EXT/INT prefixes).
    """
    segments = []

    for entity in entities:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer.upper() if hasattr(entity.dxf, "layer") else ""

        if dxftype == "LINE":
            start = Vec3(entity.dxf.start)
            end = Vec3(entity.dxf.end)
            length = start.distance(end)
            length_m = length / 1000.0
            if length_m < 0.01:
                continue
            # Explicit layer hint
            layer_hint = _get_wall_layer_hint(layer)
            segments.append({
                "start": (start.x, start.y),
                "end": (end.x, end.y),
                "length_m": length_m,
                "layer": layer,
                "layer_hint": layer_hint,
            })

        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            try:
                points = list(entity.get_points(format="xy"))
                is_closed = getattr(entity, "is_closed", False)
                layer_hint = _get_wall_layer_hint(layer)
                for i in range(len(points) - 1):
                    p1 = Vec3(points[i][0], points[i][1], 0)
                    p2 = Vec3(points[i + 1][0], points[i + 1][1], 0)
                    length_m = p1.distance(p2) / 1000.0
                    if length_m < 0.01:
                        continue
                    segments.append({
                        "start": (p1.x, p1.y),
                        "end": (p2.x, p2.y),
                        "length_m": length_m,
                        "layer": layer,
                        "layer_hint": layer_hint,
                    })
                if is_closed and len(points) > 2:
                    p1 = Vec3(points[-1][0], points[-1][1], 0)
                    p2 = Vec3(points[0][0], points[0][1], 0)
                    length_m = p1.distance(p2) / 1000.0
                    if length_m >= 0.01:
                        segments.append({
                            "start": (p1.x, p1.y),
                            "end": (p2.x, p2.y),
                            "length_m": length_m,
                            "layer": layer,
                            "layer_hint": layer_hint,
                        })
            except Exception:
                continue

    if not segments:
        return {"total_length": 0.0, "external_length": 0.0,
                "internal_length": 0.0, "segments": []}

    # Geometric external/internal classification
    _classify_wall_segments_geometric(segments)

    total_length = sum(s["length_m"] for s in segments)
    external_length = sum(s["length_m"] for s in segments if s.get("external"))
    internal_length = total_length - external_length

    # If we ended up with zero external (e.g. all walls same layer, no EXT hint),
    # use the geometric result even if it's a rough split
    if external_length == 0.0 and total_length > 0:
        # Last resort: assume 40-50% external (typical single-storey house)
        external_length = round(total_length * 0.45, 2)
        internal_length = round(total_length - external_length, 2)

    return {
        "total_length": round(total_length, 2),
        "external_length": round(external_length, 2),
        "internal_length": round(internal_length, 2),
        "segments": segments,
    }


def _get_wall_layer_hint(layer_name: str) -> str:
    """Return 'external', 'internal', or '' based on layer name."""
    l = layer_name.upper()
    if any(t in l for t in ["EXT", "EXTER", "OUTER", "PERIMETER", "EXTERNAL"]):
        return "external"
    if any(t in l for t in ["INT", "INTER", "INNER", "INTERNAL", "PARTITION"]):
        return "internal"
    return ""


def _classify_wall_segments_geometric(segments: list) -> None:
    """Classify wall segments as external/internal in-place.

    Strategy:
      1. Honour explicit EXT/INT layer hints if present.
      2. Otherwise find the bounding box of all wall endpoints.
         Segments whose midpoint is within TOLERANCE of any bbox edge → external.
         All others → internal.
    """
    # Check if we have any explicit layer hints
    has_ext_hint = any(s.get("layer_hint") == "external" for s in segments)
    has_int_hint = any(s.get("layer_hint") == "internal" for s in segments)

    if has_ext_hint or has_int_hint:
        for s in segments:
            hint = s.get("layer_hint", "")
            if hint == "external":
                s["external"] = True
            elif hint == "internal":
                s["external"] = False
            else:
                # Unknown: use geometric fallback for this segment
                s["external"] = False  # will be overridden below if near bbox
        # Still run geometric pass for unlabelled segments
        unlabelled = [s for s in segments if not s.get("layer_hint")]
        if unlabelled:
            _apply_bbox_classification(unlabelled, segments)
    else:
        _apply_bbox_classification(segments, segments)


def _apply_bbox_classification(target_segments: list, all_segments: list) -> None:
    """Classify target_segments as external (near bbox) or internal."""
    all_x = [s["start"][0] for s in all_segments] + [s["end"][0] for s in all_segments]
    all_y = [s["start"][1] for s in all_segments] + [s["end"][1] for s in all_segments]
    if not all_x:
        return

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    bbox_width = max_x - min_x
    bbox_height = max_y - min_y

    # Tolerance: 5% of bbox dimension or 300mm, whichever is larger
    tol_x = max(bbox_width * 0.05, 300)
    tol_y = max(bbox_height * 0.05, 300)

    for s in target_segments:
        mid_x = (s["start"][0] + s["end"][0]) / 2
        mid_y = (s["start"][1] + s["end"][1]) / 2
        near_edge = (
            abs(mid_x - min_x) < tol_x or
            abs(mid_x - max_x) < tol_x or
            abs(mid_y - min_y) < tol_y or
            abs(mid_y - max_y) < tol_y
        )
        s["external"] = near_edge


def _extract_rooms(floor_entities: list, wall_entities: list) -> dict:
    """Extract room polygons and areas from floor hatches or closed polylines."""
    rooms = []
    total_area = 0.0

    for entity in floor_entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices") and path.vertices:
                        points = [(v.x, v.y) for v in path.vertices]
                    elif hasattr(path, "edges"):
                        points = _edges_to_points(path.edges)
                    else:
                        continue
                    if len(points) < 3:
                        continue
                    area = _polygon_area(points) / 1e6
                    if area > 0.5:
                        rooms.append({
                            "area_m2": round(area, 2),
                            "points": points,
                            "layer": entity.dxf.layer if hasattr(entity.dxf, "layer") else "",
                        })
                        total_area += area
            except Exception:
                continue

    # Fallback: closed polylines on wall layers
    if not rooms:
        for entity in wall_entities:
            if entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                try:
                    if entity.is_closed:
                        points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                        area = _polygon_area(points) / 1e6
                        if area > 2.0:
                            rooms.append({
                                "area_m2": round(area, 2),
                                "points": points,
                                "layer": entity.dxf.layer if hasattr(entity.dxf, "layer") else "",
                            })
                            total_area += area
                except Exception:
                    continue

    return {
        "total_area": round(total_area, 2),
        "count": len(rooms),
        "rooms": rooms,
    }


def _edges_to_points(edges) -> list:
    """Convert path edges to a list of (x, y) points."""
    points = []
    for edge in edges:
        if hasattr(edge, "start"):
            points.append((edge.start.x, edge.start.y))
        elif hasattr(edge, "vertices"):
            for v in edge.vertices:
                points.append((v.x, v.y))
    return points


def _extract_doors(door_entities: list, msp, doc) -> dict:
    """Extract door counts by type from block inserts.

    Maps block insert dimensions to standard door type names:
      Door A = ~920mm,  Door B = ~820mm,  Door C = ~720mm
    """
    types: dict[str, int] = {}
    total_count = 0

    # Collect from door layer
    for entity in door_entities:
        if entity.dxftype() == "INSERT":
            type_name = _map_door_type(entity, doc)
            types[type_name] = types.get(type_name, 0) + 1
            total_count += 1

    # Scan all inserts for door-like block names if layer gave nothing
    if total_count == 0:
        for entity in msp:
            if entity.dxftype() == "INSERT":
                name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
                if any(d in name for d in ["DOOR", "DR-", "D-", "ENTRY"]):
                    type_name = _map_door_type(entity, doc)
                    types[type_name] = types.get(type_name, 0) + 1
                    total_count += 1

    return {"total_count": total_count, "types": types}


def _map_door_type(insert_entity, doc) -> str:
    """Map a door INSERT block to a standard model door type name.

    Uses block geometry (line/arc length) to determine the door width,
    then looks up the standard name from DOOR_WIDTH_MAP.
    Falls back to block name parsing if geometry is unavailable.
    """
    block_name = insert_entity.dxf.name if hasattr(insert_entity.dxf, "name") else ""
    block_name_up = block_name.upper()

    # Try to extract width from block name (e.g. DOOR_82 → 820mm, D920 → 920mm)
    import re
    nums = re.findall(r'\d{2,4}', block_name_up)
    for num_str in nums:
        num = int(num_str)
        # If it looks like a mm dimension (2-digit codes are ×10)
        if num < 100:
            num *= 10
        for (lo, hi), name in DOOR_WIDTH_MAP.items():
            if lo <= num <= hi:
                return name

    # Try block geometry: find the longest line = door width
    if block_name and block_name in doc.blocks:
        block = doc.blocks[block_name]
        max_length = 0.0
        for entity in block:
            if entity.dxftype() == "LINE":
                start = Vec3(entity.dxf.start)
                end = Vec3(entity.dxf.end)
                length = start.distance(end)
                max_length = max(max_length, length)
            elif entity.dxftype() == "ARC":
                # Arc radius = door width
                max_length = max(max_length, entity.dxf.radius)

        if max_length > 0:
            # Scale by insert x-scale
            x_scale = abs(insert_entity.dxf.xscale) if hasattr(insert_entity.dxf, "xscale") else 1.0
            width_mm = max_length * x_scale
            for (lo, hi), name in DOOR_WIDTH_MAP.items():
                if lo <= width_mm <= hi:
                    return name

    # Generic fallback
    return f"Door ({block_name or 'Unknown'})"


def _extract_windows(window_entities: list, msp, doc) -> dict:
    """Extract window counts by type from block inserts.

    Maps block dimensions to standard window type names.
    """
    types: dict[str, int] = {}
    total_count = 0

    for entity in window_entities:
        if entity.dxftype() == "INSERT":
            type_name = _map_window_type(entity, doc)
            types[type_name] = types.get(type_name, 0) + 1
            total_count += 1

    if total_count == 0:
        for entity in msp:
            if entity.dxftype() == "INSERT":
                name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
                if any(w in name for w in ["WINDOW", "WIN-", "W-", "GLAZ"]):
                    type_name = _map_window_type(entity, doc)
                    types[type_name] = types.get(type_name, 0) + 1
                    total_count += 1

    return {"total_count": total_count, "types": types}


def _map_window_type(insert_entity, doc) -> str:
    """Map a window INSERT to a standard window type name."""
    block_name = insert_entity.dxf.name if hasattr(insert_entity.dxf, "name") else ""
    block_name_up = block_name.upper()

    import re
    nums = re.findall(r'\d{3,4}', block_name_up)
    for num_str in nums:
        num = int(num_str)
        x_scale = abs(insert_entity.dxf.xscale) if hasattr(insert_entity.dxf, "xscale") else 1.0
        width_mm = num * x_scale
        for (lo, hi), name in WINDOW_WIDTH_MAP.items():
            if lo <= width_mm <= hi:
                return name

    # Try block geometry
    if block_name and block_name in doc.blocks:
        block = doc.blocks[block_name]
        max_length = 0.0
        for entity in block:
            if entity.dxftype() == "LINE":
                start = Vec3(entity.dxf.start)
                end = Vec3(entity.dxf.end)
                max_length = max(max_length, start.distance(end))

        if max_length > 0:
            x_scale = abs(insert_entity.dxf.xscale) if hasattr(insert_entity.dxf, "xscale") else 1.0
            width_mm = max_length * x_scale
            for (lo, hi), name in WINDOW_WIDTH_MAP.items():
                if lo <= width_mm <= hi:
                    return name

    return f"Window ({block_name or 'Unknown'})"


def _extract_roof(entities: list) -> dict:
    """Extract roof area and perimeter from roof layer entities."""
    area = 0.0
    perimeter = 0.0

    for entity in entities:
        dxftype = entity.dxftype()

        if dxftype == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices") and path.vertices:
                        points = [(v.x, v.y) for v in path.vertices]
                        area += _polygon_area(points) / 1e6
                        perimeter += _polygon_perimeter(points) / 1000.0
            except Exception:
                continue

        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            try:
                if entity.is_closed:
                    points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                    a = _polygon_area(points) / 1e6
                    if a > 5.0:
                        area += a
                        perimeter += _polygon_perimeter(points) / 1000.0
            except Exception:
                continue

    return {"area": round(area, 2), "perimeter": round(perimeter, 2)}


def _extract_posts(structure_entities: list, msp) -> dict:
    """Extract post/column count and positions."""
    positions = []
    count = 0

    for entity in structure_entities:
        if entity.dxftype() == "INSERT":
            name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
            if any(p in name for p in ["POST", "COL", "COLUMN", "PIER"]):
                pos = entity.dxf.insert if hasattr(entity.dxf, "insert") else (0, 0, 0)
                positions.append((pos[0], pos[1]))
                count += 1
        elif entity.dxftype() == "CIRCLE":
            center = entity.dxf.center if hasattr(entity.dxf, "center") else (0, 0, 0)
            positions.append((center[0], center[1]))
            count += 1

    return {"count": count, "positions": positions}


def _extract_stairs(entities: list) -> dict:
    """Extract stair count from stair layer entities."""
    count = 0
    for entity in entities:
        if entity.dxftype() == "INSERT":
            count += 1
        elif entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            count += 1
    stair_sets = max(1, count // 10) if count >= 10 else count
    return {"count": stair_sets}


def _extract_verandah(entities: list) -> dict:
    """Extract verandah/deck area."""
    area = 0.0
    for entity in entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices") and path.vertices:
                        points = [(v.x, v.y) for v in path.vertices]
                        area += _polygon_area(points) / 1e6
            except Exception:
                continue
        elif entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            try:
                if entity.is_closed:
                    points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                    a = _polygon_area(points) / 1e6
                    if a > 1.0:
                        area += a
            except Exception:
                continue
    return {"area": round(area, 2)}


def _extract_ceiling(entities: list) -> dict:
    """Extract ceiling area from ceiling layer entities."""
    area = 0.0
    for entity in entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices") and path.vertices:
                        points = [(v.x, v.y) for v in path.vertices]
                        area += _polygon_area(points) / 1e6
            except Exception:
                continue
        elif entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            try:
                if entity.is_closed:
                    points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                    a = _polygon_area(points) / 1e6
                    if a > 2.0:
                        area += a
            except Exception:
                continue
    return {"area": round(area, 2)}


def _polygon_area(points: list[tuple]) -> float:
    """Shoelace formula — returns absolute area."""
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return abs(area) / 2.0


def _polygon_perimeter(points: list[tuple]) -> float:
    """Calculate polygon perimeter."""
    n = len(points)
    if n < 2:
        return 0.0
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = points[j][0] - points[i][0]
        dy = points[j][1] - points[i][1]
        perimeter += math.sqrt(dx * dx + dy * dy)
    return perimeter
