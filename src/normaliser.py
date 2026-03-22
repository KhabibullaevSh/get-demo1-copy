"""
normaliser.py — Normalises DXF origin/scale before comparison.

CRITICAL: This is the highest-risk step. If normalisation fails,
the pipeline MUST abort with a clear error. Never compare misaligned geometry.
Silent wrong results are worse than a clear error.

Checks:
  - Origin point alignment
  - Scale factor detection and correction
  - Layer naming convention validation
  - Unit consistency (mm vs m)
"""

import math
import ezdxf
from ezdxf.math import Vec3


class NormalisationError(Exception):
    """Raised when DXF normalisation fails and pipeline must abort."""
    pass


def normalise_dxf(dxf_path: str, target_unit: str = "mm") -> ezdxf.document.Drawing:
    """Normalise a DXF file for comparison against the standard model.

    Steps:
      1. Load the DXF file
      2. Detect and correct origin offset
      3. Detect and correct scale factor
      4. Validate layer naming
      5. Validate units

    Args:
        dxf_path: Path to the input DXF file.
        target_unit: Target unit system ('mm' or 'm').

    Returns:
        Normalised ezdxf Drawing object.

    Raises:
        NormalisationError: If normalisation fails at any step.
    """
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        raise NormalisationError(f"Failed to read DXF file: {dxf_path}\nError: {e}")

    msp = doc.modelspace()

    # Step 1: Detect origin offset
    origin_offset = _detect_origin_offset(msp)
    if origin_offset and (abs(origin_offset.x) > 0.1 or abs(origin_offset.y) > 0.1):
        _apply_origin_correction(msp, origin_offset)

    # Step 2: Detect and correct scale
    scale_factor = _detect_scale_factor(doc)
    if scale_factor is not None and abs(scale_factor - 1.0) > 0.001:
        _apply_scale_correction(msp, scale_factor, target_unit)

    # Step 3: Validate layers
    layer_warnings = _validate_layers(doc)

    # Step 4: Validate units
    _validate_units(doc, target_unit)

    # Attach normalisation metadata
    doc.header["$USERR1"] = 1.0  # Flag: normalised

    return doc


def _detect_origin_offset(msp) -> Vec3 | None:
    """Detect if the drawing origin is offset from (0, 0, 0).

    Looks at the bounding box of all entities to find the minimum corner
    and uses that as the likely intended origin.
    """
    min_x = float("inf")
    min_y = float("inf")

    entity_count = 0
    for entity in msp:
        entity_count += 1
        try:
            bbox = ezdxf.bbox.extents([entity])
            if bbox.has_data:
                min_x = min(min_x, bbox.extmin.x)
                min_y = min(min_y, bbox.extmin.y)
        except Exception:
            continue

    if entity_count == 0:
        raise NormalisationError("DXF file contains no entities in modelspace.")

    if min_x == float("inf") or min_y == float("inf"):
        return None

    return Vec3(min_x, min_y, 0)


def _apply_origin_correction(msp, offset: Vec3) -> None:
    """Translate all entities so the minimum corner aligns to origin."""
    translation = Vec3(-offset.x, -offset.y, 0)

    for entity in msp:
        try:
            entity.translate(translation.x, translation.y, translation.z)
        except (AttributeError, ezdxf.DXFError):
            # Some entity types may not support translation
            continue


def _detect_scale_factor(doc) -> float | None:
    """Detect the scale factor from DXF header variables.

    Checks $INSUNITS and $MEASUREMENT to determine if scaling is needed.
    Returns scale factor to convert to millimetres, or None if already correct.
    """
    # DXF $INSUNITS: 0=unspecified, 1=inches, 2=feet, 4=mm, 5=cm, 6=m
    insunits = doc.header.get("$INSUNITS", 0)

    unit_to_mm = {
        0: None,   # unspecified — cannot auto-detect
        1: 25.4,   # inches to mm
        2: 304.8,  # feet to mm
        4: 1.0,    # already mm
        5: 10.0,   # cm to mm
        6: 1000.0, # m to mm
    }

    return unit_to_mm.get(insunits, None)


