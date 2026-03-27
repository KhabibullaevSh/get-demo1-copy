# Floor Panel Recovery Report — DWG Extraction Pass
**Project:** Angau Pharmacy (G303 / project2)
**Date:** 2026-03-26
**Pipeline version:** V3 BOQ
**Source DWG:** `Angau Pharmacy_frameclad.dwg` (AC1032 / R2018, 4.3 MB)
**Conversion tool:** LibreDWG 0.13.4 `dwg2dxf.exe`
**Extractor module:** `v2_ddc_pipeline/src/extractors/framecad_floor_dwg.py`

---

## Problem Statement

The V3 pipeline previously sourced floor panel quantities from `project_config.yaml`
(`floor_panel_schedule` entries). This produced LOW-confidence area-derived estimates
using an incorrect panel size (0.6 m × 3.6 m = 2.16 m²). The result was 21 + 9 = 30
panels split across two load-class zones — a number not supported by any source document.

The actual FrameCAD floor cassette schedule was locked inside the binary DWG file
(`Angau Pharmacy_frameclad.dwg`). This pass extracts that data and replaces the
config-derived estimates with DWG-backed quantities.

---

## Data Source — DWG Inspection

### DWG Structure
- Format: AutoCAD DWG AC1032 (R2018), binary
- Conversion: LibreDWG `dwg2dxf.exe` → 19.7 MB DXF
- ezdxf loaded 2911 modelspace entities; 831 entities in `Layouts` paper space
- Floor data found in: **`Layouts` paper space → `Onpage FLayout` layer**

### Floor Member Summary (from `Onpage FLayout` — HIGH confidence)

| Member | Profile | Length (mm) | Qty per panel | Type | Confidence |
|--------|---------|-------------|---------------|------|-----------|
| E1 | 150P41-115-500 | 3000 | 1 | Edge beam | HIGH |
| E2 | 150P41-115-500 | 3000 | 1 | Edge beam | HIGH |
| J1 | 150S41-095-500 | 2394 | 7 | Joist | HIGH |
| S1 | 150S41-115-500 | 2394 | 1 | Stringer | HIGH |
| S2 | 150S41-115-500 | 2394 | 1 | Stringer | HIGH |

Source: `Angau Pharmacy_frameclad.dwg` → `Layouts` paper space → `Onpage FLayout` TEXT entities.
All member IDs, profiles, lengths, and per-panel counts read directly from the DWG schedule text.

### Panel Geometry Derivation

- **Panel width** = E1/E2 edge beam length = **3000 mm** (HIGH confidence — direct DWG read)
- **Panel depth** = J1/S1/S2 span length = **2394 mm** (HIGH confidence — direct DWG read)
- **Panel area** = 3.0 m × 2.394 m = **7.182 m²** per panel

### Panel Count Derivation (MEDIUM confidence)

The DWG does not show a floor plan layout with individually labeled cassettes.
Panel count is derived from floor area:

```
floor_area_main = 64.8 m²   (from DXF architectural plan, HIGH confidence)
panel_area      = 7.182 m²  (from DWG member schedule, HIGH confidence)
raw count       = 64.8 / 7.182 = 9.02
panel_count     = 9 panels  (MEDIUM confidence — area derivation)
```

**Grid cross-check:** 9000 mm / 3000 mm = 3 panels along length × 7200 mm / 2394 mm ≈ 3 panels
along width → 3 × 3 = **9 panels** ✓ (7200 - 3 × 2394 = 18 mm residual, within connection tolerances)

**3D model cross-check:** 388 Floor 3D J1 LWPOLYLINE entities in modelspace.
9 panels × 7 joists = 63 joists. 388 / 63 ≈ 6.2 cross-section segments per joist. Plausible
for a 150mm C-section profile drawn with 5–7 straight segments. Consistent with 9 panels.

**Confidence level: MEDIUM** — The panel dimensions come from the DWG schedule (HIGH),
but the count is geometry-derived (no explicit panel count in the DWG). Manual verification
against the FrameCAD floor panel layout drawing is required.

---

## BOQ Changes (pre-DWG config-derived → DWG-derived)

### Removed (config-derived rows replaced)

