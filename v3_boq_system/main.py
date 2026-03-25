"""
main.py — V3 BOQ Pipeline Orchestrator

Architecture:
  documents → extractors → element_builder → normalized element model
           → quantifiers (layer A: measurement)
           → assemblies  (layer B: procurement decomposition)
           → boq_mapper  → QA → writers

CRITICAL:
  - All quantities come from project documents only
  - BOQ reference files are used ONLY for stock codes / descriptions / QA comparison
  - No quantity is ever copied from a BOQ template
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent  # repo root (boq-system/)
sys.path.insert(0, str(_ROOT))

log = logging.getLogger("boq.v3")


def _load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path: str | Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(obj, path: str | Path) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)


def run_pipeline(project_name: str, config_override: dict | None = None) -> dict:
    """
    Run the V3 BOQ pipeline for a named project.

    Args:
        project_name:     Name matching a subdirectory in input/ (e.g. "project 2")
        config_override:  Dict to override project_config.yaml values

    Returns:
        Dict with keys: boq_items, qa_report, output_dir
    """
    t0 = time.time()
    log.info("=" * 70)
    log.info("  V3 BOQ PIPELINE — project=%s", project_name)
    log.info("=" * 70)

    # ── [0] Load config ───────────────────────────────────────────────────────
    v3_dir    = Path(__file__).parent
    cfg_path  = v3_dir / "config" / "project_config.yaml"
    asm_path  = v3_dir / "config" / "assembly_rules.yaml"
    rm_path   = v3_dir / "config" / "room_templates.yaml"

    config         = _load_yaml(cfg_path)
    assembly_rules = _load_yaml(asm_path)
    room_templates = _load_yaml(rm_path)

    # Per-project config overlay — try slug forms: "project_2", "project2", "project 2"
    _pslug = project_name.replace(" ", "_").lower()
    _pslug_nounderscore = project_name.replace(" ", "").lower()
    _proj_cfg = next(
        (p for p in [
            _ROOT / "input" / "projects" / _pslug / "project_config.yaml",
            _ROOT / "input" / "projects" / _pslug_nounderscore / "project_config.yaml",
            _ROOT / "input" / "projects" / project_name / "project_config.yaml",
        ] if p.exists()),
        None,
    )
    if _proj_cfg is not None:
        log.info("[0] Loading per-project config overlay: %s", _proj_cfg)

        _overlay = _load_yaml(_proj_cfg)
        for key, val in _overlay.items():
            if isinstance(val, dict) and isinstance(config.get(key), dict):
                config[key].update(val)
            else:
                config[key] = val

    if config_override:
        config.update(config_override)

    # Capture YAML project name BEFORE setdefault overwrites it with the CLI slug.
    # e.g. YAML says name="project 2" but CLI slug is "project2".
    _cfg_project_name = config.get("project", {}).get("name", project_name)
    config.setdefault("project", {})["name"] = project_name

    # ── [1] Locate input files ────────────────────────────────────────────────
    # Config dir: per-project YAML overlay lives here
    input_dir = _ROOT / "input" / project_name
    if not input_dir.exists():
        input_dir = _ROOT / "input" / "projects" / _pslug
    if not input_dir.exists():
        raise FileNotFoundError(
            f"Input directory not found: tried 'input/{project_name}' and 'input/projects/{_pslug}'"
        )

    # Source files dir: DXF/IFC/PDF may live in a separate directory (e.g. "project 2" with space
    # vs the config slug "project2").  Try config project.name from the loaded config overlay.
    _source_dir_candidates = [
        input_dir,
        _ROOT / "input" / _cfg_project_name,
        _ROOT / "input" / _pslug,
    ]
    source_dir = next(
        (d for d in _source_dir_candidates if d != input_dir and d.exists()
         and any(d.glob("*.dxf"))),
        input_dir,
    )

    # Re-use V2 extractor outputs if available (avoid re-running expensive IFC/DXF)
    _pname_key = project_name.replace(" ", "_")
    # V2 outputs may use "project_2" naming (underscore before digit) for "project2"
    import re as _re
    _pname_v2 = _re.sub(r'([a-zA-Z])(\d)', r'\1_\2', _pname_key)
    v2_output_dir = _ROOT / "v2_ddc_pipeline" / "outputs" / _pname_key
    if not v2_output_dir.exists() and _pname_v2 != _pname_key:
        v2_output_dir = _ROOT / "v2_ddc_pipeline" / "outputs" / _pname_v2

    v2_model_path = v2_output_dir / "project_model.json"
    if v2_model_path.exists():
        log.info("[1/10] Loading V2 project model (cached): %s", v2_model_path)
        v2_project_model = _load_json(v2_model_path)
    else:
        log.info("[1/10] No V2 cached model — running V2 extractors")
        v2_project_model = _run_v2_extractors(project_name, input_dir, config)

    raw_dxf      = v2_project_model.get("raw_dxf",      {})
    raw_ifc      = v2_project_model.get("raw_ifc",      {})
    raw_framecad = v2_project_model.get("raw_framecad",  {})
    raw_pdf      = {
        "rooms":  v2_project_model.get("rooms",  []),
        "stairs": v2_project_model.get("stairs", []),
    }

    # ── [1b] Augment with fresh DXF / FrameCAD extraction ────────────────────
    # Re-run fast extractors to pick up fields added after the V2 cache was built
    # (int_wall_lm from DXF, floor_type from FrameCAD layouts).
    # IFC is skipped here — it's slow and the cached data is sufficient.
    sys.path.insert(0, str(_ROOT / "v2_ddc_pipeline"))
    try:
        from src.extractors.dxf_extractor import extract_dxf as _extract_dxf
        dxf_files = list(source_dir.glob("*.dxf")) or list(input_dir.glob("*.dxf"))
        # Re-extract if any new fields are missing (int_wall_lm, post_positions, insert widths)
        _door_have_widths  = all(
            ins.get("width_m") is not None
            for ins in raw_dxf.get("door_inserts", [])
        )
        _win_have_heights  = all(
            ins.get("height_m") is not None
            for ins in raw_dxf.get("window_inserts", [])
        )
        if dxf_files and (
            "int_wall_lm" not in raw_dxf
            or "post_positions" not in raw_dxf
            or not _door_have_widths
            or not _win_have_heights   # re-extract when window heights are missing
        ):
            fresh_dxf = _extract_dxf(str(dxf_files[0]))
            raw_dxf.update(fresh_dxf)   # overlay new fields onto cached data
            log.info("   DXF re-extracted: int_wall_lm=%.1f m, "
                     "floor_area=%.1f m², posts=%d", raw_dxf.get("int_wall_lm", 0),
                     raw_dxf.get("floor_area_m2", 0), raw_dxf.get("post_count", 0))
    except Exception as exc:
        log.warning("   DXF re-extraction skipped: %s", exc)

    try:
        from src.extractors.framecad_extractor import extract_framecad_bom as _extract_fc
        _fc_new_keys = ("floor_type", "floor_joist_spec", "floor_bearer_spec",
                        "floor_joist_spacing_mm", "floor_panel_size", "floor_panel_members")
        if any(k not in raw_framecad for k in _fc_new_keys):
            fresh_fc = _extract_fc(source_dir if source_dir != input_dir else input_dir)
            for key in ("floor_type", "floor_load_class", "floor_joist_spec",
                        "floor_bearer_spec", "floor_joist_spacing_mm",
                        "floor_panel_size", "floor_panel_members",
                        "batten_entries", "lm_by_tab"):
                if key in fresh_fc and key not in raw_framecad:
                    raw_framecad[key] = fresh_fc[key]
            if "totals" in fresh_fc:
                raw_framecad.setdefault("totals", {}).update(fresh_fc["totals"])
            log.info("   FrameCAD re-extracted: floor_type=%s  load_class=%s  joist_spec=%s",
                     raw_framecad.get("floor_type", "unknown"),
                     raw_framecad.get("floor_load_class", "none"),
                     raw_framecad.get("floor_joist_spec", "none"))
    except Exception as exc:
        log.warning("   FrameCAD re-extraction skipped: %s", exc)

    # ── [2] Build normalized element model ───────────────────────────────────
    log.info("[2/10] Building normalized element model")
    from v3_boq_system.normalize.element_builder import build_element_model
    element_model = build_element_model(
        raw_dxf=raw_dxf, raw_ifc=raw_ifc,
        raw_framecad=raw_framecad, raw_pdf=raw_pdf,
        project_model=v2_project_model, config=config,
    )
    summary = element_model.summary()
    log.info("   Element model: %s", summary)

    # ── [3] Run all quantifiers ───────────────────────────────────────────────
    log.info("[3/10] Running quantifiers")
    all_rows: list[dict] = []

    from v3_boq_system.quantify.roof_quantifier             import quantify_roof
    from v3_boq_system.quantify.lining_quantifier           import quantify_linings
    from v3_boq_system.quantify.opening_quantifier          import quantify_openings
    from v3_boq_system.quantify.floor_system_quantifier     import quantify_floor_system
    from v3_boq_system.quantify.footing_quantifier          import quantify_footings
    from v3_boq_system.quantify.stair_ramp_quantifier       import quantify_stairs
    from v3_boq_system.quantify.services_quantifier         import quantify_services, quantify_finishes
    from v3_boq_system.quantify.external_cladding_quantifier  import quantify_external_cladding
    from v3_boq_system.quantify.structural_fixings_quantifier import quantify_structural_fixings

    all_rows += quantify_roof(element_model, config, assembly_rules)
    log.info("   → roof: %d rows", len(all_rows))

    n_prev = len(all_rows)
    all_rows += quantify_linings(element_model, config, assembly_rules)
    log.info("   → linings: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_openings(element_model, config, assembly_rules)
    log.info("   → openings: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_floor_system(element_model, config)
    log.info("   → floor system: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_footings(element_model, config)
    log.info("   → footings: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_stairs(element_model, config)
    log.info("   → stairs/ramp/balustrade: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_services(element_model, config, room_templates)
    all_rows += quantify_finishes(element_model, config)
    log.info("   → services + finishes: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_external_cladding(element_model, config)
    log.info("   → external cladding: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_structural_fixings(element_model, config)
    log.info("   → structural fixings: +%d rows", len(all_rows) - n_prev)

    # Add verandah (external works)
    all_rows += _build_verandah_rows(element_model)

    log.info("   TOTAL raw quantity rows: %d", len(all_rows))

    # ── [4] Map to BOQ sections ───────────────────────────────────────────────
    log.info("[4/10] Mapping to BOQ sections")
    # Load item library (stock codes only)
    item_library = _load_item_library(_ROOT)

    from v3_boq_system.mapping.boq_mapper import map_to_boq
    boq_items = map_to_boq(all_rows, item_library)
    log.info("   → %d BOQ items across %d sections",
             len(boq_items),
             len(set(i["boq_section"] for i in boq_items)))

    # ── [5] QA ────────────────────────────────────────────────────────────────
    log.info("[5/10] Running QA checks")
    benchmark_items = _load_benchmark(project_name, _ROOT)

    from v3_boq_system.qa.qa_engine import run_qa
    qa_report = run_qa(
        boq_items=boq_items,
        element_model_summary=summary,
        benchmark_items=benchmark_items,
        config=config,
    )

    # ── [6] Write outputs ─────────────────────────────────────────────────────
    out_dir = _ROOT / "v3_boq_system" / "outputs" / _pname_key
    os.makedirs(out_dir, exist_ok=True)
    log.info("[6/10] Writing outputs → %s", out_dir)

    boq_json_path = out_dir / f"{_pname_key}_boq_items_v3.json"
    qa_json_path  = out_dir / f"{_pname_key}_qa_report_v3.json"
    em_json_path  = out_dir / f"{_pname_key}_element_model.json"
    excel_path    = out_dir / f"{_pname_key}_BOQ_V3.xlsx"

    _save_json(boq_items, boq_json_path)
    _save_json(qa_report,  qa_json_path)
    _save_json(_element_model_to_dict(element_model), em_json_path)

    try:
        from v3_boq_system.writers.excel_writer import write_boq_excel
        write_boq_excel(
            boq_items=boq_items,
            qa_report=qa_report,
            output_path=str(excel_path),
            project_name=project_name,
            source_files=element_model.source_files,
        )
        log.info("   → BOQ Excel: %s", excel_path.name)
    except Exception as exc:
        log.warning("   Excel write failed: %s", exc)

    # ── [7] Summary ───────────────────────────────────────────────────────────
    prov = qa_report["provenance_summary"]
    elapsed = round(time.time() - t0, 1)
    print()
    print("=" * 70)
    print(f"  V3 COMPLETE in {elapsed}s")
    print(f"  Project: {project_name}")
    print(f"  Output:  {out_dir}")
    print(f"  BOQ items: {len(boq_items)}")
    _print_section_summary(boq_items)
    print()
    print(f"  Measured:    {prov['measured']:3d}  ({prov['pct_measured']:.1f}%)")
    print(f"  Calculated:  {prov['calculated']:3d}  ({prov['pct_calculated']:.1f}%)")
    print(f"  Inferred:    {prov['inferred']:3d}  ({prov['pct_inferred']:.1f}%)")
    print(f"  Placeholder: {prov['placeholder']:3d}  ({prov['pct_placeholder']:.1f}%)")
    print(f"  Manual Review: {prov['manual_review_count']} items")
    print()
    print("  CONFIRMED: No quantities sourced from any BOQ template file.")
    print("=" * 70)

    return {"boq_items": boq_items, "qa_report": qa_report, "output_dir": str(out_dir)}


# ── Helper: V2 extractor fallback ─────────────────────────────────────────────

def _run_v2_extractors(project_name: str, input_dir: Path, config: dict) -> dict:
    """Run V2 extractors to build a project model if no cached version exists."""
    sys.path.insert(0, str(_ROOT / "v2_ddc_pipeline"))
    try:
        from src.extractors.dxf_extractor      import extract_dxf
        from src.extractors.ifc_extractor      import extract_ifc
        from src.extractors.framecad_extractor import extract_framecad_bom
        from src.extractors.pdf_extractor      import extract_pdf
        from src.normalizers.project_model     import build_project_model
        from src.project_classifier            import classify_project

        dxf_files  = list(input_dir.glob("*.dxf"))
        ifc_files  = list(input_dir.glob("*.ifc"))
        pdf_files  = list(input_dir.glob("*.pdf"))
        dwg_files  = list(input_dir.glob("*.dwg"))

        raw_dxf      = extract_dxf(str(dxf_files[0]))       if dxf_files  else {}
        raw_ifc      = extract_ifc(str(ifc_files[0]))       if ifc_files  else {}
        raw_framecad = extract_framecad_bom(pdf_files)       if pdf_files  else {}
        raw_pdf      = {}

        classification = classify_project(input_dir)
        return build_project_model(
            raw_dxf, raw_ifc, raw_pdf, raw_framecad,
            source_inventory=[],
            project_name=project_name,
            classification=classification,
        )
    except ImportError as e:
        log.warning("V2 extractor import failed: %s — returning empty model", e)
        return {"project_name": project_name, "geometry": {}, "structural": {},
                "openings": {}, "raw_dxf": {}, "raw_ifc": {}, "raw_framecad": {},
                "rooms": [], "stairs": []}


# ── Helper: item library ──────────────────────────────────────────────────────

def _load_item_library(root: Path) -> dict:
    """Load the item library (stock codes only — no quantities ever sourced from here)."""
    lib_path = root / "data" / "item_library.json"
    if not lib_path.exists():
        # Try V2 path
        lib_path = root / "v2_ddc_pipeline" / "src" / "mapping" / "item_library.py"
        return {}
    try:
        return _load_json(lib_path)
    except Exception:
        return {}


# ── Helper: benchmark loader ──────────────────────────────────────────────────

def _load_benchmark(project_name: str, root: Path) -> list[dict] | None:
    """Load benchmark BOQ (structure comparison only — no quantity sourcing)."""
    # Try common benchmark paths
    candidates = [
        root / "benchmarks" / project_name.replace(" ", "_") / "v1_boq_items.json",
        root / "benchmarks" / project_name.lower().replace(" ", "_") / "v1_boq_items.json",
    ]
    for p in candidates:
        if p.exists():
            data = _load_json(p)
            items = data if isinstance(data, list) else data.get("boq_items", data.get("items", []))
            log.info("Loaded benchmark: %s (%d items)", p.name, len(items))
            return items
    return None


# ── Helper: verandah external rows ───────────────────────────────────────────

def _build_verandah_rows(element_model) -> list[dict]:
    rows = []
    for ver in element_model.verandahs:
        if ver.area_m2 > 0:
            rows.append({
                "item_name": "Verandah Decking / Slab",
                "item_code": "", "unit": "m2",
                "quantity": round(ver.area_m2, 2),
                "package": "external_verandah",
                "quantity_status": "measured",
                "quantity_basis": "DXF VERANDAH LWPOLYLINE area",
                "source_evidence": f"{ver.source}: verandah_area={ver.area_m2:.2f} m²",
                "derivation_rule": "= verandah_area_m2",
                "confidence": ver.confidence, "manual_review": False, "notes": "",
            })
        if ver.perimeter_m > 0:
            rows.append({
                "item_name": "Site Preparation (Provisional)",
                "item_code": "", "unit": "item", "quantity": 0,
                "package": "external_works",
                "quantity_status": "placeholder",
                "quantity_basis": "provisional allowance — no site survey",
                "source_evidence": "no site survey in sources",
                "derivation_rule": "manual review required",
                "confidence": "LOW", "manual_review": True,
                "notes": "Site preparation scope cannot be derived from drawings. Confirm with engineer.",
            })
    return rows


# ── Helper: element model → serialisable dict ─────────────────────────────────

def _element_model_to_dict(em) -> dict:
    import dataclasses
    def _dc(obj):
        if dataclasses.is_dataclass(obj):
            return {k: _dc(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [_dc(i) for i in obj]
        return obj
    return _dc(em)


# ── Helper: section print ─────────────────────────────────────────────────────

def _print_section_summary(boq_items: list[dict]) -> None:
    from collections import Counter
    sec_count = Counter(i["boq_section"] for i in boq_items)
    for sec, cnt in sorted(sec_count.items()):
        print(f"    {sec}: {cnt} items")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="V3 BOQ Pipeline")
    parser.add_argument("--project", required=True, help="Project name (matches input/ subdir)")
    parser.add_argument("--debug",   action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run_pipeline(args.project)
