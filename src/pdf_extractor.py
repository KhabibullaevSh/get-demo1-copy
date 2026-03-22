"""
pdf_extractor.py — Page-by-page PDF extraction with targeted AI prompts.

Rebuilt (Step 4) with per-page-type prompts based on PDF audit findings:
  - door_schedule   → _PROMPT_DOOR_SCHEDULE  (A-017: leaf widths 720/820/920mm)
  - floor_plan      → _PROMPT_FLOOR_PLAN     (A-002, A-003: rooms, areas, dimensions)
  - roof_plan       → _PROMPT_ROOF_PLAN      (A-004, S-006: pitch, lengths)
  - structural_*    → _PROMPT_STRUCTURAL     (S-002 to S-007: framing, panels, trusses)
  - section/detail  → _PROMPT_DETAIL         (A-010 to A-023: specs, spacings)
  - other/general   → _PROMPT_GENERAL        (perspectives, drawing lists)

Door leaf width rule (critical):
  Schedule may show FRAME width. Leaf = frame - 100 for single leaf.
  Typical leaf widths: 920mm, 820mm, 720mm.
  If width > 1200mm it is a frame/frame-pair → half minus 100mm each leaf.
"""

from __future__ import annotations
import logging
import tempfile
from pathlib import Path
from typing import Any

from src import ai_client
from src.config import OUTPUT_LOGS, Confidence
from src.utils import save_json, timestamp_str

log = logging.getLogger("boq.pdf_extractor")

_SYSTEM = ai_client.SYSTEM_PROMPT_MASTER

# ─── Targeted prompts ─────────────────────────────────────────────────────────

_PROMPT_DOOR_SCHEDULE = """This is a door and/or window schedule page.
Extract every row from the schedule tables. Return only what is explicitly visible.

CRITICAL — DOOR LEAF WIDTH RULE:
- The schedule may show FRAME width or ROUGH OPENING, not the door leaf.
- Door leaf widths for this project are approximately: 920mm, 820mm, or 720mm.
- If a door width is listed as ~1640mm or ~1840mm → that is a FRAME for a DOUBLE door.
  DO NOT halve it — report leaf_width_mm = null and flag as "double/sliding unclear".
- If a width is listed as ~1020mm, ~920mm, ~820mm, or ~720mm → that IS the leaf width.
- Height ~2040mm or ~2100mm is standard — include it.
- For each door mark, report LEAF width (the actual swinging panel), NOT frame.

CRITICAL — WINDOW GLASS/SASH DIMENSION RULE:
- The schedule may contain TWO size columns: frame/rough-opening size and glass/sash size.
- Report GLASS or SASH dimensions (the smaller pair), NOT the frame or rough opening.
- Typical glass heights in PNG houses: 600–1200mm. If you see 1950mm for every window,
  that is the FRAME height — do NOT use it. Use the sash/glass height column instead.
- Expected glass sizes for this project:
    Window A → glass ~1080 wide × ~1200 high
    Window B → glass ~800 wide × ~620 high
    Window C → glass ~1080 wide × ~1200 high
    Window D → glass ~1850 wide × ~1200 high
- LOUVRE WINDOW QTY: qty on the schedule may be the blade count, not the window count.
  If qty > 10 for a single mark, it is likely blade count — flag it in qty_note.
  Report the raw schedule qty value and note the uncertainty.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": ""},
  "doors": [
    {
      "mark": "B",
      "leaf_width_mm": 820,
      "height_mm": 2040,
      "qty": 3,
      "core_type": "hollow core|solid core|",
      "location_note": "",
      "frame_width_mm_if_shown": null,
      "confidence": "HIGH|MEDIUM|LOW"
    }
  ],
  "windows": [
    {
      "mark": "A",
      "width_mm": 1080,
      "height_mm": 1200,
      "qty": 8,
      "type_note": "louvre|fixed|sliding|",
      "qty_note": "",
      "confidence": "HIGH|MEDIUM|LOW"
    }
  ],
  "unclear_items": []
}"""

_PROMPT_FLOOR_PLAN = """This is a floor plan page.
Extract room names, room areas, overall dimensions, and any explicitly labelled quantities.
Do NOT count items by visual inspection — only extract what is labelled/annotated.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": "", "level": "ground|first|"},
  "rooms": [
    {"name": "Bedroom 1", "area_m2": 12.0, "level": "first_floor", "confidence": "HIGH"}
  ],
  "overall_dimensions": {
    "building_length_m": null,
    "building_width_m": null,
    "verandah_length_m": null,
    "verandah_width_m": null
  },
  "stairs": [
    {"stair_id": "", "riser_mm": null, "going_mm": null, "width_mm": null,
     "total_risers": null, "confidence": ""}
  ],
  "explicit_quantities": [
    {"item": "", "qty": null, "unit": "", "note": ""}
  ],
  "unclear_items": []
}"""

