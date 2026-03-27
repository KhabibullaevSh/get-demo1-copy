# V3 BOQ — Bulk Item Improvement Report v1
**Project:** project2 (Angau Pharmacy — G303)
**Date:** 2026-03-26
**Pipeline output:** 172 items (was 171 after prior fixes)

---

## Engine Improvements Made

### IMPROVEMENT 1 — Floor Finish Area: Verandah Exclusion + Wet/Dry Split

| Field | Before | After |
|---|---|---|
| Item | Floor Finish Tiles / screed (1 row) | 2 rows: dry + wet |
| Quantity | 86.40 m2 (entire floor incl. verandah) | Dry: 60.30 m2 / Wet: 4.50 m2 |
| Unit | m2 | m2 |
| Confidence | HIGH | MEDIUM |
| Package | 50106 | 50106 |

**Source used:**
- Verandah area: dxf_geometry 21.60 m2 (HIGH confidence)
- Enclosed floor area: 86.40 minus 21.60 = 64.80 m2
- Room schedule: config/project_config.yaml room_schedule (6 rooms = 64.80 m2)
  - Dry: Dispensary 24.0 + Waiting 14.0 + Consulting 8.0 + Staff 6.5 + Storage 7.8 = 60.30 m2
  - Wet: Toilet 4.5 = 4.50 m2

**Why valid:** DXF floor polygon includes the verandah area. The verandah has a separate Verandah Decking / Slab row in 50113. Including it again in floor finish was a double-count. Room schedule in project_config.yaml sums exactly to 64.80 m2, enabling a genuine wet/dry split.

**Final BOQ QA:** Final BOQ lists vinyl plank (56.1 m2) + ceramic tile (5.8 m2) = 61.9 m2. Our split (60.3 + 4.5 = 64.8 m2) is slightly higher, consistent with room schedule including some corridor area. Source-driven value retained.

---

### IMPROVEMENT 2 — External Wall Interior FC Sheet: Package Reclassification

| Field | Before | After |
|---|---|---|
| Item | External Wall Lining FC Sheet | same |
| Quantity | 30 sheets | 30 sheets (unchanged) |
| Old package | 50113 External Cladding | - |
| New package | - | 50115 Internal Linings |

**Source:** DXF ext_wall_lm=38.40 m x h=2.4 m = 92.16 m2 -> ceil(92.16 x 1.05 / 3.24) = 30 sheets

**Why valid:** wall_lining_external represents FC sheet on the interior face of external walls (room-side sheeting). This is interior lining, not exterior cladding. 50115 (Internal Linings) is the correct destination. Previously, flat FC sheet appeared alongside FC Weatherboard in 50113 — an architectural mismatch.

**Impact on 50113:** Now clean — weatherboard, H-joiners, corner flashings, clips, wrap, verandah decking only.
**Impact on 50115:** Now contains all interior sheeting in one section (30 + 46 + 7 wet + 24 ceiling + 8 verandah soffit = 115 sheets total, all with individual sourcing).

---

### PRIOR FIXES (earlier session, included in this build)

| Fix | Change |
|---|---|
| Duplicate roof cladding m2 row removed | Was m2 + sheets, now sheets only |
| Gutter joiner formula corrected | 12 nr -> 3 nr (6m stock, run-count estimate) |
| Gutter display name collisions fixed | 3x SL-14 PVC Box Gutter -> 3 distinct names |
| BOM verification row removed from export | QA Check row no longer in 50107 |

---

## Items Investigated but Blocked

### Floor Panels: 21 nr (1.8kPa) + 9 nr (4kPa)
- FrameCAD BOM has no floor panel tab (panel_count=0 in element model)
- Missing: FrameCAD floor panel manufacturing schedule
- Status: BLOCKED — LOW confidence estimate retained

### Floor Joists: 100.80 lm + 43.20 lm
- No joist schedule in BOM
- Status: BLOCKED — derived from span x joist count at 450mm spacing

### External FC Weatherboard Count: 126 nr boards
- DXF gives ext_wall_lm=38.40 m (main building only)
- Laundry annex (~135 lm walls) not in source documents
- Status: BLOCKED — main building only

### Ceiling Sheets: 32 total (24 main + 8 verandah soffit)
- Gap to final BOQ: 32 vs 42 sheets
- Missing 10 sheets likely from laundry annex
- Status: Partially improved (verandah added), laundry BLOCKED

### Roof Batten Zone Split
- BOM has grade data only (grade 22 / grade 40), no zone tags
- Cannot confirm zone assignment without batten schedule
- Status: BLOCKED — grade-threshold split retained (grade >= 35mm = roof top-hat)

### Window Heights (louvre windows)
- All heights = 0 in element model (not in DXF block names)
- Affects fly screen area and blade count
- Missing: window schedule / manufacturer documentation
- Status: BLOCKED

---

## QA Comparison Table

| Item | Generated | Final BOQ | Unit | Status |
|---|---|---|---|---|
| Floor Finish Dry | 60.30 | ~56.1 | m2 | Close (+7%) |
| Floor Finish Wet | 4.50 | ~5.8 | m2 | Close (-22%) |
| Ceiling FC Sheets | 32 (24+8) | 42 | sheets | Mismatch - laundry BLOCKED |
| Ext Weatherboard area | 87.14 | - | m2 | No m2 row in final BOQ |
| Roof Cladding Sheets | 49 | - | sheets | Reasonable (3.0m stock) |
| Gutter Joiner | 3 | 3 | nr | ALIGNED |
| Gutter Eaves | 42.40 | - | lm | Measure correct |
| Downpipes | 6 | - | nr | Consistent |
| Roof Truss Frame | 1634.29 | - | lm | HIGH conf FrameCAD BOM |
| Wall Frame | 2250.69 | - | lm | HIGH conf FrameCAD BOM |
| Roof Battens Top-Hat | 85 nr x 6000mm | 27 x 5800mm | nr/len | Unit format mismatch |
| Floor Panels | 30 nr total | 9 nr | nr | Mismatch - no BOM schedule |
| Skirting Board | 97.20 | - | lm | Geometry-based |

---

## Output Files

| File | Description |
|---|---|
| boq_generated_bulk_improved_v1.xlsx | Updated BOQ workbook (172 items) |
| boq_generated_bulk_improved_v1_compare.csv | All 172 items flat CSV for comparison |
| boq_bulk_change_log_v1.csv | 5 rows changed vs prior benchmark |
| bulk_improvement_report_v1.md | This report |

---

## Regression Check

- Rows changed: 5 (1 removed, 2 package_moved, 2 added)
- Row count: 171 -> 172 (+1 net)
- Tests: 83/83 passed
- Export structure: Intact
- Unintended quantity changes: None

---

No quantities were copied from the final BOQ. All changes were based only on source extraction, source-supported derivation, or clearly flagged inference. The final BOQ was used only for QA cross-checking.
