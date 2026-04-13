"""
unit_aligner.py — Reusable unit presentation alignment.

Rules
-----
1. NEVER invent a quantity.  A conversion only proceeds when the row's
   existing ``quantity`` and ``unit`` can safely produce the target unit via
   a documented rule.
2. Source quantity truth is always preserved in ``quantity_source_value`` /
   ``quantity_source_unit``.
3. If conversion is impossible or unsupported, the item is tagged
   STYLE_MISMATCH and left unchanged.
4. "len" (stock-length unit) conversion: qty_lm / stock_length_m = nr_of_lengths.
   This requires ``stock_length_m`` to be known (from item name or config).
5. "each" ↔ "nr" are semantically equivalent; renaming only, no quantity change.

Conversion graph (source_unit → target_unit → rule_name):
  lm   → len  : lm_to_len   (requires stock_length_m)
  nr   → each : nr_to_each  (rename only)
  each → nr   : each_to_nr  (rename only)
  m2   → each : area_to_sheets (requires sheet dimensions)
"""

from __future__ import annotations
import copy
import math
import re
from typing import Any


# ---------------------------------------------------------------------------
# Stock-length patterns — extract standard stock length from description
# ---------------------------------------------------------------------------

_STOCK_LENGTH_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(?:mm|m)\b",
    re.IGNORECASE,
)

_KNOWN_STOCK_LENGTHS_MM: dict[str, float] = {
    # Battens
    "5800":  5800,
    "6000":  6000,
    "6100":  6100,
    "4800":  4800,
    # FC sheets
    "2400":  2400,
    "2700":  2700,
    # Cladding
    "4200":  4200,
    "4450":  4450,
    "3650":  3650,
    "3800":  3800,
    # Pipe / gutter
    "5800":  5800,
    "3000":  3000,
}

# Sheet dimensions for area→each conversions
_SHEET_AREAS_M2: dict[str, float] = {
    "1200x2400": 1.2 * 2.4,
    "1200x2700": 1.2 * 2.7,
    "1200x3000": 1.2 * 3.0,
}


def _extract_stock_length_mm(description: str) -> float | None:
    """Parse the first dimension in mm from *description* that matches a known
    stock length, or return None if not determinable."""
    norm = description.replace(",", "")
    matches = _STOCK_LENGTH_RE.findall(norm)
    for raw, unit in [m.split(" ") if " " in m else (m, "mm")
                      for m in matches]:
        pass

    # simpler approach: find all numbers followed by mm or m
    pattern = re.compile(r"(\d[\d\.]*)\s*(mm|m)\b", re.IGNORECASE)
    hits = pattern.findall(norm)
    for val_str, unit in hits:
        try:
            val = float(val_str)
            if unit.lower() == "m":
                val *= 1000
            if val in _KNOWN_STOCK_LENGTHS_MM.values():
                return val
            # Accept any reasonable stock length (1000–9000 mm)
            if 1000 <= val <= 9000:
                return val
        except ValueError:
            pass
    return None


def _extract_sheet_area_m2(description: str) -> float | None:
    """Try to parse sheet dimensions like '1200 x 2400 mm' from *description*."""
    norm = description.lower().replace(",", "")
    pattern = re.compile(r"(\d+)\s*[x×]\s*(\d+)")
    hits = pattern.findall(norm)
    for w_str, h_str in hits:
        w, h = int(w_str), int(h_str)
        # Both likely in mm if > 100
        if w > 100 and h > 100:
            return (w / 1000) * (h / 1000)
    return None


# ---------------------------------------------------------------------------
# Core conversion functions
# ---------------------------------------------------------------------------

ConversionResult = dict[str, Any]


