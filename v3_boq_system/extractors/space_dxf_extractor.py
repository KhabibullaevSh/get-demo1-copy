"""
space_dxf_extractor.py — Extract room/space polygons and labels from DXF.

Looks for:
  1. Closed LWPOLYLINE entities on room-specific layers (ROOMS, ROOM_BOUNDS, etc.)
  2. TEXT / MTEXT entities whose content matches known room name patterns
  3. HATCH entities whose boundary paths form room outlines

Each detected space returns a dict with:
  space_name, polygon (pts in metres), area_m2, perimeter_m, label_text,
  source_layer, confidence

When no room data is found (e.g. this DXF only has WALLS/ROOF/VERANDAH/CEILING
layers with no TEXT entities), returns an empty list — the space_builder falls
back to config room_schedule.

Architecture:
  DXF file → extract_spaces_from_dxf() → list[dict] → space_builder → SpaceElement[]
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("boq.v3.space_dxf_extractor")

MM_TO_M   = 1 / 1_000
MM2_TO_M2 = 1 / 1_000_000

# Room name keywords accepted as room labels.
# Rejects dimension strings (digits only / mm / m²), door/window tags,
# wall type tags, and free-form notes.
_ROOM_KEYWORDS = {
    "bedroom", "bed", "bed rm", "master", "ensuite",
    "bathroom", "bath", "shower",
    "toilet", "wc", "water closet", "wc room",
    "laundry", "wash",
    "kitchen", "kitchenette", "tea point", "pantry",
    "dining", "living", "lounge", "family",
    "office", "admin", "administration", "manager", "staff room",
    "store", "storage", "storeroom",
    "waiting", "reception", "lobby", "foyer",
    "corridor", "passage", "hallway", "hall",
    "consulting", "consultation", "exam", "examination", "treatment",
    "pharmacy", "dispensary", "dispensing",
    "plant", "electrical room", "switch room", "mechanical",
    "cleaner", "janitor", "mop",
    "porch", "verandah", "veranda", "alfresco", "deck",
    "garage", "carport",
    "void", "shaft",
}

_REJECT_PATTERNS = re.compile(
    r"^\d[\d\.,\s]*$"         # pure numbers / dimensions
    r"|^\d+\s*(mm|m2|m²|sqm)" # dimension annotations
    r"|^[A-Z]\d{1,3}$"        # door / window tags (e.g. D01, W3)
    r"|^(GL|RL|FL|FFL|NGL)"   # level annotations
    r"|^(TYP|SIM|NTS|N\.T\.S)",  # drawing notes
    re.IGNORECASE,
)


def _is_room_label(text: str) -> bool:
    """Return True when *text* looks like a room name, not a dimension or tag."""
    t = text.strip()
    if not t or len(t) < 3:
        return False
    if _REJECT_PATTERNS.match(t):
        return False
    tl = t.lower()
    return any(kw in tl for kw in _ROOM_KEYWORDS)


def _lwpoly_pts_m(entity) -> list[tuple[float, float]]:
    return [(p[0] * MM_TO_M, p[1] * MM_TO_M) for p in entity.get_points("xy")]


def _poly_area_perim(pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Compute area (m²) and perimeter (m) of a closed polygon."""
    try:
        from shapely.geometry import Polygon
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_valid and poly.area > 0:
            return abs(poly.area), poly.length
    except ImportError:
        pass
    return 0.0, 0.0


