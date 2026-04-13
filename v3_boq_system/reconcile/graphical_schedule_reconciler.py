"""
graphical_schedule_reconciler.py — Reconcile graphical/annotation evidence to BOQ items.

Combines outputs from:
  - dxf_annotation_extractor  (TEXT/MTEXT/DIM/ATTRIB proximity analysis)
  - pdf_graphics_analyzer      (vector line table-grid detection)
  - pdf_targeted_ocr           (OCR results from rasterized schedule crops)

Applies the same safe promotion policy as other reconcilers:
  - Promote ONLY when recovered evidence is explicit enough to justify
  - Keep rows blocked/manual_review if evidence is ambiguous or missing
  - Never invent dimensions from industry assumptions

Priority order:
  1. Window heights (unblocks louvre blade counts + fly screen areas)
  2. Door external/internal classification
  3. Stair riser/tread dimensions
  4. Footing detail dimensions
"""
from __future__ import annotations

import logging

log = logging.getLogger("boq.v3.reconcile.graphical")


def reconcile_graphical_evidence(
    dxf_annotation_result: dict,
    pdf_graphics_result: dict,
    ocr_result: dict,
    element_model,
    config: dict,
) -> dict:
    """
    Reconcile all graphical/annotation evidence to produce promotion decisions.

    Returns:
    {
      promoted_rows:           list[dict]  — updates to existing BOQ fields
      still_blocked:           list[dict]  — items that remain blocked + why
      window_heights_recovered: list[dict] — {mark, height_mm, source, confidence}
      door_hints_recovered:    list[dict]
      stair_details_recovered: list[dict]
      footing_details_recovered: list[dict]
      ocr_regions_detected:    int
      ocr_backend:             str
      table_regions_detected:  int
      notes:                   list[str]
    }
    """
    result: dict = {
        "promoted_rows":             [],
        "still_blocked":             [],
        "window_heights_recovered":  [],
        "door_hints_recovered":      [],
        "stair_details_recovered":   [],
        "footing_details_recovered": [],
        "ocr_regions_detected":      len(ocr_result.get("crops", [])),
        "ocr_backend":               ocr_result.get("ocr_backend", "unavailable"),
        "table_regions_detected":    len(pdf_graphics_result.get("all_regions", [])),
        "notes":                     [],
    }

    # ── Collect all recovered window heights ──────────────────────────────────
    all_win_heights: list[dict] = []

    # From DXF annotation proximity search
    dxf_win_heights = dxf_annotation_result.get("recovered_fields", {}).get("window_heights", [])
    all_win_heights.extend(dxf_win_heights)

    # From OCR recovered rows (schedule-typed rows with explicit marks)
    ocr_win_rows = ocr_result.get("recovered", {}).get("window_schedule_rows", [])
    for row in ocr_win_rows:
        if row.get("height_mm"):
            all_win_heights.append({
                "mark":       row.get("mark"),
                "height_mm":  row["height_mm"],
                "width_mm":   row.get("width_mm"),
                "source":     f"ocr_{ocr_result.get('ocr_backend', 'unknown')}",
                "confidence": "MEDIUM" if row.get("mark") else "LOW",
            })

    # From FrameCAD panel labels — WIDTHhHEIGHT patterns in OCR text, matched
    # to DXF windows by width.  These labels carry no window mark so standard
    # schedule-row routing doesn't surface them.
    framecad_heights = _extract_framecad_label_heights(
        ocr_result, element_model, pdf_graphics_result
    )
    if framecad_heights:
        all_win_heights.extend(framecad_heights)
        result["notes"].append(
            f"FrameCAD panel label OCR: {len(framecad_heights)} window height(s) "
            f"matched by width from FrameCAD layout drawing labels."
        )

    result["window_heights_recovered"] = all_win_heights

    # ── Collect door classification hints ─────────────────────────────────────
    dxf_door_hints = dxf_annotation_result.get("recovered_fields", {}).get("door_hints", [])
    ocr_door_rows  = ocr_result.get("recovered", {}).get("door_schedule_rows", [])
    all_door_hints = list(dxf_door_hints)
    for row in ocr_door_rows:
        t = row.get("type_text", "")
        if t:
            all_door_hints.append({
                "block_name":     row.get("mark", "?"),
                "classification": "external" if "ext" in t.lower() else "internal",
                "hint_text":      t,
                "confidence":     "LOW",  # OCR door rows are lower confidence
            })
    result["door_hints_recovered"] = all_door_hints

    # ── Collect stair + footing details ───────────────────────────────────────
    result["stair_details_recovered"] = (
        dxf_annotation_result.get("recovered_fields", {}).get("stair_details", [])
    )
    result["footing_details_recovered"] = (
        dxf_annotation_result.get("recovered_fields", {}).get("footing_details", [])
    )

    # ── Promotion decisions ───────────────────────────────────────────────────
    _promote_window_heights(all_win_heights, element_model, config, result)
    _promote_door_hints(all_door_hints, element_model, config, result)
    _promote_stair_details(result["stair_details_recovered"], element_model, config, result)
    _promote_footing_details(result["footing_details_recovered"], element_model, config, result)

    # ── Document still-blocked items ──────────────────────────────────────────
    _document_still_blocked(result, element_model, config)

    # ── Summary note ─────────────────────────────────────────────────────────
    n_promoted = len(result["promoted_rows"])
    n_blocked  = len(result["still_blocked"])
    n_table    = result["table_regions_detected"]
    n_ocr      = result["ocr_regions_detected"]

    result["notes"].append(
        f"Graphical recovery: {n_table} table regions detected in PDFs, "
        f"{n_ocr} crops processed via OCR ({result['ocr_backend']}), "
        f"{len(all_win_heights)} window heights recovered, "
        f"{len(all_door_hints)} door hints recovered, "
        f"{len(result['stair_details_recovered'])} stair details, "
        f"{len(result['footing_details_recovered'])} footing details."
    )
    result["notes"].append(
        f"Promoted: {n_promoted} BOQ field updates. Still blocked: {n_blocked} items."
    )

    log.info(
        "Graphical reconciler: win_h=%d | door_hints=%d | stair=%d | footing=%d "
        "| promoted=%d | blocked=%d",
        len(all_win_heights), len(all_door_hints),
        len(result["stair_details_recovered"]), len(result["footing_details_recovered"]),
        n_promoted, n_blocked,
    )
    return result


