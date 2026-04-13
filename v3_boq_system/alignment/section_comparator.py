"""
section_comparator.py — Compares a baseline (estimator) profile against the
AI BOQ profile and produces a structured gap/alignment report.

Section-level status codes
--------------------------
GOOD                Both sections present, families covered, style close.
PARTIAL             Section present; real family gaps exist.
MISSING             Section in baseline but absent from AI BOQ.
EXTRA               Section in AI BOQ but absent from baseline.
EMPTY_BASELINE      Section exists in baseline but has zero rows (placeholder).
UNSUPPORTED_BY_SOURCE
                    Section present in AI but schedule data is missing —
                    richness gaps are source-constrained, not export-logic gaps.
STYLE_MISMATCH      Section present, families matched; unit/naming conventions differ.

Per-family gap classification
-----------------------------
MISSING_REQUIRED        Family universally expected; absence is a real export gap.
MISSING_EXPECTED        Family generally expected; absence is notable.
MISSING_OPTIONAL        Family absent but may be project-type specific.
PROJECT_TYPE_MISMATCH   Family exists in baseline only because of project-type
                        difference; its absence in AI BOQ is expected.
STYLE_ONLY              Family present but unit/naming convention differs.
UNSUPPORTED_BY_SOURCE   Family absent because schedule data was not available.

Unit gap classification
-----------------------
PRESENTATION_MISMATCH   Units are semantically equivalent (nr/each, lm/len,
                        m2/each for sheets) — quantity basis unchanged.
UNIT_GAP                Units are genuinely different (different measurement basis).
"""

from __future__ import annotations
from collections import defaultdict
from typing import Any

from .family_classifier import FAMILY_TO_GROUP
from .project_type_inferrer import (
    classify_family_universality,
    UNIVERSALLY_REQUIRED,
    GENERALLY_EXPECTED,
)


# ---------------------------------------------------------------------------
# Unit equivalence — pairs that are PRESENTATION_MISMATCH not UNIT_GAP
# ---------------------------------------------------------------------------

# Each pair: (unit_a, unit_b) — order-independent
_PRESENTATION_EQUIVALENT_PAIRS: list[frozenset] = [
    frozenset({"nr", "each"}),       # semantic rename only
    frozenset({"lm", "len"}),        # linear metre vs stock lengths
    frozenset({"lm", "m"}),          # variant spellings
    frozenset({"m2", "each"}),       # area vs sheet count (FC sheets, cladding)
    frozenset({"m2", "sheets"}),     # area vs sheet count
    frozenset({"nr", "pcs"}),        # pieces
    frozenset({"each", "pcs"}),
    frozenset({"roll", "rolls"}),
    frozenset({"bag", "bags"}),
    frozenset({"pair", "pairs"}),
    frozenset({"set", "sets"}),
    frozenset({"len", "lengths"}),
    frozenset({"m", "meter"}),
    frozenset({"m", "metre"}),
    frozenset({"meters", "lm"}),     # cable in 'm' vs structural lm
]

# Schedules that, when absent, explain missing richness in related families
_SCHEDULE_FAMILY_MAP: dict[str, set[str]] = {
    "door_schedule":          {"door_hinge", "door_lockset", "door_stop",
                               "door_closer", "door_frame", "door_flashing"},
    "window_schedule":        {"window_flashing", "louvre_blade", "fly_screen",
                               "window_security", "glazing"},
    "room_finish_schedule":   {"floor_finish", "wet_area_lining",
                               "floor_tile_adhesive", "floor_tile_grout",
                               "wet_area_waterproofing", "painting"},
    "services_schedule":      {"hydraulic_fixture", "tapware", "pex_pipe",
                               "pex_fitting", "light_fitting", "ceiling_fan",
                               "exhaust_fan", "smoke_detector", "gpo_switch",
                               "switchboard", "cable", "conduit",
                               "hydraulic_allowance", "electrical_allowance"},
}


