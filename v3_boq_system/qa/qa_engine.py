"""
qa_engine.py — QA engine: completeness, traceability, confidence scoring.

Produces:
  A. Package completeness report
  B. Quantity provenance summary
  C. Benchmark comparison (structure only — no quantity copying)
  D. Traceability completeness check
  E. Manual review item list
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

log = logging.getLogger("boq.v3.qa")

# ── Expected package presence table ──────────────────────────────────────────
# For QA — we expect certain packages to be present for typical building types.

_EXPECTED_PACKAGES: dict[str, list[str]] = {
    "residential":        ["structural_frame","roof_cladding","openings_doors","openings_windows",
                           "wall_lining_external","ceiling_lining","finishes","floor_system","footings"],
    "commercial_low_rise":["structural_frame","roof_cladding","openings_doors","openings_windows",
                           "wall_lining_external","ceiling_lining","finishes","floor_system",
                           "footings","services"],
    "pharmacy":           ["structural_frame","roof_cladding","openings_doors","openings_windows",
                           "wall_lining_external","wall_lining_internal","ceiling_lining",
                           "finishes","floor_system","footings","services","insulation"],
}


def run_qa(
    boq_items:        list[dict],
    element_model_summary: dict,
    benchmark_items:  list[dict] | None,
    config:           dict,
) -> dict:
    """
    Run full QA suite on the generated BOQ items.

    Returns a QA report dict.
    """
    project_type = config.get("project", {}).get("type", "commercial_low_rise")
    expected     = _EXPECTED_PACKAGES.get(project_type, _EXPECTED_PACKAGES["commercial_low_rise"])

    report: dict = {
        "project_type":         project_type,
        "total_items":          len(boq_items),
        "package_completeness": {},
        "provenance_summary":   {},
        "benchmark_comparison": {},
        "traceability_check":   {},
        "manual_review_items":  [],
        "gap_analysis":         [],
        "confidence_summary":   {},
        "warnings":             [],
    }

    # ── A. Package completeness ───────────────────────────────────────────────
    present_packages = set(i.get("package", "") for i in boq_items)
    for pkg in expected:
        items_in_pkg = [i for i in boq_items if i.get("package", "") == pkg]
        present = len(items_in_pkg) > 0
        measured = sum(1 for i in items_in_pkg if i.get("quantity_status") == "measured")
        calculated = sum(1 for i in items_in_pkg if i.get("quantity_status") == "calculated")
        inferred   = sum(1 for i in items_in_pkg if i.get("quantity_status") == "inferred")
        placeholder = sum(1 for i in items_in_pkg if i.get("quantity_status") == "placeholder")
        mr_count   = sum(1 for i in items_in_pkg if i.get("manual_review"))

        # Determine status — floor_system is UNCONFIRMED when no measured source exists
        # (calculated sheeting from floor area is still unconfirmed structure)
        if present and items_in_pkg:
            if pkg == "floor_system" and measured == 0 and inferred > 0:
                pkg_status = "UNCONFIRMED"
            elif placeholder == len(items_in_pkg) and len(items_in_pkg) > 0:
                pkg_status = "PLACEHOLDER_ONLY"
            else:
                pkg_status = "OK"
        else:
            pkg_status = "MISSING"

        report["package_completeness"][pkg] = {
            "present":     present,
            "item_count":  len(items_in_pkg),
            "measured":    measured,
            "calculated":  calculated,
            "inferred":    inferred,
            "placeholder": placeholder,
            "manual_review": mr_count,
            "status":      pkg_status,
        }
        if not present:
            report["gap_analysis"].append({
                "severity": "HIGH",
                "package":  pkg,
                "issue":    f"Package '{pkg}' expected but not present in output",
                "action":   "Check extractor outputs and quantifier for this package",
            })
        elif pkg_status == "UNCONFIRMED":
            report["gap_analysis"].append({
                "severity": "MEDIUM",
                "package":  pkg,
                "issue":    f"Floor system package present but all items are inferred — no measured schedule",
                "action":   "Obtain floor panel/joist schedule from FrameCAD or structural engineer",
            })

    # ── B. Provenance summary ─────────────────────────────────────────────────
    status_counts = Counter(i.get("quantity_status", "unknown") for i in boq_items)
    total = len(boq_items) or 1
    report["provenance_summary"] = {
        "total":        len(boq_items),
        "measured":     status_counts.get("measured",     0),
        "calculated":   status_counts.get("calculated",   0),
        "inferred":     status_counts.get("inferred",     0),
        "placeholder":  status_counts.get("placeholder",  0),
        "pct_measured":    round(status_counts.get("measured",    0) / total * 100, 1),
        "pct_calculated":  round(status_counts.get("calculated",  0) / total * 100, 1),
        "pct_inferred":    round(status_counts.get("inferred",    0) / total * 100, 1),
        "pct_placeholder": round(status_counts.get("placeholder", 0) / total * 100, 1),
        "manual_review_count": sum(1 for i in boq_items if i.get("manual_review")),
    }

    # ── C. Benchmark comparison (structure only) ──────────────────────────────
    if benchmark_items:
        bench_sections = set(i.get("section", i.get("boq_section", "")) for i in benchmark_items)
        v3_sections    = set(i.get("boq_section", "") for i in boq_items)
        bench_names    = [i.get("description", i.get("item_name", "")) for i in benchmark_items]
        v3_names       = [i.get("item_name", "") for i in boq_items]

        missing_sections = bench_sections - v3_sections
        extra_sections   = v3_sections - bench_sections

        # Simple keyword overlap
        def _name_key(s: str) -> str:
            return " ".join(s.lower().split()[:3])

        bench_keys = set(_name_key(n) for n in bench_names)
        v3_keys    = set(_name_key(n) for n in v3_names)
        possibly_missing = bench_keys - v3_keys

        report["benchmark_comparison"] = {
            "benchmark_items":    len(benchmark_items),
            "v3_items":           len(boq_items),
            "missing_sections":   list(missing_sections),
            "extra_sections":     list(extra_sections),
            "potentially_missing_families": list(possibly_missing)[:20],
            "note": (
                "IMPORTANT: Benchmark used for structure comparison ONLY. "
                "No quantities from benchmark are used."
            ),
        }
        for sec in missing_sections:
            report["gap_analysis"].append({
                "severity": "MEDIUM",
                "package":  sec,
                "issue":    f"Section '{sec}' present in benchmark but missing from V3 output",
                "action":   "Review if this section is expected for this project type",
            })

    # ── D. Traceability completeness ──────────────────────────────────────────
    missing_evidence = [i for i in boq_items if not i.get("source_evidence")]
    missing_rule     = [i for i in boq_items if not i.get("derivation_rule")]
    missing_basis    = [i for i in boq_items if not i.get("quantity_basis")]

    report["traceability_check"] = {
        "items_missing_evidence": len(missing_evidence),
        "items_missing_rule":     len(missing_rule),
        "items_missing_basis":    len(missing_basis),
        "traceability_score":     round(
            (1 - len(missing_evidence) / total) * 100, 1
        ),
        "status": "OK" if not missing_evidence else "INCOMPLETE",
    }
    for i in missing_evidence[:5]:
        report["warnings"].append(
            f"Missing source_evidence on: {i.get('item_name','')} "
            f"[section={i.get('boq_section','')}]"
        )

    # ── E. Manual review items ────────────────────────────────────────────────
    report["manual_review_items"] = [
        {
            "item_no":    i.get("item_no", ""),
            "section":    i.get("boq_section", ""),
            "item_name":  i.get("item_name", ""),
            "quantity":   i.get("quantity"),
            "unit":       i.get("unit"),
            "confidence": i.get("confidence"),
            "notes":      i.get("notes", ""),
        }
        for i in boq_items if i.get("manual_review")
    ]

    # ── F. Confidence summary ─────────────────────────────────────────────────
    conf_counts = Counter(i.get("confidence", "LOW") for i in boq_items)
    report["confidence_summary"] = {
        "HIGH":   conf_counts.get("HIGH",   0),
        "MEDIUM": conf_counts.get("MEDIUM", 0),
        "LOW":    conf_counts.get("LOW",    0),
        "pct_high":   round(conf_counts.get("HIGH",   0) / total * 100, 1),
        "pct_medium": round(conf_counts.get("MEDIUM", 0) / total * 100, 1),
        "pct_low":    round(conf_counts.get("LOW",    0) / total * 100, 1),
    }

    # ── Template contamination check ─────────────────────────────────────────
    # Verify no items have "boq_template" as their source
    contaminated = [
        i for i in boq_items
        if "boq_template" in i.get("source_evidence", "").lower()
        or "benchmark" in i.get("quantity_basis", "").lower()
    ]
    if contaminated:
        report["warnings"].append(
            f"TEMPLATE CONTAMINATION DETECTED: {len(contaminated)} items "
            f"appear to source quantities from BOQ template. INVESTIGATE IMMEDIATELY."
        )
        report["gap_analysis"].append({
            "severity": "CRITICAL",
            "package":  "ALL",
            "issue":    "BOQ template contamination detected",
            "action":   "Audit source_evidence fields for template-sourced quantities",
        })
    else:
        report["warnings"].append("Template contamination check: PASSED — no BOQ template quantities detected.")

    log.info(
        "QA complete: %d items | measured=%.1f%% calculated=%.1f%% "
        "inferred=%.1f%% placeholder=%.1f%% | manual_review=%d",
        len(boq_items),
        report["provenance_summary"]["pct_measured"],
        report["provenance_summary"]["pct_calculated"],
        report["provenance_summary"]["pct_inferred"],
        report["provenance_summary"]["pct_placeholder"],
        report["provenance_summary"]["manual_review_count"],
    )
    return report
