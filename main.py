"""
main.py — BOQ Automation Agent | Rhodes PNG | G-Range + Custom Projects

Usage:
  python main.py --project "JobName"
  python main.py --project "JobName" --type G303 --highset y --yes
  python main.py --project "JobName" --dry-run
"""

from __future__ import annotations
import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent

# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BOQ Automation Agent")
    p.add_argument("--project",  required=True, help="Project name (used in output filenames)")
    p.add_argument("--type",     default="",    help="Force house type e.g. G303")
    p.add_argument("--highset",  default="",    help="Highset? y/n")
    p.add_argument("--yes",      action="store_true", help="Non-interactive: skip all prompts")
    p.add_argument("--dry-run",  action="store_true", help="Show file scan only, do not process")
    p.add_argument("--force-convert", action="store_true",
                   help="Delete cached DXF and re-convert DWG")
    p.add_argument("--debug",    action="store_true",
                   help="Enable DEBUG logging and per-item quantity trace")
    p.add_argument("--audit",    action="store_true",
                   help="Run DWG audit only — print all layers, blocks, entities, text, dims")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ── Bootstrap ──────────────────────────────────────────────────────────────
    from src.utils import setup_logging
    from src.config import ensure_output_dirs, OUTPUT_REPORTS, OUTPUT_LOGS, OUTPUT_BOQ
    ensure_output_dirs()
    log = setup_logging(args.project, debug=args.debug)

    # ── Audit mode ─────────────────────────────────────────────────────────────
    if args.audit:
        from src.config import INPUT_DIR
        from src.dwg_extractor import audit_dwg

        # Find DXF/DWG
        project_input = INPUT_DIR / args.project
        scan_dirs = [project_input, INPUT_DIR]
        dxf_path = None
        for sd in scan_dirs:
            for ext in ("*.dxf", "*.DXF", "*.dwg", "*.DWG"):
                matches = list(sd.rglob(ext)) if sd.exists() else []
                if matches:
                    dxf_path = matches[0]
                    break
            if dxf_path:
                break

        if not dxf_path:
            print("ERROR: No DXF/DWG file found in input/")
            return 1

        print(f"Auditing: {dxf_path.name}")
        report = audit_dwg(str(dxf_path))
        print(report)

        out_dir = ROOT / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.project}_DWG_Audit.txt"
        with open(str(out_path), "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nSaved to: {out_path}")

        # ── Step 2: PDF Audit ──────────────────────────────────────────────────
        print("\n" + "=" * 80)
        print("STEP 2 — PDF AUDIT")
        print("=" * 80)

        from src.file_detector import detect_files
        project_input2 = INPUT_DIR / args.project
        scan_dir2 = project_input2 if project_input2.exists() else INPUT_DIR
        files2 = detect_files(scan_dir2)
        pdfs = files2.get("pdf", [])

        if not pdfs:
            print("No PDF files found in input/")
        else:
            from src.pdf_extractor import audit_pdfs
            print(f"Auditing {len(pdfs)} PDF file(s)...")
            for p in pdfs:
                print(f"  {Path(p['path']).name}  ({p.get('pages', '?')} pages)")
            print()
            pdf_report = audit_pdfs(pdfs)
            print(pdf_report)

            pdf_out_path = ROOT / "output" / f"{args.project}_PDF_Audit.txt"
            with open(str(pdf_out_path), "w", encoding="utf-8") as f:
                f.write(pdf_report)
            print(f"\nSaved to: {pdf_out_path}")

        return 0

    _banner(args.project)

    # ── Step 1: Scan input files → source inventory ───────────────────────────
    print("\n[1/13] Scanning input files...")
    from src.file_detector import detect_files
    from src.config import INPUT_DIR

    project_input = INPUT_DIR / args.project
    if project_input.exists():
        scan_dir = project_input
        print(f"  Input folder: input/{args.project}/")
    else:
        scan_dir = INPUT_DIR
        print(f"  Input folder: input/  (tip: create input/{args.project}/ for project isolation)")

    files = detect_files(scan_dir)
    if scan_dir != INPUT_DIR:
        pass  # project folder is self-contained
    elif not any(INPUT_DIR.rglob("*.pdf")):
        legacy = INPUT_DIR.parent / "input"
        files2 = detect_files(legacy)
        for k in ("dwg", "dxf", "pdf", "ifc", "bom"):
            files[k] = _dedup(files[k] + files2[k])

    _print_files(files)

    if args.dry_run:
        print("\n  --dry-run: stopping here.")
        return 0

    # ── Step 2: Extract DWG/DXF geometry ──────────────────────────────────────
    print("\n[2/13] Extracting DWG/DXF geometry...")
    dwg_data = _run_dwg_extraction(files, args.force_convert)

    # ── Step 3: Extract PDFs ───────────────────────────────────────────────────
    print("\n[3/13] Extracting PDFs (AI vision)...")
    pdf_data = _run_pdf_extraction(files)

    # ── Step 4: Extract BOM / IFC ─────────────────────────────────────────────
    print("\n[4/13] Extracting BOM / IFC...")
    bom_data = _run_bom_extraction(files)

    # ── Build source inventory (feeds QA, no longer blocking) ─────────────────
    from src.source_inventory import build_inventory
    parse_results = {"dwg": dwg_data, "pdf": pdf_data, "bom": bom_data}
    source_inventory = build_inventory(files, parse_results)
    parsed_ok_count = sum(1 for r in source_inventory if r["parsed_ok"])
    print(f"  Source inventory: {len(source_inventory)} files  ({parsed_ok_count} parsed ok)")

    # ── Step 5: Merge all sources ──────────────────────────────────────────────
    print("\n[5/13] Merging sources...")
    from src.merger import merge_all
    # Highset detection (pre-classify)
    highset: bool | None = None
    if args.highset.lower() in ("y", "yes", "true", "1"):
        highset = True
    elif args.highset.lower() in ("n", "no", "false", "0"):
        highset = False

    # Title block
    print("  Detecting title blocks...")
    titleblock = _run_titleblock(files)
    if titleblock.get("project_name"):
        print(f"  Project name detected: {titleblock['project_name']}")
    if titleblock.get("house_type_detected"):
        detected = titleblock["house_type_detected"]
        print(f"  House type in title block: {detected} ({titleblock.get('house_type_confidence')})")

    if highset is not None:
        titleblock["highset_detected"] = highset

    merged = merge_all(dwg_data, pdf_data, bom_data, titleblock)
    _print_merge_summary(merged)

    # ── Step 6: Classify project ───────────────────────────────────────────────
    print("\n[6/13] Classifying project...")
    from src.project_classifier import classify_project

    # Override with --type argument if provided
    forced_type = (args.type or "").strip().upper()
    if forced_type:
        titleblock["house_type_detected"]   = forced_type
        titleblock["house_type_confidence"] = "HIGH"

    classification = classify_project(files, titleblock, merged)
    project_mode   = classification["project_mode"]
    model_code     = classification.get("matched_model_code")
    cl_conf        = classification["confidence"]

    print(f"  Mode       : {project_mode}")
    print(f"  Model code : {model_code or 'custom / generic'}")
    print(f"  Confidence : {cl_conf}")
    if args.debug:
        for r in classification.get("reasoning", []):
            print(f"    {r}")

    # Legacy mode variable for QA reporter compatibility
    from src.config import ProjectMode, STANDARD_MODELS_MAP
    mode = (
        ProjectMode.STANDARD.value
        if project_mode == "standard_model"
        else ProjectMode.GENERIC.value
    )
    house_type = model_code or forced_type or ""

    # ── Step 7: Build neutral quantity model ──────────────────────────────────
    print("\n[7/13] Building neutral quantity model...")
    from src.project_quantities import build_quantity_model, save_quantity_model
    quantity_model = build_quantity_model(merged, classification)
    qty_path = save_quantity_model(quantity_model, args.project)
    print(f"  Quantities  : {len(quantity_model['quantities'])} entries")
    print(f"  Saved       : {qty_path.name}")
    # Print package completeness
    completeness = quantity_model.get("completeness", {})
    if completeness:
        print("  Completeness:")
        for pkg, info in completeness.items():
            status = "OK" if info["detected"] else "--"
            print(f"    [{status}] {pkg:<12}  {info['items']:>3} quantities  {info['notes']}")

    # ── Step 8: Load item library (reference only) ────────────────────────────
    print("\n[8/13] Loading item library (reference only)...")
    from src.config import DATA_DIR, STANDARD_MODELS
    from src.item_library import load_item_library

    # Only load approved BOQ as reference for the classified model
    approved_boq_path  = None
    standard_model_path = None

    if project_mode == "standard_model" and model_code:
        # Look for the matching model file
        model_filename = STANDARD_MODELS_MAP.get(model_code)
        if model_filename:
            candidate = STANDARD_MODELS / model_filename
            if candidate.exists():
                standard_model_path = str(candidate)

    # Also check for approved BOQ in data/
    for _candidate in [DATA_DIR / "approved_boq_G303.xlsx", DATA_DIR / "approved_boq.xlsx"]:
        if _candidate.exists():
            approved_boq_path = str(_candidate)
            break

    item_library = load_item_library(approved_boq_path, standard_model_path)
    print(f"  Item library: {len(item_library)} entries  "
          f"({'approved BOQ' if approved_boq_path else 'standard model' if standard_model_path else 'none'})")

    # ── Step 9: Map quantities to BOQ items ───────────────────────────────────
    print("\n[9/13] Mapping quantities to BOQ items...")
    from src.boq_mapper import map_to_boq_items, save_boq_items
    mapped_items = map_to_boq_items(quantity_model, item_library, merged)
    boq_items_path = save_boq_items(mapped_items, args.project)
    print(f"  Mapped items: {len(mapped_items)}")
    print(f"  Saved       : {boq_items_path.name}")
    # Print section breakdown
    from collections import Counter
    section_counts = Counter(i.get("boq_section", "GENERAL") for i in mapped_items)
    for section, count in sorted(section_counts.items()):
        print(f"    {section:<28} {count:>3} item(s)")

    # ── Validate ───────────────────────────────────────────────────────────────
    print("\nValidating data...")
    from src.validator import validate
    validation = validate(merged)
    print(f"  Checks    : {len(validation['relationship_checks'])}")
    print(f"  Conflicts : {len(validation['conflicts'])}")
    print(f"  Missing   : {len(validation['missing_scope'])}")

    # ── Still run legacy quantity_calculator for standard_model only ──────────
    # For custom_project: use mapped_items from step 9 (boq_mapper output) directly.
    # For standard_model: load template + calculate_quantities as before.
    print("\n[10/13] Calculating BOQ item quantities...")
    standard_boq: list[dict] = []
    standard_geometry: dict  = {}

    if project_mode == "standard_model":
        if model_code:
            standard_boq, standard_geometry = _load_standard_model(model_code)
            print(f"  Standard BOQ loaded: {len(standard_boq)} items  (model: {model_code})")
        elif approved_boq_path:
            try:
                from src.loader import load_approved_boq
                standard_boq = load_approved_boq(approved_boq_path)
                print(f"  Approved BOQ loaded: {len(standard_boq)} items  ({Path(approved_boq_path).name})")
            except Exception as exc:
                print(f"  WARNING: Could not load approved BOQ: {exc}")
        if not standard_boq:
            standard_boq, standard_geometry = _load_standard_model(house_type)

        from src.quantity_calculator import calculate_quantities
        boq_items = calculate_quantities(
            standard_boq, merged, validation,
            standard_geometry=standard_geometry,
            debug=args.debug,
        )
        print(f"  Calculated: {len(boq_items)} items")
    else:
        # custom_project: use mapped_items from boq_mapper (step 9) directly
        # Do NOT load approved BOQ — that is a different (G303) project template
        print("  custom_project mode — using mapped_items from step 9 (no G303 template)")
        boq_items = mapped_items
        print(f"  BOQ items from mapper: {len(boq_items)}")

    # ── Step 10: Apply rates ──────────────────────────────────────────────────
    print("\n[10/13] Applying rates from library...")
    boq_items = _apply_rates(boq_items, house_type)

    # ── Cross-check against reference BOQ ────────────────────────────────────
    from src.cross_checker import cross_check, format_report as _xc_format
    # Only cross-check for standard_model — custom projects have no reference template
    xc_ref_boq = standard_boq if (project_mode == "standard_model" and standard_boq) else None
    xc_result  = cross_check(boq_items, xc_ref_boq, reference_path=approved_boq_path)
    xc_report  = _xc_format(xc_result)
    xc_path    = OUTPUT_LOGS / f"{args.project}_cross_check.txt"
    try:
        xc_path.write_text(xc_report, encoding="utf-8")
        s = xc_result['summary']
        print(f"  Cross-check: {s.get('pass_pct_of_computed', 0):.1f}% PASS (of {s.get('computed', s['pass']+s['warn']+s['flag'])} computed)  "
              f"WARN={s['warn']}  FLAG={s['flag']}  BLANK={s['blank']}")
        print(f"  Report saved: {xc_path.name}")
    except Exception as _xc_exc:
        print(f"  WARNING: Cross-check write failed: {_xc_exc}")

    # ── Step 11: Write BOQ Excel ──────────────────────────────────────────────
    print("\n[11/13] Writing BOQ workbook...")
    from src.boq_writer import write_boq

    boq_path = write_boq(
        project_name=args.project,
        boq_items=boq_items,
        validation=validation,
        merged=merged,
        project_mode=project_mode,
        approved_boq_path=approved_boq_path,
    )

    # ── Step 12: Write Summary ────────────────────────────────────────────────
    print("\n[12/13] Writing Summary workbook...")
    summary_path = None
    try:
        from src.summary_writer import write_summary
        summary_path = write_summary(
            project_name=args.project,
            boq_items=boq_items,
            validation=validation,
            merged=merged,
            files_found=files,
            boq_path=boq_path,
            project_mode=project_mode,
        )
        print(f"  Summary  : {summary_path.name}")
    except Exception as exc:
        import traceback as _tb
        print(f"  WARNING: Summary writer failed: {exc}")
        log.warning("Summary writer failed: %s\n%s", exc, _tb.format_exc())

    # ── Step 13: Write QA Report ──────────────────────────────────────────────
    print("\n[13/13] Writing QA report...")
    from src.qa_reporter import generate_report
    report = generate_report(
        project_name=args.project,
        files_found=files,
        boq_items=boq_items,
        validation=validation,
        merged=merged,
        project_mode=mode,
        house_type=house_type or "custom",
        quantity_model=quantity_model,
    )
    boq_summary = report.get("boq_summary", {})
    if boq_summary:
        print(f"  Measured    : {boq_summary.get('measured_items',0)} items ({boq_summary.get('pct_measured',0):.0f}%)")
        print(f"  Derived     : {boq_summary.get('derived_items',0)} items")
        print(f"  Provisional : {boq_summary.get('provisional_items',0)} items")
        print(f"  Manual rev  : {boq_summary.get('manual_review_items',0)} items")

    # ── Final summary ─────────────────────────────────────────────────────────
    _print_final_summary(args.project, boq_items, validation, report, boq_path, summary_path)
    return 0