def _is_presentation_mismatch(unit_a: str, unit_b: str) -> bool:
    key = frozenset({unit_a.lower(), unit_b.lower()})
    return key in _PRESENTATION_EQUIVALENT_PAIRS


def _classify_unit_gaps(
    base_units: dict[str, int],
    ai_units: dict[str, int],
) -> list[dict]:
    """Return list of unit gap dicts with classification."""
    gaps = []
    all_units = set(base_units) | set(ai_units)
    for u in all_units:
        in_base = u in base_units
        in_ai   = u in ai_units
        if in_base == in_ai:
            continue   # no gap
        # Find the counterpart unit in the other set
        counterpart = None
        for other_u in (ai_units if in_base else base_units):
            if _is_presentation_mismatch(u, other_u):
                counterpart = other_u
                break
        gap_type = "PRESENTATION_MISMATCH" if counterpart else "UNIT_GAP"
        gaps.append({
            "unit":           u,
            "in_baseline":    in_base,
            "in_ai":          in_ai,
            "gap_type":       gap_type,
            "counterpart":    counterpart,
            "note": (
                f"Baseline uses '{u}'; AI uses '{counterpart or '?'}'. "
                f"Classification: {gap_type}."
            ) if in_base else (
                f"AI uses '{u}'; baseline uses '{counterpart or '?'}'. "
                f"Classification: {gap_type}."
            ),
        })
    return gaps


def _classify_family_gap(
    family: str,
    in_baseline: bool,
    in_ai: bool,
    baseline_type: str,
    ai_type: str,
    missing_schedules: set[str],
) -> str:
    """Return the gap classification for one family."""
    if in_baseline and in_ai:
        return "PRESENT"   # no gap

    if in_baseline and not in_ai:
        # Check if a missing schedule explains this
        for sched, families in _SCHEDULE_FAMILY_MAP.items():
            if sched in missing_schedules and family in families:
                return "UNSUPPORTED_BY_SOURCE"

        universality = classify_family_universality(family, baseline_type, ai_type)
        if universality == "REQUIRED_UNIVERSAL":
            return "MISSING_REQUIRED"
        if universality == "EXPECTED_GENERAL":
            return "MISSING_EXPECTED"
        if universality == "BASELINE_TYPE_SPECIFIC":
            return "PROJECT_TYPE_MISMATCH"
        return "MISSING_OPTIONAL"

    if not in_baseline and in_ai:
        universality = classify_family_universality(family, ai_type, baseline_type)
        if universality in ("REQUIRED_UNIVERSAL", "EXPECTED_GENERAL",
                            "AI_TYPE_SPECIFIC"):
            return "EXTRA_MEANINGFUL"
        return "EXTRA_NEUTRAL"

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-section comparison
# ---------------------------------------------------------------------------

SectionResult = dict[str, Any]


