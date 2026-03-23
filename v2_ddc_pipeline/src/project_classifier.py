"""V2 project_classifier.py — identify G-range model code from filenames / dir names."""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("boq.v2.project_classifier")

# Known G-range model codes
_G_RANGE_CODES = [
    "G201", "G202", "G302", "G303",
    "G403E", "G404", "G504E",
]

_CODE_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _G_RANGE_CODES) + r")\b",
    re.IGNORECASE,
)


def classify_project(input_dir: Path) -> dict:
    """
    Search filenames, directory names, and (cheaply) any .txt file contents
    for G-range codes.

    Returns:
        {
          "project_mode":       "standard_model" | "custom_project",
          "matched_model_code": str | None,
          "confidence":         "HIGH" | "MEDIUM" | "LOW",
          "evidence_summary":   str,
        }
    """
    evidence: list[str] = []
    found_code: str | None = None

    # 1 — directory name
    dir_match = _CODE_RE.search(input_dir.name)
    if dir_match:
        found_code = dir_match.group(1).upper()
        evidence.append(f"directory name: '{input_dir.name}'")

    # 2 — file names
    if input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if not p.is_file():
                continue
            m = _CODE_RE.search(p.name)
            if m:
                code = m.group(1).upper()
                evidence.append(f"filename: '{p.name}'")
                if found_code is None:
                    found_code = code
                elif found_code != code:
                    evidence.append(f"CONFLICT: also found '{code}' in '{p.name}'")

            # 3 — peek at .txt files (cheap, no AI)
            if p.suffix.lower() == ".txt":
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                    tm = _CODE_RE.search(text)
                    if tm:
                        code = tm.group(1).upper()
                        evidence.append(f"text content of '{p.name}'")
                        if found_code is None:
                            found_code = code
                except Exception:
                    pass

    if found_code:
        confidence = "HIGH" if len(evidence) >= 2 else "MEDIUM"
        return {
            "project_mode":       "standard_model",
            "matched_model_code": found_code,
            "confidence":         confidence,
            "evidence_summary":   "; ".join(evidence),
        }

    log.info("No G-range code found — treating as custom_project")
    return {
        "project_mode":       "custom_project",
        "matched_model_code": None,
        "confidence":         "LOW",
        "evidence_summary":   "No G-range model code found in filenames or directory",
    }
