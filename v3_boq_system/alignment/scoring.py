"""
scoring.py — Calibrated dual-score commercial alignment model.

Two top-level scores
--------------------
commercial_completeness_score (0.0–1.0)
    Measures whether the AI BOQ covers the families a QS would expect for
    THIS PROJECT TYPE.  Only MISSING_REQUIRED and MISSING_EXPECTED gaps
    penalise this score heavily.  PROJECT_TYPE_MISMATCH and
    UNSUPPORTED_BY_SOURCE gaps apply a reduced penalty.
    Source-support modifier applies: if schedules are missing, gap penalties
    are further reduced for the affected families.

style_alignment_score (0.0–1.0)
    Measures how closely the AI BOQ presentation matches the reference style.
    Covers unit conventions, naming format, packaging conventions.
    PRESENTATION_MISMATCH (nr↔each, lm↔len, m2↔each for sheets) applies a
    light penalty only.  Real UNIT_GAPregisters a full penalty.

overall_commercial_alignment_score
    Weighted composite:  completeness × 0.60 + style × 0.40
    This weighting reflects that getting the quantities right (completeness)
    matters more commercially than matching the reference's presentation style.

Component scores (kept for diagnostics)
----------------------------------------
family_coverage_score       Jaccard, weighted by universality class.
packaging_alignment_score   Row-count ratio vs baseline, discounted by type compat.
source_support_score        Provenance quality (measured/calculated vs placeholder).
unit_alignment_score        Calibrated: PRESENTATION_MISMATCH penalises lightly.
naming_alignment_score      Family overlap as a naming proxy.
"""

from __future__ import annotations
from typing import Any

from .project_type_inferrer import compatibility_weight


# ---------------------------------------------------------------------------
# Gap-class penalty weights for completeness scoring
# ---------------------------------------------------------------------------

_COMPLETENESS_PENALTY: dict[str, float] = {
    "MISSING_REQUIRED":       1.00,   # full penalty
    "MISSING_EXPECTED":       0.60,   # significant but not critical
    "MISSING_OPTIONAL":       0.25,   # light
    "PROJECT_TYPE_MISMATCH":  0.05,   # near-zero: expected to be absent
    "UNSUPPORTED_BY_SOURCE":  0.15,   # source-constrained, not export logic
    "EXTRA_MEANINGFUL":       0.00,   # no penalty for extra AI content
    "EXTRA_NEUTRAL":          0.00,
    "PRESENT":                0.00,
}

# Unit gap penalty weights for style scoring
_STYLE_UNIT_PENALTY: dict[str, float] = {
    "UNIT_GAP":               1.00,
    "PRESENTATION_MISMATCH":  0.20,   # light penalty — same quantity basis
}


# ---------------------------------------------------------------------------
# Section completeness score
# ---------------------------------------------------------------------------

def _score_completeness(
    base_sec: dict | None,
    ai_sec:   dict | None,
    family_gaps: list[dict],
    type_compat: float,
) -> float:
    """Score commercial completeness for one section.

    Applies compatibility weight to reduce penalties when baseline families
    are project-type-specific rather than universally expected.
    """
    if base_sec is None:
        return 0.8 if ai_sec else 0.0   # extra AI section — acceptable

    if ai_sec is None:
        # All baseline families are missing — fully penalised by type compat
        return 0.0 * type_compat

    if not family_gaps:
        return 1.0

    # Compute weighted penalty per gap
    total_penalty = 0.0
    total_weight  = 0.0
    for gap in family_gaps:
        cls = gap.get("classification", "MISSING_OPTIONAL")
        if not gap.get("in_baseline", True):
            continue   # extra AI families don't penalise completeness
        w = _COMPLETENESS_PENALTY.get(cls, 0.25)
        # Modulate by type compat for type-specific gaps
        if cls in ("PROJECT_TYPE_MISMATCH", "MISSING_OPTIONAL"):
            w *= (1.0 - type_compat)   # if types are compatible, penalise more
        total_penalty += w
        total_weight  += 1.0

    if total_weight == 0:
        return 1.0

    penalty_rate = total_penalty / (total_weight + len(base_sec["families"]))
    return max(0.0, 1.0 - penalty_rate)


# ---------------------------------------------------------------------------
# Section style score
# ---------------------------------------------------------------------------