def _compare_section(
    code: str,
    base: dict | None,
    ai: dict | None,
    baseline_type: str = "UNKNOWN",
    ai_type:       str = "UNKNOWN",
    missing_schedules: set[str] | None = None,
) -> SectionResult:
    if missing_schedules is None:
        missing_schedules = set()

    # Both absent
    if base is None and ai is None:
        return {"code": code, "status": "MISSING", "details": {}}

    # Present in AI but not baseline
    if base is None:
        return {
            "code": code,
            "status": "EXTRA",
            "ai_label": ai["label"],
            "ai_row_count": ai["row_count"],
            "family_gaps": [],
            "unit_gaps": [],
            "details": {
                "note": "Section exists in AI BOQ but not in reference baseline."
            },
        }

    # Baseline placeholder (zero rows)
    if base["row_count"] == 0:
        return {
            "code": code,
            "status": "EMPTY_BASELINE",
            "base_label": base["label"],
            "ai_row_count": ai["row_count"] if ai else 0,
            "family_gaps": [],
            "unit_gaps": [],
            "details": {"note": "Baseline section is empty — placeholder only."},
        }

    # Present in baseline but missing from AI
    if ai is None:
        family_gaps = []
        for f in base["families"]:
            cls = _classify_family_gap(
                f, True, False, baseline_type, ai_type, missing_schedules
            )
            family_gaps.append({"family": f, "classification": cls})
        return {
            "code": code,
            "status": "MISSING",
            "base_label": base["label"],
            "base_row_count": base["row_count"],
            "base_families": base["families"],
            "family_gaps": family_gaps,
            "unit_gaps": [],
            "details": {
                "missing_families": base["families"],
                "note": "Section present in baseline but absent from AI BOQ.",
            },
        }

    # Both present — deep comparison
    base_fams = set(base["families"])
    ai_fams   = set(ai["families"])
    all_fams  = base_fams | ai_fams

    family_gaps: list[dict] = []
    required_missing = 0
    expected_missing = 0
    optional_missing = 0
    type_mismatch_missing = 0
    unsupported_missing = 0

    for f in sorted(all_fams):
        in_b = f in base_fams
        in_a = f in ai_fams
        if in_b == in_a:
            continue   # no gap; skip
        cls = _classify_family_gap(
            f, in_b, in_a, baseline_type, ai_type, missing_schedules
        )
        family_gaps.append({
            "family":         f,
            "in_baseline":    in_b,
            "in_ai":          in_a,
            "classification": cls,
        })
        if cls == "MISSING_REQUIRED":
            required_missing += 1
        elif cls == "MISSING_EXPECTED":
            expected_missing += 1
        elif cls == "MISSING_OPTIONAL":
            optional_missing += 1
        elif cls == "PROJECT_TYPE_MISMATCH":
            type_mismatch_missing += 1
        elif cls == "UNSUPPORTED_BY_SOURCE":
            unsupported_missing += 1

    unit_gaps = _classify_unit_gaps(base["units_seen"], ai["units_seen"])
    presentation_mismatches = sum(1 for g in unit_gaps
                                  if g["gap_type"] == "PRESENTATION_MISMATCH")
    unit_gaps_real = sum(1 for g in unit_gaps if g["gap_type"] == "UNIT_GAP")

    # Unsupported richness check
    prov = ai.get("provenance", {})
    total = ai["row_count"] or 1
    ph_rate = (prov.get("placeholder", 0) + ai.get("manual_review_count", 0)) / total

    # Determine status
    if ph_rate > 0.5 and (unsupported_missing + required_missing) == 0:
        status = "UNSUPPORTED_BY_SOURCE"
    elif required_missing > 0:
        status = "PARTIAL"
    elif expected_missing > 0:
        status = "PARTIAL"
    elif optional_missing > 0 and type_mismatch_missing == 0:
        status = "PARTIAL"
    elif presentation_mismatches > 0 and unit_gaps_real == 0 and required_missing == 0:
        status = "STYLE_MISMATCH"
    elif unit_gaps_real > 0 or presentation_mismatches > 0:
        status = "STYLE_MISMATCH" if required_missing == 0 else "PARTIAL"
    else:
        status = "GOOD"

    # Upgrade to GOOD if only project-type mismatches remain
    if (required_missing == 0 and expected_missing == 0 and
            optional_missing == 0 and unit_gaps_real == 0 and
            presentation_mismatches <= 2):
        status = "GOOD"

    # Style flag diffs
    base_flags = base.get("style_flags", {})
    ai_flags   = ai.get("style_flags", {})
    flag_diffs = {
        k: {"baseline": base_flags.get(k), "ai": ai_flags.get(k)}
        for k in set(base_flags) | set(ai_flags)
        if base_flags.get(k) != ai_flags.get(k)
    }

    return {
        "code": code,
        "status": status,
        "base_label": base["label"],
        "ai_label":   ai["label"],
        "base_row_count": base["row_count"],
        "ai_row_count":   ai["row_count"],
        "family_gaps": family_gaps,
        "unit_gaps": unit_gaps,
        "gap_summary": {
            "required_missing":       required_missing,
            "expected_missing":       expected_missing,
            "optional_missing":       optional_missing,
            "type_mismatch_missing":  type_mismatch_missing,
            "unsupported_missing":    unsupported_missing,
            "presentation_mismatches": presentation_mismatches,
            "real_unit_gaps":         unit_gaps_real,
        },
        "style_flag_diffs": flag_diffs,
        "unsupported_richness_rate": round(ph_rate, 3),
        "provenance": prov,
    }


