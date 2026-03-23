# Changelog — BOQ Pipeline V1 Stable

All significant changes made to the pipeline before the V1 freeze on 23 March 2026.

---

## [V1.0 — 23 March 2026] — Freeze

### Fixed
- **G303 contamination for custom projects**: custom projects no longer inherit
  the G303 workbook template, row structure, section titles, or blank rows.
  `boq_writer.py` now routes on `project_mode`: `custom_project` always calls
  `_write_full_workbook()` (clean workbook), `standard_model` may use the
  approved BOQ template only when the file exists.

- **Project classification moved after extraction/merge**: `classify_project()`
  is now Step 6 (after Step 5 merge), not Step 2. This means classification
  uses title-block data from all extracted sources rather than guessing from
  filenames alone.

- **Approved BOQ loaded as read-only reference library only**: for custom
  projects, the approved BOQ is loaded into `item_library` for stock-code and
  description lookup only. It is never copied as a row template.

- **Library match contamination fixed**: added `_SKIP_LIBRARY_MATCH` set (22
  element types) that prevents fuzzy library matches from overriding clean
  descriptions for geometry measurements, derived quantities, and hardware items.
  Raised fuzzy-match threshold from 0.35 to 0.55.

- **Keyword category map tightened**: FC ceiling sheets no longer match
  "Ceiling Batten" library entries; ceiling batten entries no longer match
  roof batten entries.

- **IFC Framecad member extraction**: added `_FRAMECAD_PATTERN` regex to
  `ifc_extractor.py` for positional section-code naming (e.g. `89S41-075-500`).
  Mark-code prefix classification: `W/B/T` → wall_frame, `R` → roof_truss.
  Millimetre-to-metre conversion applied for Framecad member lengths.

- **`qty = None` fallback removed**: `boq_writer.py` no longer substitutes an
  approved-BOQ quantity when `calc_qty` is None. Items without a source
  quantity are written with `qty = None`, `confidence = LOW`,
  `notes = "No source data — manual entry required"`.

### Added

- **`src/project_classifier.py`**: new module. Classifies projects as
  `standard_model` or `custom_project` using title-block text, filenames, and
  metadata. Score thresholds: HIGH (≥3) / MEDIUM (≥2) → `standard_model`;
  LOW → `custom_project`.

- **`src/project_quantities.py`**: new module. Builds a neutral quantity model
  from merged project data without imposing G303 template structure. Produces:
  - Direct quantities: floor area, wall lengths, roof area, structural members
  - Derived quantities: FC sheet counts, sisalation rolls, batten runs,
    gutter/fascia from perimeter, downpipe estimates, cornice/skirting/architraves
  - Manual-review placeholders: services, stairs sub-items, external works
  - 8-package completeness report: structure, roof, openings, linings, finishes,
    services, stairs, external

- **`src/boq_mapper.py`**: new module. Maps neutral quantity model entries to
  BOQ-ready item dicts. Key features:
  - `_ELEMENT_SECTION` dict: 50+ `(item_group, element_type)` → BOQ section mappings
  - `_ELEMENT_DESC` dict: 40+ element types → clean human-readable descriptions
  - `_SKIP_LIBRARY_MATCH` set: 22 element types that keep their own descriptions
  - `quantity_basis` and `quantity_rule_used` fields on every output item
  - Compatibility aliases (`qty`, `source`, `category`) for `boq_writer`

- **`src/item_library.py`**: new module. Loads approved BOQ as a read-only
  reference dictionary (307 items). Provides `find_by_keyword_category()` for
  construction-category-aware library lookup.

- **`src/source_inventory.py`**: new module. Tracks all input files with parse
  status, discipline classification, and parse warnings.

- **`src/ifc_extractor.py`**: new module (ported from Downloads, then extended).
  Full IFC member extraction with Framecad pattern recognition. Returns
  `geometry` and `structural` sub-dicts merged into the main pipeline.

- **Quantity traceability fields** — every BOQ item now carries:
  - `quantity_basis`: `measured` | `derived` | `provisional` | `manual_review`
  - `quantity_rule_used`: formula string (e.g. `ceil(92.16 m² × 1.05 ÷ 3.24 m²/sheet)`)
  - `source_evidence`: raw source reference
  - `confidence`: `HIGH` | `MEDIUM` | `LOW`

- **Excel Qty Basis column**: colour-coded column in BOQ workbook
  (green = measured, amber = derived, red = provisional, grey = manual review).

- **Excel Rule/Method column**: shows the calculation formula used for derived
  and manual-review items.

- **Package completeness console output**: Step 7 prints per-package status
  (structure / roof / openings / linings / finishes / services / stairs / external).

- **Section breakdown console output**: Step 9 prints item count per BOQ section.

- **QA package report**: `qa_reporter.py` now includes `package_qa`
  (per-section measured/derived/provisional/manual-review counts) and
  `boq_summary` (overall traceability stats) in the JSON report and text summary.

- **Services always generated**: `project_quantities.py` now emits provisional
  services items (wet area waterproofing, sanitary fixtures, plumbing builder's
  work, electrical builder's work) whenever `floor_area > 0`, rather than only
  when wet-area room names are detected. This correctly handles commercial
  projects (e.g. pharmacy) where room names don't match residential keywords.

- **`output/json/` directory**: both `project_quantities.json` and
  `boq_items.json` are saved here for inspection and benchmarking.

### Changed

- `main.py`: refactored to 13-step pipeline.
  - Step 6: `classify_project()` after merge
  - Step 7: `build_quantity_model()` → saves `project_quantities.json`
  - Step 8: `load_item_library()` as reference only
  - Step 9: `map_to_boq_items()` → saves `boq_items.json`
  - Step 10: for `custom_project` — skips `calculate_quantities()`, uses
    `mapped_items` directly
  - `project_mode` passed to `write_boq()` and `write_summary()`
  - `quantity_model` passed to `generate_report()`

- `src/summary_writer.py`: removed hardcoded G303 fallback paths. Dynamic
  resolution via `_resolve_std_geo_path()` / `_resolve_proj_summ_path()`.
  G303 fallbacks only active when `project_mode == "standard_model"`.

- `src/bom_extractor.py`: `_read_ifc()` now delegates to
  `ifc_extractor.extract_ifc()` for full structural member extraction, rather
  than a basic entity count. `_normalise()` merges IFC structural values where
  BOM raw items gave 0.

- `src/merger.py`: `_merge_geometry()` accepts `ifc_geometry` from BOM result.
  IFC added as a pick source for: `total_floor_area_m2`, `ceiling_area_m2`,
  `external_wall_length_m`, `internal_wall_length_m`, `roof_area_m2`,
  `building_length_m`, `building_width_m`, `storey_count`.

- `src/config.py`: `ensure_output_dirs()` now creates `output/json/`.

---

## Earlier sessions (before formal changelog)

The following was in place before the V1 development session:

- G-range standard model BOQ pipeline working for G303
- `calculate_quantities()` in `quantity_calculator.py` for standard models
- `write_boq()` with approved BOQ template copy
- PDF extraction with OpenAI vision API
- DWG/DXF geometry extraction
- Rate lookup and amount calculation
- Cross-check report and QA report generation