def _score_style(
    base_sec: dict | None,
    ai_sec:   dict | None,
    unit_gaps: list[dict],
    family_gaps: list[dict],
) -> float:
    """Score style alignment for one section."""
    if base_sec is None or ai_sec is None:
        return 0.5   # can't score style if section absent

    # Unit style
    if not unit_gaps and not base_sec.get("units_seen"):
        unit_score = 1.0
    elif not unit_gaps:
        unit_score = 1.0
    else:
        total_units = len(set(base_sec.get("units_seen", {})) |
                          set(ai_sec.get("units_seen", {})))
        if total_units == 0:
            unit_score = 1.0
        else:
            penalty = sum(
                _STYLE_UNIT_PENALTY.get(g["gap_type"], 0.5) for g in unit_gaps
            )
            unit_score = max(0.0, 1.0 - penalty / total_units)

    # Naming alignment (family overlap as proxy)
    base_fams = set(base_sec.get("families", []))
    ai_fams   = set(ai_sec.get("families", []))
    if not base_fams:
        naming_score = 1.0
    else:
        covered = base_fams & ai_fams
        naming_score = len(covered) / len(base_fams)

    # Combine: unit style 0.60, naming 0.40
    return 0.60 * unit_score + 0.40 * naming_score


# ---------------------------------------------------------------------------
# Source support score
# ---------------------------------------------------------------------------

def _score_source_support(ai_sec: dict | None) -> float:
    if ai_sec is None:
        return 0.0
    prov  = ai_sec.get("provenance", {})
    total = ai_sec.get("row_count", 1) or 1
    good  = prov.get("measured", 0) + prov.get("calculated", 0)
    inferred = prov.get("inferred", 0)
    effective = good + 0.5 * inferred
    return max(0.0, min(1.0, effective / total))


# ---------------------------------------------------------------------------
# Packaging alignment score
# ---------------------------------------------------------------------------

def _score_packaging(
    base_sec: dict | None,
    ai_sec:   dict | None,
    type_compat: float,
) -> float:
    if base_sec is None:
        return 0.8 if ai_sec else 0.0
    if ai_sec is None:
        return 0.0
    base_rows = base_sec.get("row_count", 0)
    ai_rows   = ai_sec.get("row_count",  0)
    if base_rows == 0:
        return 0.9

    # Row-count ratio, discounted when types differ (different expected density)
    ratio = min(ai_rows / base_rows, 1.0)
    base_score = 0.5 + 0.5 * ratio
    # Reduce gap penalty when types are incompatible
    if type_compat < 1.0:
        base_score = base_score + (1.0 - base_score) * (1.0 - type_compat)
    return round(min(1.0, base_score), 3)


# ---------------------------------------------------------------------------
# Full section score
# ---------------------------------------------------------------------------

def score_section(
    code: str,
    base_sec: dict | None,
    ai_sec:   dict | None,
    section_result: dict | None = None,
    type_compat: float = 1.0,
) -> dict:
    """Return a full score dict for one section.

    Parameters
    ----------
    section_result:
        The SectionResult from section_comparator._compare_section().
        Provides pre-classified family_gaps and unit_gaps.
    type_compat:
        Compatibility weight between baseline and AI project types (0.0–1.0).
    """
    family_gaps = section_result.get("family_gaps", []) if section_result else []
    unit_gaps   = section_result.get("unit_gaps",   []) if section_result else []

    completeness = _score_completeness(base_sec, ai_sec, family_gaps, type_compat)
    style        = _score_style(base_sec, ai_sec, unit_gaps, family_gaps)
    source       = _score_source_support(ai_sec)
    packaging    = _score_packaging(base_sec, ai_sec, type_compat)

    # Naming alignment (kept as diagnostic)
    base_fams = set(base_sec.get("families", [])) if base_sec else set()
    ai_fams   = set(ai_sec.get("families", []))   if ai_sec   else set()
    naming = len(base_fams & ai_fams) / len(base_fams) if base_fams else 1.0

    # Overall = completeness × 0.60 + style × 0.40
    overall = 0.60 * completeness + 0.40 * style

    # Apply source-support modifier to overall (low support → clamp boost)
    # If section is almost entirely unsupported, cap the overall at 0.70 to
    # reflect genuine uncertainty even if export logic is otherwise good.
    if source < 0.30 and completeness > 0.70:
        overall = min(overall, 0.70)

    gap_summary = section_result.get("gap_summary", {}) if section_result else {}

    return {
        "section_code":                      code,
        # Primary scores
        "commercial_completeness_score":     round(completeness, 3),
        "style_alignment_score":             round(style,        3),
        "overall_commercial_alignment_score": round(overall,     3),
        # Component diagnostics
        "packaging_alignment_score":         round(packaging,    3),
        "source_support_score":              round(source,       3),
        "naming_alignment_score":            round(naming,       3),
        "type_compatibility_weight":         round(type_compat,  3),
        # Gap diagnostics
        "gap_summary":                       gap_summary,
    }


# ---------------------------------------------------------------------------
# Score all sections
# ---------------------------------------------------------------------------