# ─── Standard model loading ──────────────────────────────────────────────────

def _load_standard_model(house_type: str) -> tuple[list, dict]:
    from src.config import STANDARD_MODELS, STANDARD_MODELS_MAP, DATA_DIR
    from src.loader import load_standard_model

    if house_type and house_type in STANDARD_MODELS_MAP:
        model_path = STANDARD_MODELS / STANDARD_MODELS_MAP[house_type]
    else:
        candidates = list(DATA_DIR.glob("standard_model_G303*.xlsx"))
        model_path = candidates[0] if candidates else None

    if model_path and model_path.exists():
        try:
            data = load_standard_model(str(model_path))
            return data["standard_boq"], data.get("standard_geometry", {})
        except Exception as exc:
            print(f"  WARNING: Could not load standard model: {exc}")

    print("  No standard model loaded — BOQ will contain empty template")
    return [], {}


# ─── Rate application ────────────────────────────────────────────────────────

def _apply_rates(boq_items: list, house_type: str) -> list:
    from src.config import DATA_DIR
    from src import ai_client as _ai

    rate_lib_path = DATA_DIR / "rate_library_2026_RPNG.xlsx"
    if not rate_lib_path.exists():
        candidates = list(DATA_DIR.glob("rate_library*.xlsx"))
        rate_lib_path = candidates[0] if candidates else None

    if rate_lib_path and rate_lib_path.exists():
        try:
            from src.loader import load_rate_library
            from src.pricer import apply_rates
            rate_lib = load_rate_library(str(rate_lib_path))
            use_ai = _ai.is_available()
            boq_items = apply_rates(boq_items, rate_lib, use_ai_fallback=use_ai)
            covered = sum(1 for i in boq_items if i.get("rate"))
            ai_est  = sum(1 for i in boq_items if i.get("rate_source") == "AI-estimate")
            manual  = sum(1 for i in boq_items if i.get("rate_source") == "manual-required")
            print(f"  Rates: library={covered - ai_est}  AI={ai_est}  manual={manual}"
                  f"  ({100*covered//len(boq_items) if boq_items else 0}% covered)")
        except Exception as exc:
            print(f"  WARNING: Rate application failed: {exc}")
    else:
        print("  Rate library not found — rates not applied")
    return boq_items