def align_unit(
    item: dict,
    target_unit: str,
    *,
    waste_factor: float = 1.0,
) -> ConversionResult:
    """Attempt to convert *item*'s unit to *target_unit*.

    Returns a dict with:
      converted      : bool — whether the conversion succeeded
      new_item       : dict  — copy of item with updated unit/quantity (or unchanged)
      rule_applied   : str   — name of the rule used
      style_status   : "CONVERTED" | "RENAME_ONLY" | "STYLE_MISMATCH" | "NO_CHANGE"
      note           : str
    """
    src_unit = (item.get("unit") or "").strip().lower()
    tgt = target_unit.strip().lower()
    qty = item.get("quantity")
    desc = item.get("item_name", "") or item.get("item_display_name", "")

    # Identical units — nothing to do
    if src_unit == tgt:
        return {
            "converted": True,
            "new_item": item,
            "rule_applied": "identity",
            "style_status": "NO_CHANGE",
            "note": "",
        }

    new_item = copy.deepcopy(item)
    # Always preserve the original source values
    new_item.setdefault("quantity_source_value", qty)
    new_item.setdefault("quantity_source_unit",  src_unit)

    # ── nr ↔ each (rename only) ───────────────────────────────────────────
    if (src_unit in ("nr", "each")) and (tgt in ("nr", "each")):
        new_item["unit"] = tgt
        new_item.setdefault("alignment_notes", []).append(
            f"Unit renamed {src_unit}→{tgt} (semantic equivalents)."
        )
        return {
            "converted": True,
            "new_item": new_item,
            "rule_applied": "nr_each_rename",
            "style_status": "RENAME_ONLY",
            "note": f"Renamed {src_unit} → {tgt}.",
        }

    # ── lm → len (stock-length conversion) ───────────────────────────────
    if src_unit == "lm" and tgt == "len":
        if qty is None:
            return _mismatch(item, src_unit, tgt,
                             "Cannot convert lm→len: quantity is None.")
        stock_mm = _extract_stock_length_mm(desc)
        if stock_mm is None:
            return _mismatch(item, src_unit, tgt,
                             "lm→len blocked: no stock length found in description.")
        stock_m = stock_mm / 1000
        nr_lengths = math.ceil(qty * waste_factor / stock_m)
        new_item["quantity"] = nr_lengths
        new_item["unit"] = "len"
        note = (
            f"lm→len: {qty:.2f} lm ÷ {stock_m:.3f} m/len "
            f"(from description) × {waste_factor:.0%} waste "
            f"= {nr_lengths} len.  "
            f"Source preserved as quantity_source_value={qty} lm."
        )
        new_item.setdefault("alignment_notes", []).append(note)
        new_item["quantity_basis"] = (
            f"{item.get('quantity_basis','')}  [unit_aligned: {note}]"
        )
        return {
            "converted": True,
            "new_item": new_item,
            "rule_applied": "lm_to_len",
            "style_status": "CONVERTED",
            "note": note,
        }

    # ── m2 → each (area → sheet count) ───────────────────────────────────
    if src_unit == "m2" and tgt in ("each", "nr"):
        if qty is None:
            return _mismatch(item, src_unit, tgt,
                             "Cannot convert m2→each: quantity is None.")
        sheet_m2 = _extract_sheet_area_m2(desc)
        if sheet_m2 is None:
            return _mismatch(item, src_unit, tgt,
                             "m2→each blocked: no sheet dimensions found in description.")
        nr_sheets = math.ceil(qty * waste_factor / sheet_m2)
        new_item["quantity"] = nr_sheets
        new_item["unit"] = tgt
        note = (
            f"m2→{tgt}: {qty:.2f} m² ÷ {sheet_m2:.4f} m²/sheet "
            f"× {waste_factor:.0%} waste = {nr_sheets} sheets.  "
            f"Source: {qty} m²."
        )
        new_item.setdefault("alignment_notes", []).append(note)
        return {
            "converted": True,
            "new_item": new_item,
            "rule_applied": "area_to_sheets",
            "style_status": "CONVERTED",
            "note": note,
        }

    # ── All other combinations: flag as STYLE_MISMATCH ────────────────────
    return _mismatch(
        item, src_unit, tgt,
        f"No safe conversion rule from '{src_unit}' to '{tgt}'."
    )


def _mismatch(item: dict, src: str, tgt: str, reason: str) -> ConversionResult:
    new_item = copy.deepcopy(item)
    new_item.setdefault("alignment_notes", []).append(
        f"STYLE_MISMATCH: {reason}  (kept '{src}', baseline expects '{tgt}')"
    )
    return {
        "converted": False,
        "new_item": new_item,
        "rule_applied": "none",
        "style_status": "STYLE_MISMATCH",
        "note": reason,
    }


# ---------------------------------------------------------------------------
# Batch alignment
# ---------------------------------------------------------------------------

def align_section_units(
    items: list[dict],
    target_unit_map: dict[str, str],
    *,
    waste_factor: float = 1.10,
) -> tuple[list[dict], list[dict]]:
    """Apply unit alignment rules to a list of items.

    Parameters
    ----------
    items:
        BOQ items for one section.
    target_unit_map:
        Mapping of family_name → desired unit (from baseline profile).
    waste_factor:
        Applied to lm→len conversions (default 10 % cut waste).

    Returns
    -------
    (aligned_items, conversion_log)
        aligned_items is a list of (possibly updated) item dicts.
        conversion_log records each conversion attempt.
    """
    from .family_classifier import classify

    aligned: list[dict] = []
    log: list[dict] = []

    for item in items:
        desc = item.get("item_name", "") or item.get("item_display_name", "")
        family = classify(desc)
        target = target_unit_map.get(family)

        if target is None:
            aligned.append(item)
            continue

        result = align_unit(item, target, waste_factor=waste_factor)
        aligned.append(result["new_item"])
        if result["style_status"] != "NO_CHANGE":
            log.append({
                "item_name": desc,
                "family":    family,
                "source_unit":  item.get("unit"),
                "target_unit":  target,
                "status":       result["style_status"],
                "rule_applied": result["rule_applied"],
                "note":         result["note"],
            })

    return aligned, log
