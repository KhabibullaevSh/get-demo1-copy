"""
commercial_aligner.py — Orchestrator for the full alignment pipeline.

Usage (CLI)
-----------
python -m alignment.commercial_aligner \\
    --reference  "../input/project 2_BOQ_20260323.xlsx" \\
    --ai-boq     "outputs/project2/project2_boq_items_v3.json" \\
    --output-dir "outputs/project2"

Optional flags
    --fixings-strategy  standalone|embedded   (default: auto-detect)
    --quiet

Usage (API)
-----------
from alignment.commercial_aligner import run_alignment

report = run_alignment(
    reference_boq_path  = "../input/project 2_BOQ_20260323.xlsx",
    ai_boq_path         = "outputs/project2/project2_boq_items_v3.json",
    output_dir          = "outputs/project2",
)
"""

from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

from .baseline_profiler  import profile_baseline_boq
from .ai_profiler        import profile_ai_boq
from .section_comparator import compare_profiles
from .upgrade_rules      import apply_upgrade_rules
from .scoring            import score_all_sections, format_scorecard
from .project_type_inferrer import infer_project_type, compatibility_weight


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_SCHEDULES = {
    "door_schedule",
    "window_schedule",
    "room_finish_schedule",
    "services_schedule",
}


def _detect_missing_schedules(ai_boq_path: Path) -> set[str]:
    """Heuristically determine which schedules are missing by scanning the
    ai_boq JSON for evidence.  The pipeline records schedule extraction
    results in the boq_items notes; we also check companion JSON files."""
    missing: set[str] = set()

    # Look for a pdf_schedules_v3.json companion file
    parent = ai_boq_path.parent
    stem = ai_boq_path.stem.replace("_boq_items_v3", "")
    schedules_path = parent / f"{stem}_pdf_schedules_v3.json"

    if schedules_path.exists():
        try:
            with open(schedules_path, encoding="utf-8") as fh:
                sched_data = json.load(fh)
            not_found = sched_data.get("not_found", [])
            for s in not_found:
                if s in _KNOWN_SCHEDULES:
                    missing.add(s)
        except Exception:
            pass

    # Also scan the items themselves for mentions of missing schedules
    if not missing:
        try:
            with open(ai_boq_path, encoding="utf-8") as fh:
                items = json.load(fh)
            all_notes = " ".join(
                (i.get("notes") or "") + (i.get("source_evidence") or "")
                for i in items
            ).lower()
            for sched in _KNOWN_SCHEDULES:
                if f"no {sched.replace('_', ' ')}" in all_notes:
                    missing.add(sched)
                # more general: schedule not found / not available
            # Fallback: if >40% of items are manual_review, assume schedules missing
            mr_count = sum(1 for i in items if i.get("manual_review"))
            if mr_count / max(len(items), 1) > 0.40:
                # Can't determine which schedules are missing; be conservative
                missing.update({"door_schedule", "window_schedule",
                                 "room_finish_schedule", "services_schedule"})
        except Exception:
            pass

    return missing


# ---------------------------------------------------------------------------
# Improved report builders
# ---------------------------------------------------------------------------

def _categorise_gaps(comparison: dict) -> dict:
    """Split all gaps into completeness / style / unsupported buckets."""
    completeness_gaps: list[dict] = []
    style_gaps:        list[dict] = []
    unsupported_gaps:  list[dict] = []
    type_mismatch:     list[dict] = []

    for code, res in comparison.get("section_results", {}).items():
        label = res.get("base_label") or res.get("ai_label", "")
        for fg in res.get("family_gaps", []):
            cls = fg.get("classification", "")
            entry = {"section_code": code, "section_label": label, **fg}
            if cls in ("MISSING_REQUIRED", "MISSING_EXPECTED"):
                completeness_gaps.append(entry)
            elif cls == "UNSUPPORTED_BY_SOURCE":
                unsupported_gaps.append(entry)
            elif cls == "PROJECT_TYPE_MISMATCH":
                type_mismatch.append(entry)
            elif cls in ("MISSING_OPTIONAL",):
                style_gaps.append(entry)
        for ug in res.get("unit_gaps", []):
            style_gaps.append({"section_code": code, "section_label": label, **ug})

    return {
        "completeness_gaps": completeness_gaps[:15],
        "style_gaps":        style_gaps[:15],
        "unsupported_gaps":  unsupported_gaps[:15],
        "type_mismatch":     type_mismatch[:15],
    }