# ─── Extraction wrappers ─────────────────────────────────────────────────────

def _run_titleblock(files: dict) -> dict:
    all_pdfs = files.get("pdf", [])
    if not all_pdfs:
        return {}
    try:
        from src.titleblock_detector import detect_titleblock
        return detect_titleblock(all_pdfs)
    except Exception as exc:
        print(f"  WARNING: Title block detection failed: {exc}")
        return {}


def _run_dwg_extraction(files: dict, force_convert: bool) -> dict:
    drawings = files.get("dwg", []) + files.get("dxf", [])
    if not drawings:
        print("  No DWG/DXF files found")
        return {"summary": {}, "rooms": [], "doors": [], "windows": [],
                "posts": [], "stairs": [], "dimensions": [], "warnings": []}

    drawing = drawings[0]
    path = drawing["path"]

    if force_convert and path.endswith(".dxf"):
        dxf = Path(path)
        if dxf.exists():
            dxf.unlink()
            print(f"  Deleted cached DXF: {dxf.name}")

    try:
        from src.dwg_extractor import extract_geometry
        data = extract_geometry(path)
        s = data.get("summary", {})
        print(f"  Floor: {s.get('total_floor_area_m2', 0):.1f}m²  "
              f"Ext wall: {s.get('external_wall_length_m', 0):.1f}m  "
              f"Roof: {s.get('roof_area_m2', 0):.1f}m²")
        print(f"  Doors: {s.get('door_count', 0)}  "
              f"Windows: {s.get('window_count', 0)}  "
              f"Posts: {s.get('post_count', 0)}")
        if data.get("warnings"):
            for w in data["warnings"]:
                print(f"  WARN: {w}")
        return data
    except Exception as exc:
        print(f"  ERROR in DWG extraction: {exc}")
        traceback.print_exc()
        return {"summary": {}, "rooms": [], "doors": [], "windows": [],
                "posts": [], "stairs": [], "dimensions": [], "warnings": [str(exc)]}


