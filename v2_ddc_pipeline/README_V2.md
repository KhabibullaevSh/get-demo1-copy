# V2 DDC BOQ Pipeline

## What V2 Is

Version 2 of the DDC Bill of Quantities pipeline.  V2 is a ground-up rewrite
that enforces a strict source hierarchy — all quantities come from measurable
geometry and model data, never from template BOQ files.

V1 remains frozen and untouched in the parent `boq-system/` directory.

---

## Non-Negotiable Rule

**Never copy quantities from any BOQ workbook.**

Quantities must only come from:
- DXF geometry (ezdxf + shapely)
- IFC IfcElementQuantity records (ifcopenshell)
- PDF text/schedules (AI-assisted but no quantity invention)
- FrameCAD BOM files

Existing BOQ xlsx files are for **stock-code lookup and description style ONLY**.

---

## Source Priority Hierarchy

| Category        | Priority order (highest last)                                    |
|-----------------|------------------------------------------------------------------|
| Structural lm   | fallback_rules → pdf_notes → dxf_geometry → ifc_geometry → framecad_bom |
| Openings count  | fallback_rules → dxf_blocks → ifc_doors_windows → pdf_schedule  |
| Roof geometry   | fallback_rules → pdf_notes → dxf_geometry → ifc_geometry        |
| Finishes areas  | fallback_rules → dxf_rooms → ifc_spaces → pdf_schedule          |

---

## Module Structure

```
v2_ddc_pipeline/
  main_v2.py                   Entry point
  config/
    settings.py                Paths, constants, priority lists
  src/
    source_inventory.py        Scan and classify input files
    project_classifier.py      Detect G-range model codes
    extractors/
      dxf_extractor.py         DXF geometry (ezdxf + shapely)
      ifc_extractor.py         IFC member quantities (ifcopenshell)
      pdf_extractor.py         Delegates to V1 AI extractor
      framecad_extractor.py    FrameCAD BOM parser
    normalizers/
      project_model.py         Merge extractors with priority rules
    quantity/
      derivation_rules.py      Pure derivation functions
      quantity_builder.py      Build neutral quantity model
    mapping/
      item_library.py          Load reference stock codes (no quantities)
      boq_mapper.py            Map quantities to BOQ rows
    qa/
      completeness_checker.py  Per-package completeness check
      benchmark_compare.py     Compare V2 vs V1 (structure only)
    writers/
      json_writer.py           Simple JSON output
      boq_writer_v2.py         BOQ Excel with V2 columns + colour coding
      qa_writer_v2.py          QA report (JSON + text)
  outputs/                     Generated outputs (gitignored)
  benchmarks/                  V1 benchmark files for comparison
```

---

## How to Run

```bash
cd v2_ddc_pipeline
python main_v2.py --project "project 2"
```

The `--project` argument must match a subdirectory inside `../input/`.

---

## Phase 1–3 Outputs

Running the pipeline produces (at minimum):

| File                              | Contents                                     |
|-----------------------------------|----------------------------------------------|
| `outputs/source_inventory.json`   | All input files classified by type/priority  |
| `outputs/project_model.json`      | Merged geometry + structural + openings data |
| `outputs/project_quantities.json` | Neutral quantity rows with full provenance   |
| `outputs/boq_items.json`          | BOQ-ready items with section/stock code      |
| `outputs/qa_report.json`          | Completeness + benchmark comparison          |
| `outputs/*_qa_report.txt`         | Human-readable QA summary                   |
| `outputs/*_BOQ_V2.xlsx`           | Colour-coded BOQ Excel (Phase 5)             |

---

## Known Remaining Gaps (Phase 4+)

- PDF AI extraction currently disabled in standalone mode (requires API key config)
- Internal wall length not extracted (needs room polygon analysis)
- Window/door types and sizes require PDF schedule parsing
- Services (electrical, plumbing) are provisional — no source data
- Roof pitch and slope correction not yet applied
- FrameCAD BOM parser handles xlsx/csv only; .txt BOM not yet supported

---

## Quantity Basis Colour Coding (Excel)

| Colour  | Hex     | Meaning                              |
|---------|---------|--------------------------------------|
| Green   | C6EFCE  | measured — from DXF/IFC/BOM directly |
| Amber   | FFEB9C  | derived — formula applied to measured|
| Red     | FFC7CE  | provisional — estimate / assumption  |
| Grey    | D9D9D9  | manual review required               |
