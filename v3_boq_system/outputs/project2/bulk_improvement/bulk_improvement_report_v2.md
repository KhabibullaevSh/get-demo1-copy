# Bulk Item Improvement Report — Pass 2
**Project:** Angau Pharmacy (G303 / project2)
**Date:** 2026-03-26
**Pipeline version:** V3 BOQ
**Total items:** 172
**Changes from v1:** 5 rows (opening deductions applied to lining and skirting)

---

## Scope of Pass 2

Pass 2 targeted the nine highest-impact bulk items identified during the Phase 1 audit:

1. Floor panels
2. Floor joists / posts
3. FC wall and ceiling sheet counts
4. Skirting lengths
5. Roof battens / ceiling battens
6. Roof sheets
7. Major truss / frame totals
8. Internal / external wall lengths
9. Ceiling sheets

---

## Applied Improvements

### 1. Opening Deductions — External Wall FC Sheet Lining
**File:** `quantify/lining_quantifier.py`
**Source:** DXF block geometry (`dxf_blocks`, HIGH confidence for widths)

**Problem:** The exterior wall interior lining used gross wall area (38.4 × 2.4 = 92.16 m²) without deducting openings. Every door and window penetrates the full external wall thickness and leaves no lining area behind it.

**Classification heuristic:** The element builder marks all DXF doors as `is_external=True` by default (insufficient plan context to distinguish internal from external). For this 9.0 m × 7.2 m pharmacy, a width threshold of ≥ 0.85 m is used to identify the main entrance; doors < 0.85 m are assumed internal partition doors.

**Deduction computation (interior face of external walls):**

| Opening | Width (m) | Height (m) | Qty | Area (m²) | Source |
|---------|-----------|-----------|-----|-----------|--------|
| DOOR_90 | 0.920 | 2.04 | 1 | 1.877 | DXF block LINE geometry |
| WINDOW_LOUVRE_1100 | 1.080 | 0.75* | 8 | 6.480 | DXF block + config default |
| WINDOW_LOUVRE_800 | 0.799 | 0.75* | 2 | 1.199 | DXF block + config default |
| WINDOW_LOUVRE_1800 | 1.847 | 0.75* | 1 | 1.385 | DXF block + config default |
| **Total deduction** | | | | **10.941 m²** | |

*Louvre height 0.75 m = `lining.default_louvre_height_m` config value (louvres have no height in DXF block geometry).

**Result:** 92.16 − 10.941 = 81.22 m² net → `ceil(81.22 × 1.05 / 3.24)` = **27 sheets** (was 30)

---

### 2. Opening Deductions — Internal Wall FC Sheet Lining
**File:** `quantify/lining_quantifier.py`
**Source:** DXF block geometry (HIGH for widths, MEDIUM for internal-door classification)

**Problem:** Internal wall lining used both-face gross area without deducting door openings. Each internal partition door interrupts the lining on both faces.

**Deduction computation (both partition faces):**

| Opening | Width (m) | Height (m) | Qty | Faces | Area (m²) | Source |
|---------|-----------|-----------|-----|-------|-----------|--------|
| DOOR_82 | 0.820 | 2.04 | 4 | 2 | 13.426 | DXF block LINE geometry |
| DOOR_72 | 0.720 | 2.04 | 1 | 2 | 2.938 | DXF block LINE geometry |
| **Total deduction** | | | | | **16.363 m²** | |

**Basis for internal classification:** Both DOOR_82 and DOOR_72 are narrower than the 0.85 m external entrance threshold. In a pharmacy of this size (6 rooms), 4 × 820 mm doors and 1 × 720 mm door are consistent with internal room access doors.

**Result:** 141.12 − 16.363 = 124.76 m² net → `ceil(124.76 × 1.05 / 3.24)` = **41 sheets** (was 46)

---

### 3. Opening Deductions — Skirting Board
**File:** `quantify/lining_quantifier.py`
**Source:** DXF block geometry (HIGH for widths)

**Problem:** Skirting ran continuously at perimeter + internal wall lengths without deducting door openings. Skirting physically cannot run through a door opening.

**Deduction computation:**