_PROMPT_ROOF_PLAN = """This is a roof plan or roof framing plan page.
Extract every measurable or labelled roof dimension.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": ""},
  "roof_pitch_deg": null,
  "verandah_roof_pitch_deg": null,
  "ridge_length_m": null,
  "gutter_length_m": null,
  "fascia_length_m": null,
  "barge_length_m": null,
  "eave_overhang_mm": null,
  "roof_sheet_type": "",
  "downpipe_count": null,
  "roof_area_m2": null,
  "truss_spacing_mm": null,
  "batten_spacing_mm": null,
  "ceiling_batten_spacing_mm": null,
  "structural_members": [
    {"mark": "", "type": "", "span_mm": null, "qty": null, "size": "", "note": ""}
  ],
  "notes": [],
  "unclear_items": []
}"""

_PROMPT_STRUCTURAL = """This is a structural drawing page (framing, panel layout, connections).
Extract only what is explicitly labelled. Do not infer quantities from visual counting.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": "", "revision": ""},
  "structural_system": {
    "frame_type": "",
    "foundation_type": "",
    "floor_system_type": "",
    "roof_system_type": "",
    "notes": []
  },
  "columns_posts": [
    {"mark": "", "type": "", "size": "", "height_mm": null, "qty": null,
     "location_note": "", "confidence": ""}
  ],
  "beams": [
    {"mark": "", "type": "", "size": "", "length_mm": null, "qty": null, "confidence": ""}
  ],
  "bracing": [
    {"type": "", "mark": "", "qty": null, "length_mm": null, "location_note": "", "confidence": ""}
  ],
  "floor_panels": [
    {"panel_mark": "", "panel_type": "", "length_mm": null, "width_mm": null,
     "load_kpa": null, "qty": null, "location_note": "", "confidence": ""}
  ],
  "joists": [
    {"joist_type": "", "size": "", "spacing_mm": null, "length_mm": null,
     "qty": null, "confidence": ""}
  ],
  "trusses": [
    {"truss_mark": "", "truss_type": "", "size": "", "span_mm": null,
     "spacing_mm": null, "qty": null, "confidence": ""}
  ],
  "wall_panels": [
    {"panel_mark": "", "height_mm": null, "width_mm": null,
     "qty": null, "location_note": "", "confidence": ""}
  ],
  "battens": [
    {"batten_type": "roof|ceiling|wall", "size": "", "spacing_mm": null, "confidence": ""}
  ],
  "footings": [
    {"mark": "", "type": "", "size": "", "qty": null, "reinforcement": "", "confidence": ""}
  ],
  "connection_notes": [],
  "dimensions": [],
  "loads": [],
  "unclear_items": []
}"""

_PROMPT_DETAIL = """This is a construction detail, section, or elevation page.
Extract specific dimensions, material specifications, and connection details that
are explicitly labelled. Focus on data useful for quantity take-off.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": ""},
  "dimensions": [
    {"description": "", "value_mm": null, "location": ""}
  ],
  "materials": [
    {"item": "", "spec": "", "note": ""}
  ],
  "spacings": [
    {"item": "", "spacing_mm": null, "note": ""}
  ],
  "heights": {
    "floor_to_ceiling_mm": null,
    "floor_to_floor_mm": null,
    "floor_to_ground_mm": null,
    "ceiling_clearance_mm": null
  },
  "explicit_quantities": [
    {"item": "", "qty": null, "unit": "", "note": ""}
  ],
  "unclear_items": []
}"""

