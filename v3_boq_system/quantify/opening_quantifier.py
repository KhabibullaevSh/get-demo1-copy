"""
opening_quantifier.py — Door and window schedule decomposition.

Converts each detected opening into component-level procurement items.

Sources (priority order):
  1. PDF schedule (mark, width, height, type) — HIGH confidence
  2. DXF block inserts (mark/type from block name, width from block name)
  3. IFC door/window count (totals only, no detail)

Each door/window is decomposed into:
  - leaf / frame / hardware using assembly_engine rules
  - flashings
  - accessor items (hinges, locksets, flyscreens, etc.)
"""
from __future__ import annotations

import logging
import math

from v3_boq_system.assemblies.assembly_engine import apply_all_opening_assemblies
from v3_boq_system.normalize.element_model import OpeningElement, ProjectElementModel

log = logging.getLogger("boq.v3.openings")


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


def quantify_openings(
    model:          ProjectElementModel,
    config:         dict,
    assembly_rules: dict,
) -> list[dict]:
    """
    Produce full component-level BOQ rows for all openings.
    """
    rows: list[dict] = []
    cfg   = config.get("openings", {})
    open_cfg = config.get("openings", {})
    default_door_h = open_cfg.get("default_door_height_m", 2.04)
    arch_door_lm   = config.get("finishes", {}).get("architrave_door_lm_each", 6.0)
    arch_win_lm    = config.get("finishes", {}).get("architrave_window_lm_each", 4.8)

    doors   = [o for o in model.openings if o.opening_type == "door"]
    windows = [o for o in model.openings if o.opening_type == "window"]

    overrun    = open_cfg.get("flashing_overrun_m", 0.05)
    def_win_w  = open_cfg.get("default_window_width_m", 1.2)
    def_lou_w  = open_cfg.get("default_louvre_width_m", 1.1)

    # ── Door summary row ─────────────────────────────────────────────────────
    total_doors = sum(o.quantity for o in doors)
    if total_doors > 0:
        # Group by mark for display
        for op in doors:
            width_label = f" ({op.width_m*1000:.0f}mm)" if op.width_m > 0 else ""
            rows.append(_row(
                "openings_doors",
                f"Door — {op.mark}{width_label}",
                "nr", op.quantity,
                "measured", "count from source",
                f"{op.source}: mark={op.mark} count={op.quantity}",
                "direct count",
                op.confidence,
                notes=f"swing={op.swing_type}",
            ))

        # Head flashing per door type — use actual width from block name
        # width known from DXF block → MEDIUM confidence (just overrun assumption)
        total_head_lm = sum(
            round((o.width_m + 2 * overrun) * o.quantity, 3)
            for o in doors
        )
        all_widths_known = all(o.width_m > 0 for o in doors)
        rows.append(_row(
            "openings_doors",
            "Door Head Flashing",
            "lm", round(total_head_lm, 2),
            "calculated",
            f"sum((door_width + {overrun*1000:.0f}mm overrun × 2) × count)",
            f"dxf_blocks: door widths {', '.join(f'{o.mark}={o.width_m:.2f}m×{o.quantity}' for o in doors)}",
            f"sum((width + {2*overrun:.3f}) × qty)",
            "MEDIUM" if all_widths_known else "LOW",
            notes=f"Head flashing extends {int(overrun*1000)} mm beyond door frame each side.",
        ))

        # Sill flashing — external doors only (conservative: flag all as needing review)
        total_sill_lm = sum(
            round((o.width_m + 2 * overrun) * o.quantity, 3)
            for o in doors
        )
        rows.append(_row(
            "openings_doors",
            "Door Sill Flashing",
            "lm", round(total_sill_lm, 2),
            "calculated",
            f"sum((door_width + {overrun*1000:.0f}mm overrun × 2) × count)",
            f"dxf_blocks: {total_doors} doors",
            f"sum((width + {2*overrun:.3f}) × qty)",
            "MEDIUM" if all_widths_known else "LOW",
            notes="Sill flashing for all doors. Remove from internal doors not requiring sill.",
        ))

        # Hardware per door type
        hardware_rows = apply_all_opening_assemblies(
            [{"opening_type": "door", "swing_type": o.swing_type, "quantity": o.quantity,
              "mark": o.mark, "source": o.source, "confidence": o.confidence}
             for o in doors],
            assembly_rules,
        )
        for hr in hardware_rows:
            hr["package"] = "openings_door_hardware"
        rows += hardware_rows

    # ── Window summary + decomposition ────────────────────────────────────────
    total_windows = sum(o.quantity for o in windows)
    if total_windows > 0:
        for op in windows:
            rows.append(_row(
                "openings_windows",
                f"Window — {op.mark}",
                "nr", op.quantity,
                "measured", "count from source",
                f"{op.source}: mark={op.mark} count={op.quantity}",
                "direct count",
                op.confidence,
                notes=f"type={op.swing_type}",
            ))

        # Window head flashing + sill flashing + louvre blade count
        def_lou_h = open_cfg.get("default_louvre_height_m", 0.75)   # 750 mm = 7 blades typical
        for op in windows:
            if op.width_m > 0:
                w = op.width_m
                w_src = f"block_name: {op.mark}"
                w_conf = "MEDIUM"
            elif op.swing_type == "louvre":
                w = def_lou_w
                w_src = f"config default louvre width={def_lou_w}m"
                w_conf = "LOW"
            else:
                w = def_win_w
                w_src = f"config default window width={def_win_w}m"
                w_conf = "LOW"
            flash_lm = round((w + 2 * overrun) * op.quantity, 2)

            # Head flashing
            rows.append(_row(
                "openings_windows",
                f"Window Head Flashing — {op.mark}",
                "lm", flash_lm,
                "calculated",
                f"(window_width + {overrun*1000:.0f}mm overrun × 2) × count = ({w:.2f}+{2*overrun:.3f})×{op.quantity}",
                f"{op.source}: {w_src} count={op.quantity}",
                f"({w:.2f}+{2*overrun:.3f})×{op.quantity}",
                w_conf,
                manual_review=(w_conf == "LOW"),
                notes=(f"Window width assumed {w:.2f} m (from {w_src}). "
                       "Verify from window schedule.") if w_conf == "LOW" else
                      f"Head flashing extends {int(overrun*1000)} mm beyond frame each side.",
            ))

            # Sill flashing — same formula as head flashing
            rows.append(_row(
                "openings_windows",
                f"Window Sill Flashing — {op.mark}",
                "lm", flash_lm,
                "calculated",
                f"(window_width + {overrun*1000:.0f}mm overrun × 2) × count = ({w:.2f}+{2*overrun:.3f})×{op.quantity}",
                f"{op.source}: {w_src} count={op.quantity}",
                f"({w:.2f}+{2*overrun:.3f})×{op.quantity}",
                w_conf,
                manual_review=(w_conf == "LOW"),
                notes="Sill flashing same lm as head flashing. Remove if window sits on masonry sill.",
            ))

            # Louvre blade count — ceil(frame_height_mm / 100 mm blade pitch)
            if op.swing_type == "louvre":
                h_m = op.height_m if op.height_m > 0 else def_lou_h
                h_src = "opening_schedule" if op.height_m > 0 else f"config_default={def_lou_h*1000:.0f}mm"
                blade_pitch_mm = 100
                blades_per_frame = math.ceil(h_m * 1000 / blade_pitch_mm)
                total_blades = blades_per_frame * op.quantity
                rows.append(_row(
                    "openings_windows",
                    f"Louvre Blade — {op.mark}",
                    "nr", total_blades,
                    "calculated",
                    (
                        f"ceil(frame_h({h_m*1000:.0f}mm) / {blade_pitch_mm}mm pitch) × count "
                        f"= {blades_per_frame} blades × {op.quantity}"
                    ),
                    f"{op.source}: {h_src} count={op.quantity}",
                    f"ceil({h_m*1000:.0f}/{blade_pitch_mm}) × {op.quantity}",
                    "MEDIUM" if op.height_m > 0 else "LOW",
                    manual_review=(op.height_m == 0),
                    notes=(
                        f"Frame height {h_m*1000:.0f} mm ({h_src}). "
                        f"{blades_per_frame} blades per frame at {blade_pitch_mm} mm pitch. "
                        "Verify blade count and size from window schedule."
                    ),
                ))

        win_hardware_rows = apply_all_opening_assemblies(
            [{"opening_type": "window", "swing_type": o.swing_type, "quantity": o.quantity,
              "mark": o.mark, "source": o.source, "confidence": o.confidence}
             for o in windows],
            assembly_rules,
        )
        for hr in win_hardware_rows:
            hr["package"] = "openings_window_hardware"
        rows += win_hardware_rows

    # ── Architraves (finishes associated with openings) ───────────────────────
    if total_doors > 0:
        arch_d = round(total_doors * arch_door_lm, 1)
        rows.append(_row(
            "openings_finishes",
            "Architrave — Door",
            "lm", arch_d,
            "calculated",
            f"door_count × {arch_door_lm} lm/door = {total_doors} × {arch_door_lm}",
            f"derived from door_count={total_doors}",
            f"door_count × {arch_door_lm}",
            "MEDIUM",
        ))

    if total_windows > 0:
        arch_w = round(total_windows * arch_win_lm, 1)
        rows.append(_row(
            "openings_finishes",
            "Architrave — Window",
            "lm", arch_w,
            "calculated",
            f"window_count × {arch_win_lm} lm/window = {total_windows} × {arch_win_lm}",
            f"derived from window_count={total_windows}",
            f"window_count × {arch_win_lm}",
            "MEDIUM",
        ))

    return rows
