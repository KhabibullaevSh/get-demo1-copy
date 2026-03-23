"""
framecad_extractor.py — Parse FrameCAD BOM / manufacturing summary files.

Supported formats: .xlsx, .csv, .txt
BOM quantities, when found, OVERRIDE IFC / DXF estimates for structural members.

CRITICAL: Only quantities explicitly listed in the BOM are returned.
          No quantities are invented or estimated here.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

log = logging.getLogger("boq.v2.framecad_extractor")

# Glob patterns to identify FrameCAD BOM files (case-insensitive name match)
_BOM_KEYWORDS = ("bom", "manufacturing", "summary", "framecad", "frameclad")
_BOM_EXTENSIONS = (".xlsx", ".csv", ".txt")

# Member category keywords (for description-based classification)
_CAT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bstud\b",   re.I), "wall_stud"),
    (re.compile(r"\bnoggin\b", re.I), "noggin"),
    (re.compile(r"\bplate\b",  re.I), "plate"),
    (re.compile(r"\brafter\b", re.I), "rafter"),
    (re.compile(r"\blintel\b", re.I), "lintel"),
    (re.compile(r"\bjoist\b",  re.I), "joist"),
    (re.compile(r"\bgirt\b",   re.I), "girt"),
    (re.compile(r"\btie\b",    re.I), "tie_strap"),
    (re.compile(r"\bstrap\b",  re.I), "tie_strap"),
    (re.compile(r"\bcolumn\b", re.I), "post"),
    (re.compile(r"\bpost\b",   re.I), "post"),
]


def _classify_member(description: str) -> str:
    for pat, cat in _CAT_RULES:
        if pat.search(description):
            return cat
    return "unclassified"


def _find_bom_files(input_dir: Path) -> list[Path]:
    found: list[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _BOM_EXTENSIONS:
            continue
        if any(kw in p.name.lower() for kw in _BOM_KEYWORDS):
            found.append(p)
    return found


# ─── xlsx parser ──────────────────────────────────────────────────────────────

def _parse_xlsx(path: Path) -> list[dict]:
    """Parse FrameCAD BOM xlsx.  Returns list of member dicts."""
    try:
        import openpyxl
    except ImportError:
        log.warning("openpyxl not installed — cannot parse xlsx BOM")
        return []

    members: list[dict] = []
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
        ws = wb.active

        # Find header row (first row containing 'description' or 'qty')
        header_row: int | None = None
        headers: dict[str, int] = {}
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            row_lower = [str(c).lower() if c is not None else "" for c in row]
            if any(h in row_lower for h in ("description", "qty", "quantity", "length")):
                header_row = row_idx
                headers = {v: i for i, v in enumerate(row_lower) if v}
                break

        if header_row is None:
            log.warning("No header row found in %s", path.name)
            return []

        desc_col   = next((headers[k] for k in headers if "desc" in k), None)
        qty_col    = next((headers[k] for k in headers if k in ("qty", "quantity")), None)
        len_col    = next((headers[k] for k in headers if "length" in k and "total" not in k), None)
        total_col  = next((headers[k] for k in headers if "total" in k and "length" in k), None)
        sec_col    = next((headers[k] for k in headers if "section" in k or "size" in k or "code" in k), None)
        wt_col     = next((headers[k] for k in headers if "weight" in k or "mass" in k), None)

        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            if all(c is None for c in row):
                continue
            desc  = str(row[desc_col]).strip()  if desc_col  is not None and row[desc_col]  is not None else ""
            if not desc or desc.lower() in ("none", "description"):
                continue

            try: qty   = int(float(row[qty_col]))   if qty_col   is not None and row[qty_col]   is not None else 0
            except (ValueError, TypeError): qty = 0
            try: length_mm = float(row[len_col])    if len_col   is not None and row[len_col]   is not None else 0.0
            except (ValueError, TypeError): length_mm = 0.0
            try: total_lm  = float(row[total_col]) / 1000.0  if total_col is not None and row[total_col] is not None else (qty * length_mm / 1000.0)
            except (ValueError, TypeError): total_lm = qty * length_mm / 1000.0
            section = str(row[sec_col]).strip() if sec_col is not None and row[sec_col] is not None else ""
            try: weight_kg = float(row[wt_col]) if wt_col is not None and row[wt_col] is not None else 0.0
            except (ValueError, TypeError): weight_kg = 0.0

            members.append({
                "description": desc,
                "qty":         qty,
                "length_mm":   round(length_mm, 1),
                "total_length_m": round(total_lm, 3),
                "section_code": section,
                "category":    _classify_member(desc),
                "weight_kg":   round(weight_kg, 2),
            })
    except Exception as exc:
        log.error("Error parsing xlsx BOM %s: %s", path, exc)
    return members


# ─── csv parser ───────────────────────────────────────────────────────────────

def _parse_csv(path: Path) -> list[dict]:
    members: list[dict] = []
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="ignore") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                keys_lower = {k.lower(): v for k, v in row.items()}
                desc = next((keys_lower[k] for k in keys_lower if "desc" in k), "").strip()
                if not desc:
                    continue
                try: qty = int(float(next((keys_lower[k] for k in keys_lower if k in ("qty","quantity")), 0) or 0))
                except (ValueError, TypeError): qty = 0
                try: length_mm = float(next((keys_lower[k] for k in keys_lower if "length" in k and "total" not in k), 0) or 0)
                except (ValueError, TypeError): length_mm = 0.0
                total_lm = qty * length_mm / 1000.0
                try: total_lm = float(next((keys_lower[k] for k in keys_lower if "total" in k and "length" in k), total_lm * 1000) or 0) / 1000.0
                except (ValueError, TypeError): pass
                section = next((keys_lower[k] for k in keys_lower if "section" in k or "size" in k), "").strip()
                members.append({
                    "description":    desc,
                    "qty":            qty,
                    "length_mm":      round(length_mm, 1),
                    "total_length_m": round(total_lm, 3),
                    "section_code":   section,
                    "category":       _classify_member(desc),
                    "weight_kg":      0.0,
                })
    except Exception as exc:
        log.error("Error parsing CSV BOM %s: %s", path, exc)
    return members


# ─── main entry point ─────────────────────────────────────────────────────────

def extract_framecad_bom(input_dir: Path) -> dict:
    """
    Scan *input_dir* for FrameCAD BOM files and extract member quantities.

    BOM quantities are the highest-priority structural source — they override
    IFC and DXF estimates when present.

    Returns structured dict.  If no BOM found, returns empty result with warning.
    """
    warnings: list[str] = []
    bom_files = _find_bom_files(input_dir)

    if not bom_files:
        log.info("No FrameCAD BOM files found in %s", input_dir)
        return {
            "found":       False,
            "source_file": None,
            "members":     [],
            "totals":      {},
            "warnings":    ["No FrameCAD BOM found"],
        }

    # Use first matching file
    bom_path = bom_files[0]
    log.info("Found FrameCAD BOM: %s", bom_path.name)

    ext = bom_path.suffix.lower()
    if ext == ".xlsx":
        members = _parse_xlsx(bom_path)
    elif ext == ".csv":
        members = _parse_csv(bom_path)
    else:
        members = []
        warnings.append(f"Unsupported BOM format: {ext} ({bom_path.name})")

    if not members:
        warnings.append(f"BOM file found ({bom_path.name}) but no member rows parsed")

    # Aggregate totals by category
    cat_totals: dict[str, float] = {}
    total_lm = 0.0
    for m in members:
        cat = m.get("category", "unclassified")
        cat_totals[cat] = round(cat_totals.get(cat, 0.0) + m.get("total_length_m", 0.0), 3)
        total_lm += m.get("total_length_m", 0.0)

    totals = {
        "wall_stud_lm": cat_totals.get("wall_stud",    0.0),
        "plate_lm":     cat_totals.get("plate",        0.0),
        "noggin_lm":    cat_totals.get("noggin",       0.0),
        "rafter_lm":    cat_totals.get("rafter",       0.0),
        "total_lm":     round(total_lm, 2),
        **{f"{k}_lm": v for k, v in cat_totals.items()
           if k not in ("wall_stud", "plate", "noggin", "rafter")},
    }

    log.info(
        "BOM parsed: %d members, total %.1f lm from %s",
        len(members), total_lm, bom_path.name,
    )

    return {
        "found":       True,
        "source_file": str(bom_path),
        "members":     members,
        "totals":      totals,
        "warnings":    warnings,
    }