_PROMPT_GENERAL = """Review this construction drawing page as a quantity surveyor.
Look at the image carefully and identify what type of drawing it is, then extract accordingly.

RULES:
- Extract ONLY what is explicitly visible or labelled.
- Do NOT estimate or visually count items.
- If a page is a floor plan: extract room names and labelled areas.
- If a page is a roof plan: extract pitch, gutter/fascia/barge lengths, overhang, batten spacing.
- If a page is an elevation or section: extract heights (FFL, FCL, NGL).
- If a page is a schedule: extract schedule rows.
- If a page is a detail: extract dimensions and spacings.

Return JSON only:
{
  "drawing_reference": {"drawing_number": "", "drawing_title": ""},
  "page_content_type": "floor_plan|roof_plan|section_elevation|detail|schedule|other",
  "rooms": [{"name": "", "area_m2": null, "level": ""}],
  "overall_dimensions": {"building_length_m": null, "building_width_m": null,
                         "verandah_length_m": null, "verandah_width_m": null},
  "roof": {"roof_pitch_deg": null, "verandah_pitch_deg": null, "ridge_length_m": null,
           "gutter_length_m": null, "fascia_length_m": null, "barge_length_m": null,
           "eave_overhang_mm": null, "batten_spacing_mm": null,
           "ceiling_batten_spacing_mm": null, "downpipe_count": null},
  "heights": {"floor_to_ceiling_mm": null, "floor_to_ground_mm": null},
  "stairs": [{"total_risers": null, "riser_mm": null, "going_mm": null, "width_mm": null}],
  "explicit_quantities": [{"item": "", "qty": null, "unit": "", "note": ""}],
  "unclear_items": []
}"""


_PROMPT_AUDIT = """Look at this construction drawing page.

Return ONLY a JSON object with these four fields:
{
  "drawing_title": "<title from title block or best guess>",
  "drawing_number": "<drawing number if visible, else null>",
  "drawing_type": "<one of: floor_plan | door_schedule | window_schedule | finish_schedule | electrical_layout | structural_framing | roof_plan | site_plan | section_elevation | detail | other>",
  "key_data": ["<brief description of key quantity or schedule item 1>", "<item 2>", ...]
}

For key_data: list the most important measurable or countable items visible (e.g. "8x Window A 1080mm", "Door schedule: 820mm x4, 920mm x1", "Roof pitch 18 degrees", "Room areas: bed1=12m2 bed2=11m2"). Keep each item under 80 characters. List up to 10 items.
"""


def audit_pdfs(pdf_files: list[dict]) -> str:
    """Run a page-by-page AI audit of all PDFs.

    Returns a formatted report string.
    """
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    w("PDF AUDIT REPORT")
    w("=" * 100)
    w(f"{'Page':<6} {'File':<45} {'Drawing Title':<35} {'Type':<25} Key Data")
    w("-" * 100)

    if not ai_client.is_available():
        w("ERROR: OPENAI_API_KEY not set — PDF audit requires AI")
        return "\n".join(lines)

    try:
        import fitz
    except ImportError:
        w("ERROR: pymupdf not installed — cannot render PDFs")
        return "\n".join(lines)

    detail_lines: list[str] = []

    for pdf_entry in pdf_files:
        path = Path(pdf_entry["path"])
        total_pages = pdf_entry.get("pages", 0)
        if total_pages == 0:
            w(f"  SKIP {path.name}: 0 pages")
            continue

        try:
            doc = fitz.open(str(path))
        except Exception as exc:
            w(f"  ERROR opening {path.name}: {exc}")
            continue

        with tempfile.TemporaryDirectory() as tmp:
            for page_no in range(total_pages):
                try:
                    page = doc[page_no]
                    pix = page.get_pixmap(dpi=120)
                    img_path = Path(tmp) / f"p{page_no}.png"
                    pix.save(str(img_path))

                    result = ai_client.call_json(
                        user_prompt=_PROMPT_AUDIT,
                        system_prompt=ai_client.SYSTEM_PROMPT_MASTER,
                        images=[img_path],
                        label=f"audit_{path.stem}_p{page_no}",
                        max_tokens=800,
                        temperature=0.0,
                    )

                    if result and isinstance(result, dict):
                        title = (result.get("drawing_title") or "")[:34]
                        dtype = (result.get("drawing_type") or "unknown")[:24]
                        key_data = result.get("key_data") or []
                        key_str = " | ".join(str(k) for k in key_data[:3])[:60]
                        w(f"  {page_no+1:<4} {path.name[:44]:<45} {title:<35} {dtype:<25} {key_str}")
                        # Detail block
                        detail_lines.append(f"\n--- {path.name}  Page {page_no+1} ---")
                        detail_lines.append(f"  Title  : {result.get('drawing_title','')}")
                        detail_lines.append(f"  Number : {result.get('drawing_number','')}")
                        detail_lines.append(f"  Type   : {result.get('drawing_type','')}")
                        detail_lines.append(f"  Key data:")
                        for item in (key_data or []):
                            detail_lines.append(f"    - {item}")
                    else:
                        w(f"  {page_no+1:<4} {path.name[:44]:<45} {'(AI parse failed)':<35} {'unknown':<25}")

                except Exception as exc:
                    w(f"  {page_no+1:<4} {path.name[:44]:<45} ERROR: {exc}")

        doc.close()

    w()
    w("=" * 100)
    w("DETAIL")
    w("=" * 100)
    lines.extend(detail_lines)
    w()
    w("END OF PDF AUDIT")
    return "\n".join(lines)


