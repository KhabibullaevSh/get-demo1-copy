# BOQ Pipeline Benchmarks

This folder stores known-good output snapshots used to validate and compare
pipeline versions. Each subdirectory contains one project's benchmark run.

---

## Structure

```
benchmarks/
  README.md                   ← this file
  angau_pharmacy/
    README.md                 ← project notes, issue log, expected outputs
    v1_boq_items.json         ← V1 BOQ item list (52 items)
    v1_quantities.json        ← V1 neutral quantity model (47 entries)
    v1_qa_report.json         ← V1 QA report (package_qa + boq_summary)
    v1_qa_report.txt          ← V1 QA text summary
```

---

## How to Use These Benchmarks

### Regression testing
After making changes, run the pipeline on the same project and compare output
against the benchmark files:

```python
# Compare item counts
with open("benchmarks/angau_pharmacy/v1_boq_items.json") as f:
    v1 = json.load(f)
with open("output/json/project 2_boq_items.json") as f:
    current = json.load(f)

assert len(current) >= len(v1), f"Item count regressed: {len(current)} < {len(v1)}"
```

Or use the existing `compare_boq.py` / `test_comparison.py` scripts in the
project root.

### Quality baseline
The V1 benchmark documents the minimum acceptable output for the Angau Pharmacy
project. Any V2 run should produce:
- At least 52 items (ideally more as extraction improves)
- All 8 packages detected
- Traceability fields present on all items
- No section name contamination ("Ground Level Laundry", G303 row titles, etc.)

---

## Adding a New Benchmark

1. Run the pipeline on the new project
2. Create `benchmarks/{project_name}/`
3. Copy `output/json/{project}_boq_items.json` → `v1_boq_items.json`
4. Copy `output/json/{project}_quantities.json` → `v1_quantities.json`
5. Copy latest QA report files → `v1_qa_report.json`, `v1_qa_report.txt`
6. Write a `README.md` documenting input files, known issues, and expected metrics

---

## Benchmark Projects

| Project | Type | V1 Items | V1 Packages | Run Date |
|---|---|---|---|---|
| Angau Pharmacy | Custom / Commercial | 52 | 8/8 | 23 Mar 2026 |