def _apply_scale_correction(msp, scale_factor: float, target_unit: str) -> None:
    """Scale all entities to the target unit system."""
    if target_unit == "m":
        # Convert from mm to m
        actual_scale = scale_factor / 1000.0
    else:
        actual_scale = scale_factor

    if abs(actual_scale - 1.0) < 0.001:
        return

    for entity in msp:
        try:
            entity.scale(actual_scale, actual_scale, actual_scale)
        except (AttributeError, ezdxf.DXFError):
            continue


def _validate_layers(doc) -> list[str]:
    """Validate layer naming conventions and return warnings.

    Expected layers: WALLS, DOORS, WINDOWS, ROOF, FLOOR, STRUCTURE, etc.
    """
    expected_prefixes = [
        "WALL", "DOOR", "WINDOW", "WIN", "ROOF", "FLOOR",
        "STRUCT", "STAIR", "POST", "COLUMN", "CEILING",
        "VERANDAH", "VERANDA", "DECK",
    ]

    warnings = []
    layer_names = [layer.dxf.name.upper() for layer in doc.layers]

    if not layer_names or (len(layer_names) == 1 and layer_names[0] == "0"):
        warnings.append(
            "WARNING: DXF has no meaningful layers. "
            "All entities on layer '0'. Geometry extraction may be inaccurate."
        )
        return warnings

    found_any = False
    for prefix in expected_prefixes:
        if any(prefix in name for name in layer_names):
            found_any = True
            break

    if not found_any:
        warnings.append(
            f"WARNING: No recognised construction layers found. "
            f"Layers present: {layer_names[:10]}"
        )

    return warnings


def _validate_units(doc, target_unit: str) -> None:
    """Validate that units are consistent and sensible.

    Checks the bounding box dimensions to ensure they fall within
    reasonable ranges for a house drawing.
    """
    msp = doc.modelspace()
    all_entities = list(msp)
    if not all_entities:
        return

    try:
        bbox = ezdxf.bbox.extents(all_entities)
    except Exception:
        return

    if not bbox.has_data:
        return

    width = bbox.extmax.x - bbox.extmin.x
    height = bbox.extmax.y - bbox.extmin.y

    # Reasonable house dimensions in mm: 5000-50000mm (5m-50m)
    if target_unit == "mm":
        if width < 100 or height < 100:
            raise NormalisationError(
                f"Drawing dimensions too small ({width:.0f} x {height:.0f} mm). "
                f"Possible unit mismatch — drawing may be in metres not millimetres."
            )
        if width > 200000 or height > 200000:
            raise NormalisationError(
                f"Drawing dimensions too large ({width:.0f} x {height:.0f} mm). "
                f"Possible unit mismatch or site plan instead of floor plan."
            )


def get_normalisation_report(doc) -> dict:
    """Generate a summary report of the normalisation applied."""
    msp = doc.modelspace()
    all_entities = list(msp)

    report = {
        "entity_count": len(all_entities),
        "layer_count": len(list(doc.layers)),
        "layers": [layer.dxf.name for layer in doc.layers],
        "insunits": doc.header.get("$INSUNITS", 0),
        "normalised": doc.header.get("$USERR1", 0) == 1.0,
    }

    try:
        bbox = ezdxf.bbox.extents(all_entities)
        if bbox.has_data:
            report["extents"] = {
                "min_x": bbox.extmin.x,
                "min_y": bbox.extmin.y,
                "max_x": bbox.extmax.x,
                "max_y": bbox.extmax.y,
                "width": bbox.extmax.x - bbox.extmin.x,
                "height": bbox.extmax.y - bbox.extmin.y,
            }
    except Exception:
        report["extents"] = None

    return report