# ── Promotion functions ───────────────────────────────────────────────────────

def _extract_framecad_label_heights(
    ocr_result: dict,
    element_model,
    pdf_graphics_result: dict | None = None,
) -> list[dict]:
    """
    Extract window heights from FrameCAD panel label patterns in OCR text.

    FrameCAD wall panel drawings label each structural opening as WIDTHhHEIGHT
    (e.g. 1083h1203, 803h623) in mm.  These are the structural rough-opening
    dimensions.  The labels carry no window mark, so we assign them to windows
    by matching the width to DXF block widths within a 1 % tolerance.

    Only promotes when exactly ONE DXF window matches the label width.
    """
    import re
    _LABEL_PAT = re.compile(r'(\d{3,4})[hH](\d{3,4})')

    # Build dxf_width_mm → mark lookup
    width_to_mark: dict[int, str] = {}
    for opening in element_model.openings:
        if opening.opening_type == "window" and opening.width_m > 0:
            width_to_mark[round(opening.width_m * 1000)] = opening.mark

    if not width_to_mark:
        return []

    # Collect (width_mm, height_mm) occurrence counts from all text sources.
    # Priority: embedded PDF page text (clean fitz extraction) > OCR crop text
    # (OCR text may concatenate adjacent labels, producing false dimensions).
    pair_counts: dict[tuple[int, int], int] = {}

    def _scan_text(text: str) -> None:
        for m in _LABEL_PAT.finditer(text):
            w_mm = int(m.group(1))
            h_mm = int(m.group(2))
            if 300 <= w_mm <= 3000 and 400 <= h_mm <= 2100:
                pair_counts[(w_mm, h_mm)] = pair_counts.get((w_mm, h_mm), 0) + 1

    # 1. Embedded PDF text (clean; preferred source)
    if pdf_graphics_result:
        for page in pdf_graphics_result.get("pages", []):
            page_text = page.get("page_text", "") or ""
            if page_text:
                _scan_text(page_text)

    # 2. OCR crop texts (fallback; may have concatenation artefacts)
    for crop in ocr_result.get("crops", []):
        text = crop.get("ocr_text", "") or ""
        if text:
            _scan_text(text)

    if not pair_counts:
        return []

    results: list[dict] = []
    already_promoted: set[str] = set()

    for (w_mm, h_mm), count in sorted(pair_counts.items()):
        # Find DXF windows within 1 % width tolerance
        matches = []
        for dxf_w, mark in width_to_mark.items():
            delta_pct = abs(dxf_w - w_mm) / max(dxf_w, w_mm) * 100
            if delta_pct <= 1.0:
                matches.append((delta_pct, mark, dxf_w))

        if len(matches) != 1:
            log.debug(
                "FrameCAD label %dh%d: %d width matches (need exactly 1) — skipped",
                w_mm, h_mm, len(matches),
            )
            continue

        delta_pct, mark, dxf_w = matches[0]

        if mark in already_promoted:
            log.debug(
                "FrameCAD label %dh%d: mark %s already has a height — skipped",
                w_mm, h_mm, mark,
            )
            continue

        results.append({
            "mark":        mark,
            "height_mm":   h_mm,
            "width_mm":    w_mm,
            "label_count": count,
            "source":      "framecad_panel_label_ocr",
            "confidence":  "MEDIUM",
            "note": (
                f"FrameCAD wall-panel label {w_mm}h{h_mm} (×{count} OCR hit(s)) matched "
                f"to DXF {mark} (DXF width {dxf_w}mm, Δ={delta_pct:.1f}%). "
                f"Height {h_mm}mm is the structural rough-opening height from "
                f"FrameCAD wall panel layout drawing."
            ),
        })
        already_promoted.add(mark)
        log.info(
            "FrameCAD label width-match: %dh%d ×%d → mark=%s DXF_w=%dmm Δ=%.1f%%",
            w_mm, h_mm, count, mark, dxf_w, delta_pct,
        )

    return results


