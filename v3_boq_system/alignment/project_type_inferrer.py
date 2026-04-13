"""
project_type_inferrer.py — Infers the broad project type from a BOQ profile
and provides compatibility weighting for cross-project baseline comparisons.

Project types
-------------
RESIDENTIAL_LOWSET     Single-storey house, slab or no raised floor.
RESIDENTIAL_HIGHSET    Raised/high-set house with floor cassette system.
COMMERCIAL_SMALL       Small commercial — pharmacy, clinic, retail, office.
COMMERCIAL_MEDIUM      Larger commercial — multi-tenancy, full services.
MIXED_USE              Mix of residential and commercial elements.
UNKNOWN                Cannot determine from available data.

Compatibility matrix
--------------------
Cross-type comparisons get a compatibility score 0.0–1.0:
  same type          → 1.0
  residential variants → 0.85
  commercial variants → 0.85
  residential vs commercial → 0.50
  unknown vs anything → 0.70

This weight modulates the strictness of family coverage scoring:
a low compatibility score means many baseline families are project-type-specific
and their absence in the AI BOQ should not strongly penalise completeness.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


ProjectType = Literal[
    "RESIDENTIAL_LOWSET",
    "RESIDENTIAL_HIGHSET",
    "COMMERCIAL_SMALL",
    "COMMERCIAL_MEDIUM",
    "MIXED_USE",
    "UNKNOWN",
]


# ---------------------------------------------------------------------------
# Signal definitions — each signal contributes evidence towards a project type
# ---------------------------------------------------------------------------

@dataclass
class _Signal:
    name: str
    weight: float   # positive = evidence for, negative = evidence against


# family_name → {project_type: weight}
_FAMILY_SIGNALS: dict[str, dict[str, float]] = {
    # Strong residential signals
    "ffe_laundry":       {"RESIDENTIAL_LOWSET": 2.0, "RESIDENTIAL_HIGHSET": 2.0,
                          "COMMERCIAL_SMALL": -1.0},
    "ffe_shower":        {"RESIDENTIAL_LOWSET": 2.0, "RESIDENTIAL_HIGHSET": 2.0,
                          "COMMERCIAL_SMALL": -0.5},
    "ffe_kitchen":       {"RESIDENTIAL_LOWSET": 2.0, "RESIDENTIAL_HIGHSET": 2.0,
                          "COMMERCIAL_SMALL": -0.5},
    "ceiling_fan":       {"RESIDENTIAL_LOWSET": 1.0, "RESIDENTIAL_HIGHSET": 1.0},

    # Elevated floor signals (highset)
    "floor_cassette":    {"RESIDENTIAL_HIGHSET": 2.0, "RESIDENTIAL_LOWSET": -0.5},
    "support_post":      {"RESIDENTIAL_HIGHSET": 1.5, "RESIDENTIAL_LOWSET": -0.5},
    "joist":             {"RESIDENTIAL_HIGHSET": 1.5},
    "bearer":            {"RESIDENTIAL_HIGHSET": 1.5},

    # Slab signals (lowset)
    "dpm":               {"RESIDENTIAL_LOWSET": 1.0},
    "termite_barrier":   {"RESIDENTIAL_LOWSET": 0.5, "RESIDENTIAL_HIGHSET": 0.5},

    # Stairs (may be either type)
    "stair_stringer":    {"RESIDENTIAL_HIGHSET": 1.0, "COMMERCIAL_SMALL": 0.5},
    "stair_tread":       {"RESIDENTIAL_HIGHSET": 1.0, "COMMERCIAL_SMALL": 0.5},

    # Commercial signals
    "ffe_refrigeration": {"COMMERCIAL_SMALL": 2.0, "COMMERCIAL_MEDIUM": 2.0,
                          "RESIDENTIAL_LOWSET": -1.0, "RESIDENTIAL_HIGHSET": -1.0},
    "hydraulic_allowance": {"COMMERCIAL_SMALL": 1.5, "COMMERCIAL_MEDIUM": 1.5},
    "electrical_allowance": {"COMMERCIAL_SMALL": 1.5, "COMMERCIAL_MEDIUM": 1.5},
    "air_conditioning":  {"COMMERCIAL_SMALL": 1.0, "COMMERCIAL_MEDIUM": 1.5},

    # Mixed / neutral
    "hot_water_system":  {"RESIDENTIAL_LOWSET": 0.5, "RESIDENTIAL_HIGHSET": 0.5,
                          "COMMERCIAL_SMALL": 0.5},
    "hydraulic_fixture": {"RESIDENTIAL_LOWSET": 0.5, "COMMERCIAL_SMALL": 0.5},
    "access_ramp":       {"COMMERCIAL_SMALL": 1.5, "COMMERCIAL_MEDIUM": 1.5},
    "fire_placeholder":  {"COMMERCIAL_MEDIUM": 2.0, "COMMERCIAL_SMALL": 0.5},
    "communications_placeholder": {"COMMERCIAL_MEDIUM": 2.0, "COMMERCIAL_SMALL": 1.0},
}

# Section label keywords → type evidence
_LABEL_SIGNALS: list[tuple[str, str, float]] = [
    # (keyword_lower, project_type, weight)
    ("laundry",        "RESIDENTIAL_LOWSET",   2.0),
    ("laundry",        "RESIDENTIAL_HIGHSET",  2.0),
    ("bedroom",        "RESIDENTIAL_LOWSET",   2.0),
    ("bedroom",        "RESIDENTIAL_HIGHSET",  2.0),
    ("bathroom",       "RESIDENTIAL_LOWSET",   1.5),
    ("pharmacy",       "COMMERCIAL_SMALL",     3.0),
    ("dispensary",     "COMMERCIAL_SMALL",     3.0),
    ("clinic",         "COMMERCIAL_SMALL",     3.0),
    ("retail",         "COMMERCIAL_SMALL",     2.0),
    ("office",         "COMMERCIAL_SMALL",     1.5),
    ("waiting",        "COMMERCIAL_SMALL",     1.5),
    ("consulting",     "COMMERCIAL_SMALL",     1.5),
    ("ground level",   "RESIDENTIAL_LOWSET",   1.0),
    ("high set",       "RESIDENTIAL_HIGHSET",  2.0),
    ("highset",        "RESIDENTIAL_HIGHSET",  2.0),
]

_ALL_TYPES: list[str] = [
    "RESIDENTIAL_LOWSET",
    "RESIDENTIAL_HIGHSET",
    "COMMERCIAL_SMALL",
    "COMMERCIAL_MEDIUM",
]

# Compatibility matrix: (type_a, type_b) → compatibility score
_COMPATIBILITY: dict[frozenset, float] = {
    frozenset({"RESIDENTIAL_LOWSET",  "RESIDENTIAL_LOWSET"}):  1.00,
    frozenset({"RESIDENTIAL_HIGHSET", "RESIDENTIAL_HIGHSET"}): 1.00,
    frozenset({"COMMERCIAL_SMALL",    "COMMERCIAL_SMALL"}):    1.00,
    frozenset({"COMMERCIAL_MEDIUM",   "COMMERCIAL_MEDIUM"}):   1.00,
    frozenset({"MIXED_USE",           "MIXED_USE"}):           1.00,
    frozenset({"RESIDENTIAL_LOWSET",  "RESIDENTIAL_HIGHSET"}): 0.85,
    frozenset({"COMMERCIAL_SMALL",    "COMMERCIAL_MEDIUM"}):   0.85,
    frozenset({"RESIDENTIAL_LOWSET",  "MIXED_USE"}):           0.75,
    frozenset({"RESIDENTIAL_HIGHSET", "MIXED_USE"}):           0.75,
    frozenset({"COMMERCIAL_SMALL",    "MIXED_USE"}):           0.75,
    frozenset({"COMMERCIAL_MEDIUM",   "MIXED_USE"}):           0.75,
    frozenset({"RESIDENTIAL_LOWSET",  "COMMERCIAL_SMALL"}):    0.50,
    frozenset({"RESIDENTIAL_LOWSET",  "COMMERCIAL_MEDIUM"}):   0.40,
    frozenset({"RESIDENTIAL_HIGHSET", "COMMERCIAL_SMALL"}):    0.50,
    frozenset({"RESIDENTIAL_HIGHSET", "COMMERCIAL_MEDIUM"}):   0.40,
}


def _compatibility(type_a: str, type_b: str) -> float:
    if type_a == "UNKNOWN" or type_b == "UNKNOWN":
        return 0.70
    key = frozenset({type_a, type_b})
    return _COMPATIBILITY.get(key, 0.50)


def infer_project_type(profile: dict) -> dict:
    """Infer the project type from a BOQ profile (baseline or AI).

    Returns
    -------
    {
      "project_type":    str,
      "confidence":      float,      # 0.0–1.0
      "scores":          {type: float},
      "signals_fired":   list[str],
      "reasoning":       str,
    }
    """
    scores: dict[str, float] = {t: 0.0 for t in _ALL_TYPES}
    signals_fired: list[str] = []

    # 1. Family signals
    all_families: set[str] = set()
    for sec in profile.get("sections", {}).values():
        all_families.update(sec.get("families", []))

    for family, type_weights in _FAMILY_SIGNALS.items():
        if family in all_families:
            for ptype, w in type_weights.items():
                scores[ptype] = scores.get(ptype, 0.0) + w
                if w > 0:
                    signals_fired.append(f"family:{family}→{ptype}(+{w})")

    # 2. Label signals
    for sec in profile.get("sections", {}).values():
        label_lc = sec.get("label", "").lower()
        for keyword, ptype, w in _LABEL_SIGNALS:
            if keyword in label_lc:
                scores[ptype] = scores.get(ptype, 0.0) + w
                signals_fired.append(f"label:'{keyword}'→{ptype}(+{w})")

    # 3. Structural signals from global flags
    gf = profile.get("global_flags", {})
    if gf.get("ffe_section_present"):
        scores["RESIDENTIAL_LOWSET"] += 0.5
        scores["RESIDENTIAL_HIGHSET"] += 0.5

    # Normalize to 0–1 range
    max_score = max(scores.values()) if scores else 1.0
    if max_score <= 0:
        return {
            "project_type": "UNKNOWN",
            "confidence": 0.0,
            "scores": {t: 0.0 for t in _ALL_TYPES},
            "signals_fired": signals_fired,
            "reasoning": "No signals detected.",
        }

    norm_scores = {t: round(v / max_score, 3) for t, v in scores.items()}

    # Pick winner
    sorted_types = sorted(scores, key=lambda t: scores[t], reverse=True)
    winner = sorted_types[0]
    runner_up = sorted_types[1] if len(sorted_types) > 1 else winner

    # Determine if mixed
    if scores[winner] > 0 and scores[runner_up] / scores[winner] > 0.75:
        if (winner in ("RESIDENTIAL_LOWSET", "RESIDENTIAL_HIGHSET") and
                runner_up in ("COMMERCIAL_SMALL", "COMMERCIAL_MEDIUM")):
            winner = "MIXED_USE"
        elif (runner_up in ("RESIDENTIAL_LOWSET", "RESIDENTIAL_HIGHSET") and
              winner in ("COMMERCIAL_SMALL", "COMMERCIAL_MEDIUM")):
            winner = "MIXED_USE"

    # Confidence = normalised gap between first and second
    top = scores[sorted_types[0]]
    second = scores[sorted_types[1]] if len(sorted_types) > 1 else 0
    gap = top - second
    confidence = min(1.0, gap / max(top, 1.0))

    reasoning = (
        f"Inferred '{winner}' from {len(signals_fired)} signals.  "
        f"Top score={top:.1f}, runner-up={second:.1f}, gap={gap:.1f}."
    )

    return {
        "project_type": winner,
        "confidence": round(confidence, 3),
        "scores": norm_scores,
        "signals_fired": signals_fired[:20],   # trim for readability
        "reasoning": reasoning,
    }


def compatibility_weight(baseline_type: str, ai_type: str) -> float:
    """Return a compatibility weight 0.0–1.0 for scoring modulation."""
    return _compatibility(baseline_type, ai_type)


# ---------------------------------------------------------------------------
# Family universality classification
# ---------------------------------------------------------------------------

# Families universally required for any completed building
UNIVERSALLY_REQUIRED: frozenset[str] = frozenset({
    "wall_frame",
    "roof_cladding",
    "footing_concrete",
    "strip_footing",
    "fascia",
    "gutter",
    "door",
    "window",
    "external_wall_lining",
    "ceiling_lining",
    "internal_wall_lining",
})

# Families that are standard for most buildings but may be absent in some types
GENERALLY_EXPECTED: frozenset[str] = frozenset({
    "insulation_batts",
    "floor_finish",
    "painting",
    "roof_batten",
    "weatherboard",
    "screw_fixing",
    "bolt_fixing",
    "strap_brace",
    "door_lockset",
    "door_hinge",
    "window_flashing",
    "door_flashing",
    "sisalation",
    "ridge_capping",
    "barge_capping",
})

# Families specific to residential projects
RESIDENTIAL_SPECIFIC: frozenset[str] = frozenset({
    "ffe_kitchen",
    "ffe_laundry",
    "ffe_shower",
    "floor_cassette",   # raised floor
    "joist",
    "bearer",
    "support_post",
    "ceiling_fan",
    "pex_pipe",
    "pex_fitting",
})

# Families specific to commercial projects
COMMERCIAL_SPECIFIC: frozenset[str] = frozenset({
    "ffe_refrigeration",
    "hydraulic_allowance",
    "electrical_allowance",
    "air_conditioning",
    "access_ramp",
    "fire_placeholder",
    "communications_placeholder",
    "mechanical_allowance",
})


def classify_family_universality(
    family: str,
    baseline_type: str,
    ai_type: str,
) -> str:
    """Classify how universally a family is expected given both project types.

    Returns one of:
      REQUIRED_UNIVERSAL    — expected in any project; absence is a real gap
      EXPECTED_GENERAL      — standard in most projects; absence is notable
      BASELINE_TYPE_SPECIFIC — only expected in the baseline project type
      AI_TYPE_SPECIFIC       — present in AI project type but not in baseline
      NEUTRAL               — context-dependent; treat lightly
    """
    if family in UNIVERSALLY_REQUIRED:
        return "REQUIRED_UNIVERSAL"
    if family in GENERALLY_EXPECTED:
        return "EXPECTED_GENERAL"
    # Project-type-specific
    residential_types = {"RESIDENTIAL_LOWSET", "RESIDENTIAL_HIGHSET"}
    commercial_types  = {"COMMERCIAL_SMALL", "COMMERCIAL_MEDIUM"}

    if family in RESIDENTIAL_SPECIFIC:
        if baseline_type in residential_types and ai_type not in residential_types:
            return "BASELINE_TYPE_SPECIFIC"
        if ai_type in residential_types and baseline_type not in residential_types:
            return "AI_TYPE_SPECIFIC"
    if family in COMMERCIAL_SPECIFIC:
        if baseline_type in commercial_types and ai_type not in commercial_types:
            return "BASELINE_TYPE_SPECIFIC"
        if ai_type in commercial_types and baseline_type not in commercial_types:
            return "AI_TYPE_SPECIFIC"

    return "NEUTRAL"
