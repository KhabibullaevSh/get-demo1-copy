# Roof & Wall Geometry Recovery Pass Report
**Project:** Angau Pharmacy (G303 / project2)
**Date:** 2026-03-27
**Pipeline version:** V3 BOQ
**Pass scope:** Roof cladding/sheets, ridge, fascia/gutters, roof battens,
external/internal wall lengths, FC wall sheets, skirtings, broad openings counts

**Non-negotiable rules applied:**
- Final BOQ used for QA only — no back-solving
- No guessed structural detail — weak evidence stays manual_review / blocked
- Every changed quantity carries source_file, source_layer, derivation_method, confidence

---

## Summary

| Metric | Pre-pass | Post-pass | Delta |
|--------|----------|-----------|-------|
| BOQ items (section B) | 18 | **16** | −2 |
| Total BOQ items | 171 | **169** | −2 |
| Manual review items | 68 | **66** | −2 |
| Ridge length | 10.6 lm (perimeter fraction) | **4.8 lm** (derived from plan L−W) | −5.8 lm |
| Barge length | 8.5 lm (perimeter fraction) | **0** (hip roof, no barges) | removed |
| Roof sheets | 49 (3.0m stock, wrong run) | **33** (4.5m stock, correct rafter run) | −16 |
| Ridge screws | 36 nr | **16 nr** | −20 |
| Barge capping | 8.5 lm | **removed** | — |
| Barge end caps | 2 nr | **removed** | — |

---

## Category A — Real Quantity Improvements

### A1. Ridge Length — element_builder.py fix

**Before:** `ridge_length_m = 10.6 m`
Source: `round(roof_perim × 0.25, 1) = 42.4 × 0.25 = 10.6 m` — fraction estimate, no geometric basis.

**After:** `ridge_length_m = 4.8 m`
Source: Roof plan dimensions derived from DXF area + perimeter (quadratic solution):
```
roof_area = 106.6 m²,  roof_perim = 42.4 m
L + W = perim/2 = 21.2
L × W = 106.6
Discriminant = 21.2² − 4×106.6 = 449.44 − 426.4 = 23.04  →  √23.04 = 4.8
L_roof = (21.2 + 4.8)/2 = 13.0 m
W_roof = (21.2 − 4.8)/2 =  8.2 m
Hip ridge = L − W = 13.0 − 8.2 = 4.8 m
```

**Confidence:** MEDIUM — roof plan polygon area and perimeter are HIGH (DXF ROOF layer LWPOLYLINE).
Ridge = L − W assumes equal-pitch all-sides hip roof (consistent with `roof_type=hip` in config).
No ridge length is directly drawn in the DXF ROOF layer; this remains a derived quantity.

**Dependent items changed:**
- B04 Ridge Capping: 10.6 lm → **4.8 lm**
- B05 Ridge Cap Screws: 36 nr → **16 nr** (ceil(4.8/0.3))
- B06 Ridge End Caps: 2 nr → **2 nr** (unchanged — always 2 per ridge run)

---

### A2. Barge Length — removed for hip roof

**Before:** `barge_length_m = 8.5 m`
Source: `round(roof_perim × 0.20, 1) = 42.4 × 0.20 = 8.48 ≈ 8.5 m` — fraction estimate.

**After:** `barge_length_m = 0`
Justification: The project is configured as `roof_type=hip`. A hip roof has no gable ends;
therefore it has no barge boards or barge cappings. The 0.20×perimeter estimate was a generic
fallback that incorrectly assumed a gable roof.

**Dependent items removed:**
- B13 Barge Capping: 8.5 lm **→ removed**
- B14 Barge End Caps: 2 nr **→ removed**

**Note on hip flashings:** A hip roof has 4 hip rafter lines (corner diagonals). Hip flashing/capping
typically runs the full sloped hip length. Hip length = `sqrt((W/2)² + h²)` where h = ridge height.
Without known roof pitch, hip length cannot be derived from plan geometry alone.
Hip flashings are **blocked pending pitch data** — see Category C.

---

### A3. Roof Sheet Stock Length and Count

**Before:** 49 sheets — stock 3.0m
Derivation: `run = roof_area / eaves_lm = 106.6 / 42.4 = 2.51m → min_len = 2.66m → 3.0m stock`
This `area/eaves` formula gives a weighted average run across all slopes — correct for a monopitch
or simple gable, but wrong for a hip roof where the actual eave-to-ridge distance is `W_roof/2`.