def _promote_window_heights(
    win_heights: list[dict],
    element_model,
    config: dict,
    result: dict,
) -> None:
    """
    Promote window heights to OpeningElement instances.

    Safe policy: only promote when mark matches a known window opening AND
    height is within 400–2100 mm (plausible window height).
    """
    if not win_heights:
        return

    # Build mark→OpeningElement index
    win_elements = {
        o.mark.upper(): o
        for o in element_model.openings
        if o.opening_type == "window"
    }

    promoted_marks = set()
    for h_rec in win_heights:
        h_mm = h_rec.get("height_mm")
        mark = (h_rec.get("mark") or "").upper()
        source = h_rec.get("source", "dxf_annotation")
        confidence = h_rec.get("confidence", "MEDIUM")

        if not h_mm or not (400 <= h_mm <= 2100):
            continue

        if mark and mark in win_elements:
            # Update the element model in-place
            elem = win_elements[mark]
            if elem.height_m == 0.0:   # only if not already set
                elem.height_m = round(h_mm / 1000, 3)
                elem.source   = source
                result["promoted_rows"].append({
                    "target":       "OpeningElement.height_m",
                    "mark":         mark,
                    "height_m":     elem.height_m,
                    "height_mm":    h_mm,
                    "source":       source,
                    "confidence":   confidence,
                    "promoted_by":  "graphical_schedule_reconciler",
                    "note":         f"Window height {h_mm} mm recovered from {source}.",
                })
                promoted_marks.add(mark)
                log.info("Promoted window height: mark=%s height=%d mm (source=%s)",
                         mark, h_mm, source)
        elif not mark:
            # No mark — can't safely assign to a specific window
            result["notes"].append(
                f"Window height {h_mm} mm recovered from {source} but no mark → "
                f"cannot safely assign to a specific window opening."
            )


def _promote_door_hints(
    door_hints: list[dict],
    element_model,
    config: dict,
    result: dict,
) -> None:
    """
    Apply door external/internal classification hints from DXF proximity search.

    Safe policy: only promote when:
    - Mark matches a known door opening
    - Classification is "external" or "internal" (not ambiguous)
    - Confidence is MEDIUM or above
    """
    if not door_hints:
        return

    door_elements = {
        o.mark.upper(): o
        for o in element_model.openings
        if o.opening_type == "door"
    }

    for hint in door_hints:
        mark = (hint.get("block_name") or "").upper()
        classification = hint.get("classification", "")
        confidence = hint.get("confidence", "LOW")

        if confidence == "LOW":
            continue  # Don't promote LOW confidence door hints
        if not mark or not classification:
            continue
        if mark not in door_elements:
            continue

        elem = door_elements[mark]
        # Update is_external flag
        was_external = elem.is_external
        elem.is_external = (classification == "external")

        if was_external != elem.is_external:
            result["promoted_rows"].append({
                "target":       "OpeningElement.is_external",
                "mark":         mark,
                "is_external":  elem.is_external,
                "hint_text":    hint.get("hint_text", ""),
                "source":       "dxf_annotation_proximity",
                "confidence":   confidence,
                "promoted_by":  "graphical_schedule_reconciler",
            })
            log.info("Promoted door classification: mark=%s is_external=%s",
                     mark, elem.is_external)


