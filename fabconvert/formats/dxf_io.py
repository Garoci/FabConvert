"""DXF reader / writer (built on ezdxf).

**Reader** (``DxfReader``): converts a DXF into the intermediate model + an
Y-flipped, unit-scaled Alignment.  KiCad PCB DXF exports store copper as
outline geometry (connected LINE segments tracing the boundary of each region;
CIRCLE/ARC for holes/pads).  Trace *width* is encoded geometrically — the
perpendicular distance between a matched parallel edge-pair IS the width
(:mod:`fabconvert.formats.edgepair`); when the edges pair cleanly we emit
centreline :class:`Path`s carrying ``stroke_width`` (same emission as the
Gerber fix), and otherwise fall back to filled :class:`ClosedPolygon` outlines
(never a forced bad centreline — golden rule #4).

**Writer** (``DxfWriter``): emits the intermediate model back to DXF.
**DXF has no inherent width property** (golden rule #2).  Traces therefore
become a *filled outline* (a closed LWPOLYLINE offset by half the stroke width)
— the genuine format limitation, documented in the README and the emitted
file's header comment, never silently worked around.  Pads (filled circles)
become CIRCLE entities.

No Y-flip or unit conversion happens *inside* this module: the reader uses the
Alignment from :func:`fabconvert.alignment.resolve_dxf_units`; the writer uses
a writer-side Alignment whose ``y_flip=True`` rebuilds DXF Y-up coordinates.
"""

from __future__ import annotations

import math
import pathlib
from typing import Dict, List, Optional, Tuple, Union

from ..core.geometry import (
    Arc, Circle, ClosedPolygon, GeometrySet, Line, Path,
)
from ..alignment import Alignment, Units
from ..alignment.detect import resolve_dxf_units
from .edgepair import (
    lines_form_closed_boundary, pair_parallel_edges, trace_line_polygons,
)
from . import medialaxis

# pathlib.Path is aliased Pathlib because core.geometry.Path (the centreline
# dataclass) shadows the bare name ``Path`` in this module.
Pathlib = pathlib.Path

_EPS = 1e-6


