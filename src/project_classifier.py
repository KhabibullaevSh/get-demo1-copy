"""
project_classifier.py — Classify a project as standard_model or custom_project.

Searches titleblock, filenames, and merged data for known G-Range model codes.
Returns a classification dict consumed by main.py and boq_mapper.py.
"""

from __future__ import annotations
import re
import logging
from pathlib import Path

log = logging.getLogger("boq.classifier")

# Known model codes (must match STANDARD_MODELS_MAP in config.py)
KNOWN_MODEL_CODES = ["G201", "G202", "G302", "G303", "G403E", "G404", "G504E"]

# Regex: word boundary match for model codes
_CODE_RE = re.compile(
    r'\b(' + '|'.join(re.escape(c) for c in KNOWN_MODEL_CODES) + r')\b',
    re.IGNORECASE,
)


def classify_project(files: dict, titleblock: dict, merged: dict) -> dict:
    """
    Classify a project as standard_model or custom_project.

    Returns:
      {
        "project_mode": "standard_model" | "custom_project",
        "matched_model_code": str | None,   # e.g. "G303"
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "reasoning": [str, ...]
      }
    """
    reasoning: list[str] = []
    matches: dict[str, int] = {}  # code -> hit count

    # ── 1. Titleblock (highest weight) ────────────────────────────────────────
    tb_type = str(titleblock.get("house_type_detected") or "").upper().strip()
    tb_conf = str(titleblock.get("house_type_confidence") or "").upper()

    if tb_type in KNOWN_MODEL_CODES:
        matches[tb_type] = matches.get(tb_type, 0) + 3  # weight 3
        reasoning.append(
            f"Titleblock reports house type '{tb_type}' (confidence: {tb_conf})"
        )

    # ── 2. Metadata from merged ───────────────────────────────────────────────
    meta_type = str(merged.get("metadata", {}).get("house_type") or "").upper().strip()
    if meta_type in KNOWN_MODEL_CODES:
        matches[meta_type] = matches.get(meta_type, 0) + 2
        reasoning.append(f"Merged metadata contains house type '{meta_type}'")

    # ── 3. Input filenames ────────────────────────────────────────────────────
    all_file_paths: list[str] = []
    for category, entries in files.items():
        if category == "warnings":
            continue
        for entry in entries:
            all_file_paths.append(str(entry.get("path", "")))

    for fpath in all_file_paths:
        fname = Path(fpath).name
        found = _CODE_RE.findall(fname)
        for code in found:
            code_upper = code.upper()
            matches[code_upper] = matches.get(code_upper, 0) + 1
            reasoning.append(f"Filename '{fname}' contains model code '{code_upper}'")

    # ── 4. Titleblock raw text search ─────────────────────────────────────────
    tb_raw = str(titleblock.get("raw_text") or titleblock.get("project_name") or "")
    if tb_raw:
        found = _CODE_RE.findall(tb_raw)
        for code in found:
            code_upper = code.upper()
            if code_upper not in matches or matches[code_upper] < 2:
                matches[code_upper] = matches.get(code_upper, 0) + 1
                reasoning.append(
                    f"Model code '{code_upper}' found in titleblock text"
                )

    # ── 5. Determine best match ───────────────────────────────────────────────
    if not matches:
        reasoning.append("No model code found in titleblock, filenames, or metadata")
        return {
            "project_mode":       "custom_project",
            "matched_model_code": None,
            "confidence":         "LOW",
            "reasoning":          reasoning,
        }

    best_code  = max(matches, key=lambda c: matches[c])
    best_score = matches[best_code]

    # Confidence thresholds
    if best_score >= 3:
        confidence = "HIGH"
    elif best_score >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    if confidence in ("HIGH", "MEDIUM"):
        project_mode = "standard_model"
        reasoning.append(
            f"Classified as standard_model: '{best_code}' "
            f"(score={best_score}, confidence={confidence})"
        )
    else:
        # LOW confidence → custom_project to avoid wrong template
        project_mode = "custom_project"
        reasoning.append(
            f"LOW confidence match for '{best_code}' (score={best_score}) — "
            "defaulting to custom_project"
        )
        best_code = None

    log.info(
        "Project classification: mode=%s  code=%s  confidence=%s",
        project_mode, best_code, confidence,
    )

    return {
        "project_mode":       project_mode,
        "matched_model_code": best_code,
        "confidence":         confidence,
        "reasoning":          reasoning,
    }
