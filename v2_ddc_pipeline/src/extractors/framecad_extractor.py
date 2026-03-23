"""
framecad_extractor.py — Extract FrameCAD manufacturing BOM data.

Two source formats are supported:

  1. PDF Manufacturing Summary (FrameCAD Steelwise export)
     e.g. "Angau Pharmacy Summary.pdf"
     Detected by: contains "Manufacturing Summary" + "Summary for Tab"

  2. BOM Excel / CSV (FrameCAD direct export)
     e.g. bom/*.xlsx, bom/*.csv

Priority: PDF summary (found in project folder) > BOM xlsx/csv (in bom/ folder)

BOM Category Mapping
─────────────────────
FrameCAD Steelwise organises output into tabs:

  Tab "Roof Panels"   → roof_panel_lm  (purlins forming the roof panel frame)
  Tab "Roof Trusses"  → roof_truss_lm  (truss chord + web members)
  Tab "Wall Panels"   → wall_frame_lm  (studs + plates + all wall members)
  Lintel entry        → lintel_lm      (150x32x0.95 or similar)
  Strap entry         → wall_strap_lm  (diagonal bracing strap)

Roof battens (FRAMECAD BATTEN entries in layout PDFs):
  → roof_batten_lm, roof_batten_nr from batten count × length

IFC Cross-Check (validated 23 Mar 2026)
────────────────────────────────────────
IFC "2440.000050" IfcBeam group (481.7 lm) = BOM "Tab Roof Panels" 89S41 (481.740 lm).
EXACT MATCH — those members are roof purlins, not floor joists.
The anomalous numeric description is a FrameCAD Steelwise IFC export artifact.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("boq.v2.framecad_extractor")

# Pattern for FrameCAD manufacturing summary PDF
_MFGSUMMARY_MARKERS = ("Manufacturing Summary", "Summary for Tab")
# Regex to detect an LGS section code (e.g. 89S41-075-500)
_LGS_PROFILE_RE  = re.compile(r"^\d+[A-Z]\d+-\d+-\d+")
# Regex for lintel profile (e.g. 150x32x0.95 Lintel)
_LINTEL_RE       = re.compile(r"\d+x\d+x[\d.]+\s+[Ll]intel\s+([\d.]+)")
# Regex for strap (e.g. FRAMECAD 32x0.95 Strap 10g-5  49.734)
_STRAP_RE        = re.compile(r"32x[\d.]+\s+Strap.*?([\d.]+)\s*$")
# Regex for batten (e.g. FRAMECAD BATTEN 22  176  4800)
_BATTEN_RE       = re.compile(r"BATTEN\s+(\d+)\s+(\d+)\s+(\d+)")

_TAB_MAP = {
    "Roof Panels":  "roof_panel_lm",
    "Roof Trusses": "roof_truss_lm",
    "Wall Panels":  "wall_frame_lm",
}


def _parse_manufacturing_pdf(pdf_path: Path) -> dict | None:
    """
    Parse a FrameCAD Steelwise Manufacturing Summary PDF.
    Returns structured BOM dict, or None if the file is not a manufacturing summary.
    """
    try:
        import pdfplumber
    except ImportError:
        log.warning("pdfplumber not installed — cannot parse PDF BOM")
        return None

    full_text = ""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
    except Exception as exc:
        log.error("Failed to read PDF %s: %s", pdf_path, exc)
        return None

    # Confirm it is a Manufacturing Summary
    if not all(m in full_text for m in _MFGSUMMARY_MARKERS):
        log.debug("%s is not a FrameCAD Manufacturing Summary", pdf_path.name)
        return None

    lm_by_tab: dict[str, float] = {}
    lintel_lm    = 0.0
    strap_lm     = 0.0
    batten_entries: list[dict] = []
    fixings: list[dict] = []
    current_tab: str | None = None

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # New tab section
        if "Summary for Tab" in line:
            tab_raw = line.split("Summary for Tab")[-1].strip().rstrip(":")
            current_tab = tab_raw
            continue

        # Job Summary section — stop tab-level parsing to avoid overwriting per-tab totals
        if "Job Summary" in line:
            current_tab = None
            continue

        # LGS profile line (e.g. "89S41-075-500 481.740 549.2")
        if _LGS_PROFILE_RE.match(line) and current_tab:
            parts = line.split()
            for p in parts[1:]:
                try:
                    val = float(p)
                    if val > 1.0:           # first float > 1 is the lm total
                        lm_by_tab[current_tab] = round(val, 3)
                        break
                except ValueError:
                    pass
            continue

        # Lintel (only within a tab section, not Job Summary)
        if current_tab:
            m = _LINTEL_RE.search(line)
            if m:
                try:
                    lintel_lm += float(m.group(1))
                except ValueError:
                    pass
                continue

            # Strap (only within a tab section, not Job Summary)
            m = _STRAP_RE.search(line)
            if m:
                try:
                    strap_lm += float(m.group(1))
                except ValueError:
                    pass
                continue

        # Batten
        m = _BATTEN_RE.search(line)
        if m:
            batten_entries.append({
                "grade_mm":  int(m.group(1)),
                "qty":       int(m.group(2)),
                "length_mm": int(m.group(3)),
                "total_lm":  round(int(m.group(2)) * int(m.group(3)) / 1000.0, 3),
            })
            continue

    # Build totals
    totals: dict[str, float] = {}
    for tab_name, bom_key in _TAB_MAP.items():
        if tab_name in lm_by_tab:
            totals[bom_key] = lm_by_tab[tab_name]

    totals["lintel_lm"]     = round(lintel_lm, 3)
    totals["wall_strap_lm"] = round(strap_lm,  3)
    totals["total_lgs_lm"]  = round(
        sum(lm_by_tab.get(t, 0.0) for t in _TAB_MAP), 3
    )

    if batten_entries:
        totals["roof_batten_lm"] = round(sum(e["total_lm"] for e in batten_entries), 3)
        totals["roof_batten_nr"] = sum(e["qty"] for e in batten_entries)

    log.info(
        "FrameCAD PDF BOM: roof_panel=%.1f  roof_truss=%.1f  wall_frame=%.1f  "
        "lintel=%.3f  strap=%.3f  total_lgs=%.1f lm",
        totals.get("roof_panel_lm", 0),
        totals.get("roof_truss_lm", 0),
        totals.get("wall_frame_lm", 0),
        totals.get("lintel_lm", 0),
        totals.get("wall_strap_lm", 0),
        totals.get("total_lgs_lm", 0),
    )

    return {
        "found":          True,
        "source_file":    str(pdf_path),
        "source_type":    "pdf_manufacturing_summary",
        "lm_by_tab":      lm_by_tab,
        "totals":         totals,
        "batten_entries": batten_entries,
        "fixings":        fixings,
        "warnings":       [],
    }


def _scan_for_batten_data(project_dir: Path, bom_data: dict) -> dict:
    """
    Scan all PDFs in project_dir for FRAMECAD BATTEN entries.
    Adds to bom_data["batten_entries"] and totals if new entries found.
    Skips if batten data already present from manufacturing summary.
    """
    if bom_data.get("batten_entries"):
        # Already populated from the main summary PDF
        entries = bom_data["batten_entries"]
    else:
        entries = []
        try:
            import pdfplumber
            for pdf in sorted(project_dir.rglob("*.pdf")):
                if pdf.name == Path(bom_data.get("source_file", "")).name:
                    continue   # already parsed
                try:
                    with pdfplumber.open(str(pdf)) as p:
                        for page in p.pages:
                            for line in (page.extract_text() or "").splitlines():
                                m = _BATTEN_RE.search(line)
                                if m:
                                    entries.append({
                                        "grade_mm":  int(m.group(1)),
                                        "qty":       int(m.group(2)),
                                        "length_mm": int(m.group(3)),
                                        "total_lm":  round(int(m.group(2)) * int(m.group(3)) / 1000.0, 3),
                                        "source_pdf": pdf.name,
                                    })
                except Exception:
                    pass
        except ImportError:
            pass

    if entries:
        total_lm = round(sum(e["total_lm"] for e in entries), 3)
        total_nr = sum(e["qty"] for e in entries)
        bom_data["batten_entries"]         = entries
        bom_data["totals"]["roof_batten_lm"] = total_lm
        bom_data["totals"]["roof_batten_nr"] = total_nr
        log.info("FrameCAD roof battens: %d pieces, %.1f lm", total_nr, total_lm)
    return bom_data


def _try_parse_bom_xlsx(path: Path) -> dict | None:
    """Attempt to parse an xlsx as a FrameCAD BOM. Returns dict or None."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception:
        return None

    members: list[dict] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            if row and row[0] and str(row[0]).strip().startswith("89S"):
                try:
                    lm = float(row[1]) if len(row) > 1 else 0.0
                    members.append({"profile": str(row[0]), "lm": lm, "sheet": ws.title})
                except (ValueError, TypeError):
                    pass

    if not members:
        return None

    total_lm = round(sum(m["lm"] for m in members), 3)
    log.info("FrameCAD BOM xlsx: %d rows, total=%.1f lm", len(members), total_lm)
    return {
        "found":       True,
        "source_file": str(path),
        "source_type": "xlsx_bom",
        "members":     members,
        "totals":      {"total_lgs_lm": total_lm},
        "warnings":    [],
    }