def _run_pdf_extraction(files: dict) -> dict:
    pdfs = files.get("pdf", [])
    if not pdfs:
        print("  No PDF files found")
        return {"rooms": [], "doors": [], "windows": [], "finishes": [],
                "stairs": [], "roof": {}, "structural": [], "warnings": []}

    from src import ai_client
    if not ai_client.is_available():
        print("  OPENAI_API_KEY not set — PDF vision extraction skipped")
        return {"rooms": [], "doors": [], "windows": [], "finishes": [],
                "stairs": [], "roof": {}, "structural": [], "warnings": [
                    "AI not available — set OPENAI_API_KEY for PDF extraction"]}

    try:
        from src.pdf_extractor import extract_pdfs
        data = extract_pdfs(pdfs)
        print(f"  Doors: {len(data.get('doors', []))}  "
              f"Windows: {len(data.get('windows', []))}  "
              f"Finishes: {len(data.get('finishes', []))}  "
              f"Stairs: {len(data.get('stairs', []))}")
        if data.get("warnings"):
            for w in data["warnings"][:3]:
                print(f"  WARN: {w}")
        return data
    except Exception as exc:
        print(f"  ERROR in PDF extraction: {exc}")
        return {"rooms": [], "doors": [], "windows": [], "finishes": [],
                "stairs": [], "roof": {}, "structural": [], "warnings": [str(exc)]}


