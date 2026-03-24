# Project 2 — Angau Pharmacy — V3 BOQ Benchmark Summary

**Version:** v3.0-project2-benchmark
**Date:** 2026-03-24
**Pipeline:** V3 document-truth BOQ system
**Total BOQ items:** 155 across 11 sections
**Tests passing:** 61 / 61

---

## Files in This Pack

| File | Description |
|------|-------------|
| `project_2_BOQ_V3.xlsx` | Final BOQ — colour-coded by section, per-row traceability |
| `project_2_boq_items_v3.json` | Machine-readable BOQ (155 rows with full provenance fields) |
| `project_2_element_model.json` | Normalised element model from all source documents |
| `project_2_qa_report_v3.json` | QA report — traceability, confidence, package completeness |
| `project2_benchmark.json` | Benchmark metadata — frozen for regression protection |

---

## Source Documents Used

| File | Type | Used for |
|------|------|----------|
| `ANGAU PHARMACY 01_arch.dxf` | DXF drawing | Floor area, wall perimeter, openings, verandah, stair evidence |
| `Angau Pharmacy.ifc` | IFC model | Structural members (SHS posts, verandah frame), floor elements |
| `Angau Pharmacy-Layouts.pdf` | FrameCAD layout PDF | Structural BOM (LGS lm by tab), batten schedule |
| `Angau Pharmacy Summary.pdf` | FrameCAD summary PDF | Manufacturing totals verification |
| `ANGAU PHARMACY MARKETING SET.pdf` | Marketing PDF | Context only — no extractable data |

**Not available in this project:**
- Machine-readable room schedule (none in any source)
- FrameCAD floor panel tab (not present in this BOM)
- Mechanical/services schedule
- Architectural PDF (image-based only)

---

## What the System Successfully Measured

**Measured directly from source documents (22 items, HIGH confidence):**
- Floor area: 86.4 m² (DXF WALLS polygon)
- Roof area, eaves, ridge, barge lengths (DXF ROOF polygon)
- External wall perimeter: 38.4 lm (DXF)
- Verandah area 21.6 m² and perimeter 20.4 lm (DXF VERANDAH polygon)
- All structural frame lm: roof panel 481.74, truss 1634.29, wall 2250.69, battens 1385.3 (FrameCAD BOM)
- Batten families: G22×4800mm ×176, G22×6100mm ×5, G40×6000mm ×85 (FrameCAD BOM)
- Door marks and counts: DOOR_90 ×1, DOOR_82 ×4, DOOR_72 ×1 (DXF INSERT blocks)
- Window marks and counts: LOUVRE_1100 ×8, LOUVRE_800 ×2, LOUVRE_1800 ×1 (DXF INSERT blocks)
- Pad footing count: 15 nr (DXF STRUCTURE layer)
- Verandah decking: 21.6 m² (DXF)

**Calculated from measured values (91 items, MEDIUM/HIGH confidence):**
- All roof assembly items (cladding 49 sheets at 3.0m stock, ridge, gutter, downpipes, screws)
- External wall area for insulation and paint (92.16 m²)
- Internal wall area for lining and paint (141.12 m² — both faces via FrameCAD face=2)
- Ceiling area 64.8 m² (derived from floor − verandah)
- All lining sheet counts (FC external 30, internal 46, ceiling 24 sheets)
- All opening flashings (head + sill per mark)
- Louvre blade counts per mark (64 + 16 + 8 = 88 nr total at 750mm default height)
- Door/window hardware (hinges, locksets, door stops, frames)
- Architrave (door 36 lm, window 52.8 lm)
- Skirting (97.2 lm)
- External cladding (87.14 m², 126 boards, clips, screws, sarking)
- Strip footings (ext 38.4 lm, int 29.4 lm) and concrete volumes
- Stair components (treads, newels, balustrade, handrail)
- Access ramp: 8.82 m² at 1:14 gradient from actual stair height (3 × 175mm = 525mm)

**Inferred from room type templates (37 items, LOW/MEDIUM confidence):**
- Dispensary: hand basin, tapware, cold room/refrigeration allowance, builder's works
- Consultation: hand basin, tapware, builder's works
- Toilet: WC pan, cistern, basin, accessories, waterproofing 5.78 m², wall tiling 15.3 m²
- Whole-building: switchboard, water meter, HWS, smoke detectors (3 nr)

---

## What Remains Manual Review (58 items)

| Category | Items | Reason |
|----------|-------|--------|
| Structural fixing schedule | 1 placeholder | Requires FrameCAD connection report — 256+ items in reference |
| Floor panel families | 5 items inferred | No FrameCAD floor tab — 1.8kPa/4kPa panel schedule not extracted |
| Room-based services | 17 items | Room schedule from config estimate only — no source schedule |
| AC/mechanical | 2 placeholders | No mechanical schedule in any source document |
| Stair assembly | 7 items | Stair config estimate (3 risers) — verify from architectural drawings |
| Wet area items | 3 items | Tiling and waterproofing from room type — no finish schedule |
| Substructure volumes | 4 items | Footing depths from config defaults — no geotechnical report |
| External cladding detail | 4 items | H-joiners and corner count estimated — verify from façade drawings |

---

## Placeholders (qty = 0, must be filled before ordering)

1. **Structural Fixings & Connectors** — obtain full FrameCAD fixing schedule (screws, bolts, anchors, brackets, triple grips, grommets)
2. **Bulk Earthworks** — no site survey; provisional
3. **Air Conditioning / Mechanical Ventilation** — obtain from mechanical engineer
4. **Exhaust Fan — Wet Area** — confirm with architectural drawings
5. **Site Preparation** — provisional; no site survey in sources

---

## Key QA Warnings for Estimators

> **FrameCAD Truss lm discrepancy:** IFC reports 1634.29 lm; reference shows ~490 lm.
> IFC likely over-classifies purlins and rafter members as trusses.
> Verify member classification against FrameCAD manufacturing summary before ordering.

> **Batten count discrepancy:** FrameCAD BOM reports 266 pieces (1385.3 lm) across all zones.
> Reference shows ~47 roof battens (41mm top-hat × 5800mm).
> BOM entries may include wall and ceiling batten zones.
> Reconcile scope before ordering — confirm which entries are roof-only.

> **SHS steel lm discrepancy:** IFC reports 96.69 lm; reference shows ~24.9 lm (3 × 5.8m posts + beams).
> IFC may include non-structural or secondary members classified as SHS.
> Verify steel post and beam schedule from structural drawings.

> **Floor system inferred only:** No FrameCAD floor panel tab available for this project.
> Reference has 9 × 1.8kPa panels + 2 × 4kPa panels with specific joist counts.
> Obtain FrameCAD floor panel schedule before ordering floor system components.

> **Louvre blade count:** Window height not in DXF block data — 750mm default used.
> Verify actual frame height from window schedule. Blade count may change.

---

## Pipeline Invariants (all confirmed)

- No quantities sourced from any BOQ reference file
- No Z-Unclassified rows
- All rows have source_evidence, quantity_basis, derivation_rule
- All placeholders have qty=0 and manual_review=True
- All inferred items have confidence=LOW and manual_review=True
- Template contamination check: PASSED

---

*Generated by V3 BOQ Pipeline — v3.0-project2-benchmark*
