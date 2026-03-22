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

    # ── Step 1: File detection ─────────────────────────────────────────────────
    print("\n[1/10] Scanning input files...")
    from src.file_detector import detect_files
    from src.config import INPUT_DIR

    # Per-project folder: input/ProjectName/ takes priority over shared input/
    project_input = INPUT_DIR / args.project
    if project_input.exists():
        scan_dir = project_input
        print(f"  Input folder: input/{args.project}/")
    else:
        scan_dir = INPUT_DIR
        print(f"  Input folder: input/  (tip: create input/{args.project}/ for project isolation)")

    files = detect_files(scan_dir)
    # Also check legacy flat input/ when using project subfolder
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

    # ── Step 2: Interactive mode selection ────────────────────────────────────
    print("\n[2/10] Determining project mode...")
    mode, house_type, highset = _determine_mode(args, files)
    print(f"  Mode     : {mode}")
    print(f"  Type     : {house_type or 'custom / generic'}")
    print(f"  Highset  : {highset}")

    # ── Step 3: Load approved BOQ (primary) or standard model (fallback) ─────
    print("\n[3/10] Loading approved BOQ / standard model...")
    from src.config import DATA_DIR
    approved_boq_path = None
    for _candidate in [DATA_DIR / "approved_boq_G303.xlsx", DATA_DIR / "approved_boq.xlsx"]:
        if _candidate.exists():
            approved_boq_path = str(_candidate)
            break

    standard_boq: list[dict] = []
    standard_geometry: dict = {}

    if approved_boq_path:
        try:
            from src.loader import load_approved_boq
            standard_boq = load_approved_boq(approved_boq_path)
            print(f"  Approved BOQ: {len(standard_boq)} items  ({Path(approved_boq_path).name})")
        except Exception as exc:
            print(f"  WARNING: Could not load approved BOQ: {exc}")

    if not standard_boq:
        standard_boq, standard_geometry = _load_standard_model(house_type)
        print(f"  Standard BOQ (fallback): {len(standard_boq)} items")
    else:
        # Still load geometry for area-based calculations
        _, standard_geometry = _load_standard_model(house_type)

    if args.debug and standard_geometry:
        print(f"  Standard Geometry keys: {list(standard_geometry.keys())}")

    # ── Step 4: Title block detection ─────────────────────────────────────────
    print("\n[4/10] Detecting title blocks...")
    titleblock = _run_titleblock(files)
    if titleblock.get("project_name"):
        print(f"  Project name detected: {titleblock['project_name']}")
    if titleblock.get("house_type_detected"):
        detected = titleblock["house_type_detected"]
        print(f"  House type in title block: {detected} ({titleblock.get('house_type_confidence')})")
        if not house_type and detected:
            house_type = detected
            print(f"  Using detected type: {house_type}")

    # ── Step 5: DWG extraction ────────────────────────────────────────────────
    print("\n[5/10] Extracting DWG/DXF geometry...")
    dwg_data = _run_dwg_extraction(files, args.force_convert)

    # ── Step 6: PDF extraction ────────────────────────────────────────────────
    print("\n[6/10] Extracting PDFs (AI vision)...")
    pdf_data = _run_pdf_extraction(files)

    # ── Step 7: BOM / IFC extraction ──────────────────────────────────────────
    print("\n[7/10] Extracting BOM / IFC...")
    bom_data = _run_bom_extraction(files)

    # ── Step 8: Merge ─────────────────────────────────────────────────────────
    print("\n[8/10] Merging sources...")
    from src.merger import merge_all
    if highset is not None:
        titleblock["highset_detected"] = highset
    merged = merge_all(dwg_data, pdf_data, bom_data, titleblock)
    _print_merge_summary(merged)

    # ── Step 9: Validate ──────────────────────────────────────────────────────
    print("\n[9/10] Validating data...")
    from src.validator import validate
    validation = validate(merged)
    print(f"  Checks    : {len(validation['relationship_checks'])}")
    print(f"  Conflicts : {len(validation['conflicts'])}")
    print(f"  Missing   : {len(validation['missing_scope'])}")

    # ── Step 10a: Calculate quantities ───────────────────────────────────────
    print("\n[10/10] Calculating quantities...")
    from src.quantity_calculator import calculate_quantities
    boq_items = calculate_quantities(standard_boq, merged, validation,
                                     standard_geometry=standard_geometry,
                                     debug=args.debug)

    # ── Step 10a.5: Apply rates ───────────────────────────────────────────────
    print("  Applying rates from library...")
    boq_items = _apply_rates(boq_items, house_type)

    # ── Step 10a.6: Cross-check against reference BOQ ────────────────────────
    from src.cross_checker import cross_check, format_report as _xc_format
    xc_ref_boq = standard_boq if approved_boq_path else None
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

    # ── Step 10b: Write BOQ ───────────────────────────────────────────────────
    print("\nWriting BOQ workbook...")
    from src.boq_writer import write_boq

    boq_path = write_boq(
        project_name=args.project,
        boq_items=boq_items,
        validation=validation,
        merged=merged,
        approved_boq_path=approved_boq_path,
    )

    # ── Step 10c: Write Summary ───────────────────────────────────────────────
    print("Writing Summary workbook...")
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
        )
        print(f"  Summary  : {summary_path.name}")
    except Exception as exc:
        import traceback as _tb
        print(f"  WARNING: Summary writer failed: {exc}")
        log.warning("Summary writer failed: %s\n%s", exc, _tb.format_exc())

    # ── Step 10d: QA Report ──────────────────────────────────────────────────
    print("Writing QA report...")
    from src.qa_reporter import generate_report
    report = generate_report(
        project_name=args.project,
        files_found=files,
        boq_items=boq_items,
        validation=validation,
        merged=merged,
        project_mode=mode,
        house_type=house_type or "custom",
    )

    # ── Final summary ─────────────────────────────────────────────────────────
    _print_final_summary(args.project, boq_items, validation, report, boq_path, summary_path)
    return 0


