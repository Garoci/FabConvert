"""Heuristic unit / origin detection when a file carries no explicit metadata.

This is the better-documented, manually-overridable successor to the original
project's ``DxfConverter._detect_unit_scale``.  The rule set is identical and
has been validated against the real KiCad fixture
``tests/fixtures/test6-F_Cu.dxf`` (``$INSUNITS`` unset, max drawing-unit
extent ≈ 0.87  →  correctly guessed *inches* → board 22.10 × 13.99 mm, matching
the sibling ``test6-F_Cu.svg`` viewBox ``22.0980 13.9954`` to within 0.01 mm).

Golden rule #6: "No unit/scale guess is acceptable without a test against a
real fixture."  The fixture-backed test for this heuristic lives in
``tests/test_alignment.py``.

The pipeline is:
  1. Trust explicit metadata first (``$INSUNITS`` for DXF, ``%MO`` for Gerber,
     a viewBox+unit-suffix for SVG).
  2. If metadata is absent/unitless, call :func:`detect_unit_scale_from_extent`.
  3. Whatever was decided is recorded on the resulting :class:`Alignment` as
     ``detection_note`` so it is never a silent guess.
  4. A caller-supplied :class:`~fabconvert.alignment.registration.Units`
     override always wins.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from .registration import Alignment, Units


def detect_unit_scale_from_extent(
    max_extent_drawing_units: float,
) -> Tuple[Units, float]:
    """Guess mm-per-drawing-unit from the largest dimension of the geometry.

    The heuristics, tuned against real PCB DXF exports (KiCad/Altium, which
    frequently write ``$INSUNITS=0`` even when the coords are inches or mils):

      * max extent < 20  →  inches (25.4 mm/unit)   # a 4-inch board spans ~4
      * max extent 20–500  →  mm (1.0)              # boards are 20–300 mm
      * max extent > 500  →  mils (0.0254)          # mils span thousands

    Returns ``(Units, mm_per_unit)``.  Never raises.
    """
    if max_extent_drawing_units < 20.0:
        return Units.INCH, 25.4
    if max_extent_drawing_units > 500.0:
        return Units.MIL, 0.0254
    return Units.MM, 1.0


def resolve_dxf_units(
    insunits: int,
    bbox: Optional[Tuple[float, float, float, float]],
    override: Optional[Units] = None,
) -> Alignment:
    """Build the Y-flipped, unit-scaled Alignment for a DXF reader.

    ``insunits`` is the raw ``$INSUNITS`` header integer (0 == absent/unitless).
    ``bbox`` is the DXF-Gerber-space (x_min, y_min, x_max, y_max) in *drawing
    units*, used both for the flip axis and for the heuristic.  ``override``
    lets the caller force a unit (it always wins; golden rule: manual override
    is never a guess).

    The flip axis is ``y_min + y_max`` (mirror about the bbox midline), which
    is exactly what the original ``DxfConverter._direct_dxf_to_svg`` verified
    preserves geometry — see ``Python/format_converter.py`` lines ~717-727 and
    the conclusion of ``Python/trace_y2.py``.
    """
    if override is not None:
        unit = override
        mm_per = unit.mm_per_unit
        note = f"unit overridden to {unit.value}"
    elif insunits and insunits != 0:
        from ..core.units import INSUNITS_TO_MM, INSUNITS_NAMES
        mm_per = INSUNITS_TO_MM.get(insunits, 1.0)
        unit = {v: k for k, v in {
            Units.MM: 4, Units.INCH: 1, Units.MIL: 9, Units.CM: 5, Units.M: 6
        }.items()}.get(insunits, Units.MM)
        name = INSUNITS_NAMES.get(insunits, f"INSUNITS({insunits})")
        note = f"$INSUNITS={insunits} ({name})"
    else:
        # Unitless / absent → heuristic from extent.  No guess is silent.
        if bbox is not None and all(math.isfinite(v) for v in bbox):
            ext = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
            unit, mm_per = detect_unit_scale_from_extent(ext)
            note = (f"$INSUNITS={insunits} (unitless) -> heuristic detected "
                    f"{unit.value} from extent {ext:.4f} drawing units")
        else:
            unit, mm_per = Units.MM, 1.0
            note = "$INSUNITS=0/no bbox -> defaulted to mm (no geometry to detect)"

    flip_axis = 0.0
    if bbox is not None and all(math.isfinite(v) for v in bbox):
        flip_axis = bbox[1] + bbox[3]  # ymin + ymax in drawing units

    return Alignment(
        unit_scale=mm_per,
        y_flip=True,                 # DXF is Y-up; intermediate is Y-down
        flip_axis=flip_axis,
        origin=(0.0, 0.0),
        detected_unit=unit,
        detection_note=note,
    )


def resolve_gerber_units(mo_unit: Optional[str], override: Optional[Units] = None) -> Alignment:
    """Build the (Y-flipped, mm-scaled) Alignment for a Gerber reader.

    ``mo_unit`` is the text after ``%MO`` — ``"MM"`` or ``"IN"``.  Gerber coords
    are absolute with no inherent flip, but the intermediate is Y-down and
    Gerber draws in Y-up, so ``y_flip=True``; the flip axis is the geometry
    midline in mm and is filled in by the reader once it has parsed the draws.
    """
    if override is not None:
        return Alignment(unit_scale=override.mm_per_unit, y_flip=True,
                         flip_axis=0.0, detected_unit=override,
                         detection_note=f"unit overridden to {override.value}")
    if mo_unit == "IN":
        return Alignment(unit_scale=25.4, y_flip=True, flip_axis=0.0,
                         detected_unit=Units.INCH,
                         detection_note="%MOIN*% (inches)")
    # Default mm (the Gerber spec default if %MO is absent is mm).
    return Alignment(unit_scale=1.0, y_flip=True, flip_axis=0.0,
                     detected_unit=Units.MM,
                     detection_note="%MOMM*% (mm, or defaulted)")


def svg_alignment_from_viewbox(vb: Optional[Tuple[float, float, float, float]],
                               width_attr: Optional[str] = None,
                               height_attr: Optional[str] = None) -> Alignment:
    """Build the (no-flip, mm) Alignment for an SVG reader.

    SVG is already Y-down like the intermediate, so ``y_flip=False`` and
    ``unit_scale=1.0``.  We parse unit suffixes off width/height only to flag a
    non-mm SVG (inch-suffixed SVGs are rare; PCB SVGs are mm).  For now the
    intermediate is mm so a non-mm SVG is scaled — but in practice KiCad SVGs
    carry ``mm`` which is 1:1.
    """
    unit_scale = 1.0
    detected = Units.MM
    note = "SVG (Y-down, mm) -> 1:1 intermediate"
    if width_attr and isinstance(width_attr, str):
        w = width_attr.strip()
        if w.endswith("in") or w.endswith("inch"):
            unit_scale = 25.4
            detected = Units.INCH
            note = "SVG width suffixed 'in' -> inches, scaling to mm"
    return Alignment(unit_scale=unit_scale, y_flip=False, flip_axis=0.0,
                     origin=(0.0, 0.0), detected_unit=detected,
                     detection_note=note)
