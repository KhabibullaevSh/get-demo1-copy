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

    # Width threshold separating external entrance doors (≥850mm) from internal partition
    # doors (<850mm). Consistent with the same threshold in lining_quantifier.py.
    _EXT_DOOR_MIN_W = 0.85

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

        # Total door count — primary family summary row
        rows.append(_row(
            "openings_doors",
            "Door — Total Count (all marks)",
            "nr", total_doors,
            "measured",
            f"sum of all door mark quantities = {total_doors} nr",
            f"dxf_blocks: {', '.join(f'{o.mark}×{o.quantity}' for o in doors)}",
            "sum(qty per mark)",
            "HIGH" if all(o.confidence == "HIGH" for o in doors) else "MEDIUM",
            notes=(
                f"Total doors across all marks: "
                + ", ".join(f"{o.mark} ({o.swing_type}) ×{o.quantity}" for o in doors)
                + f" = {total_doors} nr."
            ),
        ))

        # Head flashing — all doors (needed at every door opening to prevent water
        # tracking down the wall face into the frame regardless of int/ext).
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

        # Sill flashing — external doors only (weather protection at building envelope).
        # Internal partition doors do not require sill flashings.
        # Uses the same width threshold as lining_quantifier.py (_EXT_DOOR_MIN_W=0.85m).
        ext_door_ops_sill = [o for o in doors if o.width_m >= _EXT_DOOR_MIN_W]
        total_sill_lm = sum(
            round((o.width_m + 2 * overrun) * o.quantity, 3)
            for o in ext_door_ops_sill
        )
        ext_sill_widths_known = all(o.width_m > 0 for o in ext_door_ops_sill)
        rows.append(_row(
            "openings_doors",
            "Door Sill Flashing",
            "lm", round(total_sill_lm, 2),
            "calculated",
            f"sum((door_width + {overrun*1000:.0f}mm overrun × 2) × count) — external doors only (width ≥ {_EXT_DOOR_MIN_W*1000:.0f}mm)",
            (
                f"dxf_blocks: {', '.join(f'{o.mark}={o.width_m:.2f}m×{o.quantity}' for o in ext_door_ops_sill)}"
                if ext_door_ops_sill else "no external doors detected"
            ),
            f"sum((width + {2*overrun:.3f}) × qty) for ext doors only",
            "MEDIUM" if ext_sill_widths_known and ext_door_ops_sill else "LOW",
            manual_review=True,
            notes=(
                f"Sill flashing for external doors only (width ≥ {_EXT_DOOR_MIN_W*1000:.0f}mm threshold). "
                f"Classified external: {', '.join(f'{o.mark}×{o.quantity}' for o in ext_door_ops_sill) or 'none'}. "
                f"Classified internal (no sill): {', '.join(f'{o.mark}×{o.quantity}' for o in doors if o.width_m < _EXT_DOOR_MIN_W) or 'none'}. "
                "Threshold is heuristic — verify from door schedule and architectural drawings."
            ),
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
            if op.width_m > 0 and op.height_m > 0:
                _size_label = f" ({op.width_m*1000:.0f} × {op.height_m*1000:.0f}mm)"
            elif op.width_m > 0:
                _size_label = f" ({op.width_m*1000:.0f}mm wide)"
            else:
                _size_label = ""
            rows.append(_row(
                "openings_windows",
                f"Window — {op.mark}{_size_label}",
                "nr", op.quantity,
                "measured", "count from source",
                f"{op.source}: mark={op.mark} count={op.quantity}",
                "direct count",
                op.confidence,
                notes=f"type={op.swing_type}",
            ))

        # Total window count — primary family summary row
        rows.append(_row(
            "openings_windows",
            "Window — Total Count (all marks)",
            "nr", total_windows,
            "measured",
            f"sum of all window mark quantities = {total_windows} nr",
            f"dxf_blocks: {', '.join(f'{o.mark}×{o.quantity}' for o in windows)}",
            "sum(qty per mark)",
            "HIGH" if all(o.confidence == "HIGH" for o in windows) else "MEDIUM",
            notes=(
                f"Total windows across all marks: "
                + ", ".join(f"{o.mark} ({o.swing_type}) ×{o.quantity}" for o in windows)
                + f" = {total_windows} nr."
            ),
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
                _blade_note = (
                    (
                        f"Frame height {h_m*1000:.0f} mm (from {h_src}). "
                        f"{blades_per_frame} blades per frame at {blade_pitch_mm} mm pitch. "
                        "Verify blade count and size from window schedule."
                    ) if op.height_m > 0 else (
                        f"BLOCKED PENDING WINDOW SCHEDULE: Window height not available in DXF block geometry "
                        f"or opening schedule for {op.mark}. "
                        f"Using config default {h_m*1000:.0f} mm → {blades_per_frame} blades per frame at "
                        f"{blade_pitch_mm} mm pitch. "
                        "Blade count will change when window schedule is supplied. "
                        "Verify frame height and blade quantity from window/door schedule."
                    )
                )
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
                    notes=_blade_note,
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

    # ── Door frame — primary procurement family lm ────────────────────────────
    # Total door frame perimeter = sum((2 × height + width) × qty) per mark.
    # Heights: from opening schedule if available, else config default.
    # This is the primary door FRAME procurement row (not per-mark, but family total).
    if total_doors > 0:
        door_frame_lm = 0.0
        door_frame_parts = []
        all_heights_known = True
        for op in doors:
            h = op.height_m if op.height_m > 0 else default_door_h
            if op.height_m == 0:
                all_heights_known = False
            frame_lm_this = round((2 * h + op.width_m) * op.quantity, 3)
            door_frame_lm += frame_lm_this
            door_frame_parts.append(
                f"{op.mark}×{op.quantity}: (2×{h:.3f}+{op.width_m:.3f})×{op.quantity}={frame_lm_this:.3f}lm"
            )
        door_frame_lm = round(door_frame_lm, 2)
        rows.append(_row(
            "openings_doors",
            "Door Frame — Total Family lm (all marks combined)",
            "lm", door_frame_lm,
            "calculated",
            "sum((2×height + width) × qty) for each door mark",
            f"dxf_blocks: {'; '.join(door_frame_parts)}",
            "sum((2h+w)×qty per mark)",
            "MEDIUM" if all_heights_known else "LOW",
            manual_review=(not all_heights_known),
            notes=(
                f"Total door frame perimeter for all {total_doors} doors. "
                f"{'Heights from DXF/schedule.' if all_heights_known else f'Heights from config default ({default_door_h:.3f}m) — verify from door schedule.'} "
                "Widths from DXF block names (HIGH). "
                "Use for frame section procurement across all door marks."
            ),
        ))

    # ── Window frame — primary procurement family lm ──────────────────────────
    # Total louvre window frame perimeter = sum((2 × height + width) × qty) per mark.
    # Heights from FrameCAD label promotions where available (MEDIUM), else config LOW.
    if total_windows > 0:
        def_lou_h_frame = open_cfg.get("default_louvre_height_m", 0.75)
        win_frame_lm = 0.0
        win_frame_parts = []
        win_all_h_known = True
        for op in windows:
            w = op.width_m if op.width_m > 0 else def_lou_w
            h = op.height_m if op.height_m > 0 else def_lou_h_frame
            if op.height_m == 0:
                win_all_h_known = False
            frame_lm_this = round((2 * h + w) * op.quantity, 3)
            win_frame_lm += frame_lm_this
            h_src = "schedule/label" if op.height_m > 0 else "config_default"
            win_frame_parts.append(
                f"{op.mark}×{op.quantity}: (2×{h:.3f}+{w:.3f})×{op.quantity}={frame_lm_this:.3f}lm[h:{h_src}]"
            )
        win_frame_lm = round(win_frame_lm, 2)
        conf_frame = "MEDIUM" if win_all_h_known else "LOW"
        rows.append(_row(
            "openings_windows",
            "Louvre Window Frame — Total Family lm (all marks combined)",
            "lm", win_frame_lm,
            "calculated",
            "sum((2×height + width) × qty) for each window mark",
            f"dxf_blocks+framecad_labels: {'; '.join(win_frame_parts)}",
            "sum((2h+w)×qty per mark)",
            conf_frame,
            manual_review=(not win_all_h_known),
            notes=(
                f"Total louvre window frame perimeter for all {total_windows} windows. "
                f"WINDOW_LOUVRE_1100 (h=1.203m) and WINDOW_LOUVRE_800 (h=0.623m): MEDIUM (FrameCAD label evidence). "
                f"WINDOW_LOUVRE_1800 (h=0.750m): LOW (config default — height blocked). "
                "Widths from DXF block names. Use for frame section procurement across all window marks."
            ),
        ))

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

    # ── Fly screens (louvre windows — tropical/PNG building requirement) ───────
    louvre_windows = [o for o in windows if o.swing_type == "louvre"]
    if louvre_windows:
        def_lou_h = open_cfg.get("default_louvre_height_m", 0.75)
        for op in louvre_windows:
            w = op.width_m if op.width_m > 0 else open_cfg.get("default_louvre_width_m", 1.1)
            h = op.height_m if op.height_m > 0 else def_lou_h
            screen_area = round(w * h * op.quantity, 2)
            h_src = "opening_schedule" if op.height_m > 0 else f"config_default={def_lou_h*1000:.0f}mm"
            w_src = "block_name" if op.width_m > 0 else "config_default"
            _screen_note = (
                (
                    f"Fly screen for louvre window {op.mark} ({op.quantity} nr). "
                    f"Area: {w:.2f}m × {h:.3f}m = {round(w*h,3):.3f} m² each. "
                    "Tropical building — all louvre openings require insect screening. "
                    "Verify frame dimensions from window schedule."
                ) if (op.height_m > 0 and op.width_m > 0) else (
                    f"BLOCKED PENDING WINDOW SCHEDULE: "
                    + (f"Height not available in DXF for {op.mark} — using config default {h:.3f} m. " if op.height_m == 0 else "")
                    + (f"Width not available for {op.mark} — using config default {w:.2f} m. " if op.width_m == 0 else "")
                    + f"Screen area {screen_area:.2f} m² is a heuristic estimate. "
                    "Screen area will change when window schedule is supplied. "
                    "Tropical building — all louvre openings require insect screening."
                )
            )
            rows.append(_row(
                "openings_window_hardware",
                f"Fly Screen — {op.mark}",
                "m2", screen_area,
                "calculated",
                f"width({w:.2f}m) × height({h:.3f}m) × qty({op.quantity})",
                f"{op.source}: mark={op.mark} w={w:.2f}m ({w_src}) h={h:.3f}m ({h_src}) qty={op.quantity}",
                f"{w:.2f} × {h:.3f} × {op.quantity}",
                "MEDIUM" if (op.width_m > 0 and op.height_m > 0) else "LOW",
                manual_review=(op.height_m == 0),
                notes=_screen_note,
            ))

    # ── Commercial door closer (main entrance / pharmacy counter) ─────────────
    # In a pharmacy, the main public entrance typically needs a door closer for
    # controlled access and energy efficiency. Flag as MEDIUM confidence.
    if doors:
        # Identify likely entrance doors: DOOR_90 (widest single door)
        entrance_doors = [o for o in doors if o.width_m >= 0.85 or "90" in o.mark]
        if entrance_doors:
            entrance_qty = sum(o.quantity for o in entrance_doors)
            rows.append(_row(
                "openings_door_hardware",
                "Door Closer — Hydraulic (main entrance)",
                "nr", entrance_qty,
                "inferred",
                f"entrance_door_count({entrance_qty}) — widest door mark(s) in pharmacy",
                f"dxf_blocks: {', '.join(o.mark for o in entrance_doors)} (width≥850mm or DOOR_90)",
                "count of entrance-width doors",
                "MEDIUM",
                manual_review=True,
                notes=(
                    f"Door closer inferred for pharmacy entrance doors. "
                    f"Marks: {', '.join(o.mark for o in entrance_doors)} ({entrance_qty} nr). "
                    "Verify which doors require closers from door schedule and architect. "
                    "Commercial pharmacy may require controlled entry."
                ),
            ))

    return rows