# ─── Mode selection ──────────────────────────────────────────────────────────

def _determine_mode(args, files: dict) -> tuple[str, str, bool | None]:
    """Return (mode, house_type, highset)."""
    from src.config import ProjectMode, STANDARD_MODELS_MAP

    house_type = (args.type or "").strip().upper()
    highset: bool | None = None
    if args.highset.lower() in ("y", "yes", "true", "1"):
        highset = True
    elif args.highset.lower() in ("n", "no", "false", "0"):
        highset = False

    if args.yes and house_type:
        mode = ProjectMode.STANDARD.value if house_type in STANDARD_MODELS_MAP else ProjectMode.GENERIC.value
        return mode, house_type, highset

    if args.yes:
        return ProjectMode.GENERIC.value, house_type, highset

    # Interactive
    print("""
What type of project is this?

  [1] Standard G-Range house (G303, G403E, G404, G504E, G302, G202, G201)
  [2] Non-standard / custom house
  [3] Auto-detect from drawings title block
""")
    choice = input("Enter choice [1/2/3]: ").strip()

    if choice == "1":
        types_str = ", ".join(STANDARD_MODELS_MAP.keys())
        if not house_type:
            house_type = input(f"House type ({types_str}): ").strip().upper()
        if highset is None:
            hs_ans = input("Highset with laundry? (y/n): ").strip().lower()
            highset = hs_ans in ("y", "yes")
        mode = ProjectMode.STANDARD.value
    elif choice == "3":
        print("  Auto-detecting from drawings...")
        mode = ProjectMode.STANDARD.value
        # Will be resolved after titleblock detection
    else:
        mode = ProjectMode.GENERIC.value
        use_ref = input("Use standard reference model as fallback? (y/n): ").strip().lower()
        if use_ref in ("y", "yes"):
            house_type = input(f"Reference model type: ").strip().upper()

    return mode, house_type, highset


# ─── Rate application ────────────────────────────────────────────────────────

def _apply_rates(boq_items: list, house_type: str) -> list:
    """Apply rates from the rate library. AI fallback if key set."""
    from src.config import DATA_DIR
    from src import ai_client as _ai

    rate_lib_path = DATA_DIR / "rate_library_2026_RPNG.xlsx"
    if not rate_lib_path.exists():
        # Try legacy location
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


# ─── Standard model loading ──────────────────────────────────────────────────

def _load_standard_model(house_type: str) -> tuple[list, dict]:
    from src.config import STANDARD_MODELS, STANDARD_MODELS_MAP, DATA_DIR
    from src.loader import load_standard_model, load_rate_library

    # Try new location first
    if house_type and house_type in STANDARD_MODELS_MAP:
        model_path = STANDARD_MODELS / STANDARD_MODELS_MAP[house_type]
    else:
        # Fall back to legacy location
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


def _get_reference_path(house_type: str) -> str | None:
    from src.config import STANDARD_MODELS, STANDARD_MODELS_MAP, DATA_DIR
    if house_type and house_type in STANDARD_MODELS_MAP:
        p = STANDARD_MODELS / STANDARD_MODELS_MAP[house_type]
        if p.exists():
            return str(p)
    candidates = list(DATA_DIR.glob("standard_model_G303*.xlsx"))
    return str(candidates[0]) if candidates else None


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
    print("    Standard BOQ    → fallback only")


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