def _scan_extent(msp) -> Optional[Tuple[float, float, float, float]]:
    """Bounding box (xmin,ymin,xmax,ymax) in drawing units, reading DXF attrs
    directly — robust where ezdxf's per-entity bounding_box() returns nan
    (verified: it does for the 144 LINEs of test6-F_Cu.dxf)."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for e in msp:
        try:
            etype = e.dxftype()
            if etype == "LINE":
                xmin = min(xmin, e.dxf.start.x, e.dxf.end.x)
                ymin = min(ymin, e.dxf.start.y, e.dxf.end.y)
                xmax = max(xmax, e.dxf.start.x, e.dxf.end.x)
                ymax = max(ymax, e.dxf.start.y, e.dxf.end.y)
            elif etype == "CIRCLE":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                xmin = min(xmin, cx - r); ymin = min(ymin, cy - r)
                xmax = max(xmax, cx + r); ymax = max(ymax, cy + r)
            elif etype == "ARC":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                xmin = min(xmin, cx - r); ymin = min(ymin, cy - r)
                xmax = max(xmax, cx + r); ymax = max(ymax, cy + r)
            elif etype == "LWPOLYLINE":
                for pt in e.get_points(format="xy"):
                    xmin = min(xmin, pt[0]); ymin = min(ymin, pt[1])
                    xmax = max(xmax, pt[0]); ymax = max(ymax, pt[1])
            elif etype == "POLYLINE":
                for v in e.vertices:
                    xmin = min(xmin, v.dxf.location.x); ymin = min(ymin, v.dxf.location.y)
                    xmax = max(xmax, v.dxf.location.x); ymax = max(ymax, v.dxf.location.y)
        except Exception:
            pass
    if xmin == float("inf"):
        return None
    return (xmin, ymin, xmax, ymax)


class DxfReader:
    """Read a DXF file into the intermediate model (mm, Y-down) + Alignment."""

    def __init__(self):
        self.alignment: Optional[Alignment] = None
        self.geometry = GeometrySet()

    @classmethod
    def from_file(cls, path: Union[str, Pathlib],
                  unit_override: Optional[Units] = None) -> "DxfReader":
        r = cls()
        r._load(Pathlib(path), unit_override)
        return r

    def _load(self, path: Pathlib, unit_override: Optional[Units]) -> None:
        import ezdxf
        try:
            doc = ezdxf.readfile(str(path))
        except Exception:
            from ezdxf import recover as _recover
            doc, auditor = _recover.readfile(str(path))
            if auditor.has_errors:
                print(f"[DxfReader] auditor warnings for '{path}', "
                      f"proceeding with recovered data")
        msp = doc.modelspace()

        insunits = doc.header.get("$INSUNITS", 0)
        if not isinstance(insunits, int):
            insunits = 0
        bbox = _scan_extent(msp)
        self.alignment = resolve_dxf_units(insunits, bbox, unit_override)
        print(f"[DxfReader] {self.alignment.detection_note}")
        if bbox is not None:
            print(f"[DxfReader] drawing bbox "
                  f"({bbox[0]:.4f},{bbox[1]:.4f})->({bbox[2]:.4f},{bbox[3]:.4f}) "
                  f"ud, mm_per_unit={self.alignment.unit_scale}, flip_axis={self.alignment.flip_axis:.4f}")

        af = self.alignment
        sc = af.unit_scale

        # Collect raw line dicts (drawing units) for edge-pairing + polygon tracing.
        line_dicts: List[Dict[str, float]] = []
        circle_dicts: List[Dict[str, float]] = []
        arc_dicts: List[Dict[str, float]] = []

        for e in msp:
            etype = e.dxftype()
            try:
                if etype == "LINE":
                    line_dicts.append({
                        "x1": e.dxf.start.x, "y1": e.dxf.start.y,
                        "x2": e.dxf.end.x, "y2": e.dxf.end.y,
                    })
                elif etype == "CIRCLE":
                    circle_dicts.append({
                        "cx": e.dxf.center.x, "cy": e.dxf.center.y,
                        "r": e.dxf.radius})
                elif etype == "ARC":
                    arc_dicts.append({
                        "cx": e.dxf.center.x, "cy": e.dxf.center.y,
                        "r": e.dxf.radius,
                        "start": e.dxf.start_angle, "end": e.dxf.end_angle})
                elif etype == "LWPOLYLINE":
                    pts = list(e.get_points(format="xy"))
                    for i in range(len(pts) - 1):
                        line_dicts.append({
                            "x1": pts[i][0], "y1": pts[i][1],
                            "x2": pts[i + 1][0], "y2": pts[i + 1][1]})
                    if e.closed and len(pts) > 2:
                        line_dicts.append({
                            "x1": pts[-1][0], "y1": pts[-1][1],
                            "x2": pts[0][0], "y2": pts[0][1]})
            except Exception:
                continue

        # --- Trace-vs-boundary classification -------------------------------
        # KiCad plots copper two ways that look similar but are structurally
        # distinct: (a) routed traces — two parallel walls + round end-caps,
        # sometimes concatenated by KiCad's plotter into one fully-closed
        # "stadium" loop (test2-F_Cu.dxf), sometimes left as two open walls
        # (test6-F_Cu.dxf); (b) a filled copper pour / region, traced as one
        # closed polygon whose long straight walls *are* the boundary.
        #
        # The recovery engine for the trace case is the **Voronoi medial axis**
        # (:mod:`fabconvert.formats.medialaxis`): a closed trace-outline ribbon
        # polygon has a medial axis whose post-prune spine has degree
        # ``{2: …, 1: 2, ≥3: 0}`` — exactly two real trace termini and ZERO
        # branch points — so the centreline falls out as one continuous polyline
        # through every corner, with no merge/snap step.  This replaces the old
        # wall-pair midpoint + ``_snap_corner_endpoints`` + ``trace_open_chains``
        # + connectivity-gate stack that whack-a-moled "blob at 19 corners → gap
        # at 1 → blob at the fixed corner."
        #
        # Discriminator (kept faithful to the test2/test6 distinction the
        # committed regression tests pin):
        #
        #  * ``lines_form_closed_boundary``'s ``is_boundary`` is the PRIMARY
        #    pour-vs-trace discriminator (it keys on "majority of long edges are
        #    inside closed chains" — a pour's boundary satisfies this; an
        #    open-walls file does not).  We do NOT delete it: a ``width < length``
        #    per-run guard alone is NOT a reliable pour detector — measured in
        #    plan mode, an L-shaped pour with a 10mm×1mm neck yields a
        #    medial-axis run ``len=10mm, width=1mm`` that PASSES ``width<length``
        #    (a false positive).  So ``is_boundary`` stays primary.
        #  * ``pair_parallel_edges``'s *clean decomposition* (every long wall a
        #    parallel partner, ``unpaired_long == 0``) is the strong signal that
        #    a closed loop is genuine routed traces rather than a pour (a pour's
        #    boundary walls leave orphaned long edges or pair at grotesque
        #    widths the width-ratio guard rejects).  test2's loops close AND
        #    pair cleanly → routed traces → medial axis.  A pour closes but does
        #    NOT pair cleanly → filled outline.
        #
        # Order:
        #   1. closed chains present AND clean pairing      → medial axis (test2)
        #   2. closed chains present AND a pour             → filled outline
        #   3. no closed chains (open walls — test6)        → edge-pair fallback
        #   4. neither                                     → filled outline
        pairs, unpaired_long, leftover = pair_parallel_edges(line_dicts, sc)
        clean_pairing = bool(pairs) and unpaired_long == 0
        boundary_polys, is_boundary = lines_form_closed_boundary(line_dicts, sc)

        def _emit_centrelines(seg_list) -> None:
            """Append ``(cx0,cy0,cx1,cy1,width_mm)`` (drawing-second units, mm)
            centreline segments as 2-vertex stroked Paths in the intermediate
            (mm, Y-down) model.  Shared by the medial-axis and edge-pair paths."""
            for (cx0, cy0, cx1, cy1, w_mm) in seg_list:
                self.geometry.paths.append(Path(
                    segments=[(af.to_intermediate_x(cx0),
                               af.to_intermediate_y(cy0)),
                              (af.to_intermediate_x(cx1),
                               af.to_intermediate_y(cy1))],
                    stroke_width=w_mm, filled=False, closed=False))

        if boundary_polys and clean_pairing:
            # Closed trace-outline loop(s) that ALSO pair cleanly → genuine
            # routed traces drawn as a closed "stadium" loop.  Recover centreline
            # + width via the medial axis.  Corners come out as bends in
            # continuous polylines (zero branch points → no merge step), so no
            # ``_snap_corner_endpoints`` / ``trace_open_chains`` is needed.
            n_segs = 0
            for poly in boundary_polys:
                segs = medialaxis.recover_centrelines(poly, sc)
                # Belt-and-braces pour filter: a run whose recovered width is not
                # clearly shorter than its own length is a degenerate (pour)
                # medial axis, not a trace — drop it (the PRIMARY discriminator
                # is ``is_boundary``; this only catches a mis-classified run).
                # ``s[4]`` is the width in mm; the chord endpoints are in drawing
                # units (recover_centrelines' contract), so convert the chord to
                # mm before the ratio test (Bug A fix: coords are now drawing
                # units, not mm — the old mm-vs-mm comparison became mm-vs-du).
                segs = [s for s in segs
                        if s[4] < math.hypot(s[2] - s[0], s[3] - s[1]) * sc]
                _emit_centrelines(segs)
                n_segs += len(segs)
            print(f"[DxfReader] medial-axis centreline recovery "
                  f"({n_segs} segment(s) from {len(boundary_polys)} closed "
                  f"trace-outline loop(s) of {len(line_dicts)} LINEs; clean "
                  f"pairing precedence over the closed-boundary heuristic) — "
                  f"traces recovered as continuous centrelines through corners "
                  f"with no corner-patch step.")
        elif boundary_polys and is_boundary:
            # Closed chains that do NOT pair cleanly → a filled copper pour /
            # outline region (the original phantom-cloud-on-a-pour case:
            # is_boundary=True and no clean pairing => filled outline, never a
            # forced bad centreline — golden rule #4).
            print(f"[DxfReader] closed-outline geometry detected "
                  f"({len(boundary_polys)} outline(s) from "
                  f"{len(line_dicts)} LINEs) — emitting filled outline "
                  f"polygons; no centreline recovery (this is a "
                  f"region/pour, not routed traces).")
            for poly in boundary_polys:
                pts = [(af.to_intermediate_x(x), af.to_intermediate_y(y))
                       for (x, y) in poly]
                self.geometry.polygons.append(ClosedPolygon(points=pts))
        elif not boundary_polys and clean_pairing:
            # Open walls (no closed loops — the test6 case): pair_parallel_edges
            # is the recovery engine.  Kept verbatim — the open-walls shape has
            # no polygon for a medial axis to consume.
            print(f"[DxfReader] open-wall trace pairing ({len(pairs)} "
                  f"wall-pair(s), 0 unpaired long edges from "
                  f"{len(line_dicts)} LINEs) — emitting recovered "
                  f"centrelines (no closed loop to medialise; edge-pair path).")
            _emit_centrelines(pairs)
            # Any closed leftover outline (a pad/pour boundary no wall-pair
            # claimed) surfaces instead of vanishing — pass ``leftover``, never
            # the full ``line_dicts``, so consumed trace walls are not
            # re-chained into overlapping filled outlines.
            for poly in trace_line_polygons(leftover):
                pts = [(af.to_intermediate_x(x), af.to_intermediate_y(y))
                       for (x, y) in poly]
                self.geometry.polygons.append(ClosedPolygon(points=pts))
        else:
            # Neither a clean pairing nor a confident pour: not enough signal to
            # recover centrelines (DXF has no width — golden rule #2); fall back
            # to filled outlines (no forced bad centreline — golden rule #4).
            if unpaired_long > 0 or (not pairs and line_dicts):
                print(f"[DxfReader] NOTE: {unpaired_long} long LINE edge(s) "
                      f"did not pair into clean parallel edge-pairs and no "
                      f"credible pour boundary closed — emitting filled outline "
                      f"polygons; no recovered centreline for this geometry "
                      f"(DXF has no width — golden rule #2).")
            polys = boundary_polys or trace_line_polygons(line_dicts)
            for poly in polys:
                pts = [(af.to_intermediate_x(x), af.to_intermediate_y(y))
                       for (x, y) in poly]
                self.geometry.polygons.append(ClosedPolygon(points=pts))


        # --- Circles: filled pads (no width) ---
        for c in circle_dicts:
            cx = af.to_intermediate_x(c["cx"])
            cy = af.to_intermediate_y(c["cy"])
            r = c["r"] * sc
            self.geometry.circles.append(Circle(cx=cx, cy=cy, radius=r))

        # --- Arcs: stroked centreline chord (rare in PCB outline DXFs) -----
        # DXF arc is CCW in Y-up; after the Y-flip it becomes CW in Y-down.
        # Store as an Arc primitive in intermediate space (start/end recomputed
        # by endpoints() from the flipped centre).
        for a in arc_dicts:
            cx = af.to_intermediate_x(a["cx"])
            cy = af.to_intermediate_y(a["cy"])
            r = a["r"] * sc
            # Flip the angle: in Y-up, angle θ; in Y-down, the point at θ
            # reflects to (cos θ, -sin θ) relative to the (now-flipped) centre,
            # i.e. angle -θ.  Sweep direction inverts (CCW -> CW).
            self.geometry.arcs.append(Arc(
                cx=cx, cy=cy, radius=r,
                start_angle=-a["end"], end_angle=-a["start"],
                stroke_width=None))


class DxfWriter:
    """Write the intermediate model to DXF.

    **DXF has no width property (golden rule #2).**  Traces (Paths/Lines with
    stroke_width) are therefore emitted as a *filled outline*: an LWPOLYLINE
    offset by half the stroke width around the centreline and closed with
    semicircular end-caps.  Pads (filled circles with no stroke_width) become
    CIRCLE entities.  The writer writes mm and sets ``$INSUNITS=4`` (mm).
    """

    def __init__(self, alignment: Optional[Alignment] = None):
        # Default: write DXF in mm, Y-up (re-flip the Y-down intermediate).
        self.alignment = alignment or Alignment.mm(y_flip=True, flip_axis=0.0)

    def to_bytes(self, geom: GeometrySet) -> bytes:
        import ezdxf
        doc = ezdxf.new(dxfversion="R2010", setup=True)
        try:
            doc.header["$INSUNITS"] = 4  # 4 = mm
        except Exception:
            pass
        msp = doc.modelspace()

        af = self.alignment
        # The intermediate is mm; writers re-apply the Y-flip to land in DXF
        # Y-up.  Compute the flip axis as the midline of the *intermediate*
        # geometry so the bbox is preserved (same trick both directions).
        bb = geom.bounds()
        flip_axis_inter = (bb[1] + bb[3]) if (bb is not None) else 0.0

        def Y(y: float) -> float:
            # Re-flip Y-down intermediate -> Y-up DXF about the geometry midline.
            return flip_axis_inter - y

        # --- Traces -> filled outline LWPOLYLINE (offset by half-width) ---
        trace_polylines: List[List[Tuple[float, float]]] = []
        for p in geom.paths:
            if p.is_stroked() and len(p.segments) >= 2:
                trace_polylines.append(
                    _outline_polyline(p.segments, (p.stroke_width or 0.0)))
        for ln in geom.lines:
            if ln.stroke_width is not None:
                trace_polylines.append(
                    _outline_polyline(
                        [(ln.x0, ln.y0), (ln.x1, ln.y1)], ln.stroke_width))

        for outline_pts in trace_polylines:
            pts = [(x, Y(y)) for (x, y) in outline_pts]
            msp.add_lwpolyline(pts, close=True)

        # --- Filled-outline polygons straight through ---
        for poly in geom.polygons:
            if len(poly.points) < 3:
                continue
            pts = [(x, Y(y)) for (x, y) in poly.points]
            msp.add_lwpolyline(pts, close=True)

        # --- Arc traces -> approximated outline polyline ---
        for a in geom.arcs:
            if a.stroke_width is not None:
                pts = _arc_outline_pts(a)
                msp.add_lwpolyline([(x, Y(y)) for (x, y) in pts], close=True)

        # --- Pads -> CIRCLE (filled; DXF readers treat as a circle) ---
        for c in geom.circles:
            msp.add_circle((c.cx, Y(c.cy)), c.radius)

        buf = _doc_to_bytes(doc)
        return buf

    def to_file(self, geom: GeometrySet, path: Union[str, Pathlib]) -> None:
        Pathlib(path).write_bytes(self.to_bytes(geom))


def _doc_to_bytes(doc) -> bytes:
    """Serialise an ezdxf doc to bytes.

    ezdxf's ``doc.write`` writes DXF tags as **str**, so the stream must be a
    text stream — not ``BytesIO``.  (An earlier version used ``BytesIO`` and
    crashed with ``TypeError: a bytes-like object is required, not 'str'``
    because ezdxf writes the HEADER section via ``tagwriter.write_str``.)
    We buffer as text and encode to UTF-8 afterwards.
    """
    import io
    sio = io.StringIO()
    doc.write(sio)
    return sio.getvalue().encode("utf-8")


def _outline_polyline(segments: List[Tuple[float, float]],
                      width_mm: float) -> List[Tuple[float, float]]:
    """Build the filled-outline of a stroked centreline polyline.

    Offsets the centreline by ±width/2 along its normals and closes the loop at
    each end with a **round end-cap** — a semicircle discretised into short
    chord segments.  Two reasons that this is not just stylistic:

    1. **Golden rule #2 honesty.**  DXF has no native width property.  The only
       faithful representation is a filled outline whose *measured width*
       equals the trace width.  A round cap matches KiCad's own DXF plotter
       (which stylises round ends), so a reader that recovers width from the
       outline gets the same number KiCad intended.

    2. **The closed-loop invariant (this was a real bug).**  The centreline
       width is recovered by :func:`fabconvert.formats.edgepair.pair_parallel_edges`
       from the two long parallel outline walls; that algorithm treats every
       segment longer than ``PAIR_MIN_LEN_MM`` (0.5 mm) as a pairing candidate.
       A *flat* end-cap of a 0.8 mm trace is itself 0.8 mm long — above the
       threshold — so the caps of different parallel traces pair with each
       other and produce spurious 6–12 mm "widths", breaking the
       SVG→DXF→SVG round-trip.  Discretising the cap into short chords keeps
       every segment below 0.5 mm so caps are never pairing candidates; only
       the genuine long parallel walls pair, recovering exactly width/2 offset.
    """
    if len(segments) < 2 or width_mm <= 0:
        return segments
    hw = width_mm / 2.0
    # Chord count per semicircle: keep each chord well under PAIR_MIN_LEN_MM
    # (0.5 mm).  Each chord ~= hw * pi / N, so N = ceil(hw*pi/0.15)+2 suffices.
    cap_n = max(6, int(math.ceil(hw * math.pi / 0.15)) + 2)
    pts = segments
    n = len(pts)

    # --- Left/right offset sides (per centreline segment). -----------------
    # For each centreline seg P0->P1 the left wall is P0->P1 shifted by +hw
    # along the left normal, the right wall by -hw.  We collect sample points
    # along each wall (both segment endpoints), skipping degenerate segs.
    left_pts: List[Tuple[float, float]] = []
    right_pts: List[Tuple[float, float]] = []
    for i in range(n - 1):
        x0, y0 = pts[i]; x1, y1 = pts[i + 1]
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        if L < _EPS:
            continue
        nx, ny = -dy / L, dx / L  # left-hand normal (unit)
        lx, ly = nx * hw, ny * hw
        if not left_pts:
            left_pts.append((x0 + lx, y0 + ly))
            right_pts.append((x0 - lx, y0 - ly))
        left_pts.append((x1 + lx, y1 + ly))
        right_pts.append((x1 - lx, y1 - ly))
    if len(left_pts) < 2:
        return segments

    # --- Round end-caps (semicircles traced on the OUTSIDE of each end). ---
    # The semicircle is centred on the centreline endpoint with radius hw and
    # sweeps the half-plane on the OUTSIDE of the trace (behind the start,
    # ahead of the end).  In Y-down both caps sweep clockwise (-pi).
    #   START cap: from right wall (ang - pi/2) via the back (ang + pi) to the
    #     left wall (ang + pi/2) — sweep = -pi over [0 .. cap_n].
    #   END cap:   from left wall (ang + pi/2) via the front (ang) to the right
    #     wall (ang - pi/2) — sweep = -pi over [0 .. cap_n].
    # The angles are computed modulo 2*pi so the points land on the correct
    # wall regardless of heading; cos/sin are 2*pi-periodic so the literal
    # value doesn't matter, only the half-plane swept.

    def cap_points(cx, cy, ang, from_right_to_left):
        if from_right_to_left:
            a = ang - math.pi / 2  # start at right wall
        else:
            a = ang + math.pi / 2  # start at left wall
        sweep = -math.pi
        out = []
        for k in range(cap_n + 1):
            theta = a + sweep * k / cap_n
            out.append((cx + hw * math.cos(theta), cy + hw * math.sin(theta)))
        return out

    start = pts[0]; end = pts[-1]
    # direction at start (pts[1]-pts[0]) and end (pts[-1]-pts[-2]).
    sdx, sdy = pts[1][0] - start[0], pts[1][1] - start[1]
    L0 = math.hypot(sdx, sdy)
    edx, edy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
    L1 = math.hypot(edx, edy)
    if L0 < _EPS or L1 < _EPS:
        # Degenerate end — fall back to flat outline (no cap).
        return left_pts + list(reversed(right_pts))
    sang = math.atan2(sdy, sdx)
    eang = math.atan2(edy, edx)
    start_cap = cap_points(start[0], start[1], sang, from_right_to_left=True)
    end_cap = cap_points(end[0], end[1], eang, from_right_to_left=False)

    # Outline: start_cap (right->left outside) + left wall (forward) +
    # end_cap (left->right outside) + right wall (reversed).  The cap points
    # already START on the right wall and END on the left wall (or vice-versa),
    # so the joins coincide geometrically.
    outline = start_cap + left_pts + end_cap + list(reversed(right_pts))
    return outline


def _arc_outline_pts(a: Arc, n: int = 24) -> List[Tuple[float, float]]:
    """Discretise a stroked arc trace into an outlined closed polyline."""
    hw = (a.stroke_width or 0.0) / 2.0
    out: List[Tuple[float, float]] = []
    sweep = a.sweep_deg() or 360.0
    steps = max(2, int(n))
    inner: List[Tuple[float, float]] = []
    outer: List[Tuple[float, float]] = []
    for i in range(steps + 1):
        ang = math.radians(a.start_angle + sweep * i / steps)
        outer.append((a.cx + (a.radius + hw) * math.cos(ang),
                      a.cy + (a.radius + hw) * math.sin(ang)))
        inner.append((a.cx + (a.radius - hw) * math.cos(ang),
                      a.cy + (a.radius - hw) * math.sin(ang)))
    return outer + list(reversed(inner))