| Opening | Width (m) | Qty | Faces | Deduction (lm) | Face |
|---------|-----------|-----|-------|----------------|------|
| DOOR_90 | 0.920 | 1 | 1 | 0.920 | External wall interior face |
| DOOR_82 | 0.820 | 4 | 2 | 6.560 | Both partition faces |
| DOOR_72 | 0.720 | 1 | 2 | 1.440 | Both partition faces |
| **Total deduction** | | | | **8.920 lm** | |

**Result:** 97.2 − 8.92 = **88.3 lm** (was 97.2)

---

## Summary of Changes (v1 → v2)

| Item | Unit | v1 | v2 | Change | Basis |
|------|------|----|----|--------|-------|
| External Wall Lining — FC Sheet | sheets | 30 | 27 | −3 | DXF opening deductions (door + windows) |
| External Wall Lining — FC Sheet Screws | boxes | 12 | 11 | −1 | Derived from net area |
| Internal Wall Lining — FC Sheet | sheets | 46 | 41 | −5 | DXF internal door deductions (both faces) |
| Internal Wall Lining — FC Sheet Screws | boxes | 18 | 16 | −2 | Derived from net area |
| Skirting Board | lm | 97.2 | 88.3 | −8.9 | DXF door gap deductions |

---

## Blocked Items — Cannot Improve Without Additional Source Documents

### Floor Panels (currently 21 + 9 = 30 nr)
**Blocker:** FrameCAD floor panel BOM schedule not available. Building footprint (9.0 m × 7.2 m) would accommodate 9 panels at 3.0 m × 2.4 m, but this requires bearer spacing confirmation. Current formula uses 0.6 m × 3.6 m panels (2.16 m²) from default config — geometry gives a different answer than the final BOQ. **Cannot resolve without the FrameCAD floor panel schedule.**

### Floor Joists
**Blocker:** Joist spacing (450 mm current assumption) not confirmed by BOM. Number of joists = `ceil(main_floor_width / spacing) + 1` per bay = 17 per 7.2 m span at 450 mm. No BOM verification source. **Cannot improve without floor framing plan or FrameCAD joist schedule.**

### Structural Posts / Members
**Status:** HIGH confidence — FrameCAD BOM provides exact lm quantities. No change needed. Truss total (1,143 lm), wall frame (792 lm), roof panel (336 lm) all sourced directly from BOM tabs.

### Ceiling Sheets (currently 24 sheets for 64.8 m²)
**Blocker:** Final BOQ has 42 sheets, implying a significantly larger ceiling area (including laundry annex extension). Laundry annex is not in DXF geometry. **Cannot match without architectural drawings for the extension.**

### Roof Battens (zone split)
**Blocker:** FrameCAD BOM provides batten total (1,001 lm) and per-grade entries, but no zone tags. Grade threshold split (≥35 mm = roof top-hat, <35 mm = ceiling/wall) is retained as best available. **No additional source document to validate zone split.**

### Roof Sheets (49 sheets)
**Status:** Formula correct. `ceil(106.6 × 1.05 / (0.762 × 3.0)) = 49`. Stock-length selection (3.0 m based on eaves run / roof area ratio) is source-backed. No change needed.

### Window Heights (all 0.0 m in element model)
**Blocker:** Louvre blade count and fly screen area both require a window schedule with frame height. Config default 0.75 m used for lining deductions only. **Cannot derive without window schedule or architectural elevation.**

---

## Regression Check
All 83 regression tests pass after changes.

```
83 passed in 0.43s
```

Test model (`_model()` in `test_lining_logic.py`) has no openings → deduction logic returns zero for all three items → existing quantity assertions (30 sheets, 23 sheets, 97.2 lm) remain valid for the test model.

---

## Honesty Check

- **No quantity was copied from the final BOQ.**
- **No formula was tuned to match a final BOQ target.**
- All changes derive directly from DXF block widths (HIGH confidence) and config defaults for louvre heights (where DXF has no height data).
- The door internal/external classification uses a width threshold (0.85 m) based on building type reasoning, not reverse-engineering. This is flagged as MEDIUM confidence.
- Blocked items remain blocked. Floor panel count, ceiling area, and window schedule items cannot be improved without the identified missing source documents.