def _build_actions(
    comparison: dict,
    scoring:    dict,
    missing_schedules: set[str],
) -> list[dict]:
    """Generate prioritised recommended export-layer actions."""
    actions: list[dict] = []

    section_scores = scoring.get("section_scores", {})

    # 1. Unit style: lm → len (most impactful quick win)
    lm_len_sections = []
    for code, res in comparison.get("section_results", {}).items():
        for ug in res.get("unit_gaps", []):
            if (ug.get("unit") == "lm" and
                    ug.get("gap_type") == "PRESENTATION_MISMATCH"):
                lm_len_sections.append(code)
                break
    if lm_len_sections:
        actions.append({
            "priority": 1,
            "action": "UNIT_CONVERT_LM_TO_LEN",
            "description": (
                "Convert lm→len (stock-length) for batten, cladding, gutter, "
                "fascia rows where stock length is stated in description. "
                "Use unit_aligner.align_unit(item, 'len').  "
                "Source quantity preserved in quantity_source_value."
            ),
            "affected_sections": lm_len_sections,
            "effort": "LOW",
            "impact": "MEDIUM",
        })

    # 2. nr → each rename
    nr_each_sections = []
    for code, res in comparison.get("section_results", {}).items():
        for ug in res.get("unit_gaps", []):
            if ({ug.get("unit"), ug.get("counterpart")} == {"nr", "each"}):
                nr_each_sections.append(code)
                break
    if nr_each_sections:
        actions.append({
            "priority": 2,
            "action": "UNIT_RENAME_NR_TO_EACH",
            "description": (
                "Rename 'nr' → 'each' in commercial view output (semantic "
                "equivalents; no quantity change).  "
                "Apply in excel_writer.py commercial sheet render only."
            ),
            "affected_sections": nr_each_sections,
            "effort": "LOW",
            "impact": "LOW",
        })

    # 3. m2 → each for FC sheets
    area_sheet_sections = []
    for code, res in comparison.get("section_results", {}).items():
        for ug in res.get("unit_gaps", []):
            if ({ug.get("unit"), ug.get("counterpart")} == {"m2", "each"}):
                area_sheet_sections.append(code)
                break
    if area_sheet_sections:
        actions.append({
            "priority": 3,
            "action": "UNIT_CONVERT_AREA_TO_SHEETS",
            "description": (
                "Convert m²→each for FC sheet rows where sheet dimensions "
                "appear in description (1200x2400, 1200x2700).  "
                "Use unit_aligner.align_unit(item, 'each').  "
                "Source m² preserved."
            ),
            "affected_sections": area_sheet_sections,
            "effort": "LOW",
            "impact": "MEDIUM",
        })

    # 4. Missing required families
    req_gaps = comparison.get("required_gaps", [])
    if req_gaps:
        families = sorted({g["family"] for g in req_gaps
                           if g.get("classification") == "MISSING_REQUIRED"})[:5]
        actions.append({
            "priority": 4,
            "action": "ADD_MISSING_REQUIRED_FAMILIES",
            "description": (
                f"Required families absent from AI BOQ: {families}.  "
                "Verify these are actually present in source documents and "
                "that the family classifier is matching them correctly.  "
                "If source documents lack data, add placeholder rows."
            ),
            "affected_families": families,
            "effort": "MEDIUM",
            "impact": "HIGH",
        })

    # 5. Missing schedules advisory
    if missing_schedules:
        actions.append({
            "priority": 5,
            "action": "OBTAIN_MISSING_SCHEDULES",
            "description": (
                f"Missing source schedules: {sorted(missing_schedules)}.  "
                "Request these from architect/engineer to reduce manual-review "
                "rates and improve confidence.  Affected sections will score "
                "UNSUPPORTED_BY_SOURCE until schedules are available."
            ),
            "missing_schedules": sorted(missing_schedules),
            "effort": "HIGH",
            "impact": "HIGH",
        })

    # 6. Fixings distribution style
    gf_diffs = comparison.get("global_flag_diffs", {})
    if "fixings_standalone_section" in gf_diffs:
        diff = gf_diffs["fixings_standalone_section"]
        actions.append({
            "priority": 6,
            "action": "REVIEW_FIXINGS_DISTRIBUTION",
            "description": (
                f"Baseline fixings_standalone={diff['baseline']}, "
                f"AI fixings_standalone={diff['ai']}.  "
                "If baseline embeds fixings within trade sections, consider "
                "redistributing AI BOQ fixings from 50111 into respective "
                "sections.  Current AI approach (standalone) is more "
                "transparent — only change if QS review confirms embedding "
                "is preferred."
            ),
            "effort": "MEDIUM",
            "impact": "LOW",
        })

    return actions


