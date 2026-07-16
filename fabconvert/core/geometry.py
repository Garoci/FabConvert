"""Format-independent intermediate geometry model.

Every :class:`~fabconvert.alignment.Alignment`-aware reader produces a list of
the dataclasses defined here, and every writer consumes that same list.  The
model deliberately carries an optional ``stroke_width`` (in **mm**) on
:class:`Path`/:class:`Line`/:class:`Arc` so that Gerber trace widths — read
from aperture metadata, never from rendered geometry — survive a round trip.
Pads/vias are represented as filled :class:`Circle`/`ClosedPolygon`s with no
stroke width: they are a different thing from a stroked trace.

All coordinates in this module are in millimetres and in *intermediate
orientation* — that is, Y increases downward (SVG/screen convention).  The
alignment module is responsible for flipping Y on the boundary between a
math-Y-up format (DXF, Gerber) and this model; format modules never flip Y on
their own (golden rule zero).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple

Point = Tuple[float, float]


@dataclass
class Line:
    """A straight segment, optionally a stroked trace carrying its width.

    ``stroke_width`` in mm; ``None`` means "this is an outline / fill edge,
    not a trace" — distinct from 0.0 which is a genuine (degenerate) width.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    stroke_width: Optional[float] = None

    def points(self) -> Tuple[Point, Point]:
        return (self.x0, self.y0), (self.x1, self.y1)

    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Arc:
    """A circular arc, optionally a stroked trace carrying its width.

    Storage convention (matches the two source converters exactly):
    ``cx, cy`` is the centre in mm; ``radius`` in mm; ``start_angle`` and
    ``end_angle`` are in **degrees**, measured in intermediate (Y-down) space.
    The arc sweeps from ``start_angle`` to ``end_angle`` in the *counterclockwise*
    sense in intermediate space.  The DXF/DxfReader path (which reads a Y-up
    counterclockwise arc and the alignment module flips it) is responsible for
    producing start/end in this convention.
    """

    cx: float
    cy: float
    radius: float
    start_angle: float
    end_angle: float
    stroke_width: Optional[float] = None

    def endpoints(self) -> Tuple[Point, Point]:
        s = math.radians(self.start_angle)
        e = math.radians(self.end_angle)
        p0 = (self.cx + self.radius * math.cos(s),
              self.cy + self.radius * math.sin(s))
        p1 = (self.cx + self.radius * math.cos(e),
              self.cy + self.radius * math.sin(e))
        return p0, p1

    def sweep_deg(self) -> float:
        """Swept angle in degrees, always in [0, 360)."""
        d = (self.end_angle - self.start_angle) % 360.0
        return d


@dataclass
class Circle:
    """A filled circle (pad / via / drill), or a stroked ring if stroke_width set.

    Pads/vias have ``stroke_width=None`` and are *filled*.  A stroked ring
    (rare in PCB copper) carries a width and renders as an outline.
    """

    cx: float
    cy: float
    radius: float
    stroke_width: Optional[float] = None  # None => filled pad/via


@dataclass
class ClosedPolygon:
    """A filled closed outline (DXF copper region, hatch, etc.).  No width."""
    points: List[Point] = field(default_factory=list)


@dataclass
class Path:
    """An ordered polyline path, optionally stroked with ``stroke_width`` mm.

    This is the shape a Gerber D01 draw chain or a multi-segment SVG stroke
    centreline becomes.  When ``stroke_width`` is set the path is a centreline
    trace (the width is *not* baked into the geometry — golden rule #1); when
    it is ``None`` and ``filled`` is True it is a filled outline instead.
    """

    segments: List[Point] = field(default_factory=list)
    stroke_width: Optional[float] = None
    filled: bool = False
    closed: bool = False

    def is_stroked(self) -> bool:
        return self.stroke_width is not None


@dataclass
class GeometrySet:
    """Container for all primitives produced by a reader.

    Keeping the four primitive lists separate (rather than one polymorphic
    list) makes the writers plain, branch-light loops and keeps the pad-vs-trace
    distinction (golden rule #5) structural instead of inspectable-by-type.
    """

    lines: List[Line] = field(default_factory=list)
    arcs: List[Arc] = field(default_factory=list)
    circles: List[Circle] = field(default_factory=list)
    polygons: List[ClosedPolygon] = field(default_factory=list)
    paths: List[Path] = field(default_factory=list)

    def __iter__(self) -> Iterator[object]:
        yield from self.lines
        yield from self.arcs
        yield from self.circles
        yield from self.polygons
        yield from self.paths

    def __len__(self) -> int:
        return (len(self.lines) + len(self.arcs) + len(self.circles)
                + len(self.polygons) + len(self.paths))

    @property
    def is_empty(self) -> bool:
        return len(self) == 0

    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Bounding box (xmin, ymin, xmax, ymax) in mm, or None if empty.

        Stroked paths and traces contribute half their stroke_width; filled
        circles contribute their full radius.  This matches how the source
        converters computed the viewBox.
        """
        xs: List[float] = []
        ys: List[float] = []
        for ln in self.lines:
            w = (ln.stroke_width or 0.0) / 2.0
            xs.extend([ln.x0 - w, ln.x1 + w, ln.x0 + w, ln.x1 - w])
            ys.extend([ln.y0 - w, ln.y1 + w, ln.y0 + w, ln.y1 - w])
        for a in self.arcs:
            w = (a.stroke_width or 0.0) / 2.0
            r = a.radius + w
            xs.extend([a.cx - r, a.cx + r])
            ys.extend([a.cy - r, a.cy + r])
        for c in self.circles:
            w = (c.stroke_width or 0.0) / 2.0
            r = c.radius + (w if c.stroke_width is not None else 0.0)
            # filled circle: r already includes nothing extra; stroked ring: +w
            xs.extend([c.cx - r, c.cx + r])
            ys.extend([c.cy - r, c.cy + r])
        for poly in self.polygons:
            for x, y in poly.points:
                xs.append(x)
                ys.append(y)
        for p in self.paths:
            w = (p.stroke_width or 0.0) / 2.0 if p.is_stroked() else 0.0
            for x, y in p.segments:
                xs.extend([x - w, x + w])
                ys.extend([y - w, y + w])
        if not xs:
            return None
        # Filter out the spurious negative-side entries created by the lines
        # loop above (it emmitted -w/+w around both endpoints symmetrically, so
        # min/max still come out right).
        return (min(xs), min(ys), max(xs), max(ys))
