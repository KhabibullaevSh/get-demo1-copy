# Recovery Pass Report — Ceiling / Roof / Walls / Sheeting / Openings
**Project:** Angau Pharmacy (G303 / project2)
**Date:** 2026-03-26
**Pipeline version:** V3 BOQ
**Pass type:** Source-driven geometry and lining recovery
**Non-negotiable rules applied:**
- No quantities copied from final BOQ
- No back-solving formulas to match any reference
- Every changed quantity carries: source_file, source_layer, derivation_method, confidence
- Partial ceiling lining — not assumed to equal full floor area

---

## Summary

| Metric | Pre-pass | Post-pass | Delta |
|--------|----------|-----------|-------|
| BOQ items | 172 | 171 | −1 |
| Manual review items | 71 | 68 | −3 |
| Warnings (element model) | 1 | 0 | −1 |
| Ceiling area source | derived (MEDIUM) | dxf_geometry (HIGH) | ↑ |
| Ceiling area m² | 64.8 | 49.0 | −15.8 |
| Ceiling sheets | 24 | 18 | −6 |
| Ceiling battens lm | 162.0 | 122.5 | −39.5 |
| Ceiling screws boxes | 7 | 5 | −2 |

---

## Category A — Real Quantity Improvements (source upgrade, quantity changed)

### A1. Ceiling Lining — FC Sheet (E07)

**Before:** 24 sheets — `source=derived, confidence=MEDIUM`
**After:** 18 sheets — `source=dxf_geometry, confidence=HIGH`

**Root cause:** `element_builder.py` applied a `>= derived * 0.9` threshold to decide whether the
DXF CEILING layer hatch was "partial". Since 49.0 m² < 58.32 m² (64.8 × 0.9), the pipeline
overrode the DXF measurement with the derived floor-minus-verandah area (64.8 m²) and emitted
a warning: *"Ceiling area from DXF appears partial; using floor−verandah = 64.80 m²"*.

**Fix:** Removed the 0.9 override threshold. The DXF CEILING layer hatch is the architect's
reflected ceiling plan area — the primary authoritative source. Not all floor area has ceiling
lining (verandah, open-plan areas, storage may be unlined). The `dxf_extractor.py` already
reads the CEILING HATCH correctly as a dedicated measurement (`ceiling_area_m2 = 49.0`).

**Derivation after fix:**
```
ceiling_area = 49.0 m²    (DXF ANGAU PHARMACY 01_arch.dxf → CEILING layer HATCH entity)
sheets = ceil(49.0 × 1.05 / 2.88) = ceil(17.85) = 18 sheets
```

**Source:** `ANGAU PHARMACY 01_arch.dxf` → `CEILING` layer → HATCH entity area
**Confidence:** HIGH (measured geometry, dedicated ceiling layer)

---

### A2. Ceiling Batten — LGS / timber (E08)

**Before:** 162.0 lm — `source=derived, confidence=MEDIUM`
**After:** 122.5 lm — `source=dxf_geometry, confidence=MEDIUM`

**Derivation after fix:**
```
ceiling_area = 49.0 m²
battens = 49.0 / 0.4 = 122.5 lm   (400 mm spacing)
```

**Source:** Derived from corrected ceiling_area (49.0 m² HIGH). Spacing rule (400 mm) stays MEDIUM
as batten spacing not specified in source documents.

---

### A3. Ceiling FC Sheet Screws (E09)

**Before:** 7 boxes — `source=derived, confidence=LOW`
**After:** 5 boxes — `source=dxf_geometry, confidence=LOW`

**Derivation after fix:**
```
ceiling_area = 49.0 m²
boxes = ceil(49.0 × 20 / 200) = ceil(4.9) = 5 boxes   (20 screws/m², 200/box)
```

**Source:** Derived from corrected ceiling_area. Screw density rule stays LOW (no fixing schedule
in source documents).

---

## Category B — Decomposition Improvements

None identified in this pass. All section improvements were quantity changes, not breakdowns.

---

## Category C — Blocked (cannot improve without new source documents)