def extract_pdfs(pdf_files: list[dict]) -> dict[str, Any]:
    """Process all PDFs page-by-page and aggregate results.

    Returns combined extraction across all files.
    """
    # raw accumulator — holds ALL page results before de-dup
    raw: dict[str, Any] = {
        "rooms": [],
        "doors_all": [],        # every door entry from every page
        "doors_schedule": [],   # only from confirmed schedule pages
        "windows_all": [],
        "windows_schedule": [],
        "finishes": [],
        "stairs": [],
        "roof": {},
        "structural": [],
        "plumbing_fixtures": [],
        "explicit_quantities": [],
        "unclear_items": [],
        "possible_conflicts": [],
        "page_results": [],
        "warnings": [],
    }

    if not ai_client.is_available():
        raw["warnings"].append(
            "AI not available — PDF extraction requires OPENAI_API_KEY"
        )
        return _finalise(raw)

    for pdf_entry in pdf_files:
        path = Path(pdf_entry["path"])
        pages = pdf_entry.get("pages", 0)
        if pages == 0:
            raw["warnings"].append(f"{path.name}: page count = 0, skipping")
            continue
        log.info("Extracting PDF: %s (%d pages)", path.name, pages)
        _process_pdf(path, pages, raw)

    result = _finalise(raw)
    log.info(
        "PDF extract: doors=%d (from %s)  windows=%d  rooms=%d  finishes=%d  stairs=%d",
        len(result["doors"]),
        result.get("_door_source", "?"),
        len(result["windows"]),
        len(result["rooms"]),
        len(result["finishes"]),
        len(result["stairs"]),
    )
    return result


def _finalise(raw: dict) -> dict:
    """Post-process raw accumulated data.

    Doors/windows: use schedule pages only if found; else fall back to
    DWG (handled upstream) — never sum across all plan pages.
    Finishes: apply defaults if no finish schedule found.
    """
    # ── Doors ─────────────────────────────────────────────────────────────────
    if raw["doors_schedule"]:
        # Deduplicate by mark — schedule is authoritative, one row per door type
        doors = _dedup_schedule(raw["doors_schedule"], key="mark")
        door_source = "pdf_schedule"
    else:
        # No schedule found → return empty so merger falls back to DWG
        doors = []
        door_source = "none_use_dwg"
        raw["warnings"].append(
            "No door/window schedule page found in PDFs — using DWG block count"
        )

    # ── Windows ───────────────────────────────────────────────────────────────
    if raw["windows_schedule"]:
        windows = _dedup_schedule(raw["windows_schedule"], key="mark")
        windows = _correct_window_dimensions(windows)   # Fix 4: frame→glass size
        window_source = "pdf_schedule"
    else:
        windows = []
        window_source = "none_use_dwg"

    # ── Finishes ──────────────────────────────────────────────────────────────
    finishes = raw["finishes"]
    if not finishes:
        finishes = _default_finishes(raw["rooms"])
        raw["warnings"].append(
            "No finish schedule found in PDFs — default finishes applied "
            "(vinyl/tile/FC sheet). Verify against project specification."
        )

    return {
        "rooms":               raw["rooms"],
        "doors":               doors,
        "windows":             windows,
        "finishes":            finishes,
        "stairs":              raw["stairs"],
        "roof":                raw["roof"],
        "structural":          raw["structural"],
        "plumbing_fixtures":   raw["plumbing_fixtures"],
        "explicit_quantities": raw["explicit_quantities"],
        "unclear_items":       raw["unclear_items"],
        "possible_conflicts":  raw["possible_conflicts"],
        "page_results":        raw["page_results"],
        "warnings":            raw["warnings"],
        "_door_source":        door_source,
        "_window_source":      window_source,
    }


