"""
schedule_builder.py — Build normalized schedule model from PDF extraction results.

Combines raw PDF schedule data into typed PdfScheduleData and integrates
confirmed findings into the ProjectElementModel where applicable.

For project 2 (Angau Pharmacy):
  - Roof pitch = 15° recovered → sets element_model.primary_roof().pitch_deg
  - 16 opening marks confirmed (N1–N16) → documented, not used to change quantities
  - No window / door / finish / services schedule → all those rows remain manual_review

Non-negotiable rule: no quantities sourced from BOQ template files.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("boq.v3.schedule_builder")


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class OpeningScheduleRecord:
    """A single opening record recovered from a PDF schedule."""
    mark:            str   = ""
    opening_type:    str   = ""     # door | window | louvre
    width_mm:        int   = 0
    height_mm:       int   = 0      # 0 = height not found in schedule
    frame_material:  str   = ""
    hardware_notes:  str   = ""
    source:          str   = ""
    confidence:      str   = "LOW"


@dataclass
class PdfScheduleData:
    """
    Structured schedule data recovered from all source PDFs.

    None means the schedule was not found at all.
    An empty list means a schedule was found but no parseable records could be extracted.
    """
    # ── Structural / layout ──────────────────────────────────────────────────
    roof_pitch_degrees:      float | None            = None
    roof_pitch_source:       str                     = ""
    opening_marks:           list[str]               = field(default_factory=list)
    opening_mark_source:     str                     = ""
    wall_panel_ids:          list[str]               = field(default_factory=list)
    roof_truss_ids:          list[str]               = field(default_factory=list)
    panel_dimension_labels:  list[str]               = field(default_factory=list)

    # ── Schedule records (None = not found in source PDFs) ───────────────────
    window_records:          list[OpeningScheduleRecord] | None = None
    door_records:            list[OpeningScheduleRecord] | None = None
    room_finish_records:     list[dict] | None               = None
    services_records:        list[dict] | None               = None

    # ── Coverage metadata ────────────────────────────────────────────────────
    pdf_files_scanned:   list[str] = field(default_factory=list)
    schedules_not_found: list[str] = field(default_factory=list)
    schedule_coverage:   dict      = field(default_factory=dict)

    # ── Convenience accessors ────────────────────────────────────────────────

    def has_window_heights(self) -> bool:
        """True if any window record has a confirmed height from the schedule."""
        if not self.window_records:
            return False
        return any(r.height_mm > 0 for r in self.window_records)

    def has_door_type_data(self) -> bool:
        """True if door schedule was found with opening type data."""
        if not self.door_records:
            return False
        return any(r.opening_type for r in self.door_records)

    def opening_count_from_layout(self) -> int:
        """Number of structural opening marks recovered from layout drawings."""
        return len(self.opening_marks)


# ── Builder ───────────────────────────────────────────────────────────────────

def build_schedule_model(
    raw_pdf_schedules: dict,
    element_model,
    config: dict,
) -> PdfScheduleData:
    """
    Build a PdfScheduleData from raw extraction results and integrate
    confirmed findings into the element_model.

    Side effects:
      - Sets element_model.primary_roof().pitch_deg when pitch is recovered
        and the current value is 0.0 (i.e. not already set from another source).

    Returns:
        PdfScheduleData with typed fields populated from raw_pdf_schedules.
    """
    data = PdfScheduleData(
        pdf_files_scanned   = raw_pdf_schedules.get("pdf_files_scanned",   []),
        schedules_not_found = raw_pdf_schedules.get("schedules_not_found", []),
        schedule_coverage   = raw_pdf_schedules.get("schedule_coverage",   {}),
    )

    # ── Roof pitch ────────────────────────────────────────────────────────────
    pitch = raw_pdf_schedules.get("roof_pitch_degrees")
    if pitch is not None and 1.0 <= float(pitch) <= 60.0:
        data.roof_pitch_degrees = float(pitch)
        data.roof_pitch_source  = raw_pdf_schedules.get("roof_pitch_source", "")

        # Integrate into element model (only if not already set)
        roof = element_model.primary_roof() if element_model is not None else None
        if roof is not None and roof.pitch_deg == 0.0:
            roof.pitch_deg = float(pitch)
            log.info(
                "pitch_deg set on RoofElement from PDF schedule: %.3f° (source: %s)",
                float(pitch), data.roof_pitch_source,
            )

    # ── Structural layout data ────────────────────────────────────────────────
    data.opening_marks           = raw_pdf_schedules.get("opening_marks",          [])
    data.opening_mark_source     = raw_pdf_schedules.get("opening_mark_source",    "")
    data.wall_panel_ids          = raw_pdf_schedules.get("wall_panel_ids",         [])
    data.roof_truss_ids          = raw_pdf_schedules.get("roof_truss_ids",         [])
    data.panel_dimension_labels  = raw_pdf_schedules.get("panel_dimension_labels", [])

    # ── Schedule records ──────────────────────────────────────────────────────
    # window_schedule
    raw_win = raw_pdf_schedules.get("window_schedule")
    if raw_win is None:
        data.window_records = None          # schedule not found
    else:
        data.window_records = _parse_window_records(raw_win)

    # door_schedule
    raw_door = raw_pdf_schedules.get("door_schedule")
    if raw_door is None:
        data.door_records = None
    else:
        data.door_records = _parse_door_records(raw_door)

    # room_finish_schedule
    raw_finish = raw_pdf_schedules.get("room_finish_schedule")
    data.room_finish_records = None if raw_finish is None else []

    # services_schedule
    raw_svc = raw_pdf_schedules.get("services_schedule")
    data.services_records = None if raw_svc is None else []

    log.info(
        "Schedule model built: pitch=%s°, marks=%d, panels=%d, trusses=%d | "
        "not_found=%s",
        data.roof_pitch_degrees,
        len(data.opening_marks),
        len(data.wall_panel_ids),
        len(data.roof_truss_ids),
        data.schedules_not_found,
    )
    return data


# ── Schedule record parsers (stubs for future use) ────────────────────────────

def _parse_window_records(raw: dict) -> list[OpeningScheduleRecord]:
    """
    Parse raw window schedule data into typed records.

    Currently returns empty list (table parsing not implemented — schedule data
    not in text format for project 2).
    """
    return []


def _parse_door_records(raw: dict) -> list[OpeningScheduleRecord]:
    """
    Parse raw door schedule data into typed records.

    Currently returns empty list (table parsing not implemented).
    """
    return []
