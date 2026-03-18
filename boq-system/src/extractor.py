"""
extractor.py — Extracts geometry from DXF files using ezdxf.

Extracts:
  - Room polygons and areas (m²)
  - Wall polylines and lengths (lm)
  - Door/window block insert counts by type
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
    "walls": ["WALL", "WALLS", "A-WALL", "S-WALL", "EXT-WALL", "INT-WALL"],
    "doors": ["DOOR", "DOORS", "A-DOOR"],
    "windows": ["WINDOW", "WINDOWS", "WIN", "A-GLAZ", "A-WINDOW"],
    "roof": ["ROOF", "ROOFING", "A-ROOF"],
    "floor": ["FLOOR", "FLOORS", "A-FLOOR", "SLAB"],
    "structure": ["STRUCT", "STRUCTURE", "S-COLS", "S-BEAM", "COLUMN", "POST"],
    "stairs": ["STAIR", "STAIRS", "A-STAIR"],
    "ceiling": ["CEILING", "CEIL", "A-CEIL"],
    "verandah": ["VERANDAH", "VERANDA", "DECK", "PORCH"],
}


def extract_geometry(doc: ezdxf.document.Drawing) -> dict:
    """Extract all construction geometry from a normalised DXF document.

    Args:
        doc: A normalised ezdxf Drawing (output of normaliser.py).

    Returns:
        project_geometry dict with keys matching standard_geometry structure.
    """
    msp = doc.modelspace()

    # Classify entities by layer
    classified = _classify_entities(msp, doc)

    # Extract each geometry category
    walls = _extract_walls(classified.get("walls", []))
    rooms = _extract_rooms(classified.get("floor", []), classified.get("walls", []))
    doors = _extract_doors(classified.get("doors", []), msp)
    windows = _extract_windows(classified.get("windows", []), msp)
    roof = _extract_roof(classified.get("roof", []))
    posts = _extract_posts(classified.get("structure", []), msp)
    stairs = _extract_stairs(classified.get("stairs", []))
    verandah = _extract_verandah(classified.get("verandah", []))
    ceiling = _extract_ceiling(classified.get("ceiling", []))

    # Build geometry dict matching standard format
    geometry = {
        "total_floor_area": rooms.get("total_area", 0.0),
        "total_wall_length": walls.get("total_length", 0.0),
        "external_wall_length": walls.get("external_length", 0.0),
        "internal_wall_length": walls.get("internal_length", 0.0),
        "roof_area": roof.get("area", 0.0),
        "roof_perimeter": roof.get("perimeter", 0.0),
        "verandah_area": verandah.get("area", 0.0),
        "ceiling_area": ceiling.get("area", 0.0),
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

    # If no layers matched, try to classify by entity type as fallback
    if all(len(v) == 0 for v in classified.values()) and unclassified:
        classified = _classify_by_type(unclassified)

    return classified


def _classify_by_type(entities: list) -> dict:
    """Fallback classification when layer names don't match patterns.
    Uses entity types and geometry heuristics."""
    classified = {k: [] for k in LAYER_PATTERNS}

    for entity in entities:
        dxftype = entity.dxftype()
        if dxftype == "INSERT":
            # Block inserts are likely doors/windows
            block_name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
            if any(d in block_name for d in ["DOOR", "DR", "D-"]):
                classified["doors"].append(entity)
            elif any(w in block_name for w in ["WINDOW", "WIN", "W-"]):
                classified["windows"].append(entity)
            elif any(p in block_name for p in ["POST", "COL", "COLUMN"]):
                classified["structure"].append(entity)
            else:
                classified["walls"].append(entity)
        elif dxftype in ("LINE", "LWPOLYLINE", "POLYLINE"):
            # Lines and polylines are typically walls
            classified["walls"].append(entity)
        elif dxftype in ("CIRCLE", "ARC"):
            classified["structure"].append(entity)
        elif dxftype == "HATCH":
            classified["floor"].append(entity)

    return classified


def _extract_walls(entities: list) -> dict:
    """Extract wall segments, lengths, and totals."""
    segments = []
    total_length = 0.0
    external_length = 0.0
    internal_length = 0.0

    for entity in entities:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer.upper() if hasattr(entity.dxf, "layer") else ""

        if dxftype == "LINE":
            start = Vec3(entity.dxf.start)
            end = Vec3(entity.dxf.end)
            length = start.distance(end)
            # Convert mm to linear metres
            length_m = length / 1000.0
            is_external = "EXT" in layer or "OUT" in layer
            segments.append({
                "start": (start.x, start.y),
                "end": (end.x, end.y),
                "length_m": length_m,
                "layer": layer,
                "external": is_external,
            })
            total_length += length_m
            if is_external:
                external_length += length_m
            else:
                internal_length += length_m

        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            try:
                points = list(entity.get_points(format="xy"))
                for i in range(len(points) - 1):
                    p1 = Vec3(points[i][0], points[i][1], 0)
                    p2 = Vec3(points[i + 1][0], points[i + 1][1], 0)
                    length = p1.distance(p2)
                    length_m = length / 1000.0
                    is_external = "EXT" in layer or "OUT" in layer
                    segments.append({
                        "start": (p1.x, p1.y),
                        "end": (p2.x, p2.y),
                        "length_m": length_m,
                        "layer": layer,
                        "external": is_external,
                    })
                    total_length += length_m
                    if is_external:
                        external_length += length_m
                    else:
                        internal_length += length_m

                # Handle closed polylines
                if entity.is_closed and len(points) > 2:
                    p1 = Vec3(points[-1][0], points[-1][1], 0)
                    p2 = Vec3(points[0][0], points[0][1], 0)
                    length = p1.distance(p2)
                    length_m = length / 1000.0
                    total_length += length_m
            except Exception:
                continue

    return {
        "total_length": round(total_length, 2),
        "external_length": round(external_length, 2),
        "internal_length": round(internal_length, 2),
        "segments": segments,
    }


def _extract_rooms(floor_entities: list, wall_entities: list) -> dict:
    """Extract room polygons and areas from floor hatches or closed polylines."""
    rooms = []
    total_area = 0.0

    # First try: floor hatches
    for entity in floor_entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices"):
                        points = [(v.x, v.y) for v in path.vertices]
                        area = _polygon_area(points) / 1e6  # mm² to m²
                        if area > 0.5:  # Ignore tiny artifacts
                            rooms.append({
                                "area_m2": round(area, 2),
                                "points": points,
                                "layer": entity.dxf.layer if hasattr(entity.dxf, "layer") else "",
                            })
                            total_area += area
            except Exception:
                continue

    # Fallback: look for closed polylines on wall layers
    if not rooms:
        for entity in wall_entities:
            if entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                try:
                    if entity.is_closed:
                        points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                        area = _polygon_area(points) / 1e6
                        if area > 2.0:  # Minimum room size 2m²
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


def _extract_doors(door_entities: list, msp) -> dict:
    """Extract door counts by type from block inserts."""
    types = {}
    total_count = 0

    # Check door layer entities
    for entity in door_entities:
        if entity.dxftype() == "INSERT":
            block_name = entity.dxf.name if hasattr(entity.dxf, "name") else "UNKNOWN"
            types[block_name] = types.get(block_name, 0) + 1
            total_count += 1

    # Also scan all inserts for door-like block names if none found on layers
    if total_count == 0:
        for entity in msp:
            if entity.dxftype() == "INSERT":
                name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
                if any(d in name for d in ["DOOR", "DR-", "D-", "ENTRY"]):
                    types[entity.dxf.name] = types.get(entity.dxf.name, 0) + 1
                    total_count += 1

    return {"total_count": total_count, "types": types}


def _extract_windows(window_entities: list, msp) -> dict:
    """Extract window counts by type from block inserts."""
    types = {}
    total_count = 0

    for entity in window_entities:
        if entity.dxftype() == "INSERT":
            block_name = entity.dxf.name if hasattr(entity.dxf, "name") else "UNKNOWN"
            types[block_name] = types.get(block_name, 0) + 1
            total_count += 1

    if total_count == 0:
        for entity in msp:
            if entity.dxftype() == "INSERT":
                name = entity.dxf.name.upper() if hasattr(entity.dxf, "name") else ""
                if any(w in name for w in ["WINDOW", "WIN-", "W-", "GLAZ"]):
                    types[entity.dxf.name] = types.get(entity.dxf.name, 0) + 1
                    total_count += 1

    return {"total_count": total_count, "types": types}


def _extract_roof(entities: list) -> dict:
    """Extract roof area and perimeter from roof layer entities."""
    area = 0.0
    perimeter = 0.0

    for entity in entities:
        dxftype = entity.dxftype()

        if dxftype == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices"):
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
                    if a > 5.0:  # Minimum roof area
                        area += a
                        perimeter += _polygon_perimeter(points) / 1000.0
            except Exception:
                continue

    return {
        "area": round(area, 2),
        "perimeter": round(perimeter, 2),
    }


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
            # Columns often represented as circles
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

    # If multiple polylines form one stair, group them
    # Heuristic: if count > 10, likely individual treads, so count as 1 stair
    stair_sets = max(1, count // 10) if count >= 10 else count

    return {"count": stair_sets}


def _extract_verandah(entities: list) -> dict:
    """Extract verandah/deck area."""
    area = 0.0
    for entity in entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices"):
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
    """Extract ceiling area."""
    area = 0.0
    for entity in entities:
        if entity.dxftype() == "HATCH":
            try:
                for path in entity.paths:
                    if hasattr(path, "vertices"):
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
    """Calculate area of a polygon using the Shoelace formula.
    Returns absolute area (always positive)."""
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
    """Calculate perimeter of a polygon."""
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
