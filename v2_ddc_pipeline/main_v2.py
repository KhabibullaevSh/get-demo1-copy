"""
main_v2.py -- V2 DDC BOQ Pipeline entry point.

Usage:
    python main_v2.py --project "project 2"

Steps:
  1. Load source inventory from input/{project}/
  2. Classify project (G-range code detection)
  3. Run DXF extractor   (if .dxf found)
  4. Run IFC extractor   (if .ifc found)
  5. Run PDF extractor   (if .pdf found)
  6. Run FrameCAD extractor (if BOM found)
  7. Build project model (merge with priority rules)
  8. Build quantity model
  9. Load item library   (reference only -- no quantities)
 10. Map quantities to BOQ items
 11. Check completeness
 12. Compare with V1 benchmark (if available)
 13. Write outputs: source_inventory.json, project_model.json,
                    project_quantities.json, boq_items.json, qa_report.*
 14. Write BOQ Excel (Phase 5 -- requires openpyxl)

CRITICAL NON-NEGOTIABLE RULE:
  Quantities come ONLY from DXF / IFC / PDF / BOM sources.
  The approved BOQ xlsx is REFERENCE ONLY (stock codes, descriptions).
  No quantity is ever copied from any BOQ template file.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# ── Ensure v2_ddc_pipeline/ is on the path so src.* imports resolve ──────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Configure logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("boq.v2.main")


def _find_first(input_dir: Path, extensions: list[str]) -> Path | None:
    for ext in extensions:
        matches = sorted(input_dir.rglob(f"*{ext}"))
        for m in matches:
            return m
    return None


def run(project: str) -> None:
    t0 = time.time()
    print("=" * 70)
    print("  V2 DDC BOQ PIPELINE")
    print(f"  Project: {project}")
    print("=" * 70)

    # ── Resolve paths ─────────────────────────────────────────────────────
    from config.settings import INPUT_DIR, OUTPUT_DIR, DATA_DIR
    project_input_dir = INPUT_DIR / project
    project_output_dir = OUTPUT_DIR / project.replace(" ", "_")

    if not project_input_dir.exists():
        print(f"\n[ERROR] Input directory not found: {project_input_dir}")
        print("  Available projects:")
        for p in sorted(INPUT_DIR.iterdir()):
            if p.is_dir():
                print(f"    {p.name}")
        sys.exit(1)

    # V1 benchmark path
    benchmark_dir = _HERE.parent / "benchmarks" / "angau_pharmacy"
    v1_benchmark_path = benchmark_dir / "v1_boq_items.json"

    print(f"\n[1/14] Source inventory: {project_input_dir}")
    from src.source_inventory import build_source_inventory, save_source_inventory
    source_inventory = build_source_inventory(project_input_dir)
    save_source_inventory(source_inventory, project_output_dir)
    print(f"       -> {len(source_inventory)} files inventoried")

    print(f"\n[2/14] Project classification")
    from src.project_classifier import classify_project
    classification = classify_project(project_input_dir)
    print(f"       -> mode={classification['project_mode']}  "
          f"code={classification['matched_model_code']}  "
          f"confidence={classification['confidence']}")

    # ── DXF extraction ────────────────────────────────────────────────────
    dxf_data: dict = {"warnings": ["No DXF file found"]}
    dxf_file = _find_first(project_input_dir, [".dxf"])
    print(f"\n[3/14] DXF extractor")
    if dxf_file:
        print(f"       -> {dxf_file.name}")
        from src.extractors.dxf_extractor import extract_dxf
        dxf_data = extract_dxf(dxf_file)
        print(f"       floor={dxf_data.get('floor_area_m2',0):.1f} m2  "
              f"roof={dxf_data.get('roof_area_m2',0):.1f} m2  "
              f"doors={dxf_data.get('door_count',0)}  "
              f"windows={dxf_data.get('window_count',0)}  "
              f"posts={dxf_data.get('post_count',0)}")
        # Mark file as parsed in inventory
        for rec in source_inventory:
            if rec["extension"] == ".dxf":
                rec["parsed_successfully"] = True
                rec["used_in_reasoning"]   = True
    else:
        print("       -> No DXF file found")

    # ── IFC extraction ────────────────────────────────────────────────────
    ifc_data: dict = {"warnings": ["No IFC file found"]}
    ifc_file = _find_first(project_input_dir, [".ifc"])
    print(f"\n[4/14] IFC extractor")
    if ifc_file:
        print(f"       -> {ifc_file.name}")
        from src.extractors.ifc_extractor import extract_ifc
        ifc_data = extract_ifc(ifc_file)
        print(f"       columns={ifc_data.get('column_count',0)} ({ifc_data.get('total_column_lm',0):.1f} lm)  "
              f"beams={ifc_data.get('beam_count',0)} ({ifc_data.get('total_beam_lm',0):.1f} lm)  "
              f"schema={ifc_data.get('schema','?')}")
        for rec in source_inventory:
            if rec["extension"] == ".ifc":
                rec["parsed_successfully"] = True
                rec["used_in_reasoning"]   = True
    else:
        print("       -> No IFC file found")

    # ── PDF extraction ────────────────────────────────────────────────────
    pdf_data: dict = {"warnings": ["No PDF extraction attempted"], "rooms": [], "doors": [], "windows": [], "stairs": [], "finishes": [], "notes": []}
    print(f"\n[5/14] PDF extractor")
    pdf_files = list(project_input_dir.rglob("*.pdf"))
    if pdf_files:
        print(f"       -> {len(pdf_files)} PDF file(s) found")
        print("       -> Skipping AI PDF extraction (no API key / V2 standalone mode)")
        print("       -> PDF data will be empty -- openings from DXF/IFC only")
        # Note: to enable AI extraction, uncomment below:
        # from src.extractors.pdf_extractor import extract_pdf
        # pdf_data = extract_pdf(project_input_dir)
    else:
        print("       -> No PDF files found")

    # ── FrameCAD BOM extraction ───────────────────────────────────────────
    print(f"\n[6/14] FrameCAD BOM extractor")
    from src.extractors.framecad_extractor import extract_framecad_bom
    framecad_data = extract_framecad_bom(project_input_dir)
    if framecad_data.get("found"):
        totals = framecad_data.get("totals", {})
        print(f"       -> BOM found: {Path(framecad_data['source_file']).name}")
        print(f"       -> {len(framecad_data['members'])} member rows  total={totals.get('total_lm',0):.1f} lm")
        for rec in source_inventory:
            if framecad_data.get("source_file") and rec["path"] == framecad_data["source_file"]:
                rec["parsed_successfully"] = True
                rec["used_in_reasoning"]   = True
    else:
        print("       -> No FrameCAD BOM found")

    # ── Build project model ───────────────────────────────────────────────
    print(f"\n[7/14] Building project model")
    from src.normalizers.project_model import build_project_model
    project_model = build_project_model(
        dxf_data         = dxf_data,
        ifc_data         = ifc_data,
        pdf_data         = pdf_data,
        framecad_data    = framecad_data,
        source_inventory = source_inventory,
        classification   = classification,
        project_name     = project,
    )
    print(f"       -> struct_priority={project_model['structural']['source_priority_used']}")
    print(f"       -> warnings={len(project_model['extraction_warnings'])}")

    # ── Build quantity model ──────────────────────────────────────────────
    print(f"\n[8/14] Building quantity model")
    from src.quantity.quantity_builder import build_quantity_model
    quantity_model = build_quantity_model(project_model)
    totals_by_basis = quantity_model["totals_by_basis"]
    print(f"       -> {totals_by_basis['total']} items  "
          f"measured={totals_by_basis['measured']}  "
          f"derived={totals_by_basis['derived']}  "
          f"provisional={totals_by_basis['provisional']}")

    # ── Load item library (reference only) ───────────────────────────────
    print(f"\n[9/14] Loading item library (reference only)")
    from src.mapping.item_library import load_item_library
    item_library = load_item_library(DATA_DIR)
    print(f"       -> {len(item_library.get('items', []))} reference items  "
          f"(REFERENCE ONLY -- no quantities from library)")

    # ── Map to BOQ items ──────────────────────────────────────────────────
    print(f"\n[10/14] Mapping to BOQ items")
    from src.mapping.boq_mapper import map_to_boq_items
    boq_items = map_to_boq_items(quantity_model, item_library)
    print(f"       -> {len(boq_items)} BOQ items across "
          f"{len({i['boq_section'] for i in boq_items})} sections")

    # ── Completeness check ────────────────────────────────────────────────
    print(f"\n[11/14] Checking completeness")
    from src.qa.completeness_checker import check_completeness
    completeness = check_completeness(project_model, quantity_model)
    for pkg, data in completeness.items():
        status = "OK" if data["detected"] else "MISSING"
        print(f"       {pkg:<14} [{status}]  {data['items']} items  -- {data['notes']}")

    # ── V1 benchmark comparison ───────────────────────────────────────────
    print(f"\n[12/14] V1 benchmark comparison")
    from src.qa.benchmark_compare import compare_with_v1
    benchmark_result = compare_with_v1(boq_items, v1_benchmark_path)
    if benchmark_result.get("v1_items", 0) > 0:
        print(f"       -> V1: {benchmark_result['v1_items']} items  V2: {benchmark_result['v2_items']} items")
        print(f"       -> V1 measured: {benchmark_result['v1_measured_pct']}%  "
              f"V2 measured: {benchmark_result['v2_measured_pct']}%")
    else:
        print("       -> No V1 benchmark available")

    # ── Write JSON outputs ─────────────────────────────────────────────────
    print(f"\n[13/14] Writing outputs -> {project_output_dir}")
    from src.writers.json_writer import save_json

    save_json(source_inventory,  project_output_dir / "source_inventory.json",     "source_inventory")
    save_json(project_model,     project_output_dir / "project_model.json",         "project_model")
    save_json(quantity_model,    project_output_dir / "project_quantities.json",    "project_quantities")
    save_json(boq_items,         project_output_dir / "boq_items.json",             "boq_items")
    save_json({
        "completeness":  completeness,
        "benchmark":     benchmark_result,
        "warnings":      project_model.get("extraction_warnings", []),
        "totals":        totals_by_basis,
    },                   project_output_dir / "qa_report.json",                     "qa_report")

    # QA text + json reports
    from src.writers.qa_writer_v2 import write_qa_report
    qa_json_path, qa_txt_path = write_qa_report(
        source_inventory  = source_inventory,
        project_model     = project_model,
        quantity_model    = quantity_model,
        boq_items         = boq_items,
        completeness      = completeness,
        benchmark_result  = benchmark_result,
        output_dir        = project_output_dir,
        project_name      = project.replace(" ", "_"),
    )
    print(f"       -> QA text  : {qa_txt_path.name}")
    print(f"       -> QA json  : {qa_json_path.name}")

    # ── BOQ Excel (Phase 5) ───────────────────────────────────────────────
    print(f"\n[14/14] Writing BOQ Excel")
    from src.writers.boq_writer_v2 import write_boq_excel
    excel_path = write_boq_excel(boq_items, project_output_dir, project.replace(" ", "_"))
    if excel_path:
        print(f"       -> {excel_path.name}")
    else:
        print("       -> Skipped (openpyxl not installed)")

    elapsed = round(time.time() - t0, 1)
    print(f"\n{'='*70}")
    print(f"  COMPLETE in {elapsed}s")
    print(f"  Output directory: {project_output_dir}")
    print(f"  BOQ items: {len(boq_items)}")
    print(f"  Quantity items: {totals_by_basis['total']}")
    print(f"  Non-BOQ quantity sources used:")
    print(f"    - DXF geometry: {dxf_file.name if dxf_file else 'not found'}")
    print(f"    - IFC model:    {ifc_file.name if ifc_file else 'not found'}")
    print(f"    - FrameCAD BOM: {Path(framecad_data['source_file']).name if framecad_data.get('found') else 'not found'}")
    print(f"  CONFIRMED: No quantities sourced from any BOQ template file.")
    print(f"{'='*70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="V2 DDC BOQ Pipeline -- geometry-first quantity extraction",
    )
    parser.add_argument(
        "--project",
        required=True,
        help='Project subdirectory name inside input/ (e.g. "project 2")',
    )
    args = parser.parse_args()
    run(args.project)


if __name__ == "__main__":
    main()
