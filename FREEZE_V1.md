# FREEZE NOTICE — BOQ Pipeline V1 Stable

**Freeze date:** 23 March 2026
**Branch/folder:** `master` (current working directory)
**Status:** FROZEN — no major architectural changes permitted

---

## Reason for Freeze

This snapshot preserves the first fully working custom-project BOQ pipeline.

Before this freeze, the pipeline had a critical defect: all custom projects (non-G303 builds) were contaminated by the G303 standard-model workbook structure, inheriting row templates, section headers ("Alice Kivali / 24138"), and 375 blank G303 rows. The output was unusable for any custom project.

This version resolves that defect and adds quantity traceability for the first time. It is the benchmark version before V2 (DDC-based extraction rebuild) begins.

---

## What Was Intentionally Preserved

- Custom-project mode: outputs a clean workbook without G303 template inheritance
- Quantity traceability: every BOQ item carries `quantity_basis`, `quantity_rule_used`, `source_evidence`, `confidence`
- Package coverage: 8 construction packages with completeness reporting
- Section mapping: 17+ clean BOQ sections with no library-match contamination
- Quantity-basis colour coding in Excel output
- QA package report: per-section breakdown of measured/derived/provisional/manual-review items

---

## Instruction to Future Developers

V1 is frozen. Do not apply major redesign to this folder.

Permitted after freeze:
- Critical bug fixes (e.g. a crash on a specific input)
- Data corrections to `data/` (rules, approved BOQ, standard models)
- Minor output formatting tweaks that do not change the quantity model

Not permitted:
- Replacing `project_quantities.py` with a new extraction engine
- Changing the `merge_sources` → `classify_project` → `build_quantity_model` → `map_to_boq_items` pipeline order
- Removing or renaming `quantity_basis` / `quantity_rule_used` fields (V2 depends on these as a benchmark reference)

V2 architecture work must be developed separately, either in a new branch or a parallel folder. V1 is the before-picture.
