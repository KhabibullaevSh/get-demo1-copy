"""
assembly_engine.py — Rule-driven procurement assembly decomposition.

Converts measured/derived element quantities into individual procurement-ready
BOQ items using rules from assembly_rules.yaml.

Architecture: Layer A (measurement) → THIS MODULE → Layer B (procurement BOQ)

CRITICAL: Assembly rules produce quantities from geometry only.
No quantities are ever sourced from BOQ reference files.
"""
from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger("boq.v3.assembly_engine")


def _eval_qty(expr: str, x: float) -> float:
    """
    Safely evaluate an assembly quantity expression.

    'x' = the input geometry value.
    Allowed names: math.ceil, math.floor, math.sqrt, round, max, min, x.
    """
    if not expr or expr.strip() == "0":
        return 0.0
    try:
        result = eval(  # noqa: S307 — restricted namespace
            expr,
            {"__builtins__": {}},
            {"ceil": math.ceil, "floor": math.floor, "sqrt": math.sqrt,
             "round": round, "max": max, "min": min, "x": x},
        )
        return float(result)
    except Exception as exc:
        log.warning("Assembly rule eval error [expr=%r, x=%s]: %s", expr, x, exc)
        return 0.0


def apply_assembly_rule(
    rule_name:        str,
    rules_dict:       dict,
    input_value:      float,
    source_evidence:  str,
    source_confidence: str,
    extra_override:   dict | None = None,
) -> list[dict]:
    """
    Apply a named assembly rule to an input value and return BOQ procurement rows.

    Args:
        rule_name:         Key in assembly_rules.yaml (e.g. "roof_eaves")
        rules_dict:        Loaded assembly_rules.yaml as dict
        input_value:       The measured/derived geometry value (m, m², nr, etc.)
        source_evidence:   Traceability string for the input value
        source_confidence: Confidence of the input value (HIGH/MEDIUM/LOW)
        extra_override:    Dict to override item names/qtys (optional)

    Returns:
        List of BOQ procurement row dicts, each with full traceability fields.
    """
    rule = rules_dict.get(rule_name)
    if not rule:
        log.warning("Assembly rule not found: %s", rule_name)
        return []

    items = rule.get("items", [])
    result = []
    for item in items:
        qty_expr  = item.get("qty_expr", "x")
        item_conf = item.get("confidence", "low").upper()
        qty       = _eval_qty(qty_expr, input_value)

        # Determine combined confidence (floor of rule item and input)
        conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "PLACEHOLDER": 0}
        combined_conf = min(
            conf_rank.get(source_confidence.upper(), 1),
            conf_rank.get(item_conf, 1),
        )
        combined_conf_str = {3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "LOW"}[combined_conf]

        # Skip zero quantities for placeholder items
        is_placeholder = (qty == 0.0 or item_conf == "PLACEHOLDER")

        row = {
            "item_name":        item.get("name", "Unknown item"),
            "item_code":        "",   # filled by stock_code_mapper
            "unit":             item.get("unit", "nr"),
            "quantity":         qty if not is_placeholder else 0,
            "package":          rule_name,
            "quantity_status":  "placeholder" if is_placeholder else "calculated",
            "quantity_basis":   f"assembly_rule:{rule_name}",
            "source_evidence":  source_evidence,
            "derivation_rule":  (
                f"{rule_name} → {qty_expr}"
                + (f"  [note: {item.get('note','')}]" if item.get("note") else "")
            ),
            "confidence":       combined_conf_str,
            "manual_review":    is_placeholder or combined_conf_str == "LOW",
            "notes":            item.get("note", ""),
        }
        result.append(row)

    return result


def _roof_sheet_count(
    roof_area_m2: float,
    eaves_lm:     float,
    cover_w_m:    float = 0.762,
    waste:        float = 1.05,
) -> tuple[int, str, str]:
    """
    Compute corrugated sheet count using run-based stock-length selection.

    Returns (sheet_count, sheet_length_str, derivation_note)
    """
    # Stock lengths available for corrugated CGI (Custom Orb / Trimdek / Lysaght)
    stock_lengths = [3.0, 3.6, 4.2, 4.5, 5.4, 6.0, 7.2]
    top_lap_m = 0.15  # 150 mm top lap

    if eaves_lm > 0:
        run_m     = roof_area_m2 / eaves_lm
        min_len   = run_m + top_lap_m
        sheet_len = next((l for l in stock_lengths if l >= min_len), stock_lengths[-1])
        note      = f"run={run_m:.2f}m (area/eaves) + {top_lap_m*1000:.0f}mm lap → select {sheet_len}m stock"
    else:
        # Fallback when eaves_lm unknown — use 4.5m
        sheet_len = 4.5
        note      = f"eaves_lm unknown — assumed {sheet_len}m stock length"

    count = math.ceil(roof_area_m2 * waste / (cover_w_m * sheet_len))
    return count, f"{sheet_len}m", note


