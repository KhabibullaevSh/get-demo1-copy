"""
finish_schedule_reconciler.py — Reconcile room finish schedule evidence.

For project 2 (Angau Pharmacy):
  - No room finish schedule found in any source PDF.
  - All finish rows remain config-backed (LOW confidence, manual_review=True).
  - No promotions possible.

No quantities are sourced from BOQ template files.
"""
from __future__ import annotations

import logging

log = logging.getLogger("boq.v3.reconcile.finish")


def reconcile_finishes(
    schedule_data,      # PdfScheduleData
    element_model,
    config: dict,
) -> dict:
    """
    Reconcile finish schedule evidence to BOQ rows.

    Returns a reconciliation summary dict.
    """
    result: dict = {
        "promoted_rows":         [],
        "still_blocked":         [],
        "finish_schedule_found": schedule_data.room_finish_records is not None,
        "notes":                 "",
    }

    if schedule_data.room_finish_records is None:
        result["still_blocked"].extend([
            "F04 Floor Finish Dry: room areas from config room_schedule — no finish schedule",
            "F05 Floor Finish Wet: room areas from config room_schedule — no finish schedule",
            "I18 Wet Area Wall Tiling: perimeter heuristic — no room finish schedule",
            "I19 Wet Area Waterproofing: config room area — no finish schedule",
        ])
        result["notes"] = (
            "No room finish schedule found in source PDFs. "
            "Floor finish rows remain config-backed (LOW confidence). "
            "Wet area tiling and waterproofing remain heuristic (LOW confidence)."
        )

    log.info(
        "Finish reconciliation: schedule_found=%s | promoted=%d | still_blocked=%d",
        result["finish_schedule_found"],
        len(result["promoted_rows"]),
        len(result["still_blocked"]),
    )
    return result