def _run_bom_extraction(files: dict) -> dict:
    bom_files = files.get("bom", []) + files.get("ifc", [])
    if not bom_files:
        print("  No BOM / IFC files found")
        return {"raw_items": [], "normalized": {}, "warnings": []}
    try:
        from src.bom_extractor import extract_bom
        data = extract_bom(bom_files)
        n = data.get("normalized", {})
        print(f"  Wall frame: {n.get('wall_frame_lm', 0):.1f}lm  "
              f"Ceil battens: {n.get('ceiling_batten_lm', 0):.1f}lm  "
              f"Roof battens: {n.get('roof_batten_lm', 0):.1f}lm")
        return data
    except Exception as exc:
        print(f"  ERROR in BOM extraction: {exc}")
        return {"raw_items": [], "normalized": {}, "warnings": [str(exc)]}


# ─── Utilities ────────────────────────────────────────────────────────────────

def _banner(project_name: str) -> None:
    d = date.today().strftime("%d %B %Y")
    print("╔══════════════════════════════════════════════╗")
    print("║   BOQ AUTOMATION SYSTEM — Rhodes PNG         ║")
    print(f"║   Project: {project_name:<34}║")
    print(f"║   Date: {d:<36}║")
    print("╚══════════════════════════════════════════════╝")


