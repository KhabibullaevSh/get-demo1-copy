# Project 3 — [Project Name TBD]

## Setup

1. Copy `config/project_template.yaml` to this directory as `project_config.yaml`
2. Fill in all [REQUIRED] and [REVIEW] fields
3. Place input files in this directory (DXF, IFC, FrameCAD PDFs)
4. Run: `python -m v3_boq_system.main --project project3`

## Input Files Required

| Priority | File type | What it provides |
|----------|-----------|-----------------|
| HIGH | `.dxf` | Floor area, wall layout, openings geometry |
| HIGH | FrameCAD BOM PDF | Structural frame quantities, batten schedule |
| MEDIUM | `.ifc` | Structural member types and dimensions |
| MEDIUM | FrameCAD floor panel PDF | Floor system panel schedule |
| LOW | Architectural PDF | Context and opening schedule |

## Benchmark

Not yet established. Run pipeline once, review output, then freeze:
```bash
python -m v3_boq_system.main --project project3
# Review output in v3_boq_system/outputs/project3/
# When satisfied, create benchmarks/project3_v3/project3_benchmark.json
```
