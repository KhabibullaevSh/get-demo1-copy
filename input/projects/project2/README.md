# Project 2 — Angau Pharmacy

## Input Files

Place the following files in this directory (or in the legacy `input/project 2/` directory):

| File | Type | Role |
|------|------|------|
| `ANGAU PHARMACY 01_arch.dxf` | DXF | Floor area, wall perimeter, openings, verandah, stair evidence |
| `Angau Pharmacy.ifc` | IFC | Structural members (SHS posts, verandah frame), floor elements |
| `Angau Pharmacy-Layouts.pdf` | FrameCAD layout PDF | Structural BOM (LGS lm by tab), batten schedule |
| `Angau Pharmacy Summary.pdf` | FrameCAD summary PDF | Manufacturing totals verification |
| `ANGAU PHARMACY MARKETING SET.pdf` | Marketing PDF | Context only |

## Running the Pipeline

```bash
python -m v3_boq_system.main --project "project 2"
```

Or using the new project slug path:
```bash
python -m v3_boq_system.main --project project2
```

## Benchmark

Frozen at v3.0-project2-benchmark (155 items, 61 tests passing).

To verify no regressions after pipeline changes:
```bash
pytest v3_boq_system/tests/test_project2_regression.py -v
python tools/compare_benchmark_outputs.py --project project_2
```

## Known Data Gaps

- No machine-readable room schedule — config estimate used (LOW confidence)
- No FrameCAD floor panel tab — floor system inferred from IFC (LOW confidence)
- No mechanical/services schedule — AC placeholder only
- Truss lm discrepancy: IFC reports 1634 lm vs reference ~490 lm
- Batten count: BOM covers all zones, not roof-only — verify before ordering
