"""
qa_reporter.py — Generate JSON QA report, text summary, and material completeness review.
"""

from __future__ import annotations
import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from src import ai_client
from src.config import OUTPUT_REPORTS, Confidence
from src.utils import save_json, timestamp_str

log = logging.getLogger("boq.qa_reporter")

_SYSTEM = ai_client.SYSTEM_PROMPT_MASTER

_PROMPT_COMPLETENESS = """You are performing a material completeness review for a residential BOQ.

Assess whether the available drawing data is sufficient to prepare a reliable BOQ for each category.

Categories to assess:
1. Structural framing
2. Floor panels / joists / bearers
3. Roof framing / trusses
4. Bracing and connectors
5. Roof battens
6. Ceiling battens
7. FC sheets
8. Plasterboard / internal linings
9. Insulation
10. Doors
11. Windows
12. Finishes
13. Stairs and balustrades
14. Wet area fixtures
15. Roof plumbing

Return JSON only:
{
  "material_check": [
    {
      "category": "",
      "status": "COMPLETE|PARTIAL|MISSING",
      "confidence": "HIGH|MEDIUM|LOW",
      "basis": "",
      "issues": [],
      "boq_risk": "LOW|MEDIUM|HIGH",
      "recommended_estimator_action": ""
    }
  ],
  "priority_review_items": [],
  "overall_assessment": ""
}"""

_PROMPT_VARIATION = """You are comparing a project drawing set against a standard house model BOQ.
Identify deviations that may affect quantities or item descriptions.

Return JSON only:
{
  "detected_variations": [
    {"category": "", "standard_condition": "", "project_condition": "",
     "impact_on_boq": "", "risk": "LOW|MEDIUM|HIGH"}
  ],
  "removed_scope": [{"category": "", "item_or_scope": "", "reason": ""}],
  "added_scope": [{"category": "", "item_or_scope": "", "reason": ""}],
  "modified_scope": [{"category": "", "item_or_scope": "", "change_summary": ""}],
  "review_notes": []
}

Pay special attention to: laundry relocation, stair changes, raised floor/foundation,
window/door type changes, verandah changes, floor panel changes, wet area layout,
finish changes, structural framing changes."""


def generate_report(
    project_name: str,
    files_found: dict,
    boq_items: list[dict],
    validation: dict,
    merged: dict,
    project_mode: str,
    house_type: str,
) -> dict[str, Any]:
    """Generate full QA report and write to disk.  Returns report dict."""
    report: dict[str, Any] = {
        "project": project_name,
        "date": str(date.today()),
        "project_mode": project_mode,
        "house_type": house_type,
        "files_used": _summarise_files(files_found),
        "extraction_status": _extraction_status(files_found, merged),
        "conflicts": validation.get("conflicts", []),
        "missing_scope": validation.get("missing_scope", []),
        "relationship_checks": validation.get("relationship_checks", []),
        "low_confidence_items": _low_conf(boq_items),
        "derived_items": _derived(boq_items),
        "manual_review_items": _review_required(boq_items),
        "material_completeness": {},
        "standard_model_variations": {},
        "overall_notes": validation.get("overall_notes", []),
        "warnings": [],
    }

    # AI completeness review
    if ai_client.is_available():
        _run_completeness_review(report, merged, boq_items)
        _run_variation_check(report, merged, house_type)

    # Write outputs
    report_path = OUTPUT_REPORTS / f"{project_name}_QA_{timestamp_str()}.json"
    save_json(report, report_path)

    summary_path = OUTPUT_REPORTS / f"{project_name}_QA_{timestamp_str()}.txt"
    _write_text_summary(report, summary_path)

    conflicts_path = OUTPUT_REPORTS / f"{project_name}_Conflicts_{timestamp_str()}.csv"
    if report["conflicts"]:
        _write_conflicts_csv(report["conflicts"], conflicts_path)

    log.info(
        "QA report written: %s  conflicts=%d  missing=%d  low-conf=%d  review=%d",
        report_path.name,
        len(report["conflicts"]),
        len(report["missing_scope"]),
        len(report["low_confidence_items"]),
        len(report["manual_review_items"]),
    )
    return report


def _summarise_files(files: dict) -> dict:
    return {
        "dwg": len(files.get("dwg", [])),
        "dxf": len(files.get("dxf", [])),
        "pdf": len(files.get("pdf", [])),
        "ifc": len(files.get("ifc", [])),
        "bom": len(files.get("bom", [])),
    }


def _extraction_status(files: dict, merged: dict) -> dict:
    struct = merged.get("structural", {})
    return {
        "dwg_extracted": bool(merged.get("geometry", {}).get("total_floor_area_m2")),
        "pdf_extracted": bool(merged.get("doors") or merged.get("finishes")),
        "bom_extracted": bool(struct.get("bom_raw")),
        "ifc_extracted": bool(files.get("ifc")),
        "titleblock_detected": bool(merged.get("metadata", {}).get("project_name")),
        "doors_source": merged.get("audit", {}).get("doors_source", "none"),
        "windows_source": merged.get("audit", {}).get("windows_source", "none"),
        "finishes_source": merged.get("audit", {}).get("finishes_source", "none"),
        "stairs_source": merged.get("audit", {}).get("stairs_source", "none"),
    }


