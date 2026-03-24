"""
stair_ramp_quantifier.py — Stair, ramp, and balustrade quantification.

Sources (priority order):
  1. PDF stair schedule (type, risers, width)
  2. DXF STAIRS layer geometry (evidence only)
  3. Config assembly rules

Produces:
  - Stair flight(s) — prefab or in-situ
  - Treads / risers
  - Landings
  - Balustrade
  - Handrail
  - Posts / brackets
  - Ramp (if evidence)
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.normalize.element_model import ProjectElementModel, StairElement

log = logging.getLogger("boq.v3.stair_ramp")


def _row(
    package, item_name, unit, quantity, status, basis, evidence, rule,
    confidence, manual_review=False, notes="", item_code="",
) -> dict:
    return {
        "item_name": item_name, "item_code": item_code,
        "unit": unit, "quantity": quantity, "package": package,
        "quantity_status": status, "quantity_basis": basis,
        "source_evidence": evidence, "derivation_rule": rule,
        "confidence": confidence, "manual_review": manual_review, "notes": notes,
    }


def quantify_stairs(
    model:  ProjectElementModel,
    config: dict,
) -> list[dict]:
    rows: list[dict] = []

    for stair in model.stairs:
        src   = stair.source
        conf  = stair.confidence
        mr    = conf == "LOW" or conf == "MEDIUM"
        note  = stair.notes

        # ── Stair flight ──────────────────────────────────────────────────────
        is_prefab = "prefab" in stair.stair_type.lower() or stair.stair_type == "unknown"
        label = (
            f"Stair Stringer (Prefabricated Set) — {stair.risers_per_flight} RISER"
            if is_prefab and stair.risers_per_flight > 0
            else f"Stair Flight ({stair.stair_type})"
        )
        rows.append(_row(
            "stairs", label,
            "nr", stair.flights,
            "measured" if src == "pdf_schedule" else "inferred",
            f"{src}: stair evidence",
            f"{src}: {stair.source_reference or stair.element_id}",
            "direct count" if src == "pdf_schedule" else "DXF stair layer",
            conf,
            manual_review=mr,
            notes=note,
        ))

        # ── Treads / risers ───────────────────────────────────────────────────
        if stair.risers_per_flight > 0:
            total_risers = stair.flights * stair.risers_per_flight
            rows.append(_row(
                "stairs", "Stair Tread",
                "nr", total_risers,
                "calculated" if src == "pdf_schedule" else "inferred",
                f"flights × risers_per_flight = {stair.flights} × {stair.risers_per_flight}",
                f"{src}: risers_per_flight={stair.risers_per_flight}",
                f"{stair.flights} × {stair.risers_per_flight}",
                conf,
                manual_review=mr,
            ))
            # Stair run estimate
            tread_d  = stair.tread_depth_mm or 250
            run_est  = round(total_risers * tread_d / 1000, 2)
        else:
            run_est  = 0.0
            rows.append(_row(
                "stairs", "Stair Tread",
                "nr", 0,
                "placeholder", "risers per flight not known",
                f"{src}: no riser count",
                "manual review required",
                "LOW",
                manual_review=True,
                notes="Riser count not detected. Measure from stair schedule or drawings.",
            ))

        # ── Newel posts (top + bottom per flight) ─────────────────────────────
        newel_count = stair.flights * 2
        rows.append(_row(
            "stairs", "Stair Newel Post",
            "nr", newel_count,
            "calculated",
            f"flights × 2 newel posts = {stair.flights} × 2",
            f"{src}: {stair.element_id}",
            f"{stair.flights} × 2",
            "MEDIUM",
            notes="1 newel post at top and bottom of each flight.",
        ))

        # ── Balustrade and handrail — decomposed ──────────────────────────────
        balustrade_lm = stair.balustrade_lm or (run_est if run_est > 0 else 0)
        handrail_lm   = stair.handrail_lm   or (run_est if run_est > 0 else 0)
        bal_src_conf  = "LOW" if balustrade_lm == run_est else conf
        bal_status    = "calculated" if balustrade_lm == run_est and run_est > 0 else "measured"

        if balustrade_lm > 0:
            post_nr = math.ceil(balustrade_lm / 1.2)
            infill_m2 = round(balustrade_lm * 1.0, 2)  # assume 1.0 m infill height
            rows.append(_row(
                "stairs", "Stair Balustrade — Top Rail",
                "lm", round(balustrade_lm, 2),
                bal_status,
                "= stair_run_estimate" if bal_status == "calculated" else "from schedule",
                f"{src}: {stair.element_id}",
                f"run_est={run_est:.2f} m" if bal_status == "calculated" else "schedule",
                bal_src_conf, manual_review=True,
                notes="Verify length from architectural drawings.",
            ))
            rows.append(_row(
                "stairs", "Stair Balustrade Post",
                "nr", post_nr,
                "calculated",
                f"ceil(balustrade_lm / 1.2 m) = ceil({balustrade_lm:.2f}/1.2)",
                f"{src}: balustrade_lm={balustrade_lm:.2f} m",
                "ceil(lm / 1.2)",
                "LOW", manual_review=True,
                notes="1 post per 1.2 m spacing. Verify from balustrade schedule.",
            ))
            rows.append(_row(
                "stairs", "Stair Balustrade Infill (glass / mesh / picket)",
                "m2", infill_m2,
                "inferred",
                f"balustrade_lm × 1.0 m height = {balustrade_lm:.2f} × 1.0",
                f"{src}: balustrade_lm={balustrade_lm:.2f} m",
                "lm × 1.0 m infill height",
                "LOW", manual_review=True,
                notes="Infill height assumed 1.0 m. Adjust for guard height requirement (≥1.0 m at >1.0 m fall).",
            ))
        else:
            rows.append(_row(
                "stairs", "Stair Balustrade",
                "lm", 0,
                "placeholder", "stair run length not measurable from available sources",
                f"{src}: stair evidence only", "manual review required",
                "LOW", manual_review=True,
                notes="Balustrade length requires stair drawing measurements.",
            ))

        if handrail_lm > 0:
            rows.append(_row(
                "stairs", "Stair Handrail",
                "lm", round(handrail_lm, 2),
                bal_status,
                "= stair_run_estimate" if bal_status == "calculated" else "from schedule",
                f"{src}: {stair.element_id}",
                f"run_est={run_est:.2f} m" if bal_status == "calculated" else "schedule",
                bal_src_conf, manual_review=True,
                notes="Verify handrail length. Continuous rail both sides of flight where required by code.",
            ))
        else:
            rows.append(_row(
                "stairs", "Stair Handrail",
                "lm", 0,
                "placeholder", "stair run length not measurable",
                f"{src}: stair evidence only", "manual review required",
                "LOW", manual_review=True,
                notes="Handrail length requires stair drawing measurements.",
            ))

        # ── Landing ───────────────────────────────────────────────────────────
        if stair.landing_area_m2 > 0:
            rows.append(_row(
                "stairs", "Stair Landing",
                "m2", round(stair.landing_area_m2, 2),
                "measured", "from schedule",
                f"{src}: landing_area",
                "direct from schedule",
                conf,
            ))

    # ── Ramp ─────────────────────────────────────────────────────────────────
    # Emit ramp rows when stairs are present (accessibility ramp likely required).
    # Derive actual entry height from the first stair's riser geometry if available,
    # rather than using a fixed 600 mm assumption.
    if model.stairs:
        first_stair = model.stairs[0]
        if first_stair.risers_per_flight > 0 and first_stair.riser_height_mm > 0:
            ramp_height_m = round(first_stair.risers_per_flight * first_stair.riser_height_mm / 1000, 3)
            height_src    = (
                f"stair: {first_stair.risers_per_flight} risers × "
                f"{first_stair.riser_height_mm} mm = {ramp_height_m*1000:.0f} mm"
            )
        else:
            ramp_height_m = 0.6   # fallback: typical raised-floor entry step
            height_src    = "assumed 600 mm — stair riser count/height not known"

        ramp_gradient    = 1 / 14
        ramp_run_m       = round(ramp_height_m / ramp_gradient, 2)
        ramp_width_m     = 1.2
        ramp_area_m2     = round(ramp_run_m * ramp_width_m, 2)
        ramp_handrail_lm = round(ramp_run_m * 2, 2)  # both sides

        rows.append(_row(
            "stairs", "Access Ramp — Surface (concrete / non-slip)",
            "m2", ramp_area_m2,
            "inferred",
            f"1:14 gradient × {ramp_height_m*1000:.0f}mm entry height × {ramp_width_m}m wide [{height_src}]",
            f"derived from {first_stair.source}: {height_src}",
            f"{ramp_run_m} m run × {ramp_width_m} m wide",
            "LOW", manual_review=True,
            notes=(
                f"Access ramp: 1:14 gradient, {height_src} "
                f"→ {ramp_run_m} m run × {ramp_width_m} m wide = {ramp_area_m2} m². "
                "Verify need and exact dimensions from architectural drawings and BCA D3.3."
            ),
        ))
        rows.append(_row(
            "stairs", "Access Ramp — Handrail (both sides)",
            "lm", ramp_handrail_lm,
            "inferred",
            f"ramp_run × 2 sides = {ramp_run_m} × 2",
            f"derived from ramp run: {ramp_run_m} m ({height_src})",
            f"{ramp_run_m} × 2",
            "LOW", manual_review=True,
            notes="Continuous handrails both sides of ramp. Verify height and spec from architectural drawings.",
        ))
        rows.append(_row(
            "stairs", "Access Ramp — Edge Kerb / Guard",
            "lm", ramp_handrail_lm,
            "inferred",
            f"ramp_run × 2 sides = {ramp_run_m} × 2",
            f"derived from ramp run: {ramp_run_m} m ({height_src})",
            f"{ramp_run_m} × 2",
            "LOW", manual_review=True,
            notes="Kerb guard (min 75 mm high) both sides. Verify from architectural drawings.",
        ))

    # ── Verandah balustrade ───────────────────────────────────────────────────
    verandahs = model.verandahs
    for ver in verandahs:
        if ver.perimeter_m > 0:
            post_count = math.ceil(ver.perimeter_m / 1.2)
            rows.append(_row(
                "external_balustrade", "Verandah Balustrade",
                "lm", round(ver.perimeter_m, 2),
                "measured", "DXF VERANDAH perimeter",
                f"{ver.source}: verandah_perimeter={ver.perimeter_m:.2f} m",
                "= verandah_perimeter_m",
                ver.confidence,
            ))
            rows.append(_row(
                "external_balustrade", "Verandah Handrail",
                "lm", round(ver.perimeter_m, 2),
                "calculated", "= verandah_perimeter (full open edge)",
                f"{ver.source}: verandah_perimeter={ver.perimeter_m:.2f} m",
                "= verandah_perimeter",
                "MEDIUM",
                notes="Reduce if one side abuts building wall.",
            ))
            rows.append(_row(
                "external_balustrade", "Balustrade Post",
                "nr", post_count,
                "calculated", f"ceil(verandah_perimeter / 1.2 m)",
                f"derived from verandah_perimeter={ver.perimeter_m:.2f} m",
                "ceil(perimeter / 1.2)",
                "LOW",
            ))

    return rows
