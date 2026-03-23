"""
benchmark_compare.py — Compare V2 output against V1 benchmark.

IMPORTANT: This comparison is for coverage and structure ONLY.
DO NOT use V1 quantities to fill gaps in V2.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("boq.v2.benchmark_compare")


def compare_with_v1(
    v2_boq_items:      list[dict],
    v1_benchmark_path: Path,
) -> dict:
    """
    Compare V2 output vs V1 benchmark.

    Returns structural / coverage comparison.  No V1 quantities are used to
    fill V2 gaps.

    v1_benchmark_path: path to benchmarks/angau_pharmacy/v1_boq_items.json
    """
    empty_result = {
        "v1_items":            0,
        "v2_items":            len(v2_boq_items),
        "sections_v1":         [],
        "sections_v2":         list({i.get("boq_section", "") for i in v2_boq_items}),
        "sections_added":      [],
        "sections_removed":    [],
        "v2_measured_pct":     0.0,
        "v1_measured_pct":     0.0,
        "improvement_notes":   ["V1 benchmark not available"],
    }

    if not v1_benchmark_path.exists():
        log.warning("V1 benchmark not found at %s", v1_benchmark_path)
        return empty_result

    try:
        with open(v1_benchmark_path, encoding="utf-8") as fh:
            v1_items: list[dict] = json.load(fh)
    except Exception as exc:
        log.error("Failed to load V1 benchmark: %s", exc)
        empty_result["improvement_notes"] = [f"V1 benchmark load error: {exc}"]
        return empty_result

    # Sections
    sections_v1 = sorted({i.get("boq_section", i.get("item_group", "")) for i in v1_items})
    sections_v2 = sorted({i.get("boq_section", "") for i in v2_boq_items})
    sections_added   = [s for s in sections_v2 if s not in sections_v1]
    sections_removed = [s for s in sections_v1 if s not in sections_v2]

    # Measured % — V1 uses "quantity_basis" same field
    def _measured_pct(items: list[dict]) -> float:
        if not items:
            return 0.0
        measured = sum(1 for i in items if i.get("quantity_basis") == "measured")
        return round(100 * measured / len(items), 1)

    v1_meas_pct = _measured_pct(v1_items)
    v2_meas_pct = _measured_pct(v2_boq_items)

    # Improvement notes
    improvement_notes: list[str] = []
    if v2_meas_pct > v1_meas_pct:
        improvement_notes.append(
            f"V2 measured items: {v2_meas_pct}% vs V1: {v1_meas_pct}% — improvement"
        )
    elif v2_meas_pct < v1_meas_pct:
        improvement_notes.append(
            f"V2 measured items: {v2_meas_pct}% vs V1: {v1_meas_pct}% — regression (investigate)"
        )
    else:
        improvement_notes.append(f"Measured % unchanged: {v2_meas_pct}%")

    if sections_added:
        improvement_notes.append(f"New sections in V2: {sections_added}")
    if sections_removed:
        improvement_notes.append(f"Sections removed in V2: {sections_removed}")
    if len(v2_boq_items) > len(v1_items):
        improvement_notes.append(
            f"V2 has more items: {len(v2_boq_items)} vs V1: {len(v1_items)}"
        )

    log.info(
        "Benchmark compare: V1=%d items, V2=%d items | V1 measured=%.1f%%, V2 measured=%.1f%%",
        len(v1_items), len(v2_boq_items), v1_meas_pct, v2_meas_pct,
    )

    return {
        "v1_items":          len(v1_items),
        "v2_items":          len(v2_boq_items),
        "sections_v1":       sections_v1,
        "sections_v2":       sections_v2,
        "sections_added":    sections_added,
        "sections_removed":  sections_removed,
        "v2_measured_pct":   v2_meas_pct,
        "v1_measured_pct":   v1_meas_pct,
        "improvement_notes": improvement_notes,
    }