def extract_spaces_from_dxf(dxf_path: str | Path) -> list[dict]:
    """
    Extract room/space polygons from a DXF file.

    Returns a list of dicts:
      {space_name, polygon, area_m2, perimeter_m, source_layer,
       label_source, confidence}

    Returns [] when no room data is found (no TEXT entities, no room layers).
    This triggers the config-fallback path in space_builder.py.
    """
    results: list[dict] = []
    try:
        import ezdxf
    except ImportError:
        log.warning("ezdxf not installed — DXF space extraction unavailable")
        return results

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        log.warning("DXF open failed [%s]: %s", dxf_path, exc)
        return results

    msp = doc.modelspace()

    # ── Step 1: collect TEXT / MTEXT that look like room labels ──────────────
    text_labels: list[dict] = []
    for ent in msp.query("TEXT MTEXT"):
        raw = (ent.dxf.text if hasattr(ent.dxf, "text") else "") or ""
        raw = raw.replace("\\P", " ").replace("\\n", " ").strip()
        if not _is_room_label(raw):
            continue
        # Get insert position
        pos = getattr(ent.dxf, "insert", None) or getattr(ent.dxf, "attachment_point", None)
        if pos is None:
            continue
        x = pos[0] * MM_TO_M if hasattr(pos, "__getitem__") else 0.0
        y = pos[1] * MM_TO_M if hasattr(pos, "__getitem__") else 0.0
        text_labels.append({
            "text": raw,
            "x": x, "y": y,
            "layer": ent.dxf.layer,
        })

    if not text_labels:
        log.info("DXF space extraction: no TEXT/MTEXT room labels found in %s", Path(dxf_path).name)
        return []

    log.info("DXF space extraction: found %d room labels in %s", len(text_labels), Path(dxf_path).name)

    # ── Step 2: find closed LWPOLYLINE on room-related layers ────────────────
    room_poly_layers = {"ROOMS", "ROOM", "ROOM_BOUNDS", "ROOM_BOUNDARY",
                        "SPACE", "SPACES", "FLOOR_PLAN", "FLOOR PLAN", "AREA"}
    all_layers = {e.dxf.layer.upper() for e in msp}
    candidate_layers = room_poly_layers & all_layers or {"WALLS"}  # fallback to WALLS

    closed_polys: list[dict] = []
    for layer in candidate_layers:
        for ent in msp.query(f'LWPOLYLINE[layer=="{layer}"]'):
            pts = _lwpoly_pts_m(ent)
            if len(pts) < 3:
                continue
            is_closed = ent.dxf.flags & 1  # LWPOLYLINE closed flag
            if not is_closed and len(pts) >= 3:
                # Accept if first/last points are close
                dx = pts[0][0] - pts[-1][0]
                dy = pts[0][1] - pts[-1][1]
                import math
                is_closed = math.hypot(dx, dy) < 0.1  # within 100mm
            if not is_closed:
                continue
            area, perim = _poly_area_perim(pts)
            if area < 1.0:  # skip tiny polygons < 1 m²
                continue
            closed_polys.append({
                "pts": pts, "area_m2": area, "perimeter_m": perim, "layer": layer
            })

    if not closed_polys:
        log.info("DXF space extraction: no closed room polygons found — labels without boundaries")
        # Emit label-only spaces (no polygon, area unknown)
        for lbl in text_labels:
            results.append({
                "space_name": lbl["text"],
                "polygon": [],
                "area_m2": 0.0,
                "perimeter_m": 0.0,
                "source_layer": lbl["layer"],
                "label_source": "dxf_text",
                "confidence": "LOW",
                "notes": "Room label found in DXF but no closed room polygon on any layer.",
            })
        return results

    # ── Step 3: assign labels to polygons by point-in-polygon test ───────────
    try:
        from shapely.geometry import Point, Polygon as ShPoly
        shapely_ok = True
    except ImportError:
        shapely_ok = False

    used_labels: set[int] = set()
    for poly_dict in closed_polys:
        pts = poly_dict["pts"]
        matched_label = None

        if shapely_ok:
            shape = ShPoly(pts)
            if not shape.is_valid:
                shape = shape.buffer(0)
            for i, lbl in enumerate(text_labels):
                if i in used_labels:
                    continue
                if shape.contains(Point(lbl["x"], lbl["y"])):
                    matched_label = lbl
                    used_labels.add(i)
                    break

        if matched_label is None:
            # Fallback: nearest centroid
            import math
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            best_dist = float("inf")
            best_idx  = -1
            for i, lbl in enumerate(text_labels):
                if i in used_labels:
                    continue
                d = math.hypot(lbl["x"] - cx, lbl["y"] - cy)
                if d < best_dist and d < 5.0:  # within 5 m of centroid
                    best_dist = d
                    best_idx  = i
            if best_idx >= 0:
                matched_label = text_labels[best_idx]
                used_labels.add(best_idx)

        name = matched_label["text"] if matched_label else f"Room_{len(results)+1}"
        conf = "HIGH" if matched_label else "LOW"
        note = (
            "Room polygon matched to label by point-in-polygon test." if matched_label
            else "Room polygon found but no matching label — name placeholder."
        )
        results.append({
            "space_name": name,
            "polygon": [[round(p[0], 4), round(p[1], 4)] for p in pts],
            "area_m2": round(poly_dict["area_m2"], 3),
            "perimeter_m": round(poly_dict["perimeter_m"], 3),
            "source_layer": poly_dict["layer"],
            "label_source": "dxf_polygon+text" if matched_label else "dxf_polygon",
            "confidence": conf,
            "notes": note,
        })

    # Unmatched labels (labels with no enclosing polygon)
    for i, lbl in enumerate(text_labels):
        if i not in used_labels:
            results.append({
                "space_name": lbl["text"],
                "polygon": [],
                "area_m2": 0.0,
                "perimeter_m": 0.0,
                "source_layer": lbl["layer"],
                "label_source": "dxf_text_only",
                "confidence": "LOW",
                "notes": "Room label in DXF but not enclosed by any room polygon.",
            })

    log.info("DXF space extraction: %d spaces from %s", len(results), Path(dxf_path).name)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Wall-network zone decomposition (no labels needed)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_zone_perimeter(
    zone_cells: list[tuple[int, int]],
    x_coords: list[float],
    y_coords: list[float],
) -> float:
    """Perimeter = sum of all cell edges that are on the zone outer boundary."""
    cell_set = set(zone_cells)
    perim = 0.0
    for ix, iy in zone_cells:
        w = x_coords[ix + 1] - x_coords[ix]
        h = y_coords[iy + 1] - y_coords[iy]
        if (ix - 1, iy) not in cell_set:
            perim += h
        if (ix + 1, iy) not in cell_set:
            perim += h
        if (ix, iy - 1) not in cell_set:
            perim += w
        if (ix, iy + 1) not in cell_set:
            perim += w
    return perim


