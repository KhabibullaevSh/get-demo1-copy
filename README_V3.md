# V3 BOQ System — Document-Truth Pipeline

**Version:** v3.0 (benchmark: v3.0-project2-benchmark)
**Status:** Project 2 (Angau Pharmacy) frozen and benchmarked. Ready for Project 3.

---

## Core Principle

All quantities are derived from project source documents only.
BOQ reference files are used for QA structure comparison — never as a quantity source.

Source priority (highest → lowest):
1. FrameCAD BOM / manufacturing summary
2. IFC model (structural members, floor elements)
3. PDF layout schedules (room, stair, door/window)
4. DXF geometry (floor area, wall perimeter, openings)
5. `project_config.yaml` fallback (LOW confidence, manual review required)

---

## Running Project 2 (Angau Pharmacy)

```bash
# Run the pipeline
python -m v3_boq_system.main --project "project 2"

# Outputs: v3_boq_system/outputs/project_2/
#   project_2_boq_items_v3.json   — 155-row BOQ with full traceability
#   project_2_BOQ_V3.xlsx         — colour-coded Excel export
#   project_2_qa_report_v3.json   — QA report
#   project_2_element_model.json  — normalised element model

# Verify no regressions vs frozen benchmark
pytest v3_boq_system/tests/test_project2_regression.py -v

# Compare output against frozen benchmark (detailed diff)
python tools/compare_benchmark_outputs.py --project project_2
```

Expected: 155 items, 11 sections, 61 regression tests passing.

---

## Starting a New Project (Project 3+)

### 1. Create project folder
```
input/projects/project3/
├── project_config.yaml    ← copy from config/project_template.yaml, fill in
├── [project].dxf
├── [project].ifc
├── [FrameCAD]-Layouts.pdf
└── [FrameCAD]-Summary.pdf
```

### 2. Copy and fill in the project template
```bash
cp config/project_template.yaml input/projects/project3/project_config.yaml
# Edit project_config.yaml: set project name, type, and review all [REQUIRED]/[REVIEW] fields
```

### 3. Run the pipeline
```bash
python -m v3_boq_system.main --project project3
```

### 4. Review output and freeze benchmark
Once the output is reviewed and QA passes, freeze the benchmark:
```bash
# Create benchmark metadata
# benchmarks/project3_v3/project3_benchmark.json

# Run regression tests
pytest v3_boq_system/tests/ -v
```

---

## What "Manual Review" Means

Items tagged `manual_review=true` require estimator verification before procurement:

| Confidence | Source | Action |
|------------|--------|--------|
| HIGH | Directly measured from source documents | Verify units and scope only |
| MEDIUM | Calculated from measured values | Check formula and deductions |
| LOW | Inferred from room type or config estimate | Verify from architectural drawings |
| PLACEHOLDER (qty=0) | No source data available | Obtain from specialist/engineer |

---

## Adding a Project Config

Per-project configs live at `input/projects/<slug>/project_config.yaml` and are merged
on top of the shared `v3_boq_system/config/project_config.yaml` at runtime.

Only include fields that differ from the defaults. Fields not present in the per-project
config inherit from the shared config.

**Never add quantities from a BOQ reference file to any config.**
Room areas, stair riser counts, and other config-sourced values must come from
architectural drawings or site surveys — not from a pre-existing BOQ.

---

## Benchmark Protection

Project 2 is protected by:
- `benchmarks/project2_v3/project2_benchmark.json` — frozen metadata
- `v3_boq_system/tests/test_project2_regression.py` — 18 regression tests
- `tools/compare_benchmark_outputs.py` — detailed comparison utility
- Git tag: `v3.0-project2-benchmark`

If a pipeline change affects Project 2 output:
1. Run `pytest v3_boq_system/tests/test_project2_regression.py -v`
2. Run `python tools/compare_benchmark_outputs.py --project project_2`
3. If the change is intentional and correct, update `benchmarks/project2_v3/project2_benchmark.json`
   and the `PROJECT2_BENCHMARK_SUMMARY.md` accordingly, then re-tag.

---

## Pipeline Architecture

```
input/projects/<slug>/
    *.dxf  *.ifc  *.pdf
         │
    [V2 extractors]  ←  raw geometry, structural, openings
         │
    element_builder  ←  normalized ProjectElementModel
         │
    quantifiers      ←  roof, linings, openings, floor, footings, stairs, services, cladding
         │
    assembly_engine  ←  procurement decomposition (sheets, fasteners, accessories)
         │
    boq_mapper       ←  assign BOQ sections (A–K)
         │
    qa_engine        ←  traceability, confidence, completeness checks
         │
    writers          ←  JSON + Excel
```

---

## Project 2 Benchmark Summary

| Section | Items | Primary Source |
|---------|-------|---------------|
| A - Structural Frame | 13 | FrameCAD BOM + IFC |
| B - Roof | 19 | DXF geometry + FrameCAD |
| C - Insulation | 2 | Calculated from areas |
| D - Openings | 41 | DXF INSERT blocks |
| E - Linings & Ceilings | 10 | Calculated from geometry |
| F - Finishes | 6 | Calculated from geometry |
| G - Floor System | 5 | IFC floor elements (inferred) |
| H - Substructure | 8 | DXF pad count + strip footings |
| I - Services | 27 | Room type templates (LOW confidence) |
| J - Stairs | 10 | Config estimate (LOW confidence) |
| K - External Works | 14 | DXF verandah + FC weatherboard |
| **Total** | **155** | |

Known discrepancies (see `PROJECT2_BENCHMARK_SUMMARY.md`):
- Truss lm: IFC 1634 vs reference ~490 (IFC over-classifies purlins)
- Battens: BOM 266 pcs vs reference ~47 (BOM covers all zones, not roof-only)
- SHS steel: IFC 96.69 vs reference ~24.9 (IFC may include non-structural)

---

*V3 BOQ System — document-truth extraction pipeline*