# ---------------------------------------------------------------------------
# Full comparison
# ---------------------------------------------------------------------------

def compare_profiles(
    baseline_profile: dict,
    ai_profile: dict,
    *,
    baseline_type: str = "UNKNOWN",
    ai_type:       str = "UNKNOWN",
    missing_schedules: set[str] | None = None,
) -> dict:
    """Compare profiles and return a full gap report.

    Parameters
    ----------
    baseline_type, ai_type:
        Inferred project types from project_type_inferrer.infer_project_type().
    missing_schedules:
        Set of schedule names absent from source documents (e.g.
        {"door_schedule", "window_schedule"}).  Used to classify family gaps
        as UNSUPPORTED_BY_SOURCE rather than real export gaps.
    """
    base_secs = baseline_profile.get("sections", {})
    ai_secs   = ai_profile.get("sections", {})
    if missing_schedules is None:
        missing_schedules = set()

    all_codes = sorted(set(base_secs) | set(ai_secs))

    section_results: dict[str, SectionResult] = {}
    for code in all_codes:
        section_results[code] = _compare_section(
            code,
            base_secs.get(code),
            ai_secs.get(code),
            baseline_type=baseline_type,
            ai_type=ai_type,
            missing_schedules=missing_schedules,
        )

    # Summary buckets
    summary: dict[str, list[str]] = defaultdict(list)
    for code, res in section_results.items():
        status = res["status"].lower().replace(" ", "_")
        summary[status].append(code)

    # Aggregate gap lists across all sections
    all_required_gaps: list[dict] = []
    all_style_gaps:    list[dict] = []
    all_unsupported:   list[dict] = []

    for code, res in section_results.items():
        label = res.get("base_label") or res.get("ai_label", "")
        for fg in res.get("family_gaps", []):
            item = {"code": code, "label": label, **fg}
            cls = fg.get("classification", "")
            if cls in ("MISSING_REQUIRED", "MISSING_EXPECTED"):
                all_required_gaps.append(item)
            elif cls == "UNSUPPORTED_BY_SOURCE":
                all_unsupported.append(item)
        for ug in res.get("unit_gaps", []):
            if ug["gap_type"] in ("PRESENTATION_MISMATCH", "UNIT_GAP"):
                all_style_gaps.append({"code": code, "label": label, **ug})

    # Global flag diffs
    base_gf = baseline_profile.get("global_flags", {})
    ai_gf   = ai_profile.get("global_flags", {})
    global_flag_diffs = {
        k: {"baseline": base_gf.get(k), "ai": ai_gf.get(k)}
        for k in set(base_gf) | set(ai_gf)
        if base_gf.get(k) != ai_gf.get(k)
    }

    # Priority: MISSING → PARTIAL → STYLE_MISMATCH → UNSUPPORTED
    top_priorities = (
        summary.get("missing", []) +
        summary.get("partial", []) +
        summary.get("style_mismatch", []) +
        summary.get("unsupported_by_source", [])
    )

    return {
        "section_results":    section_results,
        "summary":            dict(summary),
        "global_flag_diffs":  global_flag_diffs,
        "top_priorities":     top_priorities,
        "required_gaps":      all_required_gaps[:20],
        "style_gaps":         all_style_gaps[:20],
        "unsupported_gaps":   all_unsupported[:20],
        "baseline_type":      baseline_type,
        "ai_type":            ai_type,
        "missing_schedules":  list(missing_schedules),
    }
