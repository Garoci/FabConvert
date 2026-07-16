"""The Alignment object — the single coordinate transform passed through every
reader and writer.

Why this exists as a first-class module (golden rule zero)
-----------------------------------------------------------
Y-axis and unit-scale logic must live in one place.  Without that, DXF (math
Y-up), SVG (screen Y-down), and Gerber (absolute mm) are easy to mis-map:

  * **DXF** is mathematical, Y increases upward; ``ymin``/``ymax`` are often
    negative because the origin is arbitrary.
  * **SVG** is screen-space, Y increases downward.
  * **Gerber** is absolute mm with no inherent flip.

The correct geometric mapping (verified on real KiCad files) is:

    svg_y = ymax_dxf - dxf_y      (DXF→SVG, flip about the bbox midline)

Flipping about the *bounding-box midline* — not about absolute zero — is what
preserves the geometry; flipping about zero would translate the whole board.
This module centralises that flip so format modules never invent their own.

An ``Alignment`` carries three things:

  * ``unit_scale`` — mm per source-format unit (e.g. 25.4 for inches,
    0.0254 for mils, 1.0 for mm).  Intermediate geometry is always in mm, so a
    reader multiplies source coords by ``unit_scale``; a writer divides.
  * ``y_flip`` — bool.  True when the source/target format is mathematical Y-up
    (DXF, Gerber) and the intermediate is Y-down (SVG).  When True, the reader
    applies ``y' = ymax - y`` on the way in and the writer applies the inverse
    ``y' = ymax - y`` on the way out (the transform is its own inverse).
  * ``origin`` — (x0, y0) the source-frame origin so absolute coordinates can
    be shifted to/from an intermediate frame rooted at the geometry bbox.  For
    round-trip fidelity this is normally ``(0.0, 0.0)`` for SVG/ Gerber and the
    DXF bbox minimum for DXF, but it is kept explicit so writers can rescale.

No format module flips Y or converts units on its own.  It asks the
``Alignment`` how.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class Units(Enum):
    """The unit a source file declares or that detection settles on.

    Mapping to mm lives in :data:`fabconvert.core.units.INSUNITS_TO_MM`; this
    enum is the override handle the user can pass to a reader instead of
    letting :func:`fabconvert.alignment.detect` guess (golden rule #6: no
    guess is acceptable without a fixture, but a manual override always wins).
    """

    MM = "mm"
    INCH = "inch"
    MIL = "mil"
    CM = "cm"
    M = "m"
    UNITLESS = "unitless"

    @property
    def mm_per_unit(self) -> float:
        # Kept here so callers don't need to import core.units just to scale.
        return {
            Units.MM: 1.0,
            Units.INCH: 25.4,
            Units.MIL: 0.0254,
            Units.CM: 10.0,
            Units.M: 1000.0,
            Units.UNITLESS: 1.0,
        }[self]


@dataclass
class Alignment:
    """Coordinate transform between a source/target format frame and the
    intermediate (mm, Y-down) frame.

    Construct one of these per format on the way in, and a complementary one on
    the way out.  The intermediate geometry produced by readers is in mm with
    Y-down; writers consume that and emit in their own frame using their own
    ``Alignment``.

    Parameters
    ----------
    unit_scale:
        mm per source unit, applied as ``intermediate = source * unit_scale``.
    y_flip:
        True if the format is mathematical Y-up (DXF, Gerber).  The flip is
        taken about ``flip_axis``: ``intermediate_y = flip_axis - source_y``.
    flip_axis:
        The Y value about which to mirror.  Conventionally ``ymin + ymax`` of
        the source geometry in source units (so the geometry lands at the same
        absolute extents in the intermediate, not translated).  When ``y_flip``
        is False this is ignored.
    origin:
        (x0, y0) offset applied as ``intermediate = (source - origin) *
        unit_scale`` in X (and after the Y-flip in Y).  Defaults to (0,0).
    detected_unit:
        What :func:`~fabconvert.alignment.detect` (or the user) settled on,
        kept for logging/round-trip introspection only.
    """

    unit_scale: float = 1.0
    y_flip: bool = False
    flip_axis: float = 0.0
    origin: Tuple[float, float] = (0.0, 0.0)
    detected_unit: Optional[Units] = None
    # Public-facing note logged by readers when a heuristic guess was made
    # (golden rule: no silent guess).  ``""`` means "no guess, units explicit".
    detection_note: str = ""

    # ---- Forward: source-frame → intermediate (reader side) ----------------

    def to_intermediate_y(self, source_y: float) -> float:
        """Map a source Y (Y-up if y_flip) to intermediate Y (Y-down, mm)."""
        if self.y_flip:
            return (self.flip_axis - source_y) * self.unit_scale
        return source_y * self.unit_scale

    def to_intermediate_x(self, source_x: float) -> float:
        return (source_x - self.origin[0]) * self.unit_scale

    # ---- Inverse: intermediate → target frame (writer side) ---------------

    def from_intermediate_y(self, intermediate_y: float) -> float:
        """Inverse of :meth:`to_intermediate_y`.  The Y-flip is its own inverse,
        but the unit_scale is divided back out because writers emit in their
        target units (mm stays mm, inches become inches via ``1/unit_scale``).
        """
        if self.y_flip:
            return self.flip_axis - intermediate_y / self.unit_scale
        return intermediate_y / self.unit_scale

    def from_intermediate_x(self, intermediate_x: float) -> float:
        return intermediate_x / self.unit_scale + self.origin[0]

    # ---- Convenience for writers that know they emit mm -------------------

    def flip_intermediate_y(self, intermediate_y: float) -> float:
        """Apply only the Y-flip to an already-mm intermediate Y.

        Used by a Y-up, mm writer (Gerber) that has no unit change but must
        undo the Y-down→Y-up flip.  ``flip_axis`` is in *intermediate mm* here.
        """
        if self.y_flip:
            return self.flip_axis - intermediate_y
        return intermediate_y

    @staticmethod
    def mm(y_flip: bool = False, flip_axis: float = 0.0,
           origin: Tuple[float, float] = (0.0, 0.0)) -> "Alignment":
        """An Alignment already in mm (unit_scale=1).  Useful for SVG."""
        return Alignment(unit_scale=1.0, y_flip=y_flip, flip_axis=flip_axis,
                         origin=origin, detected_unit=Units.MM)

    @staticmethod
    def for_units(units: Units, y_flip: bool = False,
                  flip_axis: float = 0.0,
                  origin: Tuple[float, float] = (0.0, 0.0)) -> "Alignment":
        return Alignment(unit_scale=units.mm_per_unit, y_flip=y_flip,
                          flip_axis=flip_axis, origin=origin,
                          detected_unit=units)