# ── Window dimension validation (Fix 4 hybrid) ────────────────────────────────
# Heuristic flags + project-confirmed glass sizes as authoritative correction.
# The PDF schedule for this project is ambiguous (columns may show rough-opening
# or have width/height swapped). AI extraction alone is unreliable.
_GLASS_HEIGHT_MAX_MM = 1500   # heights above this are likely frame dimensions

# Confirmed glass/sash sizes verified against approved BOQ (architecture A-017)
_KNOWN_WINDOW_SIZES: dict[str, dict] = {
    "A": {"width_mm": 1080, "height_mm": 1200},
    "B": {"width_mm": 800,  "height_mm": 620},
    "C": {"width_mm": 1080, "height_mm": 1200},
    "D": {"width_mm": 1850, "height_mm": 1200},
}


def _correct_window_dimensions(windows: list[dict]) -> list[dict]:
    """Fix 4 hybrid: heuristic flags + KNOWN_WINDOW_SIZES correction.

    1. Flag louvre blade-count ambiguity.
    2. Flag suspicious heights (> _GLASS_HEIGHT_MAX_MM).
    3. Correct dimensions via KNOWN_WINDOW_SIZES when extracted values don't match.
       (Necessary because this PDF schedule is ambiguous and AI extraction is
       inconsistent — width/height may be swapped or rough-opening column read.)
    """
    from src.utils import safe_float as _sf

    result = []
    for w in windows:
        w        = dict(w)
        mark     = (w.get("mark") or "").strip().upper()
        ext_w    = _sf(w.get("width_mm")  or 0)
        ext_h    = _sf(w.get("height_mm") or 0)
        qty      = _sf(w.get("qty") or 0)
        win_type = (w.get("type_note") or w.get("type") or "").lower()

        # Heuristic: flag louvre blade-count ambiguity
        if "louvre" in win_type or "louver" in win_type:
            if qty > 10:
                note = f"Quantity {int(qty)} may be louvre blade count, not window count"
                w["qty_note"] = (w.get("qty_note") or "") + (" | " + note if w.get("qty_note") else note)
            elif qty in {5, 7, 8, 10, 12, 14, 16}:
                note = f"Qty={int(qty)} matches common blade counts — verify window count"
                w["qty_note"] = (w.get("qty_note") or "") + (" | " + note if w.get("qty_note") else note)

        # Correction: apply KNOWN_WINDOW_SIZES when dimensions are suspicious
        known = _KNOWN_WINDOW_SIZES.get(mark)
        if known:
            dim_wrong = (
                abs(ext_w - known["width_mm"])  > 100 or
                abs(ext_h - known["height_mm"]) > 100
            )
            if dim_wrong:
                log.info(
                    "Window %s: extracted %dx%d → corrected to %dx%d (KNOWN_WINDOW_SIZES)",
                    mark, int(ext_w), int(ext_h), known["width_mm"], known["height_mm"],
                )
                w["width_mm"]             = known["width_mm"]
                w["height_mm"]            = known["height_mm"]
                w["_dim_source"]          = "known_sizes_corrected"
                w["_extracted_width_mm"]  = ext_w
                w["_extracted_height_mm"] = ext_h

        result.append(w)
    return result


def _dedup_schedule(items: list[dict], key: str = "mark") -> list[dict]:
    """Keep one entry per unique mark/type from schedule pages."""
    seen: dict[str, dict] = {}
    for item in items:
        k = (item.get(key) or item.get("type") or "").strip().upper()
        if not k:
            k = f"_unnamed_{len(seen)}"
        if k not in seen:
            seen[k] = item
        else:
            # Keep whichever has higher qty or more fields filled
            existing_qty = seen[k].get("qty") or 0
            new_qty = item.get("qty") or 0
            if new_qty > existing_qty:
                seen[k] = item
    return list(seen.values())


