# BOQ Automation Pipeline — V1 Stable

**Version:** 1.0 (frozen 23 March 2026)
**Classification:** Quantity-first, custom-project-aware BOQ pipeline
**Role:** Benchmark version before DDC-based V2 rebuild

---

## What This Version Is

V1 is the first working version of the BOQ automation pipeline that correctly
handles custom projects (i.e. non-standard, non-G303 building designs). It
produces a clean, project-specific BOQ with quantity traceability for every line
item, without forcing the G303 workbook template onto unrelated projects.

It is the benchmark version that V2 will be measured against.

---

## Key Capabilities

### Project Classification
- Classifies projects as `standard_model` or `custom_project` using title-block
  text, filenames, and metadata
- Standard models: G201, G202, G302, G303, G403E, G404, G504E
- Custom projects: any design not matching the G-Range code set

### Custom-Project BOQ Output
- Clean workbook written from scratch — no G303 template inheritance
- Section names and item descriptions derived from project data, not from
  the approved BOQ row structure
- 17+ BOQ sections generated automatically from project geometry and structural data

### Quantity Traceability
Every BOQ item carries four traceability fields:

| Field | Description |
|---|---|
| `quantity_basis` | `measured` / `derived` / `provisional` / `manual_review` |
| `quantity_rule_used` | Exact formula, e.g. `ceil(92.16 m² × 1.05 ÷ 3.24 m²/sheet)` |
| `source_evidence` | Source reference, e.g. `dwg_geometry / ifc`, `derived: roof_area ÷ …` |
| `confidence` | `HIGH` / `MEDIUM` / `LOW` |

### Package-Based BOQ Mapping
8 construction packages are tracked end-to-end:

| Package | Content |
|---|---|
| Structure | Wall frame (LGS), floor panels/joists, roof trusses |
| Roof | Cladding, battens, sisalation, insulation, flashings, drainage |
| Openings | Doors and windows (per-type where schedule available) |
| Linings | FC wall sheets (ext/int), FC ceiling sheets |
| Finishes | Skirting, architraves, paint (provisional) |
| Services | Wet area waterproofing, sanitary fixtures, builder's work (provisional) |
| Stairs | Flights, risers/treads, balustrade, handrail |
| External | Verandah decking, handrail, site preparation |

### Completeness Reporting
- Per-package detection status printed to console at Step 7
- Package QA (measured/derived/provisional/manual counts per section) in JSON
  report and text summary
- Overall traceability stats: `boq_summary` in QA report

### Excel Output
- Qty Basis column with colour coding (green/amber/red/grey)
- Rule/Method column showing calculation formula
- Confidence column with colour coding
- Source Evidence column

---

## Known Limitations

### Structural Data
- Framecad IFC extraction works for member lengths (lm) but does not resolve
  exact stud spacing, plate quantities, or connection hardware counts
- Roof batten quantities are derived from area/spacing rules when no BOM is
  present — accuracy is ±20% without confirmed BOM
- Ceiling batten quantities are similarly derived

### Manual-Review Rate
- ~44% of items are `manual_review` on a typical custom project (no BOM)
- This is expected: the pipeline fills known scope gaps with provisional
  placeholders rather than guessing wrong quantities
- An estimator must review and complete these items

### Services / Finishes / Stairs
- Services items (wet area, sanitary, builder's work) are always provisional
  until a services schedule is provided
- Finishes (paint, skirting, architraves) use geometry-derived estimates —
  actual specification must be confirmed from project documents
- Stair sub-items (risers, treads, balustrade, handrail) require step count
  from drawings

### Scope
- Current output is estimator-assist level, not a fully autonomous final BOQ
- All quantities marked `manual_review` or `LOW` confidence must be checked
  before the BOQ is submitted
- Room areas are not extracted from this project's PDFs (pharmacy rooms have
  no area annotations in the available drawings)

---

## Main Pipeline Steps

```
Step 1   Scan input files
Step 2   Extract DWG/DXF geometry
Step 3   Extract PDFs (AI vision)
Step 4   Extract BOM / IFC
Step 5   Merge sources
Step 6   Classify project (standard_model / custom_project)
Step 7   Build neutral quantity model → output/json/{project}_quantities.json
Step 8   Load item library (reference only)
Step 9   Map quantities to BOQ items → output/json/{project}_boq_items.json
Step 10  Calculate quantities (standard_model) or use mapped_items (custom_project)
Step 11  Write BOQ workbook → output/boq/{project}_BOQ_{date}.xlsx
Step 12  Write Summary workbook → output/boq/{project}_Summary_{date}.xlsx
Step 13  Write QA report → output/reports/{project}_QA_{datetime}.json
```

---

## Key Source Files

| File | Purpose |
|---|---|
| `main.py` | 13-step pipeline orchestrator |
| `src/project_classifier.py` | Classify standard_model vs custom_project |
| `src/project_quantities.py` | Neutral quantity model builder |
| `src/boq_mapper.py` | Quantity model → BOQ items mapper |
| `src/item_library.py` | Approved BOQ reference library loader |
| `src/boq_writer.py` | Excel workbook writer (custom_project + standard_model) |
| `src/summary_writer.py` | Summary workbook writer |
| `src/qa_reporter.py` | QA JSON + text report generator |
| `src/ifc_extractor.py` | IFC structural member extractor (Framecad-aware) |
| `src/bom_extractor.py` | BOM/IFC dispatcher |
| `src/merger.py` | Multi-source merge |
| `src/dwg_extractor.py` | DWG/DXF geometry extraction |
| `src/pdf_extractor.py` | PDF schedule extraction (OpenAI vision) |
| `src/config.py` | Constants, paths, enums, rule loading |

---

## Running the Pipeline

```bash
python main.py --project "project 2" --yes
```

Or via the BAT launcher:
```
C:\Users\User\Desktop\Run BOQ Pipeline.bat
```

---

## Benchmark Reference

See `benchmarks/angau_pharmacy/` for the known V1 output snapshot for the
Angau Pharmacy project (project 2).

- 52 BOQ items
- 17 BOQ sections
- 8/8 packages detected
- 19 measured / 10 derived / 23 manual-review items
- PGK 111,075 estimated total (rates from approved reference library)
