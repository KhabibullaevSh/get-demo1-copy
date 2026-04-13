"""
pdf_targeted_ocr.py — Run OCR on targeted schedule/annotation region crops.

Applies OCR only to the specific cropped regions identified by pdf_region_detector
(not to full documents). This minimises compute while maximising the chance of
recovering text from graphical PDF annotations.

Supported OCR backends (tried in order):
  1. pytesseract  (Tesseract-OCR via Python wrapper)
  2. easyocr      (neural-network-based, handles more varied fonts/layouts)

For each crop:
  - Runs OCR on the full crop
  - Also runs OCR with table-mode PSM (--psm 6) for schedule tables
  - Attempts to parse column structure: mark | width | height | type
  - Returns raw OCR text AND any parsed structured fields

Safe promotion policy: only return fields where the OCR result is explicit and
unambiguous. Low-confidence or partially-readable results are documented but NOT
used to override existing data.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("boq.v3.pdf_targeted_ocr")

# Minimum OCR confidence (0–100) for a word to be kept
_MIN_WORD_CONF = 50

# Patterns for schedule field recovery
_MARK_PAT   = re.compile(r'^[A-Z]\d{1,3}[A-Za-z]?$')
_DIM_PAT    = re.compile(r'\d{3,4}\s*[xX×hH]\s*\d{3,4}')
_WID_PAT    = re.compile(r'\b(\d{3,4})\b')


def run_targeted_ocr(crops: list[dict]) -> dict:
    """
    Run OCR on a list of cropped region images.

    Args:
        crops:  List of crop dicts from pdf_region_detector, each with "image_bytes".

    Returns:
    {
      "ocr_backend":  str   — "pytesseract" | "easyocr" | "unavailable"
      "crops":        list[dict]  — per-crop OCR results
      "recovered":    dict  — structured fields recovered across all crops
      "notes":        list[str]
    }
    """
    result: dict = {
        "ocr_backend":  "unavailable",
        "crops":        [],
        "recovered":    {
            "window_schedule_rows":  [],
            "door_schedule_rows":    [],
            "finish_schedule_rows":  [],
            "raw_text_blocks":       [],
        },
        "notes":        [],
    }

    if not crops:
        result["notes"].append("No crops provided — OCR skipped.")
        return result

    backend = _detect_ocr_backend()
    result["ocr_backend"] = backend

    if backend == "unavailable":
        result["notes"].append(
            "No OCR backend available (pytesseract or easyocr). "
            "Install pytesseract with Tesseract-OCR, or install easyocr, "
            "to enable OCR recovery from graphical PDFs. "
            "Skipping OCR — schedule regions were detected but not read."
        )
        # Document what was detected (without OCR result)
        for i, crop in enumerate(crops):
            result["crops"].append({
                "crop_index":  i,
                "page_num":    crop.get("page_num"),
                "region_type": crop.get("region_type"),
                "confidence":  crop.get("confidence"),
                "ocr_text":    None,
                "ocr_status":  "skipped_no_backend",
                "parsed_rows": [],
            })
        return result

    # ── Run OCR on each crop ──────────────────────────────────────────────────
    for i, crop in enumerate(crops):
        image_bytes = crop.get("image_bytes")
        if not image_bytes:
            result["crops"].append({
                "crop_index":  i,
                "page_num":    crop.get("page_num"),
                "region_type": crop.get("region_type"),
                "confidence":  crop.get("confidence"),
                "ocr_text":    None,
                "ocr_status":  "no_image_bytes",
                "parsed_rows": [],
            })
            continue

        ocr_text, ocr_words, status = _run_ocr(image_bytes, backend)

        crop_result: dict = {
            "crop_index":  i,
            "page_num":    crop.get("page_num"),
            "page_label":  crop.get("page_label"),
            "region_type": crop.get("region_type"),
            "confidence":  crop.get("confidence"),
            "ocr_text":    ocr_text,
            "ocr_status":  status,
            "parsed_rows": [],
        }

        if ocr_text:
            parsed = _parse_schedule_text(ocr_text)
            crop_result["parsed_rows"] = parsed

            # Accumulate into recovered fields
            for row in parsed:
                if row.get("schedule_type") == "window":
                    result["recovered"]["window_schedule_rows"].append(row)
                elif row.get("schedule_type") == "door":
                    result["recovered"]["door_schedule_rows"].append(row)
                elif row.get("schedule_type") == "finish":
                    result["recovered"]["finish_schedule_rows"].append(row)

            if ocr_text.strip():
                result["recovered"]["raw_text_blocks"].append({
                    "page":      crop.get("page_num"),
                    "region":    crop.get("region_type"),
                    "text_excerpt": ocr_text[:300],
                })

        result["crops"].append(crop_result)
        log.info(
            "OCR crop %d (page=%s type=%s): %d chars, %d parsed rows",
            i, crop.get("page_num"), crop.get("region_type"),
            len(ocr_text or ""), len(crop_result["parsed_rows"]),
        )

    n_win = len(result["recovered"]["window_schedule_rows"])
    n_door = len(result["recovered"]["door_schedule_rows"])
    log.info(
        "OCR complete: backend=%s | %d crops | win_rows=%d | door_rows=%d",
        backend, len(crops), n_win, n_door,
    )
    result["notes"].append(
        f"OCR backend: {backend}. Processed {len(crops)} crops. "
        f"Window schedule rows recovered: {n_win}. "
        f"Door schedule rows recovered: {n_door}."
    )
    return result


# ── Backend detection ─────────────────────────────────────────────────────────

_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\User\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
]


def _configure_tesseract() -> bool:
    """
    Try to locate the Tesseract binary and configure pytesseract.
    Returns True if Tesseract is available.
    """
    import pytesseract
    import shutil

    # 1. Already on PATH?
    if shutil.which("tesseract"):
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            pass

    # 2. Try known install locations
    from pathlib import Path
    for p in _TESSERACT_PATHS:
        if Path(p).exists():
            pytesseract.pytesseract.tesseract_cmd = p
            try:
                pytesseract.get_tesseract_version()
                log.info("Tesseract found at: %s", p)
                return True
            except Exception:
                pass

    return False


def _detect_ocr_backend() -> str:
    """Return the best available OCR backend name."""
    try:
        import pytesseract  # noqa: F401
        if _configure_tesseract():
            return "pytesseract"
    except ImportError:
        pass

    try:
        import easyocr  # noqa: F401
        return "easyocr"
    except ImportError:
        pass

    return "unavailable"


def _run_ocr(
    image_bytes: bytes,
    backend: str,
) -> tuple[str, list[dict], str]:
    """
    Run OCR on *image_bytes* using the specified backend.

    Returns: (full_text, word_list, status)
    word_list: [{text, confidence, x, y, w, h}]
    """
    if backend == "pytesseract":
        return _run_tesseract(image_bytes)
    elif backend == "easyocr":
        return _run_easyocr(image_bytes)
    return ("", [], "no_backend")


def _run_tesseract(image_bytes: bytes) -> tuple[str, list[dict], str]:
    """Run pytesseract on image bytes."""
    try:
        import pytesseract
        from PIL import Image
        import io

        _configure_tesseract()
        img = Image.open(io.BytesIO(image_bytes))

        # Try table mode first (PSM 6 = uniform block of text)
        config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,/:×xhH-()@ "
        text = pytesseract.image_to_string(img, config=config).strip()

        # Get word-level confidence data
        data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
        words = []
        for j, word in enumerate(data["text"]):
            conf = int(data["conf"][j])
            if word.strip() and conf >= _MIN_WORD_CONF:
                words.append({
                    "text":  word.strip(),
                    "conf":  conf,
                    "x":     data["left"][j],
                    "y":     data["top"][j],
                    "w":     data["width"][j],
                    "h":     data["height"][j],
                })
        return (text, words, "ok")
    except Exception as exc:
        log.warning("Tesseract OCR failed: %s", exc)
        return ("", [], f"error: {exc}")


def _run_easyocr(image_bytes: bytes) -> tuple[str, list[dict], str]:
    """Run easyocr on image bytes (lazy-loaded to avoid startup cost)."""
    try:
        import easyocr
        import numpy as np
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes)).convert("L")  # grayscale
        img_array = np.array(img)

        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        results = reader.readtext(img_array, detail=1)

        text_parts = []
        words = []
        for (bbox_pts, text, conf) in results:
            if conf >= _MIN_WORD_CONF / 100.0 and text.strip():
                text_parts.append(text.strip())
                words.append({"text": text.strip(), "conf": int(conf * 100)})

        return (" ".join(text_parts), words, "ok")
    except Exception as exc:
        log.warning("EasyOCR failed: %s", exc)
        return ("", [], f"error: {exc}")


# ── Schedule text parser ──────────────────────────────────────────────────────

def _parse_schedule_text(text: str) -> list[dict]:
    """
    Attempt to parse raw OCR text as a schedule table.

    Looks for rows matching:
      - Opening schedule: MARK | WIDTH | HEIGHT | TYPE
      - Finish schedule: ROOM | FLOOR | WALL | CEILING

    Returns a list of parsed row dicts.
    """
    rows: list[dict] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # ── Schedule header detection ─────────────────────────────────────────────
    header_line_idx = -1
    schedule_type = "unknown"
    for i, line in enumerate(lines):
        lu = line.upper()
        if "WINDOW" in lu and any(kw in lu for kw in ("SCHEDULE", "TYPE", "HEIGHT", "WIDTH")):
            schedule_type = "window"
            header_line_idx = i
            break
        if "DOOR" in lu and any(kw in lu for kw in ("SCHEDULE", "TYPE")):
            schedule_type = "door"
            header_line_idx = i
            break
        if "FINISH" in lu or "FLOOR FINISH" in lu or "WALL FINISH" in lu:
            schedule_type = "finish"
            header_line_idx = i
            break

    # Parse data rows (lines after header)
    start = header_line_idx + 1 if header_line_idx >= 0 else 0
    for line in lines[start:]:
        parsed = _parse_schedule_row(line, schedule_type)
        if parsed:
            rows.append(parsed)

    # If no header found, try to parse any line with a mark + dimension
    if not rows:
        for line in lines:
            parsed = _parse_schedule_row(line, "unknown")
            if parsed:
                rows.append(parsed)

    return rows


def _parse_schedule_row(line: str, schedule_type: str) -> dict | None:
    """
    Parse a single line as a schedule row.

    Returns a dict with parsed fields, or None if no valid pattern detected.
    """
    # Must have at least one dimension pattern to be worthwhile
    if not _DIM_PAT.search(line) and not _MARK_PAT.match(line.split()[0] if line.split() else ""):
        return None

    row: dict = {
        "schedule_type": schedule_type,
        "raw_text":      line[:120],
        "mark":          None,
        "width_mm":      None,
        "height_mm":     None,
        "type_text":     None,
    }

    tokens = line.split()
    if not tokens:
        return None

    # First token: might be a mark (W1, D2, etc.)
    if _MARK_PAT.match(tokens[0]):
        row["mark"] = tokens[0]
        # Infer schedule type from mark prefix
        if row["schedule_type"] == "unknown":
            if tokens[0].upper().startswith("W"):
                row["schedule_type"] = "window"
            elif tokens[0].upper().startswith("D"):
                row["schedule_type"] = "door"

    # Look for WxH dimension in the line
    m = _DIM_PAT.search(line)
    if m:
        parts = re.split(r'[xX×hH]', m.group())
        try:
            w = int(re.sub(r'\D', '', parts[0]))
            h = int(re.sub(r'\D', '', parts[1]))
            if 100 <= w <= 5000 and 100 <= h <= 3000:
                row["width_mm"]  = w
                row["height_mm"] = h
        except (IndexError, ValueError):
            pass

    # If no WxH but there are dimension numbers, try to pick them
    if row["width_mm"] is None:
        nums = [int(m.group()) for m in re.finditer(r'\b(\d{3,4})\b', line)]
        plausible_h = [n for n in nums if 400 <= n <= 2400]
        if len(plausible_h) >= 2:
            row["width_mm"]  = max(plausible_h)
            row["height_mm"] = min(plausible_h)
        elif len(plausible_h) == 1 and row["schedule_type"] == "window":
            row["height_mm"] = plausible_h[0]

    # Any non-numeric tokens after mark/dims = type description
    non_dim_tokens = [t for t in tokens[1:] if not re.match(r'^[\d×xhH]+$', t)
                      and len(t) > 1 and not t.isdigit()]
    if non_dim_tokens:
        row["type_text"] = " ".join(non_dim_tokens[:4])

    # Only return if we have at least a mark or a dimension
    if row["mark"] or row["width_mm"] or row["height_mm"]:
        return row
    return None
