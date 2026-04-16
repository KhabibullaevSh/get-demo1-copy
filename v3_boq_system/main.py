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


def run_pipeline(
    project_name: str,
    config_override: dict | None = None,
    export_style: str = "estimator",
) -> dict:
    """
    Run the V3 BOQ pipeline for a named project.

    Args:
        project_name:     Name matching a subdirectory in input/ (e.g. "project 2")
        config_override:  Dict to override project_config.yaml values
        export_style:     BOQ presentation mode: "engine" | "commercial" | "estimator"

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

    # ── [1c] DWG floor panel extraction ──────────────────────────────────────
    # Runs after FrameCAD re-extraction so floor_type="steel" is already in raw_framecad.
    # Only triggers when DWG present and no DWG data cached yet.
    try:
        if not raw_framecad.get("dwg_floor_panel_count"):
            _all_dwgs = list(source_dir.glob("*.dwg")) or list(input_dir.glob("*.dwg"))
            # Prefer FrameCAD structural DWG (filename contains "frameclad" or "framecad")
            _fc_dwgs  = [f for f in _all_dwgs
                         if any(x in f.name.lower() for x in ("frameclad", "framecad", "structural"))]
            dwg_files = _fc_dwgs or _all_dwgs
            if dwg_files:
                from src.extractors.framecad_floor_dwg import extract_floor_from_dwg
                _floor_area_dxf = raw_dxf.get("main_floor_area_m2") or (
                    raw_dxf.get("floor_area_m2", 0) - raw_dxf.get("verandah_area_m2", 0)
                )
                dwg_result = extract_floor_from_dwg(dwg_files[0], floor_area_m2=_floor_area_dxf)
                if dwg_result:
                    # Merge DWG-derived keys into raw_framecad (do not overwrite existing HIGH-conf data)
                    for key in (
                        "dwg_floor_panel_count", "dwg_floor_panel_count_confidence",
                        "dwg_floor_panel_count_note", "dwg_floor_member_schedule",
                        "panel_width_mm", "panel_depth_mm", "panel_area_m2",
                        "joist_profile", "joist_length_mm", "joist_qty_per_panel",
                        "joist_total_nr", "joist_lm_total",
                        "edge_beam_profile", "edge_beam_length_mm",
                        "edge_beam_qty_per_panel", "edge_beam_total_nr", "edge_beam_lm_total",
                        "stringer_profile", "stringer_length_mm",
                        "stringer_qty_per_panel", "stringer_total_nr", "stringer_lm_total",
                        "floor_panel_size", "source", "dwg_path", "dxf_temp_path",
                    ):
                        if key in dwg_result:
                            raw_framecad[key] = dwg_result[key]
                    # floor_joist_spec: only set if not already populated
                    if not raw_framecad.get("floor_joist_spec") and dwg_result.get("floor_joist_spec"):
                        raw_framecad["floor_joist_spec"] = dwg_result["floor_joist_spec"]
                    log.info(
                        "   DWG floor extracted: %d panels (%s), J1=%d×%dmm, "
                        "E1+E2=%d×%dmm, S1+S2=%d×%dmm",
                        dwg_result.get("dwg_floor_panel_count", 0),
                        dwg_result.get("dwg_floor_panel_count_confidence", "?"),
                        dwg_result.get("joist_total_nr", 0),
                        dwg_result.get("joist_length_mm", 0),
                        dwg_result.get("edge_beam_total_nr", 0),
                        dwg_result.get("edge_beam_length_mm", 0),
                        dwg_result.get("stringer_total_nr", 0),
                        dwg_result.get("stringer_length_mm", 0),
                    )
    except Exception as exc:
        log.warning("   DWG floor extraction skipped: %s", exc)

    # ── [1d] PDF schedule extraction ─────────────────────────────────────────
    # Scan all PDFs for schedule evidence: window/door schedules, room finish
    # schedules, services schedules, and FrameCAD layout data (pitch, marks).
    log.info("[1d] Extracting schedule data from source PDFs")
    raw_pdf_schedules: dict = {}
    try:
        from v3_boq_system.extractors.pdf_schedule_extractor import extract_pdf_schedules
        _pdf_files = (
            list(source_dir.glob("*.pdf")) or list(input_dir.glob("*.pdf"))
        )
        if _pdf_files:
            raw_pdf_schedules = extract_pdf_schedules(_pdf_files)
            log.info(
                "   PDF schedules: pitch=%s° | marks=%d | panels=%d | trusses=%d "
                "| not_found=%s",
                raw_pdf_schedules.get("roof_pitch_degrees", "None"),
                len(raw_pdf_schedules.get("opening_marks",        [])),
                len(raw_pdf_schedules.get("wall_panel_ids",       [])),
                len(raw_pdf_schedules.get("roof_truss_ids",       [])),
                raw_pdf_schedules.get("schedules_not_found", []),
            )
        else:
            log.info("   No PDF files found in %s — schedule extraction skipped", source_dir)
    except Exception as exc:
        log.warning("   PDF schedule extraction failed: %s — continuing", exc)

    # ── [1e] DXF annotation extraction ───────────────────────────────────────
    # Extract ALL text/mtext/dimension/attrib entities from the architectural DXF.
    # This goes beyond the V2 geometry extractor to find annotations (window marks,
    # door hints, stair riser/tread notes, footing details, paper-space schedules).
    log.info("[1e] Extracting annotations from architectural DXF")
    raw_dxf_annotations: dict = {}
    try:
        from v3_boq_system.extractors.dxf_annotation_extractor import extract_dxf_annotations
        _dxf_files = list(source_dir.glob("*.dxf")) or list(input_dir.glob("*.dxf"))
        # Prefer the architectural DXF (not FrameCAD structural)
        _arch_dxf = next(
            (f for f in _dxf_files if "arch" in f.name.lower() or "angau" in f.name.lower()),
            _dxf_files[0] if _dxf_files else None,
        )
        if _arch_dxf:
            raw_dxf_annotations = extract_dxf_annotations(_arch_dxf)
            rf = raw_dxf_annotations.get("recovered_fields", {})
            log.info(
                "   DXF annotations: %d text | %d dims | %d attribs | %d pspace "
                "| win_h=%d | door_hints=%d | stair=%d | footing=%d",
                len(raw_dxf_annotations.get("text_entities", [])),
                len(raw_dxf_annotations.get("dimension_entities", [])),
                len(raw_dxf_annotations.get("attrib_entities", [])),
                len(raw_dxf_annotations.get("paper_space_text", [])),
                len(rf.get("window_heights", [])),
                len(rf.get("door_hints", [])),
                len(rf.get("stair_details", [])),
                len(rf.get("footing_details", [])),
            )
        else:
            log.info("   No DXF files found — annotation extraction skipped")
    except Exception as exc:
        log.warning("   DXF annotation extraction failed: %s — continuing", exc)

    # ── [1f] PDF graphical analysis + targeted OCR ────────────────────────────
    # Detect schedule-like table regions from vector line patterns in PDFs.
    # Rasterize those regions and attempt OCR to recover schedule text.
    log.info("[1f] PDF graphical analysis + targeted OCR")
    raw_pdf_graphics: dict = {"pages": [], "all_regions": [], "notes": []}
    raw_ocr_results:  dict = {"ocr_backend": "unavailable", "crops": [], "recovered": {}, "notes": []}
    try:
        from v3_boq_system.extractors.pdf_graphics_analyzer import analyze_pdf_graphics
        from v3_boq_system.extractors.pdf_region_detector   import detect_and_crop_regions
        from v3_boq_system.extractors.pdf_targeted_ocr      import run_targeted_ocr

        _pdf_files_for_graphics = (
            list(source_dir.glob("*.pdf")) or list(input_dir.glob("*.pdf"))
        )
        # Only run on PDFs that have zero or minimal text (graphical PDFs)
        all_graphics_regions: list[dict] = []
        all_crops:            list[dict] = []
        for _pdf in _pdf_files_for_graphics:
            _g = analyze_pdf_graphics(_pdf)
            raw_pdf_graphics["pages"].extend(_g.get("pages", []))
            raw_pdf_graphics["all_regions"].extend(_g.get("all_regions", []))
            raw_pdf_graphics["notes"].extend(_g.get("notes", []))

            n_regions = len(_g.get("all_regions", []))
            log.info(
                "   PDF graphics: %s → %d pages | %d table candidates",
                _pdf.name, len(_g.get("pages", [])), n_regions,
            )
            if n_regions > 0:
                _crop_result = detect_and_crop_regions(_pdf, _g, min_confidence="MEDIUM")
                all_crops.extend(_crop_result.get("crops", []))

        if all_crops:
            raw_ocr_results = run_targeted_ocr(all_crops)
            log.info(
                "   OCR: backend=%s | %d crops | win_rows=%d | door_rows=%d",
                raw_ocr_results.get("ocr_backend"),
                len(all_crops),
                len(raw_ocr_results.get("recovered", {}).get("window_schedule_rows", [])),
                len(raw_ocr_results.get("recovered", {}).get("door_schedule_rows", [])),
            )
        else:
            log.info("   No schedule-region crops generated — OCR skipped")
    except Exception as exc:
        log.warning("   PDF graphical analysis / OCR failed: %s — continuing", exc)

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

    # ── [2a] Integrate PDF schedule data into element model ───────────────────
    pdf_schedule_data = None
    if raw_pdf_schedules:
        try:
            from v3_boq_system.normalize.schedule_builder import build_schedule_model
            pdf_schedule_data = build_schedule_model(
                raw_pdf_schedules=raw_pdf_schedules,
                element_model=element_model,
                config=config,
            )
            log.info(
                "   PDF schedule model: pitch=%s° (src=%s) | marks=%d | "
                "panels=%d | trusses=%d | not_found=%s",
                pdf_schedule_data.roof_pitch_degrees,
                pdf_schedule_data.roof_pitch_source,
                len(pdf_schedule_data.opening_marks),
                len(pdf_schedule_data.wall_panel_ids),
                len(pdf_schedule_data.roof_truss_ids),
                pdf_schedule_data.schedules_not_found,
            )
        except Exception as exc:
            log.warning("   PDF schedule model build failed: %s — continuing", exc)

    # ── [2b] Build space model (PASS 1) ──────────────────────────────────────
    log.info("[2b] Building space model (space/room extraction)")
    try:
        from v3_boq_system.extractors.space_dxf_extractor import (
            extract_spaces_from_dxf,
            extract_spaces_from_wall_network,
        )
        from v3_boq_system.normalize.space_builder import build_space_model

        _dxf_for_spaces = list(source_dir.glob("*.dxf")) or list(input_dir.glob("*.dxf"))
        _dxf_path = _dxf_for_spaces[0] if _dxf_for_spaces else None
        dxf_spaces         = extract_spaces_from_dxf(_dxf_path)         if _dxf_path else []
        wall_network_zones = extract_spaces_from_wall_network(_dxf_path) if _dxf_path else []
        build_space_model(
            element_model=element_model,
            raw_ifc=raw_ifc,
            raw_dxf=raw_dxf,
            dxf_spaces=dxf_spaces,
            wall_network_zones=wall_network_zones,
            config=config,
        )
        log.info(
            "   Space model: %d spaces (%d enclosed, %d wet, %d verandah)",
            len(element_model.spaces),
            len(element_model.enclosed_spaces()),
            len(element_model.wet_spaces()),
            len(element_model.verandah_spaces()),
        )
    except Exception as exc:
        log.warning("   Space model build failed: %s — continuing without space model", exc)

    # ── [2c] Graphical + annotation reconciliation (PRE-QUANTIFICATION) ──────
    # Must run before [3] so that element_model.openings[x].height_m is set
    # correctly (window heights from FrameCAD labels) when opening_quantifier runs.
    graphical_recovery: dict = {}
    try:
        from v3_boq_system.reconcile.graphical_schedule_reconciler import reconcile_graphical_evidence
        graphical_recovery = reconcile_graphical_evidence(
            dxf_annotation_result=raw_dxf_annotations,
            pdf_graphics_result=raw_pdf_graphics,
            ocr_result=raw_ocr_results,
            element_model=element_model,
            config=config,
        )
        log.info(
            "   [2c] Graphical pre-reconciliation: promoted=%d | still_blocked=%d "
            "| win_h=%d | door_hints=%d",
            len(graphical_recovery.get("promoted_rows", [])),
            len(graphical_recovery.get("still_blocked", [])),
            len(graphical_recovery.get("window_heights_recovered", [])),
            len(graphical_recovery.get("door_hints_recovered", [])),
        )
    except Exception as exc:
        log.warning("   [2c] Graphical pre-reconciliation failed: %s — continuing", exc)

    # ── [2d] Build canonical geometry model ───────────────────────────────────
    # Post-reconciliation canonical layer: fuses multi-source candidates,
    # classifies each opening / wall face / space, pre-computes net areas.
    # Must run AFTER [2c] so that window heights from FrameCAD labels are resolved.
    canonical_geom = None
    try:
        from v3_boq_system.normalize.geometry_reconciler import build_canonical_geometry
        from v3_boq_system.normalize.geometry_index import GeometryIndex
        canonical_geom  = build_canonical_geometry(element_model, config)
        _geom_index     = GeometryIndex(canonical_geom)
        log.info("   [2d] Canonical geometry: %s", canonical_geom.summary_dict())
    except Exception as exc:
        log.warning("   [2d] Canonical geometry build failed: %s — continuing without", exc)
        _geom_index = None

    # ── [3] Run all quantifiers ───────────────────────────────────────────────
    log.info("[3/10] Running quantifiers")
    all_rows: list[dict] = []

    from v3_boq_system.quantify.roof_quantifier             import quantify_roof
    from v3_boq_system.quantify.lining_quantifier           import quantify_linings
    from v3_boq_system.quantify.opening_quantifier          import quantify_openings
    from v3_boq_system.quantify.floor_system_quantifier     import quantify_floor_system
    from v3_boq_system.quantify.footing_quantifier          import quantify_footings
    from v3_boq_system.quantify.stair_ramp_quantifier       import quantify_stairs
    from v3_boq_system.quantify.services_quantifier           import quantify_services
    from v3_boq_system.quantify.finish_zone_quantifier        import quantify_finish_zones
    from v3_boq_system.quantify.external_cladding_quantifier  import quantify_external_cladding
    from v3_boq_system.quantify.structural_fixings_quantifier import quantify_structural_fixings

    all_rows += quantify_roof(element_model, config, assembly_rules)
    log.info("   → roof: %d rows", len(all_rows))

    n_prev = len(all_rows)
    all_rows += quantify_linings(element_model, config, assembly_rules,
                                  canonical_geom=canonical_geom)
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
    all_rows += quantify_finish_zones(element_model, config)
    log.info("   → services + finishes: +%d rows", len(all_rows) - n_prev)

    n_prev = len(all_rows)
    all_rows += quantify_external_cladding(element_model, config,
                                            canonical_geom=canonical_geom)
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

    # ── [4b] Reconcile schedule evidence ─────────────────────────────────────
    reconciliation: dict = {}
    if pdf_schedule_data is not None:
        try:
            from v3_boq_system.reconcile.opening_schedule_reconciler import reconcile_openings
            from v3_boq_system.reconcile.finish_schedule_reconciler  import reconcile_finishes
            from v3_boq_system.reconcile.services_schedule_reconciler import reconcile_services
            reconciliation["openings"] = reconcile_openings(
                pdf_schedule_data, element_model, config)
            reconciliation["finishes"] = reconcile_finishes(
                pdf_schedule_data, element_model, config)
            reconciliation["services"] = reconcile_services(
                pdf_schedule_data, element_model, config)
            log.info(
                "   Reconciliation: opening_marks=%d | "
                "still_blocked=%d opening / %d finish / %d services",
                len(pdf_schedule_data.opening_marks),
                len(reconciliation["openings"].get("still_blocked", [])),
                len(reconciliation["finishes"].get("still_blocked", [])),
                len(reconciliation["services"].get("still_blocked", [])),
            )
        except Exception as exc:
            log.warning("   Schedule reconciliation failed: %s — continuing", exc)

    # ── [4c] Register graphical recovery result ───────────────────────────────
    # Reconciliation already ran at [2c] (pre-quantification) so element model
    # heights were applied before the quantifiers.  Just register the result
    # for QA and output writing here.
    if graphical_recovery:
        reconciliation["graphical"] = graphical_recovery
        log.info(
            "   Graphical reconciliation (from [2c]): promoted=%d | still_blocked=%d "
            "| win_h=%d | door_hints=%d",
            len(graphical_recovery.get("promoted_rows", [])),
            len(graphical_recovery.get("still_blocked", [])),
            len(graphical_recovery.get("window_heights_recovered", [])),
            len(graphical_recovery.get("door_hints_recovered", [])),
        )

    # ── [5] QA ────────────────────────────────────────────────────────────────
    log.info("[5/10] Running QA checks")
    benchmark_items = _load_benchmark(project_name, _ROOT)

    from v3_boq_system.qa.qa_engine import run_qa
    qa_report = run_qa(
        boq_items=boq_items,
        element_model_summary=summary,
        benchmark_items=benchmark_items,
        config=config,
        pdf_schedule_data=pdf_schedule_data,
        reconciliation=reconciliation,
        graphical_recovery=graphical_recovery or None,
    )

    # ── [6] Write outputs ─────────────────────────────────────────────────────
    out_dir = _ROOT / "v3_boq_system" / "outputs" / _pname_key
    os.makedirs(out_dir, exist_ok=True)
    log.info("[6/10] Writing outputs → %s", out_dir)

    boq_json_path      = out_dir / f"{_pname_key}_boq_items_v3.json"
    qa_json_path       = out_dir / f"{_pname_key}_qa_report_v3.json"
    em_json_path       = out_dir / f"{_pname_key}_element_model.json"
    spaces_json_path   = out_dir / f"{_pname_key}_spaces_v3.json"
    canon_geom_path    = out_dir / f"{_pname_key}_canonical_geometry_v3.json"
    sched_json_path    = out_dir / f"{_pname_key}_pdf_schedules_v3.json"
    annot_json_path    = out_dir / f"{_pname_key}_dxf_annotations_v3.json"
    graphics_json_path = out_dir / f"{_pname_key}_pdf_graphics_regions_v3.json"
    ocr_json_path      = out_dir / f"{_pname_key}_targeted_ocr_results_v3.json"
    graphical_json_path= out_dir / f"{_pname_key}_graphical_recovery_v3.json"
    excel_path         = out_dir / f"{_pname_key}_BOQ_V3.xlsx"

    _save_json(boq_items, boq_json_path)
    _save_json(qa_report,  qa_json_path)
    _save_json(_element_model_to_dict(element_model), em_json_path)

    # Canonical geometry debug output
    if canonical_geom is not None and _geom_index is not None:
        try:
            _save_json(_geom_index.full_debug_dict(), canon_geom_path)
            log.info("   → Canonical geometry JSON: %s", canon_geom_path.name)
        except Exception as exc:
            log.warning("   Canonical geometry JSON write failed: %s", exc)

    # DXF annotation results — strip large text_entities list for JSON (just summary)
    if raw_dxf_annotations:
        try:
            annot_out = {
                k: v for k, v in raw_dxf_annotations.items()
                if k not in ("text_entities", "dimension_entities",
                             "attrib_entities", "leader_entities")
            }
            annot_out["text_entity_count"]      = len(raw_dxf_annotations.get("text_entities", []))
            annot_out["dimension_entity_count"] = len(raw_dxf_annotations.get("dimension_entities", []))
            annot_out["attrib_entity_count"]    = len(raw_dxf_annotations.get("attrib_entities", []))
            annot_out["paper_space_text_count"] = len(raw_dxf_annotations.get("paper_space_text", []))
            _save_json(annot_out, annot_json_path)
            log.info("   → DXF annotations JSON: %s", annot_json_path.name)
        except Exception as exc:
            log.warning("   DXF annotations JSON write failed: %s", exc)

    # PDF graphics regions
    if raw_pdf_graphics.get("all_regions"):
        try:
            _save_json(raw_pdf_graphics, graphics_json_path)
            log.info("   → PDF graphics regions JSON: %s", graphics_json_path.name)
        except Exception as exc:
            log.warning("   PDF graphics JSON write failed: %s", exc)

    # OCR results — strip image_bytes from crops (not serializable)
    if raw_ocr_results.get("crops"):
        try:
            ocr_out = dict(raw_ocr_results)
            ocr_out["crops"] = [
                {k: v for k, v in c.items() if k != "image_bytes"}
                for c in raw_ocr_results.get("crops", [])
            ]
            _save_json(ocr_out, ocr_json_path)
            log.info("   → OCR results JSON: %s", ocr_json_path.name)
        except Exception as exc:
            log.warning("   OCR results JSON write failed: %s", exc)

    # Graphical recovery summary
    if graphical_recovery:
        try:
            _save_json(graphical_recovery, graphical_json_path)
            log.info("   → Graphical recovery JSON: %s", graphical_json_path.name)
        except Exception as exc:
            log.warning("   Graphical recovery JSON write failed: %s", exc)

    # PDF schedule extraction results
    if raw_pdf_schedules:
        try:
            import dataclasses
            sched_out: dict = dict(raw_pdf_schedules)
            if pdf_schedule_data is not None:
                sched_out["reconciliation"] = reconciliation
            _save_json(sched_out, sched_json_path)
            log.info("   → PDF schedules JSON: %s", sched_json_path.name)
        except Exception as exc:
            log.warning("   PDF schedules JSON write failed: %s", exc)

    # PASS 1 output: spaces.json — full space model with finish zone summary
    if element_model.spaces:
        try:
            import dataclasses
            from v3_boq_system.normalize.space_builder import compute_finish_zone_summary
            spaces_out = {
                "spaces": [
                    dataclasses.asdict(s) for s in element_model.spaces
                ],
                "finish_zone_summary": compute_finish_zone_summary(element_model.spaces),
            }
            _save_json(spaces_out, spaces_json_path)
            log.info("   → spaces JSON: %s (%d spaces)", spaces_json_path.name,
                     len(element_model.spaces))
        except Exception as exc:
            log.warning("   spaces.json write failed: %s", exc)

    # ── [6b] Apply alignment / commercial-block upgrade rules ─────────────────
    # Runs the RULE_PIPELINE from the alignment layer to add commercial_block,
    # commercial_block_sort_key, export_class, estimator names, etc.
    # Engine quantities are never modified — only presentation fields are added.
    boq_items_for_excel = boq_items
    try:
        from v3_boq_system.alignment.upgrade_rules import RULE_PIPELINE
        context = {"export_style": export_style, "fixings_strategy": "standalone"}
        items = list(boq_items)  # shallow copy — rules mutate item dicts in place
        for rule_fn in RULE_PIPELINE:
            items, rule_log = rule_fn(items, context)
            if rule_log:
                log.debug("   alignment rule %s: %d entries", rule_fn.__name__, len(rule_log))
        boq_items_for_excel = items
        cb_headers = [i for i in items if i.get("derivation_rule") == "insert_commercial_block_headers"]
        log.info("   → alignment rules applied (style=%s): %d items, %d commercial-block headers",
                 export_style, len(items), len(cb_headers))
    except Exception as exc:
        log.warning("   Alignment rules failed — Excel will render as flat list: %s", exc)

    try:
        from v3_boq_system.writers.excel_writer import write_boq_excel
        write_boq_excel(
            boq_items=boq_items_for_excel,
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
            # WPC decking supply area (5% cut waste for boards)
            _wpc_waste = 1.05
            _wpc_supply = round(ver.area_m2 * _wpc_waste, 2)
            rows.append({
                "item_name": "Verandah Decking — WPC Supply Area (5% cut waste)",
                "item_code": "", "unit": "m2",
                "quantity": _wpc_supply,
                "package": "external_verandah",
                "quantity_status": "calculated",
                "quantity_basis": f"verandah_area({ver.area_m2:.2f}) × {_wpc_waste} waste = {_wpc_supply} m²",
                "source_evidence": f"{ver.source}: verandah_area={ver.area_m2:.2f} m²",
                "derivation_rule": f"{ver.area_m2:.2f} × {_wpc_waste}",
                "confidence": ver.confidence, "manual_review": True,
                "notes": (
                    f"WPC composite decking board supply area including 5% cut waste. "
                    f"Net area: {ver.area_m2:.2f} m². Supply (5% waste): {_wpc_supply} m². "
                    "Verify decking board width, fixing centres and colour from specification. "
                    "Confirm surface finish: WPC, hardwood, or concrete slab."
                ),
            })
            # WPC board count (nominal 140mm wide boards including gaps)
            _board_w_mm = 140
            _board_l_m = 3.6  # typical WPC stock length
            _board_w_m = _board_w_mm / 1000
            # boards needed to cover area: rows = ceil(short_dim / board_w), boards per row = ceil(long_dim / board_l)
            # simpler: total = ceil(supply_area / (board_w × board_l))
            import math as _math
            _board_count = _math.ceil(_wpc_supply / (_board_w_m * _board_l_m))
            rows.append({
                "item_name": f"Verandah Decking — WPC Board Count ({_board_w_mm}mm × {_board_l_m:.1f}m)",
                "item_code": "", "unit": "nr",
                "quantity": _board_count,
                "package": "external_verandah",
                "quantity_status": "calculated",
                "quantity_basis": (
                    f"ceil(supply_area({_wpc_supply}m²) / (board_w({_board_w_mm}mm) × board_l({_board_l_m}m))) = "
                    f"ceil({_wpc_supply} / {_board_w_m * _board_l_m:.3f}) = {_board_count} nr"
                ),
                "source_evidence": f"{ver.source}: verandah_area={ver.area_m2:.2f} m²",
                "derivation_rule": f"ceil({_wpc_supply} / ({_board_w_m} × {_board_l_m}))",
                "confidence": "LOW", "manual_review": True,
                "notes": (
                    f"Estimated WPC board count at {_board_w_mm}mm nominal width, {_board_l_m}m stock length. "
                    f"{_wpc_supply} m² ÷ ({_board_w_m}m × {_board_l_m}m) = {_board_count} nr. "
                    "LOW confidence — board size assumed. Verify from supplier/specification."
                ),
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
    parser.add_argument(
        "--export-style",
        choices=["engine", "commercial", "estimator"],
        default="estimator",
        help="BOQ export presentation mode (default: estimator)",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run_pipeline(args.project, export_style=args.export_style)
