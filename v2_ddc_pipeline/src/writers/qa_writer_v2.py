"""
qa_writer_v2.py — Write QA report (JSON + text summary).

Includes:
  - source_inventory summary
  - extraction_warnings
  - completeness by package
  - v2_vs_v1 comparison
  - measured / derived / provisional / manual counts
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("boq.v2.qa_writer_v2")


def write_qa_report(
    source_inventory:  list[dict],
    project_model:     dict,
    quantity_model:    dict,
    boq_items:         list[dict],
    completeness:      dict,
    benchmark_result:  dict,
    output_dir:        Path,
    project_name:      str = "project",
) -> tuple[Path, Path]:
    """
    Write QA report as JSON and text summary.
    Returns (json_path, txt_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    totals = quantity_model.get("totals_by_basis", {})
    struct_priority = project_model.get("structural", {}).get("source_priority_used", "unknown")
    warnings = project_model.get("extraction_warnings", [])

    # ── Build report dict ─────────────────────────────────────────────────
    report: dict = {
        "project_name":   project_name,
        "source_inventory_summary": {
            "total_files":    len(source_inventory),
            "by_category":    _count_by(source_inventory, "source_category"),
            "by_discipline":  _count_by(source_inventory, "discipline"),
            "by_priority":    _count_by(source_inventory, "priority"),
        },
        "extraction_warnings":    warnings,
        "warning_count":          len(warnings),
        "structural_source_used": struct_priority,
        "quantity_totals": totals,
        "completeness_by_package": completeness,
        "v2_vs_v1_comparison":     benchmark_result,
        "boq_item_count":          len(boq_items),
        "sections": sorted({i.get("boq_section", "") for i in boq_items}),
    }

    json_path = output_dir / f"{project_name}_qa_report.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str, ensure_ascii=False)
    log.info("QA JSON → %s", json_path)

    # ── Text summary ──────────────────────────────────────────────────────
    lines: list[str] = [
        "=" * 70,
        f"V2 DDC PIPELINE — QA REPORT",
        f"Project: {project_name}",
        "=" * 70,
        "",
        "SOURCE INVENTORY",
        f"  Total files:  {len(source_inventory)}",
    ]
    for cat, count in sorted(_count_by(source_inventory, "source_category").items()):
        lines.append(f"    {cat:<30} {count}")

    lines += [
        "",
        "STRUCTURAL SOURCE USED:",
        f"  {struct_priority}",
        "",
        "QUANTITY SUMMARY",
        f"  Total items:      {totals.get('total', 0)}",
        f"  Measured:         {totals.get('measured', 0)}",
        f"  Derived:          {totals.get('derived', 0)}",
        f"  Provisional:      {totals.get('provisional', 0)}",
        f"  Manual review:    {totals.get('manual_review', 0)}",
        "",
        "COMPLETENESS BY PACKAGE",
    ]
    for pkg, data in completeness.items():
        lines.append(
            f"  {pkg:<14} items={data['items']:<3}  "
            f"measured={data['measured_items']}  "
            f"derived={data['derived_items']}  "
            f"provisional={data['provisional_items']}  "
            f"| {data['notes']}"
        )

    v1 = benchmark_result or {}
    if v1.get("v1_items", 0) > 0:
        lines += [
            "",
            "V2 vs V1 COMPARISON",
            f"  V1 items: {v1.get('v1_items')}   V2 items: {v1.get('v2_items')}",
            f"  V1 measured: {v1.get('v1_measured_pct')}%   V2 measured: {v1.get('v2_measured_pct')}%",
        ]
        for note in v1.get("improvement_notes", []):
            lines.append(f"  • {note}")

    if warnings:
        lines += ["", f"EXTRACTION WARNINGS ({len(warnings)})"]
        for w in warnings[:20]:
            lines.append(f"  ! {w}")
        if len(warnings) > 20:
            lines.append(f"  ... and {len(warnings)-20} more (see JSON)")

    lines += ["", "=" * 70]

    txt = "\n".join(lines)
    txt_path = output_dir / f"{project_name}_qa_report.txt"
    txt_path.write_text(txt, encoding="utf-8")
    log.info("QA text → %s", txt_path)

    return json_path, txt_path


def _count_by(records: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        val = str(r.get(field, "unknown"))
        counts[val] = counts.get(val, 0) + 1
    return counts