| Item | Was (config) | Reason for removal |
|------|-------------|-------------------|
| Floor Panel — Standard Load (1.8kPa) | 21 nr [LOW] | Incorrect panel size (0.6×3.6m). DWG gives actual cassette dimensions. |
| Floor Panel — High-Load (4kPa) | 9 nr [LOW] | No DWG evidence for load-zone split. Single panel type in schedule. |
| Floor Joist LGS — 1.8kPa Zone | 100.8 lm [LOW] | Replaced by DWG-derived J1 member rows. |
| Floor Joist LGS — 4kPa Zone | 43.2 lm [LOW] | Replaced by DWG-derived J1 member rows. |
| Floor Bearer (pair) — 1.8kPa Zone | 3 nr [LOW] | No bearer evident in DWG floor member schedule. |
| Floor Bearer (pair) — 4kPa Zone | 2 nr [LOW] | No bearer evident in DWG floor member schedule. |

### Added (DWG-derived rows)

| Item | Unit | Quantity | Confidence | Derivation |
|------|------|----------|-----------|------------|
| Floor Cassette Panel — 3000mm × 2394mm | nr | **9** | MEDIUM | floor_area / panel_area |
| Floor Joist (J1) — 150S41-095-500 × 2394mm | nr | **63** | MEDIUM | 7/panel × 9 panels |
| Floor Joist (J1) — 150S41-095-500 (total lm) | lm | **150.82** | MEDIUM | 63 × 2394mm |
| Floor Edge Beam (E1+E2) — 150P41-115-500 × 3000mm | nr | **18** | MEDIUM | 2/panel × 9 panels |
| Floor Stringer (S1+S2) — 150S41-115-500 × 2394mm | nr | **18** | MEDIUM | 2/panel × 9 panels |

### Unchanged

| Item | Unit | Quantity | Note |
|------|------|----------|------|
| Floor Sheet (FC / plywood) | sheets | 24 | Unchanged — same floor area |
| Floor Sheet Fixing Screws | boxes | 2 | Unchanged — derived from sheet count |

---

## What Became Recoverable vs Blocked

### Recovered ✓
- **Panel dimensions** (HIGH): E1/E2 length = 3000 mm; J1/S1/S2 length = 2394 mm
- **Panel count** (MEDIUM): 9 panels from area / cassette area
- **Joist profile and count** (HIGH profile/qty-per-panel; MEDIUM total): J1 150S41-095-500 × 7/panel
- **Edge beam profile and count** (HIGH profile; MEDIUM total): E1+E2 150P41-115-500 × 2/panel
- **Stringer profile and count** (HIGH profile; MEDIUM total): S1+S2 150S41-115-500 × 2/panel

### Still Blocked ✗
- **Load-class zone split (1.8 kPa vs 4 kPa):** The DWG floor member schedule shows
  a single panel type. No load-zone tags are present in the DWG. Cannot partition
  joists/panels into load zones without a structural load map or engineer confirmation.
  **Confidence stays MEDIUM for total quantities; zone split is BLOCKED.**

- **Bearer schedule:** No bearers (B-type members) appear in the DWG floor member
  schedule. FrameCAD cassette systems may not use traditional bearers — cassettes sit
  on sub-floor frame or piers directly. Cannot emit bearer rows without explicit evidence.

- **Floor panel layout drawing:** The DWG Layouts paper space contains a `Panel Stud`
  layer (433 entities) and wall panel layout views, but no floor cassette plan view
  with labeled panels. The `Onpage FLayout` table gives per-panel member data only.

---

## Honesty Check

- **No quantity was copied from the final BOQ.**
- **No formula was tuned to match a BOQ target.**
- Panel dimensions are read verbatim from the DWG `Onpage FLayout` schedule text (HIGH confidence).
- Panel count is derived from floor area (DXF) / panel area (DWG) with explicit derivation shown.
- The 3D modelspace cross-check (388 J1 entities / 63 joists ≈ 6.2 segments/joist) is
  consistent with 9 panels but does not independently confirm the count.
- Blocked items (load zones, bearers) remain blocked.

---

## Regression Check

All 83 regression tests pass after implementation.

```
83 passed in 0.44s
```

New code paths (DWG extractor, element_builder CASE 2.5, quantifier CASE 0.5) do not affect
the test model — test models have no `dwg_floor_panel_count` in `raw_framecad`, so they
route through existing CASE 2/3 paths unchanged.