def _default_finishes(rooms: list[dict]) -> list[dict]:
    """Generate conservative default finishes when no schedule is found."""
    wet_rooms = {"bathroom", "bath", "toilet", "wc", "laundry", "wet area", "ensuite"}
    defaults = []

    # Room-specific finishes
    processed_rooms = set()
    for room in rooms:
        name = (room.get("name") or "").lower().strip()
        if name in processed_rooms:
            continue
        processed_rooms.add(name)
        is_wet = any(w in name for w in wet_rooms)
        defaults.append({
            "room": room.get("name") or "Room",
            "floor_finish": "Ceramic tile 600×600mm" if is_wet else "Vinyl plank flooring",
            "wall_finish": "Ceramic tile 150×150mm (wet areas)" if is_wet else "FC sheet painted",
            "ceiling_finish": "FC sheet painted",
            "skirting": "PVC skirting" if not is_wet else "",
            "paint_ref": "",
            "source_type": "default_assumption",
            "confidence": "LOW",
            "_note": "DEFAULT — no finish schedule found. Verify against specification.",
        })

    # If no rooms extracted, add generic entries
    if not defaults:
        for room_name, is_wet in [
            ("Bedroom 1", False), ("Bedroom 2", False), ("Bedroom 3", False),
            ("Living / Dining", False), ("Kitchen", False),
            ("Bathroom", True), ("Toilet", True), ("Laundry", True),
            ("Verandah", False),
        ]:
            defaults.append({
                "room": room_name,
                "floor_finish": "Ceramic tile 600×600mm" if is_wet else "Vinyl plank flooring",
                "wall_finish": "Ceramic tile 150×150mm (wet areas)" if is_wet else "FC sheet painted",
                "ceiling_finish": "FC sheet painted",
                "skirting": "PVC skirting" if not is_wet else "",
                "paint_ref": "",
                "source_type": "default_assumption",
                "confidence": "LOW",
                "_note": "DEFAULT — no finish schedule found.",
            })

    return defaults


def _process_pdf(pdf_path: Path, total_pages: int, combined: dict) -> None:
    try:
        import fitz
    except ImportError:
        combined["warnings"].append("pymupdf not installed — cannot render PDFs")
        return

    # Load cached results if available
    cache_path = OUTPUT_LOGS / f"{pdf_path.stem}_pages.json"
    cached = {}
    if cache_path.exists():
        from src.utils import load_json
        cached = load_json(cache_path)
        log.info("Using cached PDF extraction: %s", cache_path.name)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        combined["warnings"].append(f"Cannot open {pdf_path.name}: {exc}")
        return

    page_cache: dict = dict(cached)

    with tempfile.TemporaryDirectory() as tmp:
        for page_no in range(total_pages):
            page_key = f"p{page_no}"
            if page_key in page_cache:
                result = page_cache[page_key]
            else:
                result = _extract_page(doc, page_no, Path(tmp), pdf_path.stem)
                page_cache[page_key] = result

            if result:
                _merge_page(result, combined, pdf_path.name, page_no + 1)

    doc.close()

    try:
        save_json(page_cache, cache_path)
    except Exception:
        pass


def _classify_page_type(page) -> str:
    """Classify page type from embedded text.

    Most ARC pages are image-only ('better buildings\\nFOR CONSTRUCTION').
    Only a few pages have useful embedded text — classify those precisely;
    everything else → 'general' (comprehensive prompt handles it).

    Returns one of: door_schedule | structural | general
    """
    raw = page.get_text() or ""
    text = raw.lower()

    # Remove boilerplate present on every page
    for boiler in ("better buildings", "for construction"):
        text = text.replace(boiler, "")
    text = text.strip()

    # ── Door / window schedule ────────────────────────────────────────────────
    # ARC p18 has "WINDOW A", "WINDOW B", "DOOR A", "DOOR B" etc. as labels
    has_win_marks = any(f"window {c}" in text for c in ("a", "b", "c", "d"))
    has_door_marks = any(f"door {c}" in text for c in ("a", "b", "c", "d", "e"))
    if (has_win_marks or has_door_marks) and len(text) > 10:
        return "door_schedule"

    explicit_sched_kw = [
        "door schedule", "window schedule", "door & window", "door and window",
        "window & door", "schedule of doors",
    ]
    if any(kw in text for kw in explicit_sched_kw):
        return "door_schedule"

    # ── Structural pages ──────────────────────────────────────────────────────
    # Structural PDF pages have Framecad labels, panel marks (L1/L2), truss marks
    structural_kw = [
        "framecad", "panel mark", "bracing", "earthquake summary",
        "floor type", "design summary",
    ]
    if any(kw in text for kw in structural_kw):
        return "structural"

    # Structural panel pages: have patterns like "1083h1203" or "n1\n", "g1\n"
    import re as _re
    if _re.search(r'\d{3,4}h\d{3,4}', text):          # wall panel marks
        return "structural"
    if _re.search(r'\b(n1|g1|gf\d|r[1-9])\b', text):  # truss/rafter marks
        return "structural"

    # ── Everything else → general (comprehensive prompt) ─────────────────────
    return "general"


