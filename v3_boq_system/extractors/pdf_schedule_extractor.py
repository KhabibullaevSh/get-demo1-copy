"""
pdf_schedule_extractor.py — Extract structured schedule data from project PDFs.

Targets:
  - FrameCAD layout drawings (wall panel layout, roof truss layout)
  - Window / door schedules (architectural PDFs)
  - Room finish schedules
  - Services / fixture schedules

For project 2 (Angau Pharmacy), finding summary:
  - Marketing Set PDF:  5-page vector CAD — ALL content is graphical; no text extractable
  - Summary PDF:        FrameCAD manufacturing BOM (already used by v2 pipeline)
  - Layouts PDF:        FrameCAD structural layouts (wall panels + roof trusses)
      → Roof pitch = 15.000° confirmed (Truss Design Summary table)
      → 16 structural opening marks (N1–N16) confirmed
      → 30 wall panel IDs (L1–L30) confirmed
      → 8 roof truss IDs (R1–R8) confirmed
      → Panel dimension labels extracted (format ambiguous — documented, not promoted)
      → NO window schedule, door schedule, finish schedule, or services schedule

Non-negotiable rule: no quantities sourced from BOQ template files.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("boq.v3.pdf_schedule_extractor")


def extract_pdf_schedules(pdf_paths: list[Path]) -> dict:
    """
    Scan all PDFs in the list for schedule evidence.

    Returns a structured dict with recovered schedule data and coverage report.
    """
    result: dict = {
        # ── Structural / roof ────────────────────────────────────────────────
        "roof_pitch_degrees":   None,
        "roof_pitch_source":    None,
        # ── Opening marks from structural layout ─────────────────────────────
        "opening_marks":        [],
        "opening_mark_source":  None,
        # ── Panel / truss IDs from layout ────────────────────────────────────
        "wall_panel_ids":           [],
        "roof_truss_ids":           [],
        "panel_dimension_labels":   [],
        # ── Schedules searched for but not found ─────────────────────────────
        "window_schedule":       None,   # None = not found
        "door_schedule":         None,
        "room_finish_schedule":  None,
        "services_schedule":     None,
        # ── Coverage ─────────────────────────────────────────────────────────
        "schedule_coverage":     {},
        "schedules_not_found":   [],
        "pdf_files_scanned":     [],
    }

    for pdf_path in pdf_paths:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            continue
        result["pdf_files_scanned"].append(str(pdf_path.name))
        _scan_pdf(pdf_path, result)

    # Record what was searched for but not found
    for category in ("window_schedule", "door_schedule",
                     "room_finish_schedule", "services_schedule"):
        if result[category] is None:
            result["schedules_not_found"].append(category)

    log.info(
        "PDF schedule extraction complete: %d PDFs scanned | "
        "pitch=%s° | marks=%d | panels=%d | trusses=%d | not_found=%s",
        len(result["pdf_files_scanned"]),
        result["roof_pitch_degrees"],
        len(result["opening_marks"]),
        len(result["wall_panel_ids"]),
        len(result["roof_truss_ids"]),
        result["schedules_not_found"],
    )
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_text_all_pages(pdf_path: Path) -> list[str]:
    """
    Extract text from all pages.

    Tries PyMuPDF (fitz) first — it handles FrameCAD PDFs better than pdfplumber.
    Falls back to pdfplumber if fitz is unavailable.
    """
    pages_text: list[str] = []

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        for page in doc:
            pages_text.append(page.get_text() or "")
        doc.close()
        if any(t.strip() for t in pages_text):
            return pages_text
    except ImportError:
        pass
    except Exception as exc:
        log.debug("fitz extraction failed for %s: %s", pdf_path.name, exc)

    # Fallback: pdfplumber
    pages_text = []
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
    except Exception as exc:
        log.debug("pdfplumber extraction failed for %s: %s", pdf_path.name, exc)

    return pages_text


def _scan_pdf(pdf_path: Path, result: dict) -> None:
    """Scan a single PDF and update result dict in-place."""
    pages_text = _extract_text_all_pages(pdf_path)
    all_text = "\n".join(pages_text)

    if not any(t.strip() for t in pages_text):
        result["schedule_coverage"][pdf_path.name] = ["no_text_recoverable"]
        log.info(
            "PDF %s: no text recoverable (vector CAD or image-only PDF)",
            pdf_path.name,
        )
        return

    coverage_notes: list[str] = []

    # ── Roof pitch (from FrameCAD Truss Design Summary) ──────────────────────
    # FrameCAD PDF tables are stored right-to-left: value appears BEFORE label.
    # Two patterns handled:
    #   Normal:   "Roof Pitch 15.000"    → group(1) = "15.000"
    #   Inverted: "15.000\nRoof Pitch"   → group(1) = "15.000" (FrameCAD right-to-left)
    # Both patterns are tried; the first that yields a value in [1°, 60°] wins.
    _pitch_candidates: list[float] = []
    for _pat in (
        r'Roof\s+Pitch\s*:?\s+(\d+(?:\.\d+)?)',   # normal order
        r'(\d+\.\d{1,3})\s*\n\s*Roof\s+Pitch',    # FrameCAD inverted order
    ):
        _m = re.search(_pat, all_text, re.IGNORECASE)
        if _m:
            try:
                _pitch_candidates.append(float(_m.group(1)))
            except ValueError:
                pass
    _valid_pitches = [v for v in _pitch_candidates if 1.0 <= v <= 60.0]
    if _valid_pitches and result["roof_pitch_degrees"] is None:
        pitch_val = _valid_pitches[0]
        if True:
            result["roof_pitch_degrees"] = pitch_val
            result["roof_pitch_source"] = f"framecad_layout:{pdf_path.name}"
            coverage_notes.append(f"roof_pitch={pitch_val}deg")
            log.info(
                "PDF %s: roof pitch = %.3f° (FrameCAD Truss Design Summary)",
                pdf_path.name, pitch_val,
            )

    # ── Opening marks (N1–N16 style, from WALL PANELS LAYOUT) ────────────────
    opening_marks_found = set(re.findall(r'\bN(\d{1,2})\b', all_text))
    if opening_marks_found:
        marks = sorted(
            (f"N{m}" for m in opening_marks_found),
            key=lambda s: int(s[1:]),
        )
        if len(marks) > len(result["opening_marks"]):
            result["opening_marks"] = marks
            result["opening_mark_source"] = f"framecad_layout:{pdf_path.name}"
            coverage_notes.append(f"opening_marks={len(marks)}")
            log.info(
                "PDF %s: %d opening marks: %s",
                pdf_path.name, len(marks), marks,
            )

    # ── Wall panel IDs (L1–L30 style) ────────────────────────────────────────
    panel_ids_found = set(re.findall(r'\bL(\d{1,2})\b', all_text))
    if panel_ids_found:
        ids = sorted(
            (f"L{p}" for p in panel_ids_found),
            key=lambda s: int(s[1:]),
        )
        if len(ids) > len(result["wall_panel_ids"]):
            result["wall_panel_ids"] = ids
            coverage_notes.append(f"wall_panel_ids={len(ids)}")

    # ── Roof truss IDs (R1–R8 style) ─────────────────────────────────────────
    truss_ids_found = set(re.findall(r'\bR(\d{1,2})\b', all_text))
    if truss_ids_found:
        ids = sorted(
            (f"R{t}" for t in truss_ids_found),
            key=lambda s: int(s[1:]),
        )
        if len(ids) > len(result["roof_truss_ids"]):
            result["roof_truss_ids"] = ids
            coverage_notes.append(f"roof_truss_ids={len(ids)}")

    # ── Panel dimension labels (WIDTHhHEIGHT format, e.g. "2050h830") ────────
    dim_labels_found = re.findall(r'\b(\d{3,4}h\d{3,4})\b', all_text)
    if dim_labels_found:
        labels = sorted(set(dim_labels_found))
        if len(labels) > len(result["panel_dimension_labels"]):
            result["panel_dimension_labels"] = labels
            coverage_notes.append(f"panel_dim_labels={len(labels)}")
            log.info("PDF %s: panel dim labels: %s", pdf_path.name, labels)

    # ── Window schedule search ────────────────────────────────────────────────
    _win_patterns = [
        r'WINDOW\s+SCHEDULE',
        r'WINDOW\s+TYPE\s+SCHEDULE',
        r'GLAZING\s+SCHEDULE',
        r'WDW\s+SCHEDULE',
    ]
    for pat in _win_patterns:
        if re.search(pat, all_text, re.IGNORECASE):
            coverage_notes.append("window_schedule_header_found")
            result["window_schedule"] = {
                "source": pdf_path.name,
                "status": "header_found_not_parseable",
                "records": [],
            }
            log.info("PDF %s: window schedule header found (not yet parseable)", pdf_path.name)
            break

    # ── Door schedule search ──────────────────────────────────────────────────
    _door_patterns = [
        r'DOOR\s+SCHEDULE',
        r'DOOR\s+TYPE\s+SCHEDULE',
    ]
    for pat in _door_patterns:
        if re.search(pat, all_text, re.IGNORECASE):
            coverage_notes.append("door_schedule_header_found")
            result["door_schedule"] = {
                "source": pdf_path.name,
                "status": "header_found_not_parseable",
                "records": [],
            }
            log.info("PDF %s: door schedule header found (not yet parseable)", pdf_path.name)
            break

    # ── Room finish schedule search ───────────────────────────────────────────
    _finish_patterns = [
        r'FINISH\s+SCHEDULE',
        r'ROOM\s+FINISH',
        r'FINISHES\s+SCHEDULE',
    ]
    for pat in _finish_patterns:
        if re.search(pat, all_text, re.IGNORECASE):
            coverage_notes.append("finish_schedule_header_found")
            result["room_finish_schedule"] = {
                "source": pdf_path.name,
                "status": "header_found_not_parseable",
                "records": [],
            }
            break

    # ── Services / fixture schedule search ───────────────────────────────────
    _svc_patterns = [
        r'SERVICES\s+SCHEDULE',
        r'FIXTURE\s+SCHEDULE',
        r'SANITARY\s+FIXTURE',
        r'ELECTRICAL\s+SCHEDULE',
        r'MECHANICAL\s+SCHEDULE',
    ]
    for pat in _svc_patterns:
        if re.search(pat, all_text, re.IGNORECASE):
            coverage_notes.append("services_schedule_header_found")
            result["services_schedule"] = {
                "source": pdf_path.name,
                "status": "header_found_not_parseable",
                "records": [],
            }
            break

    if not coverage_notes:
        coverage_notes.append("no_schedule_data_found")

    result["schedule_coverage"][pdf_path.name] = coverage_notes
