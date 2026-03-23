# Benchmark: Angau Pharmacy (project 2) — V1 Stable

**Run date:** 23 March 2026
**Pipeline version:** V1 stable
**Project type:** Custom project (pharmacy / commercial)
**Location:** Papua New Guinea

---

## Input Files

| File | Type | Notes |
|---|---|---|
| ANGAU PHARMACY 01_arch.dwg | DWG | Architectural floor plan |
| ANGAU PHARMACY 01_arch.dxf | DXF | Converted from DWG — used for geometry extraction |
| Angau Pharmacy_frameclad.dwg | DWG | Framecad structural drawing |
| ANGAU PHARMACY MARKETING SET.pdf | PDF (5pg) | Room schedule, elevations |
| Angau Pharmacy Summary.pdf | PDF (1pg) | Structural summary |
| Angau Pharmacy-Layouts.pdf | PDF (3pg) | Layout drawings |
| Angau Pharmacy.ifc | IFC (IFC2X3) | Framecad structural model |

---

## V1 Output Summary

| Metric | Value |
|---|---|
| Total BOQ items | 52 |
| BOQ sections | 17 |
| Packages detected | 8 / 8 |
| Measured items | 19 (36%) |
| Derived items | 10 (19%) |
| Manual review items | 23 (44%) |
| Estimated total | PGK 111,075 |
| Conflicts | 0 |
| Low-confidence items | 18 |

---

## Section Breakdown

| Section | Items |
|---|---|
| 50114 - DOORS, WINDOWS & GLAZINGS | 6 |
| 50124 - STAIRS, RAMPS, & BALUSTRADES | 1 |
| CEILING | 3 |
| CEILING LININGS | 1 |
| DOORS & WINDOWS | 4 |
| EXTERNAL WORKS | 3 |
| FINISHES | 5 |
| FLOOR | 2 |
| INSULATION | 2 |
| ROOF | 4 |
| ROOF CLADDING | 1 |
| ROOF DRAINAGE | 2 |
| ROOF STRUCTURE | 3 |
| SERVICES | 4 |
| STAIRS | 4 |
| WALL FRAMING | 5 |
| WALL LININGS | 2 |

---

## Package Completeness

All 8 packages detected OK:

| Package | Quantities | Notes |
|---|---|---|
| structure | 1 | Wall frame lm derived from DWG wall lengths (no BOM) |
| roof | 11 | Roof area 106.6 m². Cladding, sisalation, battens, drainage derived. |
| openings | 6 | 6 doors (3 types), 11 windows (3 types) from DWG/PDF |
| linings | 4 | FC sheet counts derived from wall/ceiling areas |
| finishes | 5 | Skirting, architraves, paint from geometry. 18-item finish schedule from PDF. |
| services | 4 | All provisional — no services schedule in drawings |
| stairs | 4 | 1 stair flight from PDF. Step count unknown — manual entry required. |
| external | 3 | Verandah 21.6 m² from DWG. Handrail and site prep provisional. |

---

## Key Geometry (from DXF/IFC)

| Dimension | Value | Source |
|---|---|---|
| Floor area | 86.4 m² | DWG polygon |
| Verandah area | 21.6 m² | DWG polygon |
| External wall length | 38.4 lm | DWG |
| Internal wall length | 29.4 lm | DWG |
| Roof area | 106.6 m² | DWG polygon |
| Roof perimeter | 42.4 m | DWG |
| Ridge length | 9.0 m | DWG |
| Doors | 6 | DWG blocks |
| Windows | 11 | DWG blocks |
| Stair flights | 1 | PDF schedule |

---

## Known Issues in This Benchmark

1. **Roof truss quantity is lm (1634.289 lm), not unit count** — Framecad IFC
   provides total member length, not truss count. This is correct data but may
   confuse estimators expecting a count. Confirm with Framecad schedule.

2. **No room areas extracted** — Angau Pharmacy PDF has 18 room names but no
   area annotations. Room area table shows 0 for all rooms.

3. **Services all provisional** — No services schedule in the drawing set.
   All 4 services items require manual entry before the BOQ can be priced.

4. **Stair step count unknown** — The stair flight was detected in the PDF
   but step count was not found. Stair risers/treads/balustrade/handrail
   all require manual entry.

5. **Wall frame derived (no BOM)** — The structural IFC (Framecad) was extracted
   but the member classifier returned 0 lm for wall frame due to the Framecad
   positional code naming scheme. Wall frame quantity is derived from DWG wall
   lengths (67.8 lm). Actual BOM from Framecad software would be higher fidelity.

---

## Benchmark Files

| File | Description |
|---|---|
| `v1_boq_items.json` | Full BOQ item list with all traceability fields |
| `v1_quantities.json` | Neutral quantity model (pre-mapping) |
| `v1_qa_report.json` | Full QA report including package_qa and boq_summary |
| `v1_qa_report.txt` | Human-readable QA text summary |