def extract_spaces_from_wall_network(dxf_path: str | Path) -> list[dict]:
    """
    Decompose the building interior into enclosed zones using axis-aligned
    wall-network analysis — no room labels needed.

    Algorithm:
      1. Read WALLS layer LWPOLYLINEs → axis-aligned line segments (H/V).
      2. Determine building bounding box (from large closed LWPOLYLINE or extent).
      3. Build X-grid and Y-grid from all unique wall coordinates + boundary.
      4. BFS / flood-fill across grid cells, blocked only by actual wall segments
         or the building boundary.
      5. Each connected region of cells = one enclosed zone.

    Returns a list of zone dicts:
      {zone_id, area_m2, perimeter_m, bbox, cell_count,
       confidence, source_type, notes}

    Confidence is MEDIUM: geometry is measured from DXF, but room identity
    (which zone is which room) is unknown without labels.
    Returns [] when no axis-aligned walls are found.
    """
    import math as _math
    from collections import deque

    try:
        import ezdxf
    except ImportError:
        log.warning("ezdxf not installed — wall-network extraction unavailable")
        return []

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        log.warning("DXF open failed for wall-network [%s]: %s", dxf_path, exc)
        return []

    msp = doc.modelspace()

    AXIS_TOL  = 0.002   # 2 mm — axis-alignment tolerance
    COORD_TOL = 0.006   # 6 mm — coordinate snap tolerance

    # ── Step 1: Extract axis-aligned segments from WALLS layer ────────────────
    h_segs: list[tuple[float, float, float]] = []   # (x_lo, x_hi, y)
    v_segs: list[tuple[float, float, float]] = []   # (x, y_lo, y_hi)

    for ent in msp.query('LWPOLYLINE[layer=="WALLS"]'):
        pts_raw = list(ent.get_points("xy"))
        pts = [(p[0] * MM_TO_M, p[1] * MM_TO_M) for p in pts_raw]
        # Iterate consecutive pairs (including closing segment for closed polys)
        n = len(pts)
        if n < 2:
            continue
        pairs = [(pts[i], pts[i + 1]) for i in range(n - 1)]
        if ent.dxf.flags & 1 and n >= 3:        # closed flag
            pairs.append((pts[-1], pts[0]))
        for (x0, y0), (x1, y1) in pairs:
            if _math.hypot(x1 - x0, y1 - y0) < COORD_TOL:
                continue
            if abs(y1 - y0) < AXIS_TOL:          # horizontal
                h_segs.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            elif abs(x1 - x0) < AXIS_TOL:        # vertical
                v_segs.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))

    if not h_segs and not v_segs:
        log.info("Wall-network: no axis-aligned wall segments in %s", Path(dxf_path).name)
        return []

    # ── Step 2: Building bounding box ─────────────────────────────────────────
    # Try large closed LWPOLYLINE on any layer first
    bld_xmin = bld_xmax = bld_ymin = bld_ymax = None
    for ent in msp.query("LWPOLYLINE"):
        if not (ent.dxf.flags & 1):
            continue
        pts = [(p[0] * MM_TO_M, p[1] * MM_TO_M) for p in ent.get_points("xy")]
        if len(pts) < 4:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # Shoelace area
        area = abs(sum(
            xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i]
            for i in range(len(xs))
        )) / 2.0
        if area > 10.0:   # must be at least 10 m²
            x0c, x1c = min(xs), max(xs)
            y0c, y1c = min(ys), max(ys)
            if bld_xmin is None or (x1c - x0c) * (y1c - y0c) > (bld_xmax - bld_xmin) * (bld_ymax - bld_ymin):
                bld_xmin, bld_xmax = x0c, x1c
                bld_ymin, bld_ymax = y0c, y1c

    if bld_xmin is None:
        # Fallback: extent of all wall segments
        all_xs = [s[0] for s in h_segs] + [s[1] for s in h_segs] + [s[0] for s in v_segs]
        all_ys = [s[2] for s in h_segs] + [s[1] for s in v_segs] + [s[2] for s in v_segs]
        bld_xmin, bld_xmax = min(all_xs), max(all_xs)
        bld_ymin, bld_ymax = min(all_ys), max(all_ys)

    log.info(
        "Wall-network: bldg bbox (%.2f,%.2f)–(%.2f,%.2f), %d H-segs, %d V-segs",
        bld_xmin, bld_ymin, bld_xmax, bld_ymax, len(h_segs), len(v_segs),
    )

    # ── Step 3: Build grid ────────────────────────────────────────────────────
    def _snap(vals: list[float]) -> list[float]:
        sv = sorted(set(vals))
        out = [sv[0]]
        for v in sv[1:]:
            if v - out[-1] > COORD_TOL:
                out.append(v)
        return out

    x_coords = _snap(
        [bld_xmin, bld_xmax]
        + [s[0] for s in v_segs]
        + [s[0] for s in h_segs]
        + [s[1] for s in h_segs]
    )
    y_coords = _snap(
        [bld_ymin, bld_ymax]
        + [s[1] for s in v_segs]
        + [s[2] for s in v_segs]
        + [s[2] for s in h_segs]
    )

    nx = len(x_coords) - 1
    ny = len(y_coords) - 1

    if nx < 1 or ny < 1:
        return []

    # ── Step 4: Wall / boundary edge tests ───────────────────────────────────
    def _has_v_wall(x_val: float, y_lo: float, y_hi: float) -> bool:
        """True when x=x_val is the building boundary or a vertical wall spanning [y_lo, y_hi]."""
        if abs(x_val - bld_xmin) < COORD_TOL or abs(x_val - bld_xmax) < COORD_TOL:
            return True
        for sx, sy0, sy1 in v_segs:
            if abs(sx - x_val) < COORD_TOL and sy0 <= y_lo + COORD_TOL and sy1 >= y_hi - COORD_TOL:
                return True
        return False

    def _has_h_wall(y_val: float, x_lo: float, x_hi: float) -> bool:
        """True when y=y_val is the building boundary or a horizontal wall spanning [x_lo, x_hi]."""
        if abs(y_val - bld_ymin) < COORD_TOL or abs(y_val - bld_ymax) < COORD_TOL:
            return True
        for sx0, sx1, sy in h_segs:
            if abs(sy - y_val) < COORD_TOL and sx0 <= x_lo + COORD_TOL and sx1 >= x_hi - COORD_TOL:
                return True
        return False

    # ── Step 5: BFS flood-fill ────────────────────────────────────────────────
    visited = [[False] * ny for _ in range(nx)]
    zones: list[dict] = []

    for start_ix in range(nx):
        for start_iy in range(ny):
            if visited[start_ix][start_iy]:
                continue
            queue: deque[tuple[int, int]] = deque([(start_ix, start_iy)])
            visited[start_ix][start_iy] = True
            zone_cells: list[tuple[int, int]] = [(start_ix, start_iy)]

            while queue:
                ix, iy = queue.popleft()
                x0c = x_coords[ix];  x1c = x_coords[ix + 1]
                y0c = y_coords[iy];  y1c = y_coords[iy + 1]

                for nix, niy, edge_check in (
                    (ix - 1, iy,     lambda: _has_v_wall(x0c, y0c, y1c)),
                    (ix + 1, iy,     lambda: _has_v_wall(x1c, y0c, y1c)),
                    (ix,     iy - 1, lambda: _has_h_wall(y0c, x0c, x1c)),
                    (ix,     iy + 1, lambda: _has_h_wall(y1c, x0c, x1c)),
                ):
                    if nix < 0 or nix >= nx or niy < 0 or niy >= ny:
                        continue
                    if visited[nix][niy]:
                        continue
                    if not edge_check():
                        visited[nix][niy] = True
                        zone_cells.append((nix, niy))
                        queue.append((nix, niy))

            # Compute zone metrics
            zone_area = round(sum(
                (x_coords[ix + 1] - x_coords[ix]) * (y_coords[iy + 1] - y_coords[iy])
                for ix, iy in zone_cells
            ), 4)
            if zone_area < 0.5:   # skip sub-0.5 m² fragments (structural gaps)
                continue

            zone_perim = round(
                _compute_zone_perimeter(zone_cells, x_coords, y_coords), 3
            )
            zone_xs = {x_coords[ix] for ix, _ in zone_cells} | {x_coords[ix + 1] for ix, _ in zone_cells}
            zone_ys = {y_coords[iy] for _, iy in zone_cells} | {y_coords[iy + 1] for _, iy in zone_cells}
            bbox = (round(min(zone_xs), 4), round(min(zone_ys), 4),
                    round(max(zone_xs), 4), round(max(zone_ys), 4))

            z_id = f"zone_{len(zones) + 1}"
            zones.append({
                "zone_id":     z_id,
                "area_m2":     zone_area,
                "perimeter_m": zone_perim,
                "bbox":        bbox,
                "cell_count":  len(zone_cells),
                "confidence":  "MEDIUM",
                "source_type": "dxf_wall_network",
                "notes": (
                    f"DXF wall-network {z_id}: {len(zone_cells)} cell(s), "
                    f"area={zone_area:.2f} m², perim={zone_perim:.2f} m, "
                    f"bbox=({bbox[0]:.2f},{bbox[1]:.2f})–({bbox[2]:.2f},{bbox[3]:.2f})"
                ),
            })

    log.info(
        "Wall-network: %d zones from %s (%.2f m² total)",
        len(zones), Path(dxf_path).name,
        sum(z["area_m2"] for z in zones),
    )
    return zones