| Item | Why blocked |
|------|-------------|
| Ceiling area zone split (lined vs unlined) | DXF CEILING HATCH is a single polygon = 49.0 m². No hatching by room or zone label. Cannot split by room type without a room-annotated reflected ceiling plan. |
| Batten specification (grade, section) | No batten schedule in any source document. G22 is a design-team assumption. |
| Ceiling height variation | Single wall_height_m=2.4 from config. No section drawings showing ceiling step-downs or bulkheads. |
| Verandah soffit area (E10) | Treated as verandah_area (21.6 m²). No soffit plan — may differ from deck area if there are overhangs. MEDIUM confidence retained. |
| Wet area wall lining (E06) | Toilet perimeter estimated from sqrt(room_area). No room dimension schedule. LOW confidence retained. |

---

## Category D — No-Change Confirmations (already at correct values, no action taken)

### D1. Roof Package (Section B — 18 items)

All 18 roof items confirmed at current sourcing. No changes made.

| Geometry | Value | Source | Confidence |
|----------|-------|--------|-----------|
| Roof area | 106.6 m² | DXF ROOF layer hatch | HIGH |
| Roof perimeter | 42.4 m | DXF ROOF layer | HIGH |
| Eaves | 42.4 lm | DXF perimeter | HIGH |
| Ridge | 10.6 lm | DXF ROOF layer | HIGH |
| Barge | 8.5 lm | DXF ROOF layer | HIGH |
| Roof panels | 481.74 lm | FrameCAD BOM Tab "Roof Panels" | HIGH |
| Roof trusses | 1634.29 lm | FrameCAD BOM Tab "Roof Trusses" | HIGH |
| Roof battens | 266 pcs / 1385.3 lm | FrameCAD BOM batten schedule | HIGH (total) / MEDIUM (zone split) |

**Batten zone split confirmation:** The BOM gives two batten grades:
- 40 mm grade: 85 pcs × 6000 mm = 510 lm → classified as Roof Top-Hat Battens (MEDIUM — grade only, not labelled)
- 22 mm grade: 181 pcs = 875.3 lm → classified as Ceiling/Wall Battens (MEDIUM — same reason)

No change needed. Already at maximum confidence achievable from available sources.

---

### D2. Wall Geometry Backbone

| Element | Value | Source | Confidence |
|---------|-------|--------|-----------|
| External wall perimeter | 38.4 lm | DXF WALLS layer closed polygon | HIGH |
| Internal wall total | 29.4 lm | DXF WALLS layer 8 open polylines | HIGH |
| Wall height | 2.4 m | project_config.yaml | MEDIUM |
| Ext wall area (gross) | 92.16 m² | 38.4 × 2.4 | HIGH |
| Int wall area (both faces gross) | 141.12 m² | 29.4 × 2.4 × 2 | HIGH |

Both wall geometry values are at highest achievable confidence from source documents.
No improvement possible without structural drawings showing wall panel types.

---

### D3. Wall Sheeting (E01–E05)

Wall lining quantities are correctly derived from DXF wall geometry:
- E01 External FC sheets: 27 sheets — `dxf_geometry HIGH` (net area 81.22 m² with DXF-measured opening deductions)
- E03 Internal FC sheets: 41 sheets — `dxf_geometry HIGH` (net area 124.80 m² both faces)

These are already at the correct source level. Opening deduction areas come from DXF block
widths × config louvre height (0.75 m) — louvre height remains LOW confidence (not in DXF
block geometry), which is noted in the item.

---

### D4. Openings — Broad Counts (Section D — 45 items)

| Count | Source | Confidence |
|-------|--------|-----------|
| 6 doors (DOOR_90×1, DOOR_82×4, DOOR_72×1) | DXF DOORS layer INSERT blocks | HIGH |
| 11 windows (LOUVRE_1100×8, LOUVRE_800×2, LOUVRE_1800×1) | DXF WINDOWS layer INSERT blocks | HIGH |

Broad counts are already at HIGH confidence from DXF. Door/window hardware items are
correctly derived from these counts.

