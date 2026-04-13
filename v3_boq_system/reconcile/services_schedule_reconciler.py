"""
services_schedule_reconciler.py — Reconcile services / fixture schedule evidence.

For project 2 (Angau Pharmacy):
  - No electrical, mechanical, or plumbing schedule found in any source PDF.
  - All services rows remain manual_review / placeholder.
  - No promotions possible.

No quantities are sourced from BOQ template files.
"""
from __future__ import annotations

import logging

log = logging.getLogger("boq.v3.reconcile.services")


def reconcile_services(
    schedule_data,      # PdfScheduleData
    element_model,
    config: dict,
) -> dict:
    """
    Reconcile services / fixture schedule evidence to BOQ rows.

    Returns a reconciliation summary dict.
    """
    result: dict = {
        "promoted_rows":          [],
        "still_blocked":          [],
        "services_schedule_found": schedule_data.services_records is not None,
        "notes":                  "",
    }

    if schedule_data.services_records is None:
        result["still_blocked"].extend([
            "I20 AC/Mechanical Ventilation: no mechanical schedule in source PDFs",
            "I21 Exhaust Fan Wet Area: no services schedule",
            "I22–I25 Whole-building services: no electrical/plumbing schedule",
            "I01–I17 Room-template services: no MEP schedule",
        ])
        result["notes"] = (
            "No services schedule found in source PDFs. "
            "All services rows remain manual_review / placeholder."
        )

    log.info(
        "Services reconciliation: schedule_found=%s | promoted=%d | still_blocked=%d",
        result["services_schedule_found"],
        len(result["promoted_rows"]),
        len(result["still_blocked"]),
    )
    return result