**After:** 33 sheets — stock 4.5m
Derivation:
```
W_roof   = (eaves_lm/2 − ridge_lm) / 2 = (21.2 − 4.8) / 2 = 8.2 m
rafter_run = W_roof / 2 = 4.1 m   (horizontal eave-to-ridge distance)
min_sheet_len = 4.1 + 0.15 (lap) = 4.25 m → select 4.5m stock
sheet_count = ceil(106.6 × 1.05 / (0.762 × 4.5)) = ceil(32.64) = 33 sheets
```

**Source evidence:** `dxf_geometry: roof_area=106.60 m², eaves_lm=42.40 m, rafter_run=4.10 m`
**Confidence:** MEDIUM — area and eaves are HIGH; rafter_run derivation assumes equal-pitch hip.
`manual_review=True` retained — sheet profile and exact length must be confirmed from spec.

---

## Category B — Decomposition Improvements

None in this pass. All changes were quantity corrections, not decompositions.

---

## Category C — Blocked (missing source docs)

| Item | Why blocked |
|------|-------------|
| Hip flashings (4 hips) | Hip rafter length requires roof pitch. Pitch not in DXF or config. Cannot derive hip flashing lm without pitch or section drawing. |
| Ridge height | No section drawing or elevation. Pitch/height not stored in DXF ROOF polygon. |
| Gutter layout per side | DXF eaves polygon is the full perimeter; no gutter plan showing which sides are connected. Total 42.4 lm is correct; side breakdown blocked. |
| Roof sheet profile | No spec or schedule in source docs. Profile (Custom Orb, Trimdek, etc.) not determinable. |
| Roof pitch | Not in DXF, not in config, not in FrameCAD BOM. Needed for hip flashing and precise sheet length. |

---

## Category D — No-Change Confirmations

### D1. Roof Battens (A07–A10, section B routed to A)

All batten items confirmed correct. No changes made.

| Item | Value | Source | Confidence |
|------|-------|--------|-----------|
| Batten total | 266 pcs / 1385.3 lm | FrameCAD BOM BATTEN schedule | HIGH |
| Roof top-hat battens (≥35mm grade): G40 | 85 pcs × 6000mm = 510.0 lm | FrameCAD BOM | MEDIUM (zone inferred from grade) |
| Ceiling/wall battens (<35mm grade): G22 | 181 pcs = 875.3 lm | FrameCAD BOM | LOW (zone inferred from grade) |

Zone split inferred from batten grade (≥35mm = roof, <35mm = ceiling/wall). FrameCAD BOM does not
tag zones explicitly. Grade inference is confirmed correct methodology; no improvement possible
without a labelled batten layout drawing.

---

### D2. Eaves, Fascia, and Gutters

All eaves-derived items confirmed correct. No changes made.

| Item | Value | Source | Confidence |
|------|-------|--------|-----------|
| Eaves total | 42.4 lm | DXF ROOF perimeter (all-sides hip) | HIGH |
| Fascia Board (B07) | 42.4 lm | = eaves_lm | MEDIUM |
| Birdproof Foam (B09) | 42.4 lm | = eaves_lm | MEDIUM |
| Eaves Gutter (B10) | 42.4 lm | = eaves_lm | MEDIUM |
| Gutter Joiners (B11) | 3 nr | formula from eaves_lm | MEDIUM |
| Gutter Stop Ends (B12) | 6 nr | formula from eaves_lm | MEDIUM |

For a hip roof, eaves run on all 4 sides = full perimeter of the roof plan = 42.4 lm. Confirmed.

---

### D3. External Wall Geometry

| Element | Value | Source | Confidence |
|---------|-------|--------|-----------|
| Ext wall perimeter | 38.4 lm | DXF WALLS layer closed polygon | HIGH |
| Wall height | 2.4 m | project_config.yaml | MEDIUM |
| Ext wall gross area | 92.16 m² | 38.4 × 2.4 | HIGH |

Already at maximum achievable confidence from source documents. No change.

---

### D4. Internal Wall Geometry

| Element | Value | Source | Confidence |
|---------|-------|--------|-----------|
| Int wall total | 29.4 lm | DXF WALLS layer — 8 open polylines | HIGH |
| Int wall gross area (both faces) | 141.12 m² | 29.4 × 2.4 × 2 | HIGH |