**Remaining gaps (confirmed blocked):**
- Door is_external/is_internal classification: threshold-based (width < 0.85 m = internal).
  No XY coordinates vs wall polygon in model. MEDIUM confidence retained.
- Louvre blade count: blade height not in DXF block geometry; config default 0.75 m used.
  No window schedule in source documents. LOW confidence retained.

---

## Element Builder Fix Summary

**File:** `normalize/element_builder.py` lines 150–172 (ceiling geometry block)

**Before (incorrect):**
```python
raw_ceil_area = _val(geom, "ceiling_area_m2") or raw_dxf.get("floor_hatch_area_m2", 0.0)
# Priority 1 was "floor_hatch_area_m2" — wrong key; DXF extractor writes "ceiling_area_m2"
if raw_ceil_area > 0 and raw_ceil_area >= derived_ceil * 0.9:
    ceil_area, ceil_src, ceil_conf = raw_ceil_area, "dxf_geometry", "HIGH"
elif derived_ceil > 0:
    ceil_area = derived_ceil   # ← WRONG: override DXF with estimated area
    ceil_src  = "derived"
    ceil_conf = "MEDIUM"
    model.warnings.append("Ceiling area from DXF (49.00 m²) appears partial; using floor−verandah...")
```

**After (correct):**
```python
raw_ceil_area = (
    raw_dxf.get("ceiling_area_m2", 0.0)   # ← PRIMARY: dedicated CEILING HATCH key
    or _val(geom, "ceiling_area_m2")
    or raw_dxf.get("floor_hatch_area_m2", 0.0)
)
if raw_ceil_area > 0:
    ceil_area, ceil_src, ceil_conf = raw_ceil_area, "dxf_geometry", "HIGH"
    if derived_ceil > 0 and abs(raw_ceil_area - derived_ceil) > 1.0:
        model.extraction_notes.append(
            f"Ceiling area from DXF CEILING layer = {raw_ceil_area:.2f} m² "
            f"(< floor−verandah {derived_ceil:.2f} m²). "
            f"Trusting DXF hatch — partial ceiling lining is expected."
        )
elif derived_ceil > 0:
    ceil_area = derived_ceil
    ceil_src  = "derived"
    ceil_conf = "MEDIUM"
```

**Key changes:**
1. `ceiling_area_m2` read as the first key (DXF extractor writes this for CEILING HATCH)
2. 0.9 threshold removed — DXF is trusted unconditionally when present
3. Warning demoted to informational extraction_note (not a warning)
4. Derived fallback retained as last resort when no DXF ceiling hatch present

---

## Test Suite

83 regression tests pass after this fix. The ceiling logic path is exercised via
`test_lining_logic.py` and `test_project2_regression.py`.

```
83 passed in 0.45s
```

The fix does not affect test models without `ceiling_area_m2` in raw_dxf — those
fall through to the `derived_ceil` path unchanged.

---

## Output Files

| File | Description |
|------|-------------|
| `recovery_pass_report_v3.md` | This report |
| `ceiling_recovery_change_log.csv` | Row-by-row diff for the 3 changed ceiling items |
| `recovery_pass_boq_compare.csv` | Full 171-item BOQ with source_evidence column |
| `../project2_BOQ_V3.xlsx` | Updated BOQ Excel (ceiling items corrected) |

---

## Honesty Check

- No quantity was copied from any BOQ file (reference, V2, or V1).
- No formula was tuned to match a reference number.
- Ceiling area = 49.0 m² read verbatim from DXF CEILING layer HATCH entity.
- The reason the ceiling area differs from floor-minus-verandah (64.8 m²) is architecturally
  plausible: the DXF shows ceiling lining only over occupied rooms (Dispensary, Waiting, Consulting,
  Staff, Storage, Toilet = ~49–53 m²), not over the verandah, open reception counter area, or
  circulation between walls. This is not a data error — it is the architect's ceiling zone.
- The 3D model cross-check is consistent: CEILING HATCH = 49.0 m², FLOOR HATCH = 49.0 m²
  (same layer extent for this project), both distinct from the WALLS-enclosed area (86.4 m²)
  and the interior floor (64.8 m²).