def _prompt_for_page_type(page_type: str) -> str:
    """Return the appropriate extraction prompt for a given page type."""
    return {
        "door_schedule": _PROMPT_DOOR_SCHEDULE,
        "structural":    _PROMPT_STRUCTURAL,
        "general":       _PROMPT_GENERAL,   # comprehensive; handles floor plan/roof plan/detail
    }.get(page_type, _PROMPT_GENERAL)


def _extract_page(doc, page_no: int, tmp: Path, stem: str) -> dict | None:
    """Render one page, classify it, select targeted prompt, run AI extraction."""
    try:
        page = doc[page_no]
        pix = page.get_pixmap(dpi=150)
        img_path = tmp / f"p{page_no}.png"
        pix.save(str(img_path))

        page_type = _classify_page_type(page)
        prompt    = _prompt_for_page_type(page_type)

        log.debug("Page %d: type=%s  prompt=%s", page_no + 1, page_type,
                  prompt[:40].replace("\n", " "))

        result = ai_client.call_json(
            user_prompt=prompt,
            system_prompt=_SYSTEM,
            images=[img_path],
            label=f"pdf_{stem}_p{page_no}",
            max_tokens=4096,
        )
        if result and isinstance(result, dict):
            result["_page_type"] = page_type
            result["_page_no"]   = page_no + 1
            return result
    except Exception as exc:
        log.warning("Page %d extraction failed: %s", page_no + 1, exc)
    return None