Already at maximum achievable confidence. No change.

---

### D5. FC Wall Sheets (E01, E03)

| Item | Qty | Derivation | Confidence |
|------|-----|-----------|-----------|
| E01 Ext FC sheets | 27 | DXF: net=81.22 m² (gross 92.16 − openings 10.94) / 3.24 × 1.05 | MEDIUM |
| E03 Int FC sheets | 41 | DXF: net=124.80 m² (gross 141.12 − int doors 16.32) / 3.24 × 1.05 | HIGH |

Opening deductions for E01/E03 use DXF door/window widths × config wall height — correct methodology.
No improvement possible without confirmed louvre heights (currently 0.75m config default).

---

### D6. Skirtings (F01)

| Item | Qty | Derivation | Confidence |
|------|-----|-----------|-----------|
| F01 Skirting Board | 88.3 lm | ext_wall(38.4) + int_wall×2(58.8) − door_gaps(8.92) | MEDIUM |

Correctly derived from DXF wall geometry. Door gap deductions use DXF block widths. No change.

---

### D7. Broad Door and Window Counts

| Count | Source | Confidence |
|-------|--------|-----------|
| 6 doors (DOOR_90×1, DOOR_82×4, DOOR_72×1) | DXF DOORS layer INSERT blocks | HIGH |
| 11 windows (LOUVRE_1100×8, LOUVRE_800×2, LOUVRE_1800×1) | DXF WINDOWS layer INSERT blocks | HIGH |

Broad counts at maximum confidence. No change.

---

## Code Changes Summary

### `normalize/element_builder.py` — Roof geometry (lines ~185–218)

Replaced perimeter-fraction estimates with quadratic-derived roof plan dimensions:
```python
# Quadratic solve: L × W = roof_area, 2(L+W) = roof_perim
half_perim = roof_perim / 2
disc = half_perim ** 2 - 4.0 * roof_area
roof_long  = (half_perim + sqrt(disc)) / 2   # L = 13.0 m
roof_short = (half_perim - sqrt(disc)) / 2   # W = 8.2 m

if roof_type == "hip":
    ridge_est = max(0.0, round(roof_long - roof_short, 1))  # = 4.8 m
    barge_est = 0.0   # no barges on a hip roof
```

### `assemblies/assembly_engine.py` — `_roof_sheet_count` and `apply_all_roof_assemblies`

Added `rafter_run_m: float | None = None` parameter. When provided:
```python
min_len   = rafter_run_m + top_lap_m  # 4.1 + 0.15 = 4.25 m
sheet_len = 4.5m stock   # first stock length ≥ 4.25m
```

### `quantify/roof_quantifier.py` — Hip rafter run derivation

Added pre-assembly computation of `rafter_run_m` from corrected ridge and eaves:
```python
if roof_type == "hip" and ridge_lm > 0 and eaves_lm > 0:
    W_roof       = (eaves_lm / 2 - ridge_lm) / 2   # = 8.2 m
    rafter_run_m = W_roof / 2                        # = 4.1 m
```

---

## Test Suite

83 regression tests pass after all three changes.
Existing `test_cladding_sheet_count_row_emitted` still passes — that test calls
`apply_all_roof_assemblies` without `rafter_run_m`, so it gets the old `area/eaves` path
(3.0m stock, 49 sheets). The new path is triggered only when `quantify_roof` computes
`rafter_run_m` from corrected ridge geometry.

```
83 passed in 0.67s
```

---

## Output Files

| File | Description |
|------|-------------|
| `roof_wall_recovery_report_v3.md` | This report |
| `roof_recovery_change_log.csv` | Row-by-row diff for changed/removed items |
| `roof_wall_boq_compare.csv` | Full 169-item BOQ with source_evidence column |
| `../project2_BOQ_V3.xlsx` | Updated BOQ Excel |

---

## Honesty Check

- No quantity was copied from any BOQ file.
- No formula was tuned to match a reference number.
- Ridge = 4.8m is a geometric derivation from DXF-measured area (106.6 m²) and perimeter (42.4 m²).
- Rafter run = 4.1m is derived from the same geometry; no additional source was consulted.
- The barge=0 conclusion follows from `roof_type=hip` in the project config — a hip roof has no
  gable ends and therefore no barge boards. This is structural reasoning, not number matching.
- Hip flashings remain blocked (C-category) because hip rafter length requires pitch, which is
  not available in any current source document.
