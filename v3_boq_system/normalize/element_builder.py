"""
element_builder.py — Build the normalized ProjectElementModel from V2 extractor outputs.

This module is the bridge between raw extractor dictionaries (as produced by the
V2 extractor pipeline) and the typed ProjectElementModel.

It applies source priority logic and fills in derived estimates where measured
data is absent, always recording the basis for every value.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

from v3_boq_system.normalize.element_model import (
    BaseElement,
    CeilingElement,
    FinishZoneElement,
    FloorElement,
    FloorSystemElement,
    FootingElement,
    OpeningElement,
    ProjectElementModel,
    RoofElement,
    RoomElement,
    StairElement,
    StructuralFrameElement,
    VerandahElement,
    WallElement,
)

log = logging.getLogger("boq.v3.element_builder")


# ── helpers ───────────────────────────────────────────────────────────────────

def _val(d: dict, key: str, default: Any = None) -> Any:
    """Extract value from a {value, source, confidence} dict or plain scalar."""
    v = d.get(key, default)
    if isinstance(v, dict):
        return v.get("value", default)
    return v if v is not None else default


def _src(d: dict, key: str) -> tuple[str, str]:
    """Return (source, confidence) for a wrapped field."""
    v = d.get(key, {})
    if isinstance(v, dict):
        return v.get("source", "unknown"), v.get("confidence", "LOW")
    return "unknown", "MEDIUM" if v else "LOW"


def _parse_door_width(block_name: str, door_block_width_map: dict) -> float:
    """Extract door width from block name.  Falls back to config map then regex."""
    if block_name in door_block_width_map:
        return door_block_width_map[block_name]
    m = re.search(r"(\d{2,3})", block_name)
    if m:
        cm = int(m.group(1))
        if 50 <= cm <= 200:
            return round(cm / 100, 2)
    return 0.9   # default


def _classify_room(room_name: str, patterns: dict) -> str:
    """Match room name against room_type_patterns config."""
    lower = room_name.lower()
    for rtype, keywords in patterns.items():
        if rtype == "unknown":
            continue
        for kw in keywords:
            if kw in lower:
                return rtype
    return "unknown"


# ── main builder ──────────────────────────────────────────────────────────────

def build_element_model(
    raw_dxf:      dict,
    raw_ifc:      dict,
    raw_framecad: dict,
    raw_pdf:      dict,
    project_model: dict,
    config:        dict,
) -> ProjectElementModel:
    """
    Build a ProjectElementModel from all extractor outputs.

    Args:
        raw_dxf:       Output dict from dxf_extractor
        raw_ifc:       Output dict from ifc_extractor
        raw_framecad:  Output dict from framecad_extractor
        raw_pdf:       Output dict from pdf_extractor (rooms, stairs, schedules)
        project_model: V2-style merged project_model dict (for fallback)
        config:        Loaded project_config.yaml dict

    Returns:
        ProjectElementModel with all elements populated.
    """
    cfg = config or {}
    proj_cfg = cfg.get("project", {})
    struct_cfg = cfg.get("structural", {})
    lining_cfg = cfg.get("lining", {})
    open_cfg = cfg.get("openings", {})
    footing_cfg = cfg.get("footings", {})

    wall_height = struct_cfg.get("wall_height_m", 2.4)
    door_block_map = open_cfg.get("door_block_width_map", {})
    room_patterns = {}  # filled from room_templates.yaml if available

    model = ProjectElementModel(
        project_name=project_model.get("project_name", "unknown"),
        project_type=proj_cfg.get("type", "unknown"),
    )

    # ── Geometry: floor ──────────────────────────────────────────────────────
    geom = project_model.get("geometry", {})

    floor_area = _val(geom, "floor_area_m2") or raw_dxf.get("floor_area_m2", 0.0)
    ext_perim  = _val(geom, "ext_wall_perimeter_m") or raw_dxf.get("ext_wall_perimeter_m", 0.0)
    floor_src, floor_conf = _src(geom, "floor_area_m2")

    if floor_area > 0:
        model.floors.append(FloorElement(
            element_id="floor_gf",
            area_m2=floor_area,
            perimeter_m=ext_perim,
            source=floor_src,
            source_reference=raw_dxf.get("source_file", ""),
            confidence=floor_conf,
        ))

    # ── Geometry: verandah ───────────────────────────────────────────────────
    ver_area  = _val(geom, "verandah_area_m2") or raw_dxf.get("verandah_area_m2", 0.0)
    ver_perim = _val(geom, "verandah_perimeter_m") or raw_dxf.get("verandah_perimeter_m", 0.0)
    if ver_area > 0:
        ver_src, ver_conf = _src(geom, "verandah_area_m2")
        model.verandahs.append(VerandahElement(
            element_id="verandah_main",
            area_m2=ver_area,
            perimeter_m=ver_perim,
            source=ver_src,
            source_reference=raw_dxf.get("source_file", ""),
            confidence=ver_conf,
        ))

    # ── Geometry: ceiling ────────────────────────────────────────────────────
    raw_ceil_area = _val(geom, "ceiling_area_m2") or raw_dxf.get("floor_hatch_area_m2", 0.0)
    derived_ceil  = round(floor_area - ver_area, 2) if floor_area > 0 else 0.0
    if raw_ceil_area > 0 and raw_ceil_area >= derived_ceil * 0.9:
        ceil_area, ceil_src, ceil_conf = raw_ceil_area, "dxf_geometry", "HIGH"
    elif derived_ceil > 0:
        ceil_area  = derived_ceil
        ceil_src   = "derived"
        ceil_conf  = "MEDIUM"
        model.warnings.append(
            f"Ceiling area from DXF ({raw_ceil_area:.2f} m²) appears partial; "
            f"using floor−verandah = {derived_ceil:.2f} m²"
        )
    else:
        ceil_area, ceil_src, ceil_conf = raw_ceil_area, "dxf_geometry", "MEDIUM"

    if ceil_area > 0:
        model.ceilings.append(CeilingElement(
            element_id="ceiling_gf",
            area_m2=ceil_area,
            source=ceil_src,
            confidence=ceil_conf,
        ))

    # ── Geometry: roof ───────────────────────────────────────────────────────
    roof_area  = _val(geom, "roof_area_m2") or raw_dxf.get("roof_area_m2", 0.0)
    roof_perim = _val(geom, "roof_perimeter_m") or raw_dxf.get("roof_perimeter_m", 0.0)
    if roof_area > 0:
        roof_src, roof_conf = _src(geom, "roof_area_m2")
        # Derive ridge / barge from perimeter (conservative estimates)
        ridge_est  = round(roof_perim * 0.25, 1)  # ~25% of perimeter is ridge
        barge_est  = round(roof_perim * 0.2,  1)  # ~20% gable ends
        model.roofs.append(RoofElement(
            element_id="roof_main",
            area_m2=roof_area,
            perimeter_m=roof_perim,
            eaves_length_m=roof_perim,         # full perimeter = all-sides eaves (hip)
            ridge_length_m=ridge_est,
            barge_length_m=barge_est,
            roof_type=proj_cfg.get("roof_type", cfg.get("roof", {}).get("roof_type", "hip")),
            source=roof_src,
            source_reference=raw_dxf.get("source_file", ""),
            confidence=roof_conf,
        ))

    # ── Walls ────────────────────────────────────────────────────────────────
    if ext_perim > 0:
        model.walls.append(WallElement(
            element_id="ext_wall",
            wall_type="external",
            length_m=ext_perim,
            height_m=wall_height,
            faces=1,
            source="dxf_geometry",
            source_reference=raw_dxf.get("source_file", ""),
            confidence="HIGH" if ext_perim > 0 else "LOW",
        ))

    # Internal wall — DXF measured preferred; ratio fallback
    int_wall_lm_dxf = raw_dxf.get("int_wall_lm", 0.0)
    if int_wall_lm_dxf > 0:
        int_wall_est = round(int_wall_lm_dxf, 1)
        int_src      = "dxf_geometry"
        int_conf     = "HIGH"
        int_note     = (f"Measured from DXF WALLS layer: "
                        f"{len(raw_dxf.get('int_wall_segments', []))} internal wall runs.")
    else:
        ratio        = lining_cfg.get("int_wall_lm_ratio", 0.34)
        int_wall_est = round(floor_area * ratio, 1) if floor_area > 0 and ratio > 0 else 0.0
        int_src      = "derived_ratio"
        int_conf     = "LOW"
        int_note     = (f"Estimated from floor area × {ratio} ratio. "
                        "Replace with measured value when available.")

    if int_wall_est > 0:
        model.walls.append(WallElement(
            element_id="int_wall",
            wall_type="internal",
            length_m=int_wall_est,
            height_m=wall_height,
            faces=2,
            source=int_src,
            source_reference=(f"DXF WALLS layer: {len(raw_dxf.get('int_wall_segments',[]))} polylines"
                              if int_src == "dxf_geometry"
                              else f"floor_area × {lining_cfg.get('int_wall_lm_ratio',0.34)}"),
            confidence=int_conf,
            notes=int_note,
        ))

    # ── Structural frames ────────────────────────────────────────────────────
    struct = project_model.get("structural", {})
    struct_priority = struct.get("source_priority_used", "unknown")

    frame_map = {
        "roof_panel_lm":    ("roof_panel",     "Tab Roof Panels"),
        "roof_truss_lm":    ("roof_truss",     "Tab Roof Trusses"),
        "wall_frame_lm":    ("wall_frame",     "Tab Wall Panels"),
        "lintel_lm":        ("lintel",         "Lintel entries"),
        "wall_strap_lm":    ("wall_strap",     "Strap entries"),
        "verandah_frame_lm":("verandah_frame", "V1/verandah members"),
        "steel_shs_lm":     ("steel_shs",      "SHS steel"),
        "roof_batten_lm":   ("roof_batten",    "FrameCAD BATTEN entries"),
    }
    # batten_entries are stored in structural dict as bom_batten_entries
    bom_batten_entries = struct.get("bom_batten_entries") or raw_framecad.get("batten_entries", [])

    for field_key, (frame_type, ref) in frame_map.items():
        v = _val(struct, field_key)
        if v and v > 0:
            src_s, conf_s = _src(struct, field_key)
            entries = bom_batten_entries if frame_type == "roof_batten" else []
            model.structural_frames.append(StructuralFrameElement(
                element_id=frame_type,
                frame_type=frame_type,
                total_lm=v,
                source=src_s or struct_priority,
                source_reference=ref,
                confidence=conf_s or ("HIGH" if struct_priority == "framecad_bom" else "MEDIUM"),
                member_entries=entries,
            ))

    # ── Floor system ─────────────────────────────────────────────────────────
    # Priority: FrameCAD BOM floor tabs → FrameCAD floor_type → IFC joists → slab fallback
    fc_lm_by_tab     = raw_framecad.get("lm_by_tab", {})
    floor_panel_tabs = {k: v for k, v in fc_lm_by_tab.items() if "floor" in k.lower()}
    floor_joist_lm   = raw_ifc.get("floor_joist_lm", 0.0)
    fc_floor_type    = raw_framecad.get("floor_type", "").lower()    # "steel" | "concrete" | ""
    fc_load_class    = raw_framecad.get("floor_load_class", "")
    fc_joist_spec    = raw_framecad.get("floor_joist_spec", "")
    fc_bearer_spec   = raw_framecad.get("floor_bearer_spec", "")
    fc_joist_spacing = raw_framecad.get("floor_joist_spacing_mm", 0)
    fc_panel_size    = raw_framecad.get("floor_panel_size", "")
    fc_panel_members = raw_framecad.get("floor_panel_members", [])   # per-member detail if available

    if floor_panel_tabs:
        # Case 1: FrameCAD BOM has floor panel tabs (highest confidence)
        # Pull per-member detail if available to populate joist_count / length fields.
        total_member_lm = sum(m["total_lm"] for m in fc_panel_members)
        member_lengths  = sorted(set(m["length_mm"] for m in fc_panel_members))
        member_counts   = {m["length_mm"]: sum(x["qty"] for x in fc_panel_members
                                                if x["length_mm"] == m["length_mm"])
                           for m in fc_panel_members}
        # Dominant joist profile from members
        dominant_profile = ""
        if fc_panel_members:
            from collections import Counter
            dominant_profile = Counter(m["profile"] for m in fc_panel_members).most_common(1)[0][0]

        for tab_name, tab_lm in floor_panel_tabs.items():
            # Use member-derived lm when available (more precise than tab total)
            use_lm = round(total_member_lm, 2) if total_member_lm > 0 else tab_lm
            joist_l_mm = member_lengths[0] if len(member_lengths) == 1 else 0
            joist_nr   = sum(m["qty"] for m in fc_panel_members) if fc_panel_members else 0
            model.floor_systems.append(FloorSystemElement(
                element_id=f"floor_panel_{tab_name.replace(' ','_').lower()}",
                assembly_type="floor_panel",
                total_joist_lm=use_lm,
                load_class=fc_load_class,
                joist_length_mm=joist_l_mm,
                joist_count=joist_nr,
                source="framecad_bom",
                source_reference=f"FrameCAD Tab: {tab_name}",
                confidence="HIGH",
                notes=(
                    f"Profile: {dominant_profile or 'LGS C-section'}. "
                    f"Members: {joist_nr} pieces. "
                    + (f"Load class: {fc_load_class}." if fc_load_class else "")
                ) if fc_panel_members else "",
            ))

    elif floor_joist_lm > 0:
        # Case 2: IFC floor joists
        model.floor_systems.append(FloorSystemElement(
            element_id="floor_joist_ifc",
            assembly_type="floor_joist",
            total_joist_lm=floor_joist_lm,
            load_class=fc_load_class,
            source="ifc_model",
            source_reference="IfcMember floor_joist classification",
            confidence="HIGH",
        ))

    elif fc_floor_type == "steel":
        # Case 3: FrameCAD layout confirms steel floor but no detailed schedule
        spec_parts = []
        if fc_joist_spec:    spec_parts.append(f"Joist: {fc_joist_spec}")
        if fc_bearer_spec:   spec_parts.append(f"Bearer: {fc_bearer_spec}")
        if fc_joist_spacing: spec_parts.append(f"@{fc_joist_spacing} mm crs")
        if fc_load_class:    spec_parts.append(f"Load class: {fc_load_class}")
        spec_note = " | ".join(spec_parts) if spec_parts else ""

        model.floor_systems.append(FloorSystemElement(
            element_id="floor_steel_derived",
            assembly_type="steel_floor_frame",
            floor_area_m2=floor_area,
            load_class=fc_load_class,
            source="pdf_layout",
            source_reference="FrameCAD layout Design Summary: Floor Type Steel",
            confidence="MEDIUM",
            notes=(
                "Steel floor frame confirmed from FrameCAD layout Design Summary. "
                "No FrameCAD floor panel tab found in manufacturing summary — "
                "quantities derived from floor area. Obtain panel schedule for accuracy."
                + (f" Spec detected: {spec_note}." if spec_note else "")
            ),
        ))
        model.warnings.append(
            "Floor Type Steel confirmed but no floor panel schedule found. "
            "Floor system quantities are area-derived estimates only."
        )
        # Store spec fields on model for use by quantifier
        if fc_joist_spec:
            model.floor_systems[-1].notes = model.floor_systems[-1].notes  # already set
        # Attach spec as source_reference sub-fields via notes (quantifier reads notes)
        if spec_note:
            model.extraction_notes.append(
                f"Floor spec from PDF: {spec_note}"
            )

    elif fc_floor_type in ("concrete", "slab") or (not fc_floor_type and floor_area > 0):
        # Case 4: Slab on ground (explicit or fallback)
        is_confirmed = fc_floor_type in ("concrete", "slab")
        slab_thick = footing_cfg.get("slab_thickness_mm", 100)
        model.footings.append(FootingElement(
            element_id="slab_gf",
            footing_type="slab",
            area_m2=floor_area,
            perimeter_m=ext_perim,
            thickness_mm=slab_thick,
            concrete_m3=round(floor_area * slab_thick / 1000, 2),
            reinforcement=footing_cfg.get("mesh_type", "SL72"),
            source="pdf_layout" if is_confirmed else "derived",
            source_reference=(
                "FrameCAD layout Design Summary: Floor Type Concrete"
                if is_confirmed
                else "No floor joist/panel/type evidence — slab on ground assumed"
            ),
            confidence="MEDIUM" if is_confirmed else "LOW",
            notes=(
                f"Slab on ground {'confirmed from FrameCAD layout' if is_confirmed else 'assumed — no floor framing evidence'}. "
                "Verify from structural drawings."
            ),
        ))

    # ── Post / pad footings from DXF structure circles ────────────────────
    # When steel floor frame is confirmed and post grid is present, add pad footings.
    post_count = raw_dxf.get("post_count", 0)
    if post_count > 0 and fc_floor_type == "steel":
        model.footings.append(FootingElement(
            element_id="pad_posts",
            footing_type="pad",
            count=post_count,
            source="dxf_geometry",
            source_reference=(
                f"DXF STRUCTURE layer: {post_count} circle entities"
            ),
            confidence="MEDIUM",
            notes=(
                f"{post_count} post/column positions detected in DXF STRUCTURE layer. "
                "Pad footings assumed beneath each post. "
                "Verify footing size and depth from structural drawings."
            ),
        ))

    # ── Openings ─────────────────────────────────────────────────────────────
    door_inserts   = raw_dxf.get("door_inserts", [])
    window_inserts = raw_dxf.get("window_inserts", [])
    default_door_h = open_cfg.get("default_door_height_m", 2.04)

    # Group doors by block name.  Width comes from DXF block geometry (LINE length × xscale).
    # Falls back to block name regex if block geometry unavailable.
    door_groups: dict[str, list] = {}  # block_name → list of inserts
    for ins in door_inserts:
        bn = ins.get("block_name", "DOOR_UNKNOWN")
        door_groups.setdefault(bn, []).append(ins)

    for bn, inserts in door_groups.items():
        qty = len(inserts)
        # Width: prefer DXF block geometry; fall back to block name regex
        geom_widths = [i["width_m"] for i in inserts if i.get("width_m", 0) > 0]
        if geom_widths:
            width = round(sum(geom_widths) / len(geom_widths), 3)
            w_conf = "HIGH"
            w_note = f"Width from DXF block LINE geometry: {width:.3f} m"
        else:
            width = _parse_door_width(bn, door_block_map)
            w_conf = "MEDIUM"
            w_note = f"Width from block name regex: {width:.3f} m"
        swing = "sliding" if "SLD" in bn.upper() or "SLIDING" in bn.upper() else "hinged"
        model.openings.append(OpeningElement(
            element_id=f"door_{bn.lower()}",
            opening_type="door",
            mark=bn,
            width_m=width,
            height_m=default_door_h,
            quantity=qty,
            swing_type=swing,
            is_external=True,   # conservative default
            source="dxf_blocks",
            source_reference=f"DXF INSERT block={bn} count={qty}",
            confidence=w_conf,
            notes=w_note,
        ))

    # Group windows by (block_name, width_bucket_mm) so different-sized inserts
    # of the same block type produce separate OpeningElements.
    # Width bucket: round to nearest 100 mm to handle floating-point noise.
    win_groups: dict[tuple, list] = {}  # (block_name, width_mm_bucket) → list of inserts
    for ins in window_inserts:
        bn = ins.get("block_name", "WIN_UNKNOWN")
        w_m = ins.get("width_m", 0.0)
        # Round to nearest 100 mm bucket
        w_bucket_mm = round(w_m * 10) * 100 if w_m > 0 else 0
        key = (bn, w_bucket_mm)
        win_groups.setdefault(key, []).append(ins)

    for (bn, w_bucket_mm), inserts in win_groups.items():
        qty   = len(inserts)
        swing = "louvre" if "LOUVRE" in bn.upper() or "LOUVER" in bn.upper() else "standard"
        # Use measured width if available
        geom_widths = [i["width_m"] for i in inserts if i.get("width_m", 0) > 0]
        if geom_widths:
            width_m = round(sum(geom_widths) / len(geom_widths), 3)
            w_conf  = "MEDIUM"  # MEDIUM: geometry measured but no schedule confirmation
            w_note  = f"Width from DXF block LINE geometry × xscale: {width_m:.3f} m"
        else:
            width_m = 0.0
            w_conf  = "LOW"
            w_note  = "Width not available from block geometry — use config default"

        # Attempt height extraction from DXF block geometry.
        # The DXF extractor may store height_m on window inserts when the block
        # bounding-box or explicit height LINE is available.
        geom_heights = [i["height_m"] for i in inserts if i.get("height_m", 0) > 0]
        if geom_heights:
            height_m = round(sum(geom_heights) / len(geom_heights), 3)
            h_note   = f"height from DXF block geometry: {height_m:.3f} m"
            log.info("Window %s: height %.3f m from DXF block geometry", bn, height_m)
        else:
            height_m = 0.0  # will fall back to config default in quantifier
            h_note   = "height not in DXF block — quantifier will use config default"

        # Mark encodes block + size for display (e.g. WINDOW_LOUVRE_1080)
        mark = f"{bn}_{w_bucket_mm}" if w_bucket_mm > 0 else bn
        model.openings.append(OpeningElement(
            element_id=f"win_{mark.lower()}",
            opening_type="window",
            mark=mark,
            width_m=width_m,
            height_m=height_m,
            quantity=qty,
            swing_type=swing,
            has_flyscreen=True,   # standard for tropical climate
            source="dxf_blocks",
            source_reference=f"DXF INSERT block={bn} count={qty} width~{w_bucket_mm}mm",
            confidence=w_conf,
            notes=f"{w_note}; {h_note}",
        ))

    # Fallback to total counts if no insert detail
    if not door_inserts:
        door_count_fallback = _val(project_model.get("openings", {}), "door_count") or 0
        if door_count_fallback > 0:
            model.openings.append(OpeningElement(
                element_id="door_total",
                opening_type="door",
                quantity=door_count_fallback,
                source="dxf_blocks",
                confidence="MEDIUM",
                notes="Block-level door data not available; total count only",
            ))
    if not window_inserts:
        win_count_fallback = _val(project_model.get("openings", {}), "window_count") or 0
        if win_count_fallback > 0:
            model.openings.append(OpeningElement(
                element_id="window_total",
                opening_type="window",
                quantity=win_count_fallback,
                swing_type="louvre",
                has_flyscreen=True,
                source="dxf_blocks",
                confidence="MEDIUM",
                notes="Block-level window data not available; total count only",
            ))

    # ── Rooms from PDF (or config fallback) ──────────────────────────────────
    pdf_rooms = raw_pdf.get("rooms", []) if raw_pdf else []
    room_type_patterns = {}   # populated from config if passed separately

    # Fall back to project_config room_schedule when no machine-readable schedule
    # is present in any input source.  Config rooms carry LOW confidence to signal
    # they are estimates that must be verified against final architectural drawings.
    config_rooms = cfg.get("room_schedule", [])
    room_source_list = pdf_rooms if pdf_rooms else config_rooms
    room_source_tag  = "pdf_schedule" if pdf_rooms else "project_config"
    room_conf_default = "HIGH" if pdf_rooms else "LOW"

    for r in room_source_list:
        if not isinstance(r, dict):
            continue
        rname = r.get("name", "")
        # Config entries may carry explicit room_type; PDF entries need classification
        rtype = r.get("room_type") or _classify_room(rname, room_type_patterns or {})
        area  = r.get("area_m2") or 0.0
        wet   = r.get("is_wet_area", rtype in ("toilet","accessible_wc","bathroom","laundry","kitchen","cleaner"))
        model.rooms.append(RoomElement(
            element_id=f"room_{rname.lower().replace(' ','_').replace('/','_')}",
            room_name=rname,
            room_type=rtype,
            area_m2=area,
            source=room_source_tag,
            confidence=room_conf_default if area else "LOW",
            is_wet_area=wet,
            notes=("Configured estimate — verify room layout and areas from architectural drawings."
                   if room_source_tag == "project_config" else ""),
        ))

    # ── Room area cross-validation against DXF floor geometry ────────────────
    # Cross-check the SUM of config room areas against the DXF-measured interior
    # floor area (floor_area − verandah).  This validates the total only.
    # Individual room areas remain config estimates (LOW confidence) — the DXF
    # geometry does not tell us how the interior is partitioned.
    # Confidence is NOT upgraded: individual areas are still unverified estimates.
    if room_source_tag == "project_config" and model.rooms:
        interior_area = round(floor_area - ver_area, 2) if ver_area > 0 else floor_area
        room_area_sum = round(sum(r.area_m2 for r in model.rooms), 2)
        if interior_area > 0 and room_area_sum > 0:
            tol = interior_area * 0.05   # 5% tolerance
            if abs(room_area_sum - interior_area) <= tol:
                log.info(
                    "Room schedule total check: config sum %.1f m² matches DXF interior %.1f m². "
                    "Individual room areas remain LOW confidence (config estimates).",
                    room_area_sum, interior_area,
                )
                model.extraction_notes.append(
                    f"Room schedule total check: config sum {room_area_sum:.1f} m² matches "
                    f"DXF interior floor area {interior_area:.1f} m². "
                    "Individual room areas remain LOW confidence — breakdown is a config estimate, "
                    "not derived from source geometry."
                )
            else:
                log.warning(
                    "Room schedule: config sum %.1f m² differs from DXF interior %.1f m² "
                    "(delta %.1f m²) — room areas remain LOW confidence",
                    room_area_sum, interior_area, abs(room_area_sum - interior_area),
                )

    # ── Stairs ───────────────────────────────────────────────────────────────
    stair_ev    = _val(geom, "stair_evidence") or False
    stair_lines = _val(geom, "stair_line_count") or 0

    pdf_stairs    = raw_pdf.get("stairs", []) if raw_pdf else []
    config_stairs = cfg.get("stair_schedule", [])

    if pdf_stairs:
        for s in pdf_stairs:
            if isinstance(s, dict):
                risers = s.get("risers", 0)
                model.stairs.append(StairElement(
                    element_id="stair_pdf",
                    stair_type=s.get("type", "prefab"),
                    flights=1,
                    risers_per_flight=risers,
                    source="pdf_schedule",
                    confidence="HIGH",
                ))
    elif config_stairs:
        # Use configured stair schedule — richer than DXF evidence alone
        for s in config_stairs:
            if isinstance(s, dict):
                model.stairs.append(StairElement(
                    element_id=s.get("element_id", "stair_cfg"),
                    stair_type=s.get("stair_type", "prefab"),
                    flights=s.get("flights", 1),
                    risers_per_flight=s.get("risers_per_flight", 0),
                    tread_depth_mm=s.get("tread_depth_mm", 250),
                    riser_height_mm=s.get("riser_height_mm", 175),
                    width_m=s.get("width_m", 1.2),
                    balustrade_lm=s.get("balustrade_lm", 0.0),
                    handrail_lm=s.get("handrail_lm", 0.0),
                    landing_area_m2=s.get("landing_area_m2", 0.0),
                    source=s.get("source", "project_config"),
                    confidence=s.get("confidence", "LOW"),
                    notes=s.get("notes", "Configured estimate — verify from architectural drawings."),
                ))
    elif stair_ev:
        model.stairs.append(StairElement(
            element_id="stair_dxf",
            stair_type="unknown",
            flights=1,
            source="dxf_geometry",
            source_reference=f"STAIRS layer: {stair_lines} lines",
            confidence="MEDIUM",
            notes=f"DXF STAIRS layer detected ({stair_lines} lines). Type/details unknown.",
        ))

    # ── Finishes ─────────────────────────────────────────────────────────────
    if floor_area > 0:
        model.finish_zones.append(FinishZoneElement(
            element_id="floor_finish",
            finish_type="floor",
            material=footing_cfg.get("floor_finish_type",
                      cfg.get("finishes", {}).get("floor_finish_type", "tiles")),
            area_m2=floor_area,
            source="dxf_geometry",
            confidence="HIGH",
        ))

    # ── Source file inventory ─────────────────────────────────────────────────
    for f in [raw_dxf.get("source_file"), raw_ifc.get("source_file"),
              raw_framecad.get("source_file")]:
        if f and f not in model.source_files:
            model.source_files.append(f)

    log.info(
        "Element model built: %d walls  %d openings  %d rooms  %d floors  "
        "%d roofs  %d frame elements  %d stairs  %d footings",
        len(model.walls), len(model.openings), len(model.rooms),
        len(model.floors), len(model.roofs),
        len(model.structural_frames), len(model.stairs), len(model.footings),
    )
    return model