def _build_gap_report(
    comparison: dict,
    scoring:    dict,
    baseline_type: str,
    ai_type:    str,
    missing_schedules: set[str],
    gap_cats:   dict,
    upgrade_log: list[dict] | None = None,
) -> str:
    lines = [
        "=" * 72,
        "  COMMERCIAL ALIGNMENT GAP REPORT  (calibrated dual-score model)",
        f"  Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"  Baseline type: {baseline_type}  |  AI type: {ai_type}  |  "
        f"Compatibility: {int(compatibility_weight(baseline_type, ai_type)*100)}%",
        f"  Overall:       {scoring['overall_project_score']:.1%}  "
        f"(Grade {scoring['grade']})",
        f"  Completeness:  {scoring['overall_completeness_score']:.1%}  "
        f"Style: {scoring['overall_style_score']:.1%}",
        "=" * 72,
    ]

    # ── Section results ──────────────────────────────────────────────────────
    def _section(title: str, codes: list[str]) -> None:
        if not codes:
            return
        lines.append(f"\n── {title} ──")
        res_map = comparison.get("section_results", {})
        for code in codes:
            res = res_map.get(code, {})
            label = res.get("base_label") or res.get("ai_label") or ""
            sc = scoring["section_scores"].get(code, {})
            comp = sc.get("commercial_completeness_score", 0)
            sty  = sc.get("style_alignment_score", 0)
            gs   = sc.get("gap_summary", {})
            lines.append(
                f"  {code}  {label:<38}  "
                f"comp={comp:.2f} style={sty:.2f}"
            )
            req  = gs.get("required_missing", 0)
            exp  = gs.get("expected_missing", 0)
            opt  = gs.get("optional_missing", 0)
            uns  = gs.get("unsupported_missing", 0)
            pres = gs.get("presentation_mismatches", 0)
            ug   = gs.get("real_unit_gaps", 0)
            if req:  lines.append(f"       Required families missing:      {req}")
            if exp:  lines.append(f"       Expected families missing:      {exp}")
            if opt:  lines.append(f"       Optional families missing:      {opt}")
            if uns:  lines.append(f"       Source-unsupported gaps:        {uns}")
            if pres: lines.append(f"       Presentation mismatches (pres): {pres}")
            if ug:   lines.append(f"       Real unit gaps:                 {ug}")

    summary = comparison.get("summary", {})
    _section("GOOD sections",            summary.get("good", []))
    _section("STYLE MISMATCH sections",  summary.get("style_mismatch", []))
    _section("PARTIAL sections",         summary.get("partial", []))
    _section("MISSING sections",         summary.get("missing", []))
    _section("EXTRA sections",           summary.get("extra", []))
    _section("EMPTY BASELINE",           summary.get("empty_baseline", []))
    _section("UNSUPPORTED BY SOURCE",    summary.get("unsupported_by_source", []))

    # ── Top 10 completeness gaps ─────────────────────────────────────────────
    cg = gap_cats.get("completeness_gaps", [])
    if cg:
        lines.append("\n── Top Completeness Gaps (MISSING_REQUIRED / MISSING_EXPECTED) ──")
        for g in cg[:10]:
            lines.append(
                f"  [{g['classification']:<22}] "
                f"{g['section_code']} · {g['family']}"
            )

    # ── Top 10 style gaps ────────────────────────────────────────────────────
    sg = gap_cats.get("style_gaps", [])
    if sg:
        lines.append("\n── Top Style Gaps (presentation / optional / unit) ──")
        for g in sg[:10]:
            if "unit" in g:
                lines.append(
                    f"  [UNIT {g.get('gap_type','?'):<18}] "
                    f"{g['section_code']} · unit='{g['unit']}'"
                    + (f" ↔ '{g['counterpart']}'" if g.get("counterpart") else "")
                )
            else:
                lines.append(
                    f"  [{g.get('classification','?'):<22}] "
                    f"{g['section_code']} · {g['family']}"
                )

    # ── Top 10 unsupported gaps ──────────────────────────────────────────────
    ug = gap_cats.get("unsupported_gaps", [])
    if ug:
        lines.append("\n── Top Unsupported-by-Source Gaps ──")
        for g in ug[:10]:
            lines.append(
                f"  [UNSUPPORTED_BY_SOURCE    ] "
                f"{g['section_code']} · {g['family']}"
            )

    # ── Closed / remaining gaps from this pass ───────────────────────────────
    if upgrade_log:
        closed_lm   = [e for e in upgrade_log if e.get("rule") == "apply_lm_to_len"]
        closed_m2   = [e for e in upgrade_log if e.get("rule") == "apply_area_to_sheets"]
        closed_fam  = [e for e in upgrade_log if e.get("rule") == "add_missing_commercial_families"]
        closed_fix  = [e for e in upgrade_log if e.get("rule") == "fixings_redistribution"]
        if any([closed_lm, closed_m2, closed_fam, closed_fix]):
            lines.append("\n── Gaps Closed in This Pass ──")
            if closed_lm:
                lines.append(
                    f"  lm→len conversions applied:    {len(closed_lm)} items"
                )
                for e in closed_lm[:5]:
                    lines.append(f"    · {e.get('item_name','')} [{e.get('section','')}]")
            if closed_m2:
                lines.append(
                    f"  m2→each conversions applied:   {len(closed_m2)} items"
                )
                for e in closed_m2[:5]:
                    lines.append(f"    · {e.get('item_name','')} [{e.get('family','')}]")
            if closed_fam:
                lines.append(
                    f"  Missing-family placeholders:   {len(closed_fam)} added"
                )
                for e in closed_fam[:5]:
                    lines.append(
                        f"    · {e.get('family','')} in {e.get('section','')} "
                        f"[{e.get('action','')}]"
                    )
            if closed_fix:
                lines.append(
                    f"  Fixings redistributed:         {len(closed_fix)} items"
                )

        # Gaps that remain style-only (PRESENTATION_MISMATCH, not converted)
        style_remain = [g for g in gap_cats.get("style_gaps", []) if "unit" in g]
        if style_remain:
            lines.append(
                f"\n── Remaining Style-Only Gaps (presentation mismatches) ──"
            )
            lines.append(
                f"  {len(style_remain)} unit presentation gaps remain "
                f"(nr↔each / lm↔len / m2↔each without sheet dims)."
            )

        # Gaps that remain unsupported-by-source
        unsup = gap_cats.get("unsupported_gaps", [])
        if unsup:
            lines.append(
                f"\n── Remaining Unsupported-by-Source Gaps ──"
            )
            lines.append(
                f"  {len(unsup)} gaps are source-constrained "
                f"(missing schedule data — not export-logic failures)."
            )

    # ── Recommended actions ──────────────────────────────────────────────────
    actions = _build_actions(comparison, scoring, missing_schedules)
    if actions:
        lines.append("\n── Recommended Export-Layer Actions (priority order) ──")
        for a in sorted(actions, key=lambda x: x["priority"]):
            lines.append(
                f"  P{a['priority']}  [{a['effort']:<6} effort / "
                f"{a['impact']:<6} impact]  {a['action']}"
            )
            desc_lines = [a["description"][i:i+65]
                          for i in range(0, len(a["description"]), 65)]
            for dl in desc_lines:
                lines.append(f"       {dl}")

    # ── Global flag diffs ────────────────────────────────────────────────────
    gfd = comparison.get("global_flag_diffs", {})
    if gfd:
        lines.append("\n── Global Style Flag Differences ──")
        for flag, diff in gfd.items():
            lines.append(
                f"  {flag:<42}  baseline={diff['baseline']}  ai={diff['ai']}"
            )

    # ── Baseline compatibility note ──────────────────────────────────────────
    compat = compatibility_weight(baseline_type, ai_type)
    lines.append(f"\n── Baseline Compatibility Note ──")
    if compat >= 0.85:
        lines.append(
            f"  Baseline and AI project types are closely compatible "
            f"({int(compat*100)}%).  Gap penalties are not modulated."
        )
    elif compat >= 0.60:
        lines.append(
            f"  Partial compatibility ({int(compat*100)}%): some baseline "
            f"families are project-type-specific and their absence carries "
            f"reduced scoring penalty (PROJECT_TYPE_MISMATCH)."
        )
    else:
        lines.append(
            f"  LOW compatibility ({int(compat*100)}%): baseline is a "
            f"DIFFERENT PROJECT TYPE ({baseline_type} vs {ai_type}).  "
            f"Many gap penalties are modulated.  Treat style scores "
            f"conservatively — completeness score is more meaningful here."
        )

    lines.append("\n" + "=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run_alignment(
    reference_boq_path: str | Path,
    ai_boq_path:        str | Path,
    output_dir:         str | Path,
    *,
    fixings_strategy: str | None = None,   # "standalone" | "embedded" | None=auto
    export_style:     str = "commercial",  # "engine" | "commercial" | "estimator"
    write_files:  bool = True,
    verbose:      bool = True,
) -> dict:
    """Run the full baseline alignment pipeline.

    Parameters
    ----------
    export_style:
        Controls which upgrade rules are applied:
        - ``"engine"``      — tag export_class only; no alignment transforms
        - ``"commercial"``  — full alignment rules, no subgroup headers (default)
        - ``"estimator"``   — commercial + subgroup headers + section remaps
                              + estimator-grade display names
    """
    ref_path = Path(reference_boq_path)
    ai_path  = Path(ai_boq_path)
    out_dir  = Path(output_dir)

    if verbose:
        print(f"[alignment] Profiling baseline BOQ: {ref_path.name}")
    baseline_profile = profile_baseline_boq(ref_path)

    if verbose:
        print(f"[alignment] Profiling AI BOQ:       {ai_path.name}")
    ai_profile = profile_ai_boq(ai_path)

    # Project type inference
    baseline_inferred = infer_project_type(baseline_profile)
    ai_inferred       = infer_project_type(ai_profile)
    baseline_type = baseline_inferred["project_type"]
    ai_type       = ai_inferred["project_type"]

    if verbose:
        print(f"[alignment] Baseline type: {baseline_type} "
              f"(conf={baseline_inferred['confidence']:.0%})")
        print(f"[alignment] AI type:       {ai_type} "
              f"(conf={ai_inferred['confidence']:.0%})")

    # Missing schedules detection
    missing_schedules = _detect_missing_schedules(ai_path)
    if verbose and missing_schedules:
        print(f"[alignment] Missing schedules: {sorted(missing_schedules)}")

    if verbose:
        print("[alignment] Comparing profiles …")
    comparison = compare_profiles(
        baseline_profile, ai_profile,
        baseline_type=baseline_type,
        ai_type=ai_type,
        missing_schedules=missing_schedules,
    )

    # Load AI items for upgrade rules
    with open(ai_path, encoding="utf-8") as fh:
        ai_items: list[dict] = json.load(fh)

    context = {
        "baseline_profile":  baseline_profile,
        "ai_profile":        ai_profile,
        "comparison_report": comparison,
        "fixings_strategy":  fixings_strategy,
        "export_style":      export_style,
        "missing_schedules": missing_schedules,
    }

    if verbose:
        print("[alignment] Applying upgrade rules …")
    upgraded_items, upgrade_log = apply_upgrade_rules(ai_items, context)

    if verbose:
        print("[alignment] Scoring …")
    scoring = score_all_sections(
        comparison, baseline_profile, ai_profile,
        baseline_type=baseline_type,
        ai_type=ai_type,
    )

    gap_cats = _categorise_gaps(comparison)

    # ── Assemble full report ─────────────────────────────────────────────────
    report = {
        "meta": {
            "run_timestamp":        datetime.now().isoformat(timespec="seconds"),
            "reference_file":       str(ref_path),
            "ai_boq_file":          str(ai_path),
            "baseline_type":        baseline_type,
            "ai_type":              ai_type,
            "type_compatibility":   round(compatibility_weight(baseline_type, ai_type), 3),
            "missing_schedules":    sorted(missing_schedules),
            "export_style":         export_style,
            "baseline_sections":    len(baseline_profile["sections"]),
            "ai_sections":          len(ai_profile["sections"]),
            "ai_total_items":       ai_profile["total_items"],
            "upgraded_item_count":  len(upgraded_items),
            "upgrade_log_entries":  len(upgrade_log),
        },
        "project_type_inference": {
            "baseline": baseline_inferred,
            "ai":       ai_inferred,
        },
        "baseline_profile": baseline_profile,
        "ai_profile":       ai_profile,
        "comparison":       comparison,
        "scoring":          scoring,
        "gap_categories":   gap_cats,
        "recommended_actions": _build_actions(comparison, scoring, missing_schedules),
        "upgrade_log":      upgrade_log,
    }

    if write_files:
        out_dir.mkdir(parents=True, exist_ok=True)

        report_path    = out_dir / "alignment_report.json"
        upgraded_path  = out_dir / "project_boq_aligned.json"
        scorecard_path = out_dir / "alignment_scorecard.txt"
        gap_path       = out_dir / "alignment_gap_report.txt"

        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        with open(upgraded_path, "w", encoding="utf-8") as fh:
            json.dump(upgraded_items, fh, indent=2, ensure_ascii=False)

        scorecard_text = format_scorecard(scoring)
        scorecard_path.write_text(scorecard_text, encoding="utf-8")

        gap_text = _build_gap_report(
            comparison, scoring, baseline_type, ai_type,
            missing_schedules, gap_cats,
            upgrade_log=upgrade_log,
        )
        gap_path.write_text(gap_text, encoding="utf-8")

        report["output_files"] = {
            "alignment_report_json": str(report_path),
            "upgraded_items_json":   str(upgraded_path),
            "scorecard_txt":         str(scorecard_path),
            "gap_report_txt":        str(gap_path),
        }

        if verbose:
            for p in (report_path, upgraded_path, scorecard_path, gap_path):
                print(f"[alignment] Written: {p.name}")

    if verbose:
        print(format_scorecard(scoring))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the BOQ commercial alignment pipeline."
    )
    parser.add_argument("--reference",         required=True)
    parser.add_argument("--ai-boq",            required=True)
    parser.add_argument("--output-dir",        required=True)
    parser.add_argument("--fixings-strategy",  choices=["standalone", "embedded"],
                        default=None)
    parser.add_argument("--export-style",
                        choices=["engine", "commercial", "estimator"],
                        default="commercial")
    parser.add_argument("--quiet",             action="store_true")
    args = parser.parse_args()

    run_alignment(
        reference_boq_path = args.reference,
        ai_boq_path        = args.ai_boq,
        output_dir         = args.output_dir,
        fixings_strategy   = args.fixings_strategy,
        export_style       = args.export_style,
        verbose            = not args.quiet,
    )


if __name__ == "__main__":
    main()