def _print_files(files: dict) -> None:
    print(f"\n  FILES FOUND IN input/")
    for category, entries in files.items():
        if category == "warnings":
            continue
        if entries:
            for e in entries:
                print(f"    [{category.upper()}] {Path(e['path']).name}  "
                      f"({e.get('size_kb', '?')} KB"
                      + (f"  {e.get('type','')}  {e.get('pages','')}pg" if category == "pdf" else "")
                      + ")")
    print(f"\n  EXTRACTION PLAN:")
    print("    Structural      → BOM/IFC preferred")
    print("    Geometry        → DWG/DXF preferred")
    print("    Schedules       → PDF preferred")
    print("    Derived items   → rules library")
    print("    Standard BOQ    → reference only (post-classification)")


def _print_merge_summary(merged: dict) -> None:
    geo = merged.get("geometry", {})
    print(f"  Floor: {geo.get('total_floor_area_m2', 0):.1f}m²  "
          f"Roof: {geo.get('roof_area_m2', 0):.1f}m²  "
          f"Ext wall: {geo.get('external_wall_length_m', 0):.1f}m")
    print(f"  Doors: {len(merged.get('doors', []))}  "
          f"Windows: {len(merged.get('windows', []))}  "
          f"Finishes: {len(merged.get('finishes', []))}")
    if merged.get("conflicts"):
        print(f"  Conflicts detected: {len(merged['conflicts'])}")


def _print_final_summary(
    project_name: str, boq_items: list, validation: dict,
    report: dict, boq_path: Path, summary_path: Path | None = None,
) -> None:
    from src.config import OUTPUT_REPORTS
    total = sum(
        (i.get("qty") or 0) * (i.get("rate") or 0) for i in boq_items
    )
    low_conf  = sum(1 for i in boq_items if (i.get("confidence") or "").upper() == "LOW")
    review    = sum(1 for i in boq_items if i.get("issue_flag") in ("REVIEW_REQUIRED", "MISSING_DATA"))
    conflicts = len(validation.get("conflicts", []))

    print("\n" + "=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)
    print(f"  BOQ     : {boq_path}")
    if summary_path:
        print(f"  Summary : {summary_path}")
    print(f"  Reports : {OUTPUT_REPORTS}")
    print(f"  Total   : PGK {total:,.2f}" if total else "  Total   : (rates not applied)")
    print(f"  Items   : {len(boq_items)}")
    print(f"  Conflicts           : {conflicts}")
    print(f"  Low-confidence items: {low_conf}")
    print(f"  Manual review req'd : {review}")
    missing = report.get("missing_scope", [])
    high_risk = [m for m in missing if m.get("risk") == "HIGH"]
    if high_risk:
        print(f"\n  HIGH-RISK MISSING SCOPE:")
        for m in high_risk:
            print(f"    [{m['risk']}] {m['category']}: {m['description']}")
    print("=" * 60)


def _dedup(entries: list) -> list:
    seen = set()
    out = []
    for e in entries:
        p = e["path"]
        if p not in seen:
            seen.add(p)
            out.append(e)
    return out


if __name__ == "__main__":
    sys.exit(main())
