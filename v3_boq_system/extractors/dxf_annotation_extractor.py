"""
dxf_annotation_extractor.py — Extract full annotation data from architectural DXF.

Recovers (beyond the V2 geometry extractor):
  - All TEXT / MTEXT entities with position, layer, content
  - DIMENSION entities: measured values + positions
  - LEADER / MLEADER annotation text
  - Block ATTRIB values (attribute tags within INSERT entities)
  - Paper-space layout text (may contain window/door schedules)
  - Spatial context: for each opening INSERT, find nearby text/dim clues

Recovery targets (priority order):
  1. Window heights  → louvre blade count + fly screen area
  2. Door external/internal classification
  3. Stair riser/tread dimensions
  4. Footing detail dimensions

Safe promotion policy: only promote when explicit evidence is found.
No invented dimensions from industry assumptions.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("boq.v3.dxf_annotation_extractor")

MM_TO_M = 1 / 1_000

# Proximity search radius (mm in DXF coordinates = plan mm)
# Text annotations are typically placed within 500 mm of the symbol they label
_PROXIMITY_MM = 1500.0

# Patterns for window dimension annotations
# Matches: "1200x900", "1100×750", "1200h900", "900H", "H=900", "HT 900"
_WIN_WxH     = re.compile(r'(\d{3,4})\s*[xX×hH]\s*(\d{3,4})')
_WIN_H_ONLY  = re.compile(r'(?:H[TT]?|height)[\s=:]*(\d{3,4})\b', re.IGNORECASE)
_WIN_MARK    = re.compile(r'^[Ww]\d{1,3}[A-Za-z]?$')
_DOOR_MARK   = re.compile(r'^[Dd]\d{1,3}[A-Za-z]?$')

# Door classification hints in nearby text
_EXT_HINTS   = re.compile(r'\b(ext(?:ernal)?|entry|entrance|front|main|exterior)\b', re.IGNORECASE)
_INT_HINTS   = re.compile(r'\b(int(?:ernal)?|interior|partition|intn\'l)\b', re.IGNORECASE)

# Stair riser/tread patterns — "12R@175" or "175R" or "14 risers" etc.
_STAIR_RISER = re.compile(r'(\d+)\s*[Rr](?:iser)?s?\s*[@x×\s]*(\d+)', re.IGNORECASE)
_STAIR_TREAD = re.compile(r'(\d+)\s*[Gg](?:oing)?s?\s*[@x×\s]*(\d+)', re.IGNORECASE)

# Footing detail — depth/size mentions near footing notation
_FTG_DEPTH   = re.compile(r'(?:depth|dp|DP|D)\s*[=:≈]?\s*(\d{2,4})\s*mm', re.IGNORECASE)
_FTG_SIZE    = re.compile(r'(\d{3,4})\s*[xX×]\s*(\d{3,4})\s*(?:pad|footing|ftg|strap)', re.IGNORECASE)


def extract_dxf_annotations(dxf_path: str | Path) -> dict:
    """
    Extract full annotation data from *dxf_path* (model space + all paper space layouts).

    Returns a dict:
    {
      "text_entities":      list[dict]  — all TEXT/MTEXT with text, x_mm, y_mm, layer
      "dimension_entities": list[dict]  — all DIMENSION measured values + positions
      "attrib_entities":    list[dict]  — ATTRIB values from block inserts
      "leader_entities":    list[dict]  — LEADER/MLEADER text + positions
      "paper_space_text":   list[dict]  — TEXT/MTEXT from all paper-space layouts
      "opening_contexts":   dict        — {window_mark: {nearby_text, nearby_dims, ...}}
      "recovered_fields":   dict        — {window_heights, door_hints, stair_details, footing_details}
      "notes":              list[str]
    }
    """
    result: dict = {
        "dxf_file":           str(dxf_path),
        "text_entities":      [],
        "dimension_entities": [],
        "attrib_entities":    [],
        "leader_entities":    [],
        "paper_space_text":   [],
        "opening_contexts":   {"windows": [], "doors": []},
        "recovered_fields": {
            "window_heights":       [],
            "door_hints":           [],
            "stair_details":        [],
            "footing_details":      [],
        },
        "notes":              [],
    }

    try:
        import ezdxf
    except ImportError:
        result["notes"].append("ezdxf not installed — DXF annotation extraction skipped")
        log.warning("ezdxf not installed — DXF annotation extraction skipped")
        return result

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as exc:
        result["notes"].append(f"Failed to open DXF: {exc}")
        log.error("Failed to open DXF %s: %s", dxf_path, exc)
        return result

    # ── Extract from model space ──────────────────────────────────────────────
    msp = doc.modelspace()
    _extract_layout_annotations(msp, "model_space", result["text_entities"],
                                 result["dimension_entities"], result["attrib_entities"],
                                 result["leader_entities"])

    # ── Extract from all paper-space layouts ──────────────────────────────────
    pspace_texts: list[dict] = []
    for layout in doc.layouts:
        if layout.name.lower() in ("model", "*model_space", "model_space"):
            continue
        _extract_layout_annotations(layout, layout.name, pspace_texts,
                                     result["dimension_entities"],
                                     result["attrib_entities"],
                                     result["leader_entities"])
    result["paper_space_text"] = pspace_texts

    n_total = (len(result["text_entities"]) + len(result["dimension_entities"])
               + len(result["attrib_entities"]) + len(pspace_texts))
    log.info(
        "DXF annotations extracted from %s: %d TEXT/MTEXT | %d DIM | %d ATTRIB | %d pspace",
        Path(dxf_path).name,
        len(result["text_entities"]),
        len(result["dimension_entities"]),
        len(result["attrib_entities"]),
        len(pspace_texts),
    )

    # ── Collect door/window INSERT positions from model space ─────────────────
    win_inserts  = _collect_inserts(msp, layer_keyword="WINDOW")
    door_inserts = _collect_inserts(msp, layer_keyword="DOOR")

    # ── Spatial context: pair openings with nearby annotations ────────────────
    all_ms_texts = result["text_entities"]
    all_ms_dims  = result["dimension_entities"]

    for ins in win_inserts:
        nearby_text = _nearby_entities(all_ms_texts, ins["x_mm"], ins["y_mm"], _PROXIMITY_MM)
        nearby_dims = _nearby_entities(all_ms_dims,  ins["x_mm"], ins["y_mm"], _PROXIMITY_MM)
        ctx = {
            "block_name":  ins["block_name"],
            "x_mm":        ins["x_mm"],
            "y_mm":        ins["y_mm"],
            "nearby_text": [e["text"] for e in nearby_text],
            "nearby_dims": [e.get("measurement") for e in nearby_dims],
        }
        result["opening_contexts"]["windows"].append(ctx)
        # Try to recover window height from nearby annotations
        h_mm = _extract_window_height(nearby_text, nearby_dims, ins["block_name"])
        if h_mm:
            result["recovered_fields"]["window_heights"].append({
                "block_name": ins["block_name"],
                "height_mm":  h_mm,
                "source":     "dxf_annotation_proximity",
                "confidence": "MEDIUM",
            })

    for ins in door_inserts:
        nearby_text = _nearby_entities(all_ms_texts, ins["x_mm"], ins["y_mm"], _PROXIMITY_MM)
        ctx = {
            "block_name": ins["block_name"],
            "x_mm":       ins["x_mm"],
            "y_mm":       ins["y_mm"],
            "nearby_text": [e["text"] for e in nearby_text],
        }
        result["opening_contexts"]["doors"].append(ctx)
        hint = _extract_door_hint(nearby_text, ins["block_name"])
        if hint:
            result["recovered_fields"]["door_hints"].append({
                "block_name":    ins["block_name"],
                "classification": hint["classification"],
                "hint_text":      hint["text"],
                "confidence":     hint["confidence"],
            })

    # ── Scan ALL text for stair details ──────────────────────────────────────
    all_texts_combined = result["text_entities"] + pspace_texts
    stair_details = _extract_stair_details(all_texts_combined)
    result["recovered_fields"]["stair_details"] = stair_details

    # ── Scan ALL text for footing details ────────────────────────────────────
    footing_details = _extract_footing_details(all_texts_combined)
    result["recovered_fields"]["footing_details"] = footing_details

    # ── Search paper space for schedule-like text patterns ───────────────────
    _scan_pspace_for_schedules(pspace_texts, result)

    # ── Summary notes ─────────────────────────────────────────────────────────
    n_win_heights = len(result["recovered_fields"]["window_heights"])
    n_door_hints  = len(result["recovered_fields"]["door_hints"])
    n_stair       = len(result["recovered_fields"]["stair_details"])
    n_footing     = len(result["recovered_fields"]["footing_details"])

    result["notes"].append(
        f"Annotation scan: {len(result['text_entities'])} model-space text entities, "
        f"{len(pspace_texts)} paper-space text entities, "
        f"{len(result['dimension_entities'])} dimensions."
    )
    result["notes"].append(
        f"Recovery: window_heights={n_win_heights}, door_hints={n_door_hints}, "
        f"stair_details={n_stair}, footing_details={n_footing}."
    )

    log.info(
        "DXF annotation recovery: win_heights=%d | door_hints=%d | stair=%d | footing=%d",
        n_win_heights, n_door_hints, n_stair, n_footing,
    )
    return result


# ── Layout entity extraction ───────────────────────────────────────────────────

def _extract_layout_annotations(
    layout, layout_name: str,
    texts: list, dims: list, attribs: list, leaders: list,
) -> None:
    """Extract all annotation entities from a single DXF layout into the provided lists."""
    for entity in layout:
        try:
            etype = entity.dxftype()

            if etype == "TEXT":
                t = entity.dxf.get("text", "").strip()
                if t:
                    pt = entity.dxf.insert
                    texts.append({
                        "text":   t,
                        "x_mm":   round(pt[0], 1),
                        "y_mm":   round(pt[1], 1),
                        "layer":  entity.dxf.get("layer", ""),
                        "height_mm": round(entity.dxf.get("height", 0), 1),
                        "layout": layout_name,
                    })

            elif etype == "MTEXT":
                raw = entity.text if hasattr(entity, "text") else entity.dxf.get("text", "")
                t = _strip_mtext_codes(raw)
                if t:
                    pt = entity.dxf.insert
                    texts.append({
                        "text":   t,
                        "x_mm":   round(pt[0], 1),
                        "y_mm":   round(pt[1], 1),
                        "layer":  entity.dxf.get("layer", ""),
                        "height_mm": round(entity.dxf.get("char_height", 0), 1),
                        "layout": layout_name,
                    })

            elif etype == "DIMENSION":
                measured = None
                try:
                    measured = round(float(entity.dxf.get("actual_measurement", 0)), 2)
                except Exception:
                    pass
                # Also try the override text
                dim_text = entity.dxf.get("text", "").strip() or ""
                # Position of the dimension line midpoint
                try:
                    pt = entity.dxf.defpoint  # definition point (origin)
                except Exception:
                    pt = (0, 0, 0)
                dims.append({
                    "measurement": measured,
                    "dim_text":    dim_text,
                    "x_mm":        round(pt[0], 1),
                    "y_mm":        round(pt[1], 1),
                    "layer":       entity.dxf.get("layer", ""),
                    "layout":      layout_name,
                })

            elif etype == "INSERT":
                # Extract ATTRIB values from block inserts
                try:
                    for attrib in entity.attribs:
                        tag_name = attrib.dxf.get("tag", "").strip().upper()
                        value    = attrib.dxf.get("text", "").strip()
                        if tag_name and value:
                            pt_a = attrib.dxf.insert
                            attribs.append({
                                "block_name": entity.dxf.get("name", ""),
                                "tag":        tag_name,
                                "value":      value,
                                "x_mm":       round(pt_a[0], 1),
                                "y_mm":       round(pt_a[1], 1),
                                "layer":      entity.dxf.get("layer", ""),
                                "layout":     layout_name,
                            })
                except (AttributeError, TypeError):
                    pass

            elif etype in ("LEADER", "MLEADER"):
                _extract_leader(entity, etype, leaders, layout_name)

        except Exception:
            pass  # robustly skip malformed entities


def _extract_leader(entity, etype: str, leaders: list, layout_name: str) -> None:
    """Extract annotation text from LEADER or MLEADER entity."""
    try:
        if etype == "MLEADER":
            # ezdxf MultiLeader: get annotation text
            mleader_text = ""
            try:
                mleader_text = entity.get_mtext_content().strip()
            except Exception:
                pass
            if not mleader_text:
                try:
                    mleader_text = entity.dxf.get("text", "").strip()
                except Exception:
                    pass
            if mleader_text:
                # Position: first leader line start or context base
                try:
                    pt = entity.context.base_point
                except Exception:
                    pt = (0, 0, 0)
                leaders.append({
                    "text":   _strip_mtext_codes(mleader_text),
                    "x_mm":   round(pt[0], 1),
                    "y_mm":   round(pt[1], 1),
                    "layer":  entity.dxf.get("layer", ""),
                    "layout": layout_name,
                })
        else:  # LEADER (old-style)
            ann_text = entity.dxf.get("text", "").strip()
            if ann_text:
                try:
                    vs = list(entity.vertices)
                    pt = vs[-1] if vs else (0, 0, 0)
                except Exception:
                    pt = (0, 0, 0)
                leaders.append({
                    "text":   ann_text,
                    "x_mm":   round(pt[0], 1),
                    "y_mm":   round(pt[1], 1),
                    "layer":  entity.dxf.get("layer", ""),
                    "layout": layout_name,
                })
    except Exception:
        pass


def _strip_mtext_codes(raw: str) -> str:
    """Remove MTEXT formatting codes (\\P, \\f, color codes, etc.)."""
    if not raw:
        return ""
    # Remove RTF-style formatting codes common in MTEXT
    t = re.sub(r'\\[a-zA-Z]+\d*(?:;|\s)?', ' ', raw)
    t = re.sub(r'\{[^}]*\}', ' ', t)        # remove {grouped} blocks
    t = re.sub(r'\\[\\;]', '', t)           # escape chars
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


# ── INSERT collector ──────────────────────────────────────────────────────────

def _collect_inserts(msp, layer_keyword: str) -> list[dict]:
    """Collect INSERT entities from model space matching a layer keyword."""
    inserts: list[dict] = []
    for entity in msp:
        try:
            if entity.dxftype() == "INSERT":
                layer = entity.dxf.get("layer", "").upper()
                if layer_keyword.upper() in layer:
                    pt = entity.dxf.insert
                    inserts.append({
                        "block_name": entity.dxf.get("name", ""),
                        "x_mm":       round(pt[0], 1),
                        "y_mm":       round(pt[1], 1),
                        "layer":      layer,
                    })
        except Exception:
            pass
    return inserts


# ── Spatial proximity ─────────────────────────────────────────────────────────

def _nearby_entities(entities: list[dict], cx: float, cy: float, radius_mm: float) -> list[dict]:
    """Return entities whose (x_mm, y_mm) is within *radius_mm* of (cx, cy)."""
    result = []
    for e in entities:
        dx = e.get("x_mm", 0) - cx
        dy = e.get("y_mm", 0) - cy
        if math.sqrt(dx * dx + dy * dy) <= radius_mm:
            result.append(e)
    return result


# ── Window height recovery ────────────────────────────────────────────────────

def _extract_window_height(nearby_text: list[dict], nearby_dims: list[dict],
                            block_name: str) -> int | None:
    """
    Try to extract window HEIGHT in mm from nearby annotations.

    Priority:
    1. Text matching WxH pattern (width × height) — takes the second value
    2. Text matching "H=NNN" or "HT NNN" pattern
    3. DIMENSION measurement in plausible window height range (400–2100 mm)
       near a window whose width might already be known — take smallest dim value
    """
    # ── 1. Explicit W×H annotation near the window ───────────────────────────
    for ent in nearby_text:
        t = ent.get("text", "")
        m = _WIN_WxH.search(t)
        if m:
            try:
                h_candidate = int(m.group(2))
                # Plausible window height: 400mm sill light to 2100mm full-height
                if 400 <= h_candidate <= 2100:
                    log.info("Window height from W×H annotation: block=%s text='%s' → h=%d mm",
                             block_name, t, h_candidate)
                    return h_candidate
            except ValueError:
                pass

    # ── 2. "H=NNN" or "HT NNN" annotation ───────────────────────────────────
    for ent in nearby_text:
        t = ent.get("text", "")
        m = _WIN_H_ONLY.search(t)
        if m:
            try:
                h_candidate = int(m.group(1))
                if 400 <= h_candidate <= 2100:
                    log.info("Window height from H-annotation: block=%s text='%s' → h=%d mm",
                             block_name, t, h_candidate)
                    return h_candidate
            except ValueError:
                pass

    # ── 3. Dimension measurement in plausible height range ───────────────────
    height_dims = []
    for d in nearby_dims:
        meas = d.get("measurement")
        if meas and 400 <= meas <= 2100:
            height_dims.append(meas)
    if height_dims:
        h_candidate = int(min(height_dims))  # take smallest plausible measurement
        log.info("Window height from DIMENSION entity: block=%s → h=%d mm", block_name, h_candidate)
        return h_candidate

    return None


# ── Door classification recovery ──────────────────────────────────────────────

def _extract_door_hint(nearby_text: list[dict], block_name: str) -> dict | None:
    """Return classification hint dict if external/internal evidence found near a door."""
    for ent in nearby_text:
        t = ent.get("text", "")
        if _EXT_HINTS.search(t):
            return {"classification": "external", "text": t[:60], "confidence": "MEDIUM"}
        if _INT_HINTS.search(t):
            return {"classification": "internal", "text": t[:60], "confidence": "MEDIUM"}
    return None


# ── Stair detail recovery ─────────────────────────────────────────────────────

def _extract_stair_details(all_texts: list[dict]) -> list[dict]:
    """Scan all text entities for stair riser/tread dimension annotations."""
    details: list[dict] = []
    stair_layers = {"stairs", "stair", "staircase", "step", "steps"}
    stair_keywords = re.compile(r'\b(stair|step|riser|going|tread|ramp)\b', re.IGNORECASE)

    for ent in all_texts:
        t = ent.get("text", "")
        layer = ent.get("layer", "").lower()
        # Check if it's on a stair layer or contains stair keywords
        on_stair_layer = any(kw in layer for kw in stair_layers)
        has_stair_kw = bool(stair_keywords.search(t))
        if not on_stair_layer and not has_stair_kw:
            continue

        m_riser = _STAIR_RISER.search(t)
        m_tread  = _STAIR_TREAD.search(t)
        if m_riser or m_tread:
            detail: dict = {
                "text":   t[:120],
                "layer":  layer,
                "x_mm":   ent.get("x_mm"),
                "y_mm":   ent.get("y_mm"),
            }
            if m_riser:
                detail["risers"]     = int(m_riser.group(1))
                detail["riser_h_mm"] = int(m_riser.group(2))
            if m_tread:
                detail["tread_d_mm"] = int(m_tread.group(2))
            details.append(detail)
            log.info("Stair detail from DXF annotation: %s", detail)
    return details


# ── Footing detail recovery ───────────────────────────────────────────────────

def _extract_footing_details(all_texts: list[dict]) -> list[dict]:
    """Scan all text entities for footing depth/size annotations."""
    details: list[dict] = []
    footing_keywords = re.compile(
        r'\b(footing|ftg|pad|slab|pier|pile|strip|strap|raft|foundation)\b', re.IGNORECASE
    )

    for ent in all_texts:
        t = ent.get("text", "")
        layer = ent.get("layer", "").lower()
        on_footing_layer = any(kw in layer for kw in ("footing", "ftg", "slab", "found"))
        has_footing_kw = bool(footing_keywords.search(t))
        if not on_footing_layer and not has_footing_kw:
            continue

        m_depth = _FTG_DEPTH.search(t)
        m_size  = _FTG_SIZE.search(t)
        if m_depth or m_size:
            detail: dict = {"text": t[:120], "layer": layer}
            if m_depth:
                detail["depth_mm"] = int(m_depth.group(1))
            if m_size:
                detail["size_mm"] = f"{m_size.group(1)}×{m_size.group(2)}"
            details.append(detail)
            log.info("Footing detail from DXF annotation: %s", detail)
    return details


# ── Paper-space schedule scanner ──────────────────────────────────────────────

def _scan_pspace_for_schedules(pspace_texts: list[dict], result: dict) -> None:
    """
    Search paper-space text entities for schedule table patterns.

    Looks for header rows like:
      - "WINDOW SCHEDULE" / "DOOR SCHEDULE" / "WINDOW TYPE" / "DOOR TYPE"
      - Followed by data rows with mark | width | height | type columns
    """
    all_text_upper = [e["text"].upper() for e in pspace_texts]

    schedule_headers = {
        "window": re.compile(r'WINDOW\s+(?:SCHEDULE|TYPE|TYPES)'),
        "door":   re.compile(r'DOOR\s+(?:SCHEDULE|TYPE|TYPES)'),
        "finish": re.compile(r'(?:ROOM\s+)?FINISH\s+SCHEDULE'),
    }

    for sched_type, pattern in schedule_headers.items():
        for txt in all_text_upper:
            if pattern.search(txt):
                result["notes"].append(
                    f"Paper-space layout contains '{sched_type.upper()} SCHEDULE' "
                    f"header — schedule may be present but not parsed as structured data "
                    f"(text extraction from DXF layouts is available)."
                )
                log.info("Paper-space schedule header found: %s", sched_type)
                # Mark as found but not parseable without column structure
                result["recovered_fields"][f"{sched_type}_schedule_header_in_pspace"] = True
                break

    # Look for dimension-like rows near schedule headers
    # Simple heuristic: consecutive text entities where first looks like a mark (W1, D1)
    # and subsequent look like dimension values
    window_rows = _parse_pspace_schedule_rows(pspace_texts, mark_pattern=_WIN_MARK)
    if window_rows:
        result["recovered_fields"]["window_heights"] += window_rows
        result["notes"].append(
            f"Paper-space: {len(window_rows)} potential window schedule row(s) detected."
        )

    door_rows = _parse_pspace_schedule_rows(pspace_texts, mark_pattern=_DOOR_MARK)
    if door_rows:
        result["notes"].append(
            f"Paper-space: {len(door_rows)} potential door schedule row(s) detected."
        )


def _parse_pspace_schedule_rows(
    pspace_texts: list[dict],
    mark_pattern: re.Pattern,
    x_tolerance_mm: float = 5000.0,   # wide — schedule columns can be far apart
    y_tolerance_mm: float = 200.0,    # tight — same row = same Y ± 200 mm
) -> list[dict]:
    """
    Try to parse schedule rows from paper-space text entities.

    Looks for groups of text entities that share the same Y coordinate (within
    tolerance) where one entity matches *mark_pattern* (e.g. W1, D2).

    For each such group: check if any other entity in the row has a W×H pattern.
    """
    recovered: list[dict] = []
    if not pspace_texts:
        return recovered

    # Group by Y coordinate
    y_groups: dict[int, list[dict]] = {}
    for ent in pspace_texts:
        y_bucket = round(ent.get("y_mm", 0) / y_tolerance_mm)
        y_groups.setdefault(y_bucket, []).append(ent)

    for y_bucket, group in y_groups.items():
        mark_ents = [e for e in group if mark_pattern.match(e.get("text", "").strip())]
        if not mark_ents:
            continue

        mark_text = mark_ents[0]["text"].strip()
        # Look for WxH dimension in same row
        for ent in group:
            t = ent.get("text", "")
            m = _WIN_WxH.search(t)
            if m:
                try:
                    h_candidate = int(m.group(2))
                    w_candidate = int(m.group(1))
                    if 400 <= h_candidate <= 2400:
                        recovered.append({
                            "mark":       mark_text,
                            "width_mm":   w_candidate,
                            "height_mm":  h_candidate,
                            "source":     "dxf_paper_space_schedule_row",
                            "confidence": "MEDIUM",
                            "row_text":   " | ".join(e["text"] for e in group[:8]),
                        })
                        log.info(
                            "Paper-space schedule row: mark=%s width=%d height=%d",
                            mark_text, w_candidate, h_candidate,
                        )
                except ValueError:
                    pass

    return recovered