def _promote_stair_details(
    stair_details: list[dict],
    element_model,
    config: dict,
    result: dict,
) -> None:
    """
    Promote stair riser/tread dimensions to StairElement instances.

    Safe policy: only promote when riser height is in plausible range (100–250mm)
    and riser count is in plausible range (2–20).
    """
    if not stair_details:
        return

    stair_elements = [e for e in element_model.stairs] if hasattr(element_model, "stairs") else []
    if not stair_elements:
        return

    for detail in stair_details:
        risers    = detail.get("risers")
        riser_h   = detail.get("riser_h_mm")
        tread_d   = detail.get("tread_d_mm")

        if not (risers and riser_h):
            continue
        if not (2 <= risers <= 20 and 100 <= riser_h <= 250):
            continue

        # Apply to first stair element that has 0 risers
        for stair in stair_elements:
            if stair.risers_per_flight == 0:
                stair.risers_per_flight = risers
                stair.riser_height_mm   = riser_h
                if tread_d:
                    stair.tread_depth_mm = tread_d
                result["promoted_rows"].append({
                    "target":          "StairElement",
                    "risers_per_flight": risers,
                    "riser_height_mm":   riser_h,
                    "tread_depth_mm":    tread_d,
                    "source":          "dxf_annotation",
                    "confidence":      "MEDIUM",
                    "promoted_by":     "graphical_schedule_reconciler",
                })
                log.info("Promoted stair detail: %dR@%dmm G%s",
                         risers, riser_h, f"{tread_d}mm" if tread_d else "?")
                break


def _promote_footing_details(
    footing_details: list[dict],
    element_model,
    config: dict,
    result: dict,
) -> None:
    """
    Promote footing depth/size to FootingElement instances.

    Safe policy: only promote depth when in plausible range (200–2000mm).
    """
    if not footing_details:
        return

    footing_elements = [e for e in element_model.footings] if hasattr(element_model, "footings") else []
    if not footing_elements:
        return

    for detail in footing_details:
        depth = detail.get("depth_mm")
        size  = detail.get("size_mm")

        if depth and 200 <= depth <= 2000:
            for ftg in footing_elements:
                if hasattr(ftg, "thickness_mm") and ftg.thickness_mm in (0, 100):
                    ftg.thickness_mm = depth
                    result["promoted_rows"].append({
                        "target":       "FootingElement.thickness_mm",
                        "depth_mm":     depth,
                        "source":       "dxf_annotation",
                        "confidence":   "MEDIUM",
                        "promoted_by":  "graphical_schedule_reconciler",
                    })
                    log.info("Promoted footing depth: %d mm", depth)
                    break


def _document_still_blocked(result: dict, element_model, config: dict) -> None:
    """
    Document items that remain blocked after all graphical recovery attempts.
    """
    promoted_window_marks = {
        r["mark"] for r in result["promoted_rows"]
        if r.get("target") == "OpeningElement.height_m"
    }

    # Still-blocked windows (height not recovered)
    for opening in element_model.openings:
        if opening.opening_type == "window" and opening.swing_type == "louvre":
            if opening.height_m == 0.0 and opening.mark.upper() not in promoted_window_marks:
                result["still_blocked"].append({
                    "item":   f"Louvre Blade / Fly Screen — {opening.mark}",
                    "reason": (
                        "Window height not found in DXF annotations (no proximity text with "
                        "WxH pattern found near window INSERT position), no OCR result, "
                        "and no window schedule in source PDFs. "
                        "Requires window schedule from client."
                    ),
                    "requires": "window_schedule",
                })

    # Still-blocked: door classification if no hints recovered
    if not result["door_hints_recovered"]:
        result["still_blocked"].append({
            "item":   "Door external/internal classification — all doors",
            "reason": (
                "No explicit EXT/INT annotation found near door INSERT positions in DXF. "
                "Classification remains heuristic (width ≥ 850mm = external). "
                "Requires door schedule or explicit notation from architectural drawings."
            ),
            "requires": "door_schedule",
        })

    # Still-blocked: stair details if not recovered
    stair_elements = getattr(element_model, "stairs", [])
    if stair_elements:
        unrec_stairs = [s for s in stair_elements if s.risers_per_flight == 0]
        if unrec_stairs:
            result["still_blocked"].append({
                "item":   f"Stair detail — {len(unrec_stairs)} stair(s) without riser data",
                "reason": (
                    "No explicit riser/going annotation found in DXF text entities "
                    "(STAIRS layer or stair-keyword text). "
                    "Requires stair detail drawing or section drawing."
                ),
                "requires": "stair_detail_drawing",
            })