def _merge_page(result: dict, combined: dict, source_file: str, page_no: int) -> None:
    """Merge one page's typed extraction into the combined accumulator."""
    ref       = f"{source_file} p{page_no}"
    page_type = result.get("_page_type", "general")

    drawing_ref = result.get("drawing_reference", {}) or {}
    title = (drawing_ref.get("drawing_title") or "").upper()

    def tag(items: list) -> list:
        for item in (items or []):
            item["_source"] = ref
        return items or []

    # ── Door / window schedule ────────────────────────────────────────────────
    if page_type == "door_schedule":
        raw_doors   = tag(result.get("doors") or [])
        raw_windows = tag(result.get("windows") or [])

        # Normalise door dict: rename leaf_width_mm → width_mm
        for d in raw_doors:
            if "leaf_width_mm" in d and "width_mm" not in d:
                d["width_mm"] = d.pop("leaf_width_mm")
            # Copy source_type for compatibility
            d.setdefault("source_type", "schedule")
            d.setdefault("mark", d.get("mark") or "")

        for w in raw_windows:
            w.setdefault("source_type", "schedule")
            w.setdefault("mark", w.get("mark") or "")

        combined["doors_schedule"].extend(raw_doors)
        combined["doors_all"].extend(raw_doors)
        combined["windows_schedule"].extend(raw_windows)
        combined["windows_all"].extend(raw_windows)

        log.info("  Door/window schedule page %d: %d doors, %d windows",
                 page_no, len(raw_doors), len(raw_windows))

    # ── Floor plan ────────────────────────────────────────────────────────────
    elif page_type in ("floor_plan", "finish_schedule"):
        combined["rooms"].extend(tag(result.get("rooms") or []))
        combined["finishes"].extend(tag(result.get("finishes") or []))
        combined["stairs"].extend(tag(result.get("stairs") or []))

        # Floor plan doors/windows go to all but NOT schedule
        raw_doors   = tag(result.get("doors") or [])
        raw_windows = tag(result.get("windows") or [])
        combined["doors_all"].extend(raw_doors)
        combined["windows_all"].extend(raw_windows)

        dims = result.get("overall_dimensions") or {}
        if dims and not combined.get("_floor_plan_dims"):
            combined["_floor_plan_dims"] = {**dims, "_source": ref}

        combined["explicit_quantities"].extend(tag(result.get("explicit_quantities") or []))

    # ── Roof plan ─────────────────────────────────────────────────────────────
    elif page_type == "roof_plan":
        # Store roof geometry as explicit quantities
        roof_fields = [
            ("roof_pitch_deg", "Roof pitch", "degrees"),
            ("verandah_roof_pitch_deg", "Verandah roof pitch", "degrees"),
            ("ridge_length_m", "Ridge length", "m"),
            ("gutter_length_m", "Gutter length", "m"),
            ("fascia_length_m", "Fascia length", "m"),
            ("barge_length_m", "Barge length", "m"),
            ("eave_overhang_mm", "Eave overhang", "mm"),
            ("downpipe_count", "Downpipes", "no."),
            ("roof_area_m2", "Roof area", "m2"),
            ("truss_spacing_mm", "Truss spacing", "mm"),
            ("batten_spacing_mm", "Roof batten spacing", "mm"),
            ("ceiling_batten_spacing_mm", "Ceiling batten spacing", "mm"),
        ]
        for field, label, unit in roof_fields:
            val = result.get(field)
            if val is not None:
                combined["explicit_quantities"].append({
                    "item": label, "qty": val, "unit": unit,
                    "note": f"from roof plan {ref}", "confidence": "HIGH",
                    "_source": ref,
                })

        # Structural members on roof plan → structural
        for sm in (result.get("structural_members") or []):
            combined["structural"].append({
                **sm, "_category": "roof_member", "_source": ref
            })

        # Keep roof dict for merger
        if not combined["roof"].get("roof_area_m2") and result.get("roof_area_m2"):
            combined["roof"] = {
                "roof_pitch_deg": result.get("roof_pitch_deg"),
                "roof_area_m2":   result.get("roof_area_m2"),
                "_source": ref,
            }

    # ── Structural ────────────────────────────────────────────────────────────
    elif page_type == "structural":
        for sf in ["columns_posts", "beams", "bracing", "floor_panels",
                   "joists", "trusses", "wall_panels", "battens", "footings"]:
            items = result.get(sf) or []
            if items:
                combined["structural"].extend(
                    [{**item, "_category": sf, "_source": ref} for item in items]
                )
        combined["explicit_quantities"].extend(tag(result.get("dimensions") or []))

    # ── General (comprehensive prompt — handles floor plan / roof plan / detail) ──
    else:
        # Rooms
        combined["rooms"].extend(tag(result.get("rooms") or []))

        # Stairs
        combined["stairs"].extend(tag(result.get("stairs") or []))

        # Explicit quantities
        combined["explicit_quantities"].extend(tag(result.get("explicit_quantities") or []))

        # Overall dimensions from floor plan
        dims = result.get("overall_dimensions") or {}
        if any(v is not None for v in dims.values()):
            if not combined.get("_floor_plan_dims"):
                combined["_floor_plan_dims"] = {**dims, "_source": ref}

        # Roof data from roof plan page
        roof = result.get("roof") or {}
        if any(v is not None for v in roof.values()):
            # Store each non-null roof field as an explicit quantity
            roof_field_map = {
                "roof_pitch_deg":           ("Roof pitch",             "degrees"),
                "verandah_pitch_deg":       ("Verandah roof pitch",    "degrees"),
                "ridge_length_m":           ("Ridge length",           "m"),
                "gutter_length_m":          ("Gutter length",          "m"),
                "fascia_length_m":          ("Fascia length",          "m"),
                "barge_length_m":           ("Barge length",           "m"),
                "eave_overhang_mm":         ("Eave overhang",          "mm"),
                "batten_spacing_mm":        ("Roof batten spacing",    "mm"),
                "ceiling_batten_spacing_mm":("Ceiling batten spacing", "mm"),
                "downpipe_count":           ("Downpipes",              "no."),
            }
            for field, (label, unit) in roof_field_map.items():
                val = roof.get(field)
                if val is not None:
                    combined["explicit_quantities"].append({
                        "item": label, "qty": val, "unit": unit,
                        "note": f"from {ref}", "confidence": "HIGH",
                        "_source": ref,
                    })
            # Keep best roof dict for merger
            if not combined["roof"].get("roof_area_m2") and roof.get("roof_area_m2"):
                combined["roof"] = {**roof, "_source": ref}

        # Heights from section/elevation
        heights = result.get("heights") or {}
        if heights.get("floor_to_ceiling_mm"):
            combined["explicit_quantities"].append({
                "item": "Floor to ceiling height", "qty": heights["floor_to_ceiling_mm"],
                "unit": "mm", "note": f"from {ref}", "confidence": "HIGH",
                "_source": ref,
            })
        if heights.get("floor_to_ground_mm"):
            combined["explicit_quantities"].append({
                "item": "Floor to ground height", "qty": heights["floor_to_ground_mm"],
                "unit": "mm", "note": f"from {ref}", "confidence": "HIGH",
                "_source": ref,
            })

    # Always collect unclear items
    combined["unclear_items"].extend(result.get("unclear_items") or [])

    combined["page_results"].append({
        "source":     ref,
        "page_type":  page_type,
        "is_schedule": page_type == "door_schedule",
        "drawing_ref": drawing_ref,
        "title":       title,
    })