def _low_conf(items: list) -> list:
    return [
        {"item_no": i.get("item_no"), "description": i.get("description"),
         "confidence": i.get("confidence"), "source": i.get("source")}
        for i in items if (i.get("confidence") or "").upper() == "LOW"
    ]


def _derived(items: list) -> list:
    return [
        {"item_no": i.get("item_no"), "description": i.get("description"),
         "assumption": i.get("assumption"), "comment": i.get("comment")}
        for i in items if i.get("issue_flag") == "DERIVED_QUANTITY"
    ]


def _review_required(items: list) -> list:
    return [
        {"item_no": i.get("item_no"), "description": i.get("description"),
         "issue_flag": i.get("issue_flag"), "comment": i.get("comment")}
        for i in items if i.get("issue_flag") in ("REVIEW_REQUIRED", "MISSING_DATA")
    ]


def _run_completeness_review(report: dict, merged: dict, boq_items: list) -> None:
    import json as _json
    summary = {
        "geometry": merged.get("geometry", {}),
        "structural_keys": list(merged.get("structural", {}).keys()),
        "door_count": len(merged.get("doors", [])),
        "window_count": len(merged.get("windows", [])),
        "finish_count": len(merged.get("finishes", [])),
        "stair_count": len(merged.get("stairs", [])),
        "has_bom": bool(merged.get("structural", {}).get("bom_raw")),
    }
    prompt = (
        _PROMPT_COMPLETENESS
        + f"\n\nAvailable data summary:\n{_json.dumps(summary, indent=2, default=str)}"
    )
    result = ai_client.call_json(
        user_prompt=prompt, system_prompt=_SYSTEM,
        label="completeness_review", max_tokens=4096,
    )
    if result and isinstance(result, dict):
        report["material_completeness"] = result
    else:
        report["warnings"].append("AI completeness review returned no result")


def _run_variation_check(report: dict, merged: dict, house_type: str) -> None:
    import json as _json
    meta = merged.get("metadata", {})
    geo = merged.get("geometry", {})
    summary = {
        "house_type": house_type,
        "highset": meta.get("highset"),
        "laundry_location": meta.get("laundry_location"),
        "floor_area": geo.get("total_floor_area_m2"),
        "roof_area": geo.get("roof_area_m2"),
        "door_types": [d.get("type_mapped") for d in merged.get("doors", [])],
        "window_types": [w.get("type_mapped") for w in merged.get("windows", [])],
        "stair_flights": len(merged.get("stairs", [])),
    }
    prompt = (
        _PROMPT_VARIATION
        + f"\n\nProject vs standard comparison data:\n{_json.dumps(summary, indent=2, default=str)}"
    )
    result = ai_client.call_json(
        user_prompt=prompt, system_prompt=_SYSTEM,
        label="variation_check", max_tokens=3000,
    )
    if result and isinstance(result, dict):
        report["standard_model_variations"] = result
    else:
        report["warnings"].append("AI variation check returned no result")


def _write_text_summary(report: dict, path: Path) -> None:
    lines = [
        "=" * 60,
        f"QA REPORT — {report['project']}",
        f"Date: {report['date']}",
        f"Mode: {report['project_mode']}  House type: {report['house_type']}",
        "=" * 60,
        "",
        "FILES USED:",
        *(f"  {k}: {v}" for k, v in report["files_used"].items()),
        "",
        "EXTRACTION STATUS:",
        *(f"  {k}: {v}" for k, v in report["extraction_status"].items()),
        "",
        f"CONFLICTS: {len(report['conflicts'])}",
        *(
            f"  [{c.get('severity')}] {c.get('item_name')}: "
            f"{c.get('source_a')}={c.get('value_a')} vs {c.get('source_b')}={c.get('value_b')}"
            for c in report["conflicts"]
        ),
        "",
        f"MISSING SCOPE: {len(report['missing_scope'])}",
        *(f"  [{m.get('risk')}] {m.get('category')}: {m.get('description')}"
          for m in report["missing_scope"]),
        "",
        f"LOW CONFIDENCE ITEMS: {len(report['low_confidence_items'])}",
        f"DERIVED/ASSUMED ITEMS: {len(report['derived_items'])}",
        f"MANUAL REVIEW REQUIRED: {len(report['manual_review_items'])}",
        "",
        "RELATIONSHIP CHECKS:",
        *(f"  [{c.get('status')}] {c.get('check_name')}: {c.get('details')}"
          for c in report["relationship_checks"]),
        "",
        "OVERALL NOTES:",
        *(f"  • {n}" for n in report["overall_notes"]),
        "",
        "=" * 60,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_conflicts_csv(conflicts: list, path: Path) -> None:
    fields = ["item_name", "source_a", "value_a", "source_b", "value_b",
              "diff_pct", "severity", "recommended_action"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(conflicts)