def extract_framecad_bom(project_input_dir: Path) -> dict:
    """
    Main entry point — scan for and parse FrameCAD manufacturing data.

    Search order:
      1. PDF manufacturing summary in project_input_dir
      2. xlsx/csv BOM in project_input_dir/../bom/

    Returns dict with:
      found, source_file, source_type, totals
      totals keys (all in lm unless noted):
        roof_panel_lm, roof_truss_lm, wall_frame_lm,
        lintel_lm, wall_strap_lm, total_lgs_lm,
        roof_batten_lm, roof_batten_nr (integers)
    """
    empty = {
        "found":    False,
        "totals":   {},
        "members":  [],
        "warnings": [f"No FrameCAD BOM files found in {project_input_dir}"],
    }

    # 1. Scan project dir PDFs for manufacturing summary
    bom: dict | None = None
    try:
        import pdfplumber   # noqa: F401 — test import only
        for pdf in sorted(project_input_dir.rglob("*.pdf")):
            bom = _parse_manufacturing_pdf(pdf)
            if bom:
                break
    except ImportError:
        log.warning("pdfplumber not installed — PDF BOM parsing skipped")

    # 2. Fall back to xlsx/csv in bom/ folder
    if bom is None:
        bom_dir = project_input_dir.parent.parent / "bom"
        if bom_dir.exists():
            for ext in ("*.xlsx", "*.csv"):
                for f in sorted(bom_dir.rglob(ext)):
                    bom = _try_parse_bom_xlsx(f)
                    if bom:
                        break

    if bom is None:
        log.info("No FrameCAD BOM files found in %s", project_input_dir)
        return empty

    # Augment with batten data from all PDFs
    bom = _scan_for_batten_data(project_input_dir, bom)

    return bom
