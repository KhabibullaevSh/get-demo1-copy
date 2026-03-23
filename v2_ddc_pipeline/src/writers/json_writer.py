"""json_writer.py — Simple JSON output utilities."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("boq.v2.json_writer")


def save_json(data: dict | list, path: Path, label: str = "") -> Path:
    """
    Write *data* to *path* as formatted JSON.

    Creates parent directories as needed.
    Returns the path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str, ensure_ascii=False)
    tag = f" [{label}]" if label else ""
    log.info("Saved%s → %s", tag, path)
    return path
