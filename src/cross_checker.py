"""
cross_checker.py — Compare system-generated BOQ against a reference (approved) BOQ.

The approved BOQ is used ONLY for post-hoc cross-checking, never as a quantity source.

Match order:
  1. Ordered row-index match (_row_idx) — same position in template
  2. Description similarity (fuzzy fallback)

Result categories per matched row:
  PASS    — qty difference ≤ 10%
  WARN    — qty difference 10–30%
  FLAG    — qty difference > 30%
  BLANK   — system qty is None/0 (no source found)
  SKIP    — either side is a header/blank row

Unmatched:
  MISSING — row in system BOQ but no matching reference row
  NEW     — row in reference BOQ but no matching system row

Output:
  {
    "rows": [per-item result dicts],
    "summary": {
      "total": n,
      "pass": n, "warn": n, "flag": n, "blank": n, "skip": n,
      "missing": n, "new": n,
      "pass_pct": 0.0,
      "source_breakdown": {"dwg_derived": n, "pdf_schedule": n, "rule": n, "none": n, ...},
    },
    "reference_path": str | None,
  }
"""

from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("boq.cross_checker")


def cross_check(
    system_boq: list[dict],
    reference_boq: list[dict] | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    """Compare system BOQ against reference BOQ.

    Args:
        system_boq:     Output of calculate_quantities().
        reference_boq:  Optional reference rows (approved BOQ).  If None, only
                        source breakdown and BLANK summary is reported.
        reference_path: Display path for the reference file.

    Returns:
        Cross-check result dict (see module docstring).
    """
    rows: list[dict] = []
    ref_matched: set[int] = set()

    # Index reference rows by _row_idx for O(1) lookup
    ref_by_idx: dict[int, dict] = {}
    if reference_boq:
        for r in reference_boq:
            idx = r.get("_row_idx")
            if idx is not None:
                ref_by_idx[idx] = r

    for item in system_boq:
        row_result = _check_item(item, ref_by_idx, ref_matched)
        rows.append(row_result)

    # NEW rows: reference rows with no system match
    new_rows: list[dict] = []
    if reference_boq:
        for ref in reference_boq:
            idx = ref.get("_row_idx")
            if idx not in ref_matched:
                ref_qty = ref.get("qty")
                if ref_qty and float(ref_qty) > 0:
                    new_rows.append({
                        "status":      "NEW",
                        "row_idx":     idx,
                        "description": ref.get("description") or "",
                        "ref_qty":     ref_qty,
                        "sys_qty":     None,
                        "diff_pct":    None,
                        "comment":     "In reference BOQ but not in system BOQ",
                    })

    summary = _summarise(rows, new_rows, system_boq)

    log.info(
        "Cross-check: total=%d  PASS=%d  WARN=%d  FLAG=%d  BLANK=%d  MISSING=%d  NEW=%d",
        summary["total"],
        summary["pass"],
        summary["warn"],
        summary["flag"],
        summary["blank"],
        summary["missing"],
        summary["new"],
    )

    return {
        "rows":           rows + new_rows,
        "summary":        summary,
        "reference_path": reference_path,
    }


# ─── Per-item check ───────────────────────────────────────────────────────────

def _check_item(item: dict, ref_by_idx: dict, ref_matched: set) -> dict:
    """Evaluate one system BOQ item against the reference."""
    desc       = (item.get("description") or "").strip()
    sys_qty    = item.get("qty")
    source     = item.get("source") or "none"
    confidence = item.get("confidence") or "LOW"
    issue_flag = item.get("issue_flag") or ""
    row_idx    = item.get("_row_idx")
    category   = (item.get("category") or "").strip()

    # Skip header / section rows (no qty expected)
    if not desc or (sys_qty is None and not issue_flag):
        return {
            "status":      "SKIP",
            "row_idx":     row_idx,
            "description": desc,
            "category":    category,
            "sys_qty":     sys_qty,
            "ref_qty":     None,
            "diff_pct":    None,
            "source":      source,
            "confidence":  confidence,
            "comment":     "Header/empty row",
        }

    # Blank — system could not compute qty
    if sys_qty is None or issue_flag == "BLANK":
        ref_row = ref_by_idx.get(row_idx)
        ref_qty = None
        if ref_row:
            ref_matched.add(row_idx)
            ref_qty = ref_row.get("qty")
        return {
            "status":      "BLANK",
            "row_idx":     row_idx,
            "description": desc,
            "category":    category,
            "sys_qty":     None,
            "ref_qty":     ref_qty,
            "diff_pct":    None,
            "source":      source,
            "confidence":  confidence,
            "comment":     item.get("comment") or "No source data for this item",
        }

    sys_qty_f = _safe_float(sys_qty)

    # Match to reference
    ref_row = ref_by_idx.get(row_idx)
    ref_qty_f: float | None = None
    if ref_row:
        ref_matched.add(row_idx)
        ref_qty_f = _safe_float(ref_row.get("qty"))

    if ref_qty_f is None:
        # No reference row — system-only item
        return {
            "status":      "MISSING",
            "row_idx":     row_idx,
            "description": desc,
            "category":    category,
            "sys_qty":     sys_qty_f,
            "ref_qty":     None,
            "diff_pct":    None,
            "source":      source,
            "confidence":  confidence,
            "comment":     "No matching row in reference BOQ",
        }

    # Both present — compare
    if ref_qty_f == 0 and sys_qty_f == 0:
        status = "PASS"
        diff_pct = 0.0
    elif ref_qty_f == 0:
        diff_pct = 100.0
        status = "FLAG"
    else:
        diff_pct = abs(sys_qty_f - ref_qty_f) / ref_qty_f * 100.0
        if diff_pct <= 10.0:
            status = "PASS"
        elif diff_pct <= 30.0:
            status = "WARN"
        else:
            status = "FLAG"

    return {
        "status":      status,
        "row_idx":     row_idx,
        "description": desc,
        "category":    category,
        "sys_qty":     sys_qty_f,
        "ref_qty":     ref_qty_f,
        "diff_pct":    round(diff_pct, 1),
        "source":      source,
        "confidence":  confidence,
        "comment":     item.get("comment") or "",
    }


# ─── Summary ──────────────────────────────────────────────────────────────────

def _summarise(rows: list[dict], new_rows: list[dict], system_boq: list[dict]) -> dict:
    """Build summary statistics."""
    counts: dict[str, int] = {
        "pass": 0, "warn": 0, "flag": 0, "blank": 0, "skip": 0, "missing": 0, "new": 0,
    }
    for r in rows:
        key = r["status"].lower()
        counts[key] = counts.get(key, 0) + 1
    counts["new"] = len(new_rows)

    # Exclude SKIP from total
    total = sum(v for k, v in counts.items() if k != "skip")

    # PASS % = PASS / (PASS + WARN + FLAG) — only over computed (non-BLANK) items
    computed = counts["pass"] + counts["warn"] + counts["flag"]
    pass_pct_computed = round(counts["pass"] / computed * 100, 1) if computed > 0 else 0.0

    # PASS % over all assessable (including BLANK) for secondary metric
    assessable = computed + counts["blank"]
    pass_pct = round(counts["pass"] / assessable * 100, 1) if assessable > 0 else 0.0

    # Source breakdown
    source_counts: dict[str, int] = {}
    for item in system_boq:
        src = item.get("source") or "none"
        source_counts[src] = source_counts.get(src, 0) + 1

    return {
        "total":                  total,
        "computed":               computed,
        "pass":                   counts["pass"],
        "warn":                   counts["warn"],
        "flag":                   counts["flag"],
        "blank":                  counts["blank"],
        "skip":                   counts["skip"],
        "missing":                counts["missing"],
        "new":                    counts["new"],
        "pass_pct":               pass_pct,           # over assessable (all items)
        "pass_pct_of_computed":   pass_pct_computed,  # over computed items only
        "source_breakdown":       source_counts,
    }


# ─── Report formatter ─────────────────────────────────────────────────────────

def format_report(result: dict) -> str:
    """Format cross-check result as a plain-text report."""
    s = result["summary"]
    ref_path = result.get("reference_path") or "(no reference)"
    lines = [
        "BOQ CROSS-CHECK REPORT",
        "=" * 80,
        f"Reference : {ref_path}",
        f"Total rows    : {s['total']}",
        f"Computed      : {s.get('computed', s['pass']+s['warn']+s['flag'])}  "
        f"(PASS={s['pass']}  WARN={s['warn']}  FLAG={s['flag']})",
        f"BLANK         : {s['blank']}  (no source — needs BOM or rule)",
        f"MISSING/NEW   : {s['missing']}/{s['new']}",
        f"PASS % (computed items): {s.get('pass_pct_of_computed', 0):.1f}%",
        f"PASS % (all items)     : {s['pass_pct']:.1f}%",
        "",
        "SOURCE BREAKDOWN",
        "-" * 40,
    ]
    for src, cnt in sorted(s["source_breakdown"].items(), key=lambda x: -x[1]):
        lines.append(f"  {src:<30} {cnt:>5}")

    # WARN rows
    warn_rows = [r for r in result["rows"] if r["status"] == "WARN"]
    if warn_rows:
        lines += ["", "WARN rows (10–30% diff):", "-" * 40]
        for r in warn_rows[:20]:
            lines.append(
                f"  [{r['diff_pct']:5.1f}%]  SYS={str(r['sys_qty']):<8}  "
                f"REF={str(r['ref_qty']):<8}  {r['description'][:55]}"
            )
        if len(warn_rows) > 20:
            lines.append(f"  ... and {len(warn_rows)-20} more")

    # FLAG rows
    flag_rows = [r for r in result["rows"] if r["status"] == "FLAG"]
    if flag_rows:
        lines += ["", "FLAG rows (>30% diff):", "-" * 40]
        for r in flag_rows[:20]:
            lines.append(
                f"  [{r['diff_pct']:5.1f}%]  SYS={str(r['sys_qty']):<8}  "
                f"REF={str(r['ref_qty']):<8}  {r['description'][:55]}"
            )
        if len(flag_rows) > 20:
            lines.append(f"  ... and {len(flag_rows)-20} more")

    # BLANK rows
    blank_rows = [r for r in result["rows"] if r["status"] == "BLANK"]
    if blank_rows:
        lines += ["", "BLANK rows (no source data):", "-" * 40]
        for r in blank_rows[:30]:
            lines.append(
                f"  REF={str(r['ref_qty']):<8}  {r['description'][:55]}"
                + (f"  [{r['comment'][:40]}]" if r.get("comment") else "")
            )
        if len(blank_rows) > 30:
            lines.append(f"  ... and {len(blank_rows)-30} more")

    lines += ["", "=" * 80]
    return "\n".join(lines)


# ─── Utilities ────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
