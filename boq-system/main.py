"""
main.py — Runs the full BOQ automation pipeline.

Usage:
  python main.py --project "Job123" --dxf input/drawings/job123.dxf
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --pdf input/pdfs/
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --bom input/bom/job123_bom.xlsx
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --phase 1
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.loader import load_standard_model, load_rate_library
from src.normaliser import normalise_dxf, NormalisationError, get_normalisation_report
from src.extractor import extract_geometry
from src.change_detector import detect_changes, summarise_changes
from src.quantity_calculator import calculate_quantities, discover_new_items
from src.dependent_scaler import scale_dependent_items, auto_detect_dependents
from src.pricer import apply_rates
from src.qa_checker import run_qa_checks
from src.writer import write_output


def main():
    args = parse_args()

    print("=" * 60)
    print(f"  BOQ Automation System — G303 Standard House Model")
    print(f"  Project: {args.project}")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    errors = []
    phase = args.phase or 2  # Default to full pipeline

    # ── Step 1: Load standard model ──
    print("\n[Step 1/10] Loading standard model...")
    try:
        model = load_standard_model(args.model)
        standard_boq = model["standard_boq"]
        standard_geometry = model["standard_geometry"]
        rules = model["rules"]
        print(f"  ✓ Loaded {len(standard_boq)} BOQ items, "
              f"{len(standard_geometry)} geometry elements, "
              f"{len(rules)} rules")
    except Exception as e:
        print(f"  ✗ FATAL: {e}")
        sys.exit(1)

    # ── Step 2: Load rate library ──
    print("\n[Step 2/10] Loading rate library...")
    try:
        rate_library = load_rate_library(args.rates)
        print(f"  ✓ Loaded {len(rate_library)} rate entries")
    except Exception as e:
        print(f"  ✗ WARNING: {e}")
        rate_library = []
        errors.append(f"Rate library load failed: {e}")

    # ── Step 3: Normalise DXF ──
    print("\n[Step 3/10] Normalising DXF...")
    try:
        doc = normalise_dxf(args.dxf)
        report = get_normalisation_report(doc)
        print(f"  ✓ Normalised: {report['entity_count']} entities, "
              f"{report['layer_count']} layers")
        if report.get("extents"):
            ext = report["extents"]
            print(f"  ✓ Extents: {ext['width']:.0f} x {ext['height']:.0f} mm")
    except NormalisationError as e:
        print(f"  ✗ ABORT: Normalisation failed — {e}")
        print("  Pipeline cannot continue with misaligned geometry.")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ ABORT: {e}")
        sys.exit(1)

    # ── Step 4: Extract project geometry ──
    print("\n[Step 4/10] Extracting project geometry...")
    try:
        project_geometry = extract_geometry(doc)
        print(f"  ✓ Floor area: {project_geometry.get('total_floor_area', 0)} m²")
        print(f"  ✓ Wall length: {project_geometry.get('total_wall_length', 0)} lm")
        print(f"  ✓ Roof area: {project_geometry.get('roof_area', 0)} m²")
        print(f"  ✓ Doors: {project_geometry.get('door_count', 0)}, "
              f"Windows: {project_geometry.get('window_count', 0)}")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")
        traceback.print_exc()
        project_geometry = {}
        errors.append(f"Geometry extraction failed: {e}")

    # ── Step 5: Read PDF schedules (Phase 2 only) ──
    pdf_data = None
    if phase >= 2 and args.pdf:
        print("\n[Step 5/10] Reading PDF schedules (GPT-4o vision)...")
        try:
            from src.pdf_reader import read_pdfs
            pdf_data = read_pdfs(args.pdf)
            page_count = len(pdf_data.get("pages", []))
            door_count = len(pdf_data.get("door_schedule", []))
            window_count = len(pdf_data.get("window_schedule", []))
            print(f"  ✓ Processed {page_count} pages")
            print(f"  ✓ Extracted: {door_count} door entries, "
                  f"{window_count} window entries")
            if pdf_data.get("errors"):
                for err in pdf_data["errors"]:
                    print(f"  ⚠ {err}")
                    errors.append(err)
        except Exception as e:
            print(f"  ⚠ PDF reading failed: {e}")
            errors.append(f"PDF reading failed: {e}")
    else:
        print("\n[Step 5/10] Skipping PDF reading (Phase 1 or no PDF path)")

    # ── Step 6: Load Framecad BOM (if provided) ──
    bom_data = None
    if args.bom:
        print("\n[Step 5b] Loading Framecad BOM...")
        try:
            bom_data = _load_bom(args.bom)
            print(f"  ✓ Loaded {len(bom_data)} BOM items")
        except Exception as e:
            print(f"  ⚠ BOM load failed: {e}")
            errors.append(f"BOM load failed: {e}")

    # ── Step 7: Detect changes ──
    print("\n[Step 6/10] Detecting changes vs standard...")
    change_log = detect_changes(project_geometry, standard_geometry)
    summary = summarise_changes(change_log)
    print(f"  ✓ Compared {summary['total_elements_compared']} elements")
    print(f"  ✓ Changed: {summary['changed_count']}, "
          f"Unchanged: {summary['unchanged_count']} "
          f"({100 - summary['change_percentage']:.0f}% pass-through)")
    if summary.get("largest_delta"):
        ld = summary["largest_delta"]
        print(f"  ✓ Largest change: {ld['element']} ({ld['delta_pct']:+.1f}%)")

    # ── Step 8: Build project BOQ ──
    print("\n[Step 7/10] Building project BOQ...")
    project_boq = calculate_quantities(
        standard_boq, change_log, project_geometry, rules, pdf_data
    )

    # Override with BOM data if available
    if bom_data:
        project_boq = _apply_bom_overrides(project_boq, bom_data)

    # Discover new items (requires API key)
    if os.getenv("OPENAI_API_KEY"):
        new_items = discover_new_items(
            project_boq, change_log, project_geometry, pdf_data
        )
        if new_items:
            project_boq.extend(new_items)
            print(f"  ✓ Discovered {len(new_items)} new items via AI")

    unchanged = sum(1 for i in project_boq if i.get("source") == "standard")
    recalculated = sum(1 for i in project_boq if i.get("source") == "recalculated")
    new = sum(1 for i in project_boq if i.get("source") == "new")
    print(f"  ✓ BOQ: {len(project_boq)} items total")
    print(f"    Unchanged: {unchanged}, Recalculated: {recalculated}, New: {new}")

    # ── Step 9: Scale dependent items (Phase 2) ──
    if phase >= 2:
        print("\n[Step 8/10] Scaling dependent items...")
        project_boq = scale_dependent_items(project_boq, change_log, rules)
        scaled = sum(1 for i in project_boq if i.get("source") == "rule-scaled")
        print(f"  ✓ Rule-scaled: {scaled} items")

        unruled = auto_detect_dependents(project_boq)
        if unruled:
            print(f"  ⚠ {len(unruled)} fixing/fastener items have no scaling rule")
    else:
        print("\n[Step 8/10] Skipping dependent scaling (Phase 1)")

    # ── Step 10: Apply rates ──
    print("\n[Step 9/10] Applying rates...")
    use_ai = bool(os.getenv("OPENAI_API_KEY")) and phase >= 2
    project_boq = apply_rates(project_boq, rate_library, use_ai_fallback=use_ai)

    library_rates = sum(1 for i in project_boq if i.get("rate_source") == "library")
    standard_rates = sum(1 for i in project_boq if i.get("rate_source") == "standard")
    ai_rates = sum(1 for i in project_boq if i.get("rate_source") == "AI-estimate")
    manual_rates = sum(1 for i in project_boq if i.get("rate_source") == "manual-required")
    print(f"  ✓ Rates: library={library_rates}, standard={standard_rates}, "
          f"AI={ai_rates}, manual-needed={manual_rates}")

    # ── Step 11: QA checks ──
    print("\n[Step 10/10] Running QA checks...")
    qa_flags = run_qa_checks(project_boq, standard_boq, project_geometry, change_log)
    passes = sum(1 for f in qa_flags if f.get("status") == "PASS")
    warns = sum(1 for f in qa_flags if f.get("status") == "WARN")
    fails = sum(1 for f in qa_flags if f.get("status") == "FAIL")
    print(f"  ✓ QA: {passes} pass, {warns} warnings, {fails} failures")

    for flag in qa_flags:
        if flag.get("status") == "FAIL":
            print(f"  ✗ FAIL: {flag['check']} — {flag['details']}")
        elif flag.get("status") == "WARN":
            print(f"  ⚠ WARN: {flag['check']} — {flag['details']}")

    # ── Write output ──
    print("\n" + "=" * 60)
    print("Writing output...")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    filepath = write_output(
        project_name=args.project,
        project_boq=project_boq,
        change_log=change_log,
        standard_boq=standard_boq,
        qa_flags=qa_flags,
        output_dir=output_dir,
    )
    print(f"  ✓ Output saved: {filepath}")

    # ── Summary ──
    total_amount = sum(
        float(i.get("qty", 0) or 0) * float(i.get("rate", 0) or 0)
        for i in project_boq
    )
    print(f"\n  Total BOQ Amount: PGK {total_amount:,.2f}")
    print(f"  Items: {len(project_boq)}")
    print(f"  Confidence: HIGH={sum(1 for i in project_boq if i.get('confidence') == 'HIGH')}, "
          f"MED={sum(1 for i in project_boq if i.get('confidence') == 'MEDIUM')}, "
          f"LOW={sum(1 for i in project_boq if i.get('confidence') == 'LOW')}")

    if errors:
        print(f"\n  ⚠ {len(errors)} non-fatal errors occurred:")
        for err in errors:
            print(f"    - {err}")

    print("\n" + "=" * 60)
    print("  Pipeline complete.")
    print("=" * 60)


def _load_bom(bom_path: str) -> list[dict]:
    """Load Framecad BOM export."""
    import openpyxl

    wb = openpyxl.load_workbook(bom_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=1, values_only=True))

    if not rows:
        wb.close()
        return []

    headers = [str(h).strip().lower() if h else "" for h in rows[0]]
    items = []

    for row in rows[1:]:
        if not row or all(cell is None for cell in row):
            continue
        item = {}
        for i, header in enumerate(headers):
            if i < len(row):
                item[header.replace(" ", "_")] = row[i]
        if item.get("description") or item.get("part_name"):
            items.append(item)

    wb.close()
    return items


def _apply_bom_overrides(project_boq: list[dict], bom_data: list[dict]) -> list[dict]:
    """Override structural quantities with Framecad BOM data.

    BOM is king — if present, its structural quantities override everything.
    Mark overridden items as HIGH confidence.
    """
    # Build BOM lookup by description keywords
    bom_lookup = {}
    for bom_item in bom_data:
        desc = str(bom_item.get("description", bom_item.get("part_name", ""))).lower()
        bom_lookup[desc] = bom_item

    for i, item in enumerate(project_boq):
        item_desc = str(item.get("description", "")).lower()

        # Check for structural items that match BOM
        for bom_desc, bom_item in bom_lookup.items():
            if not bom_desc:
                continue

            # Fuzzy match: check if key words overlap
            bom_words = set(bom_desc.split())
            item_words = set(item_desc.split())
            common = bom_words & item_words

            if len(common) >= 2 or bom_desc in item_desc:
                bom_qty = bom_item.get("quantity", bom_item.get("qty", 0))
                try:
                    bom_qty = float(bom_qty)
                except (ValueError, TypeError):
                    continue

                project_boq[i]["qty"] = bom_qty
                project_boq[i]["confidence"] = "HIGH"
                project_boq[i]["source"] = "BOM"
                project_boq[i]["notes"] = (
                    f"Framecad BOM override. Was {item.get('qty', 0)}, "
                    f"BOM says {bom_qty}"
                )
                break

    return project_boq


def parse_args():
    parser = argparse.ArgumentParser(
        description="BOQ Automation System — G303 Standard House Model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --project "Job123" --dxf input/drawings/job123.dxf
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --pdf input/pdfs/
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --bom input/bom/job123_bom.xlsx
  python main.py --project "Job123" --dxf input/drawings/job123.dxf --phase 1
        """,
    )

    parser.add_argument(
        "--project", required=True,
        help="Project name (used in output filename)",
    )
    parser.add_argument(
        "--dxf", required=True,
        help="Path to project DXF file",
    )
    parser.add_argument(
        "--pdf", default=None,
        help="Path to directory containing PDF drawing sets",
    )
    parser.add_argument(
        "--bom", default=None,
        help="Path to Framecad BOM Excel export",
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2], default=None,
        help="Pipeline phase (1=basic, 2=full with AI). Default: 2",
    )
    parser.add_argument(
        "--model", default=None,
        help="Path to standard model Excel file",
    )
    parser.add_argument(
        "--rates", default=None,
        help="Path to rate library Excel file",
    )

    args = parser.parse_args()

    # Default paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if args.model is None:
        args.model = os.path.join(base_dir, "data", "standard_model_G303.xlsx")
    if args.rates is None:
        args.rates = os.path.join(base_dir, "data", "rate_library.xlsx")

    return args


if __name__ == "__main__":
    main()