def apply_all_roof_assemblies(
    roof_area_m2:    float,
    eaves_lm:        float,
    ridge_lm:        float,
    barge_lm:        float,
    valley_lm:       float,
    downpipe_count:  int,
    rules:           dict,
    evidence_prefix: str = "dxf_geometry",
    roof_confidence: str = "HIGH",
    apron_lm:        float = 0.0,
) -> list[dict]:
    """Apply all roof assembly rules and return combined row list."""
    rows = []
    if roof_area_m2 > 0:
        rows += apply_assembly_rule(
            "roof_cladding", rules, roof_area_m2,
            f"{evidence_prefix}: roof_area={roof_area_m2:.2f} m²",
            roof_confidence,
        )
        # Sheet count — computed from stock-length selection, NOT from assembly rule
        sheet_count, sheet_len_str, sheet_note = _roof_sheet_count(roof_area_m2, eaves_lm)
        rows.append({
            "item_name":       "Roof Cladding Sheet (corrugated / CGI)",
            "item_code":       "",
            "unit":            "sheets",
            "quantity":        sheet_count,
            "package":         "roof_cladding",
            "quantity_status": "calculated",
            "quantity_basis":  f"area×waste / (cover_w×sheet_len) = {roof_area_m2:.1f}×1.05/(0.762×{sheet_len_str})",
            "source_evidence": f"{evidence_prefix}: roof_area={roof_area_m2:.2f} m², eaves_lm={eaves_lm:.2f} m",
            "derivation_rule": f"ceil(area × 1.05 / (0.762 × {sheet_len_str}))",
            "confidence":      "MEDIUM",
            "manual_review":   True,
            "notes":           sheet_note + " Verify sheet length and profile from spec.",
        })
    if ridge_lm > 0:
        rows += apply_assembly_rule(
            "roof_ridge", rules, ridge_lm,
            f"{evidence_prefix}: ridge_length={ridge_lm:.2f} lm",
            "MEDIUM",
        )
    if eaves_lm > 0:
        rows += apply_assembly_rule(
            "roof_eaves", rules, eaves_lm,
            f"{evidence_prefix}: eaves_length={eaves_lm:.2f} lm",
            "MEDIUM",
        )
    if apron_lm > 0:
        rows += apply_assembly_rule(
            "roof_apron", rules, apron_lm,
            f"{evidence_prefix}: apron_length={apron_lm:.2f} lm",
            "LOW",
        )
    if barge_lm > 0:
        rows += apply_assembly_rule(
            "roof_barge", rules, barge_lm,
            f"{evidence_prefix}: barge_length={barge_lm:.2f} lm",
            "LOW",
        )
    if valley_lm > 0:
        rows += apply_assembly_rule(
            "roof_valley", rules, valley_lm,
            f"{evidence_prefix}: valley_length={valley_lm:.2f} lm",
            "MEDIUM",
        )
    if downpipe_count > 0:
        rows += apply_assembly_rule(
            "roof_drainage", rules, downpipe_count,
            f"{evidence_prefix}: downpipe_count={downpipe_count}",
            "MEDIUM",
        )
    return rows


def apply_all_opening_assemblies(
    openings: list[dict],
    rules:    dict,
) -> list[dict]:
    """
    Decompose all opening elements into procurement rows.

    Each opening dict must have: opening_type, swing_type, quantity, mark,
    source, confidence.
    """
    rows = []
    for op in openings:
        qty       = op.get("quantity", 1)
        swing     = op.get("swing_type", "hinged").lower()
        op_type   = op.get("opening_type", "door").lower()
        mark      = op.get("mark", "")
        evidence  = f"{op.get('source','')}: {mark} count={qty}"
        conf      = op.get("confidence", "MEDIUM")

        if op_type == "door":
            rule_key = "door_single_sliding" if swing == "sliding" else "door_single_hinged"
        elif op_type == "window":
            rule_key = "window_louvre" if swing == "louvre" else "window_standard"
        else:
            continue

        rule_rows = apply_assembly_rule(rule_key, rules, qty, evidence, conf)

        # Annotate each row with opening mark for traceability
        for r in rule_rows:
            r["source_evidence"] = f"{evidence}  →  {r['source_evidence']}" if r["source_evidence"] != evidence else evidence
            r["notes"] = (f"opening_mark={mark}  " + r.get("notes", "")).strip()
        rows += rule_rows

    return rows