def score_all_sections(
    comparison_report: dict,
    baseline_profile: dict,
    ai_profile: dict,
    *,
    baseline_type: str = "UNKNOWN",
    ai_type:       str = "UNKNOWN",
) -> dict[str, Any]:
    """Score every section and return a full scoring result.

    Returns
    -------
    {
      "section_scores": {code: score_dict},
      "overall_completeness_score": float,
      "overall_style_score": float,
      "overall_project_score": float,
      "grade": str,
      "type_compatibility": float,
      "priority_sections": list[str],
      "grade_narrative": str,
    }
    """
    base_secs = baseline_profile.get("sections", {})
    ai_secs   = ai_profile.get("sections", {})
    res_map   = comparison_report.get("section_results", {})

    compat = compatibility_weight(baseline_type, ai_type)
    all_codes = sorted(set(base_secs) | set(ai_secs))

    section_scores: dict[str, dict] = {}
    for code in all_codes:
        section_scores[code] = score_section(
            code,
            base_secs.get(code),
            ai_secs.get(code),
            section_result=res_map.get(code),
            type_compat=compat,
        )

    n = len(section_scores) or 1
    overall_completeness = sum(
        s["commercial_completeness_score"] for s in section_scores.values()
    ) / n
    overall_style = sum(
        s["style_alignment_score"] for s in section_scores.values()
    ) / n
    overall = 0.60 * overall_completeness + 0.40 * overall_style

    grade = "F"
    if overall >= 0.85:  grade = "A"
    elif overall >= 0.72: grade = "B"
    elif overall >= 0.58: grade = "C"
    elif overall >= 0.42: grade = "D"

    priority = sorted(
        section_scores,
        key=lambda c: section_scores[c]["overall_commercial_alignment_score"],
    )

    # Grade narrative
    compat_pct = int(compat * 100)
    narrative_parts = [
        f"Overall {overall:.1%} (Grade {grade}).",
        f"Baseline type: {baseline_type}, AI type: {ai_type}, "
        f"compatibility: {compat_pct}%.",
        f"Completeness {overall_completeness:.1%}, style {overall_style:.1%}.",
    ]
    if compat < 0.70:
        narrative_parts.append(
            "Low baseline compatibility: many gap penalties are "
            "project-type differences, not true export-logic gaps."
        )

    return {
        "section_scores":            section_scores,
        "overall_completeness_score": round(overall_completeness, 3),
        "overall_style_score":        round(overall_style,        3),
        "overall_project_score":      round(overall,              3),
        "grade":                      grade,
        "type_compatibility":         round(compat,               3),
        "priority_sections":          priority,
        "grade_narrative":            "  ".join(narrative_parts),
    }


# ---------------------------------------------------------------------------
# Scorecard formatter
# ---------------------------------------------------------------------------

def format_scorecard(scoring_result: dict) -> str:
    lines = [
        "=" * 72,
        "  COMMERCIAL ALIGNMENT SCORECARD  (calibrated dual-score model)",
        f"  Overall:      {scoring_result['overall_project_score']:.1%}  "
        f"(Grade {scoring_result['grade']})",
        f"  Completeness: {scoring_result['overall_completeness_score']:.1%}  "
        f"Style: {scoring_result['overall_style_score']:.1%}  "
        f"Type compat: {scoring_result['type_compatibility']:.0%}",
        f"  {scoring_result.get('grade_narrative', '')}",
        "=" * 72,
        f"  {'CODE':<8} {'COMPLETE':>9} {'STYLE':>7} {'TOTAL':>7} "
        f"{'PKG':>6} {'SRC':>6}  Gap summary",
        "-" * 72,
    ]
    for code, s in sorted(scoring_result["section_scores"].items()):
        gs = s.get("gap_summary", {})
        req = gs.get("required_missing", 0)
        exp = gs.get("expected_missing", 0)
        sty = gs.get("presentation_mismatches", 0)
        uns = gs.get("unsupported_missing", 0)
        gap_str = ""
        if req: gap_str += f" req={req}"
        if exp: gap_str += f" exp={exp}"
        if sty: gap_str += f" pres={sty}"
        if uns: gap_str += f" unsup={uns}"

        lines.append(
            f"  {code:<8} "
            f"{s['commercial_completeness_score']:>9.2f} "
            f"{s['style_alignment_score']:>7.2f} "
            f"{s['overall_commercial_alignment_score']:>7.2f} "
            f"{s['packaging_alignment_score']:>6.2f} "
            f"{s['source_support_score']:>6.2f} "
            f"{gap_str}"
        )
    lines += [
        "-" * 72,
        "  Priority sections (lowest overall first):",
        "  " + ", ".join(scoring_result["priority_sections"]),
        "=" * 72,
    ]
    return "\n".join(lines)
