"""
utils.py — Shared helpers: logging, JSON I/O, text normalisation, numeric extraction.
"""

from __future__ import annotations
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import OUTPUT_LOGS


def setup_logging(project_name: str, debug: bool = False) -> logging.Logger:
    """Configure root logger writing to console + log file."""
    log_file = OUTPUT_LOGS / f"{project_name}_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("boq")


def save_json(data: Any, path: Path, indent: int = 2) -> None:
    """Write *data* to *path* as UTF-8 JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, default=str, ensure_ascii=False)


def load_json(path: Path) -> Any:
    """Load JSON from *path*, return {} on failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def normalise_text(s: str | None) -> str:
    """Lowercase, strip, collapse whitespace."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def extract_number(s: Any) -> float | None:
    """Pull first numeric value from a cell / string."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"[-+]?\d*\.?\d+", str(s))
    return float(m.group()) if m else None


def safe_float(v: Any, default: float = 0.0) -> float:
    """Convert to float, return *default* on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def keyword_match(text: str, keywords: list[str]) -> bool:
    """Return True if any keyword appears in *text* (case-insensitive)."""
    t = normalise_text(text)
    return any(kw in t for kw in keywords)


def mm_to_m(mm: float | None) -> float | None:
    return mm / 1000.0 if mm is not None else None


def area_from_length_height(length_m: float, height_m: float) -> float:
    return round(length_m * height_m, 3)


def ceiling_from_floor(floor_area: float, verandah_area: float = 0.0) -> float:
    return max(0.0, round(floor_area - verandah_area, 3))


def timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slug(s: str) -> str:
    """Convert string to filesystem-safe slug."""
    return re.sub(r"[^\w\-]", "_", s).strip("_")
