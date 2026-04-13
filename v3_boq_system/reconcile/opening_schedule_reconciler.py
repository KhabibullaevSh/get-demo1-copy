"""
opening_schedule_reconciler.py — Reconcile PDF schedule evidence to opening elements.

Promotion policy:
  - Only promote a row when the schedule explicitly supplies the missing field.
  - If no window/door schedule was found: heights and external/internal classification
    remain manual_review / blocked — no invented dimensions.

For project 2 (Angau Pharmacy):
  - FrameCAD wall panel layout confirms 16 structural opening marks (N1–N16).
  - This is cross-checked against the DXF opening count; count is consistent.
  - No window heights available → D26/D29/D32 louvre blades remain BLOCKED PENDING
    WINDOW SCHEDULE.
  - No door type schedule → external/internal classification remains heuristic.

No quantities are sourced from BOQ template files.
"""
from __future__ import annotations

import logging

log = logging.getLogger("boq.v3.reconcile.opening")


def reconcile_openings(
    schedule_data,          # PdfScheduleData
    element_model,
    config: dict,
) -> dict:
    """
    Reconcile opening schedule evidence to existing opening elements.

    Returns a reconciliation summary dict:
    {
        promoted_rows:              list[dict]  — BOQ row field updates
        still_blocked:              list[str]   — items that remain manual_review
        opening_marks_from_layout:  list[str]
        window_schedule_found:      bool
        door_schedule_found:        bool
        opening_count_dxf:          int
        opening_count_layout:       int
        count_consistent:           bool
        notes:                      str
    }
    """
    n_marks   = len(schedule_data.opening_marks)
    n_dxf     = sum(e.quantity for e in element_model.openings)
    count_ok  = abs(n_marks - n_dxf) <= 2

    result: dict = {
        "promoted_rows":              [],
        "still_blocked":              [],
        "opening_marks_from_layout":  schedule_data.opening_marks,
        "window_schedule_found":      schedule_data.window_records is not None,
        "door_schedule_found":        schedule_data.door_records   is not None,
        "opening_count_dxf":          n_dxf,
        "opening_count_layout":       n_marks,
        "count_consistent":           count_ok,
        "notes":                      "",
    }

    # ── Opening mark count cross-check ────────────────────────────────────────
    note_parts = []
    if n_marks:
        note_parts.append(
            f"FrameCAD wall panel layout confirms {n_marks} structural opening "
            f"marks ({schedule_data.opening_marks[0]}–{schedule_data.opening_marks[-1]} "
            f"from {schedule_data.opening_mark_source}). "
            f"DXF opening count = {n_dxf}. "
            + ("Counts consistent (delta ≤ 2)." if count_ok
               else f"Count discrepancy: layout={n_marks}, DXF={n_dxf}. "
                    "Investigate structural layout vs architectural DXF.")
        )

    # ── Still-blocked: window heights ─────────────────────────────────────────
    if schedule_data.window_records is None:
        result["still_blocked"].extend([
            "D26 Louvre Blade WINDOW_LOUVRE_1100: height — no window schedule in PDFs",
            "D29 Louvre Blade WINDOW_LOUVRE_800: height — no window schedule in PDFs",
            "D32 Louvre Blade WINDOW_LOUVRE_1800: height — no window schedule in PDFs",
            "D42 Fly Screen WINDOW_LOUVRE_1100: height — no window schedule in PDFs",
            "D43 Fly Screen WINDOW_LOUVRE_800: height — no window schedule in PDFs",
            "D44 Fly Screen WINDOW_LOUVRE_1800: height — no window schedule in PDFs",
        ])
        note_parts.append(
            "No window schedule found in source PDFs. "
            "Window heights remain config-default 750mm (LOW confidence). "
            "Louvre blade counts and fly screen areas remain BLOCKED PENDING WINDOW SCHEDULE."
        )

    # ── Still-blocked: door types ─────────────────────────────────────────────
    if schedule_data.door_records is None:
        result["still_blocked"].append(
            "DOOR_*: external/internal classification — no door schedule in PDFs"
        )

    result["notes"] = " ".join(note_parts)

    log.info(
        "Opening reconciliation: marks=%d (DXF=%d, consistent=%s) | "
        "promoted=%d | still_blocked=%d",
        n_marks, n_dxf, count_ok,
        len(result["promoted_rows"]),
        len(result["still_blocked"]),
    )
    return result
