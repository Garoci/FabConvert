"""Gerber reader / writer (direct text parsing — not pygerber rendering).

**Reader** (``GerberReader``): parses raw Gerber text directly into the
intermediate model + an Alignment.  The aperture definition is the
*authoritative* source of trace width (golden rule #1) — e.g.
``%ADD11C,0.2mm*%`` defines a 0.2mm round aperture.  pygerber's renderer
rasterizes the copper silhouette into a filled region, which loses the width
entirely downstream; that is why we parse the text here instead.  The supported
subset is the common PCB-copper case (round circle apertures + D02→D01 linear
draws + D03 flashes).  Macro apertures, region fills (G36/G37), and
step-and-repeat raise :class:`UnsupportedGerberConstruct` rather than be
guessed at — they cannot be represented as centreline+width.

The coordinate format string ``%FSLAX<IL><DL>Y<ID><DD>*%`` is parsed exactly:
integer/decimal-place digit counts per axis (KiCad writes X46Y46 = 4 integer +
6 decimal places).  Coordinates arrive as already-scaled integers; dividing by
``10**decimal`` recovers mm (verified on ``test6-F_Cu.gbr``:
``X68000000 / 1e6 == 68.000 mm``).

**Writer** (``GerberWriter``): reconstructs the real aperture from the
intermediate model's ``stroke_width`` and emits ``%ADD`` aperture selects +
``D01`` linear draws + ``D03`` flashes.  Pads (filled circles) become aperture
flashes.  Filled :class:`ClosedPolygon`s (copper pours / outline regions) are
emitted as ``G36``/``G37`` region fills — the native Gerber construct for a
filled area — so a pour round-trips INTO Gerber instead of vanishing (previously
these polygons were silently dropped with only a printed NOTE, which left a real
pour file silently missing its copper).  Re-reading a region we wrote through
this module's own :class:`GerberReader` is a separate, pre-existing limitation
(the reader raises :class:`UnsupportedGerberConstruct` on ``G36``/``G37``,
documented); a standard Gerber viewer / CAM tool reads the regions directly.

Golden rule zero: the Y-flip lives on the Alignment (Gerber is Y-up vs the
Y-down intermediate), not in this module.  The reader computes the flip axis as
the geometry midline in mm after parsing; the writer re-flips it back.
"""

from __future__ import annotations

import math
import re
import pathlib
from typing import Dict, List, Optional, Tuple, Union

from ..core.geometry import (
    Arc, Circle, ClosedPolygon, GeometrySet, Line, Path,
)
from ..alignment import Alignment, Units
from ..alignment.detect import resolve_gerber_units

# pathlib.Path is aliased Pathlib because core.geometry.Path (the centreline
# dataclass) shadows the bare name ``Path`` in this module.
Pathlib = pathlib.Path


class UnsupportedGerberConstruct(Exception):
    """Raised when a Gerber construct cannot be represented as centreline +
    width.  ::: (AM macro, G36/G37 region, SR block).  Carries the offending
    construct name so a caller can decide whether to fall back / re-export."""


_FS_RE = re.compile(r"%FS([LIT])([AN])X(\d)(\d)Y(\d)(\d)\*")
_MO_RE = re.compile(r"%MO(MM|IN)\*")
_ADD_RE = re.compile(r"%ADD(\d+)([A-Z]+),")
_ADD_CIRC_RE = re.compile(r"%ADD(\d+)C,([\d.]+)\*%")
_AM_RE = re.compile(r"^%AM", re.MULTILINE)
_SR_RE = re.compile(r"%SR")
# NOTE: the parser strips the trailing ``*`` from each line BEFORE matching
# (``s = ln.strip().rstrip("*")``), so these regexes must NOT require a
# trailing ``*`` — an earlier version had ``\*$`` here and matched nothing.
_OP_RE = re.compile(r"^X(-?\d+)Y(-?\d+)D(0[123])$")
_SEL_RE = re.compile(r"^D(\d{1,3})$")
_OP_X_RE = re.compile(r"^X(-?\d+)D(0[123])$")
_OP_Y_RE = re.compile(r"^Y(-?\d+)D(0[123])$")


class GerberReader:
    """Read a Gerber file into the intermediate model (mm, Y-down) + Alignment.

    Only the centreline+width subset is supported (round apertures, linear
    draws, flashes).  Macro apertures, G36/G37 region fills, and step-and-repeat
    raise :class:`UnsupportedGerberConstruct`.
    """

    def __init__(self):
        self.alignment: Optional[Alignment] = None
        self.geometry = GeometrySet()

    @classmethod
    def from_file(cls, path: Union[str, Pathlib],
                 unit_override: Optional[Units] = None) -> "GerberReader":
        r = cls()
        r._load(Pathlib(path), unit_override)
        return r

    @classmethod
    def from_string(cls, text: str,
                    unit_override: Optional[Units] = None) -> "GerberReader":
        r = cls()
        r._parse(text, unit_override)
        return r

    def _load(self, path: Pathlib, unit_override: Optional[Units]) -> None:
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
        self._parse(text, unit_override)

    def _parse(self, text: str, unit_override: Optional[Units]) -> None:
        # --- Unsupported constructs first: never guess (golden rule #1) ---
        if _AM_RE.search(text):
            raise UnsupportedGerberConstruct("AM macro aperture present")
        if "G36" in text or "G37" in text:
            raise UnsupportedGerberConstruct("G36/G37 region fill present")
        if _SR_RE.search(text):
            raise UnsupportedGerberConstruct("SR step-and-repeat block present")

        # --- Coordinate format ---
        fs = _FS_RE.search(text)
        if not fs:
            raise UnsupportedGerberConstruct(
                "%FS coordinate-format string missing — cannot safely interpret")
        x_dec, y_dec = int(fs.group(4)), int(fs.group(6))
        div_x = 10.0 ** x_dec
        div_y = 10.0 ** y_dec

        # --- Units ---
        mo = _MO_RE.search(text)
        mo_unit = mo.group(1) if mo else None
        self.alignment = resolve_gerber_units(mo_unit, unit_override)
        unit_factor = self.alignment.unit_scale  # 25.4 if inches, 1.0 if mm

        # --- Apertures: only circular apertures are supported ---
        apertures: Dict[int, float] = {}
        for m in re.finditer(r"%ADD(\d+)([A-Z]+),([\d.]+)\*%", text):
            code, shape, dia = int(m.group(1)), m.group(2), float(m.group(3))
            if shape != "C":
                raise UnsupportedGerberConstruct(
                    f"aperture D{code} is {shape}, not a circle")
            apertures[code] = dia  # diameter in source units (mm/in)

        # --- Walk the command stream ---
        cur_ap: Optional[int] = None
        cur_pos: Optional[Tuple[float, float]] = None  # (x_mm, y_mm) in source units
        draws: List[Tuple[int, float, float, float, float]] = []
        flashes: List[Tuple[int, float, float]] = []
        for ln in text.split("\n"):
            s = ln.strip().rstrip("*")
            if not s:
                continue
            m_sel = _SEL_RE.match(s)
            if m_sel:
                cur_ap = int(m_sel.group(1))
                continue
            m_op = _OP_RE.match(s)
            if m_op:
                xv, yv, code = int(m_op.group(1)), int(m_op.group(2)), m_op.group(3)
            else:
                # lenient single-axis moves
                m_x = _OP_X_RE.match(s); m_y = _OP_Y_RE.match(s)
                if m_x:
                    xv = int(m_x.group(1)); code = m_x.group(2)
                    yv = int(round((cur_pos[1] if cur_pos else 0.0)
                                   * div_y / unit_factor))
                elif m_y:
                    yv = int(m_y.group(1)); code = m_y.group(2)
                    xv = int(round((cur_pos[0] if cur_pos else 0.0)
                                   * div_x / unit_factor))
                else:
                    continue
            x_mm = xv / div_x * unit_factor
            y_mm = yv / div_y * unit_factor
            if code == "02":          # move / light-off
                cur_pos = (x_mm, y_mm)
            elif code == "01":        # draw from cur_pos to here
                if cur_pos is None:
                    cur_pos = (x_mm, y_mm)
                draws.append((cur_ap, cur_pos[0], cur_pos[1], x_mm, y_mm))
                cur_pos = (x_mm, y_mm)
            elif code == "03":         # flash aperture
                flashes.append((cur_ap, x_mm, y_mm))
                cur_pos = (x_mm, y_mm)

        if not draws and not flashes:
            raise UnsupportedGerberConstruct("no draws or flashes found")

        # --- Compute the Y-flip axis in mm (geometry midline) ---
        # Gerber coords are in source units (already mm if %MOMM).  Gather
        # the bbox in mm to compute the flip-axis the intermediate expects.
        xs: List[float] = []; ys: List[float] = []
        for _ap, x0, y0, x1, y1 in draws:
            xs.extend([x0, x1]); ys.extend([y0, y1])
        for ap, x, y in flashes:
            r = (apertures.get(ap, 0.0) * unit_factor) / 2.0
            xs.extend([x - r, x + r]); ys.extend([y - r, y + r])
        flip_axis_mm = (min(ys) + max(ys)) if ys else 0.0
        # The Alignment's flip_axis is in source units; convert mm back.
        self.alignment.flip_axis = flip_axis_mm / unit_factor
        af = self.alignment

        # --- Emit centreline paths + filled circles into the intermediate ---
        for ap, x0, y0, x1, y1 in draws:
            dia = apertures.get(ap)
            if dia is None or dia <= 0:
                continue
            ix0 = af.to_intermediate_x(x0)
            iy0 = af.to_intermediate_y(y0)
            ix1 = af.to_intermediate_x(x1)
            iy1 = af.to_intermediate_y(y1)
            self.geometry.paths.append(Path(
                segments=[(ix0, iy0), (ix1, iy1)],
                stroke_width=dia * unit_factor,
                filled=False, closed=False))

        for ap, x, y in flashes:
            dia = apertures.get(ap)
            if dia is None or dia <= 0:
                continue
            ix = af.to_intermediate_x(x)
            iy = af.to_intermediate_y(y)
            self.geometry.circles.append(Circle(
                cx=ix, cy=iy, radius=(dia * unit_factor) / 2.0,
                stroke_width=None))


class GerberWriter:
    """Write the intermediate model to Gerber (mm, Y-up).

    Reconstructs apertures from the intermediate model's ``stroke_width``
    (golden rule #1) and emits D02→D01 draws (traces) and D03 flashes (pads).
    The intermediate is Y-down; Gerber is Y-up, so the writer re-flips Y about
    the geometry midline.  Output is in mm (`%MOMM*%`).
    """

    def __init__(self, alignment: Optional[Alignment] = None,
                 precision: int = 6):
        self.alignment = alignment or Alignment.mm(y_flip=True)
        self.precision = precision

    def to_string(self, geom: GeometrySet) -> str:
        div = 10 ** self.precision

        # Re-flip Y about the intermediate geometry midline (mm).
        bb = geom.bounds()
        flip_axis_inter = (bb[1] + bb[3]) if (bb is not None) else 0.0
        def Y(y: float) -> float:
            return flip_axis_inter - y

        # --- Collect distinct trace widths -> aperture table ---
        widths: Dict[float, int] = {}
        next_d = 10
        for p in geom.paths:
            if p.is_stroked():
                w = round(p.stroke_width, 6)
                if w not in widths:
                    widths[w] = next_d; next_d += 1
        for ln in geom.lines:
            if ln.stroke_width is not None:
                w = round(ln.stroke_width, 6)
                if w not in widths:
                    widths[w] = next_d; next_d += 1
        for a in geom.arcs:
            if a.stroke_width is not None:
                w = round(a.stroke_width, 6)
                if w not in widths:
                    widths[w] = next_d; next_d += 1
        # Pads -> flashing apertures (round).
        pad_radii: Dict[float, int] = {}
        for c in geom.circles:
            if c.stroke_width is None:
                r = round(c.radius, 6)
                if r not in pad_radii:
                    pad_radii[r] = next_d; next_d += 1

        # --- Build the Gerber text ---
        L: List[str] = []
        L.append("%FSLAX36Y36*%")     # 3 int + 6 decimal places (matches KiCad style)
        L.append("%MOMM*%")
        for w, code in sorted(widths.items(), key=lambda kv: kv[1]):
            L.append(f"%ADD{code}C,{w:.6f}*%")
        for r, code in sorted(pad_radii.items(), key=lambda kv: kv[1]):
            L.append(f"%ADD{code}C,{r*2:.6f}*%")

        def fmt(carry_ap: Optional[int], last_pos: Optional[Tuple[float, float]],
                x: float, y: float, op: str) -> Tuple[str, Optional[Tuple[float, float]]]:
            xi = int(round(x * div))
            yi = int(round(y * div))
            return f"X{xi}Y{yi}D{op}*", (x, y)

        cur_ap: Optional[int] = None
        for p in geom.paths:
            if not p.is_stroked() or len(p.segments) < 2:
                continue
            w = round(p.stroke_width, 6)
            ap = widths[w]
            if ap != cur_ap:
                L.append(f"D{ap}*")
                cur_ap = ap
            x0, y0 = p.segments[0]; x1, y1 = p.segments[1]
            # move (light off) to start, draw (D01) to end
            L.append(f"X{int(round(x0 * div))}Y{int(round(Y(y0) * div))}D02*")
            L.append(f"X{int(round(x1 * div))}Y{int(round(Y(y1) * div))}D01*")

        for ln in geom.lines:
            if ln.stroke_width is None:
                continue
            w = round(ln.stroke_width, 6); ap = widths[w]
            if ap != cur_ap:
                L.append(f"D{ap}*"); cur_ap = ap
            L.append(f"X{int(round(ln.x0 * div))}Y{int(round(Y(ln.y0) * div))}D02*")
            L.append(f"X{int(round(ln.x1 * div))}Y{int(round(Y(ln.y1) * div))}D01*")

        for a in geom.arcs:
            if a.stroke_width is None:
                continue
            w = round(a.stroke_width, 6); ap = widths[w]
            if ap != cur_ap:
                L.append(f"D{ap}*"); cur_ap = ap
            p0, p1 = a.endpoints()
            L.append(f"X{int(round(p0[0] * div))}Y{int(round(Y(p0[1]) * div))}D02*")
            L.append(f"X{int(round(p1[0] * div))}Y{int(round(Y(p1[1]) * div))}D01*")

        # --- Pads: flashes ---
        for c in geom.circles:
            if c.stroke_width is not None:
                continue  # stroked rings -> could draw; rare; skip (documented)
            r = round(c.radius, 6); ap = pad_radii[r]
            if ap != cur_ap:
                L.append(f"D{ap}*"); cur_ap = ap
            L.append(f"X{int(round(c.cx * div))}Y{int(round(Y(c.cy) * div))}D03*")

        # --- Filled polygons -> G36/G37 region fills ----------------------
        # ClosedPolygon carries no centreline+width (it is an outline / pour
        # boundary), so it cannot be written as a D01 draw or D03 flash.  Gerber
        # DOES have a native representation: the G36/G37 region.  Between G36
        # (region-on) and G37 (region-off) every D02/D01 vector-traces the
        # outline — no aperture is required in region mode — and the enclosed
        # area is filled.  This is the right thing to do: a copper pour read
        # from a DXF now actually round-trips INTO Gerber instead of vanishing.
        #
        # Previously these polygons were silently dropped with only a printed
        # NOTE — a real pour file produced a Gerber silently missing its copper
        # (worse than failing).  Emitting regions fixes that.  Region mode is
        # self-contained (no aperture state; gerber_io's GerberReader raises
        # UnsupportedGerberConstruct on G36/37 — re-reading a region we wrote is
        # a separate, pre-existing limitation, documented), and rendering it
        # here cannot corrupt the centreline draws above because draws/flashes
        # happened before G36 and no draw command appears between G36 and G37.
        emitted_polys = 0
        for poly in geom.polygons:
            # Drop closing duplicate (a closed chain's first==last vertex) and
            # any sub-µm-coincident points so the outline has >= 3 clean
            # vertices; a degenerate outline is skipped, never emitted half.
            pts = _dedup_polygon_points(
                [(x, Y(y)) for (x, y) in poly.points], 1.0 / div)
            if len(pts) < 3:
                continue
            L.append("G36*")                       # region mode on
            x0, y0 = pts[0]
            L.append(f"X{int(round(x0 * div))}Y{int(round(y0 * div))}D02*")  # move to start
            for (px, py) in pts[1:]:
                L.append(f"X{int(round(px * div))}Y{int(round(py * div))}D01*")
            L.append("G37*")                       # region mode off; area filled
            emitted_polys += 1
        if len(geom.polygons) and emitted_polys != len(geom.polygons):
            # Never silently succeed with missing copper: report any polygon we
            # could not represent as a region (degenerate outline) loudly.
            print(f"[GerberWriter] WARNING: {len(geom.polygons) - emitted_polys} "
                  f"of {len(geom.polygons)} filled polygon(s) had a degenerate "
                  f"outline (< 3 clean vertices) and were dropped; "
                  f"{emitted_polys} emitted as G36/G37 region fills.")

        return "\n".join(L) + "\nM02*\n"

    def to_file(self, geom: GeometrySet, path: Union[str, Pathlib]) -> None:
        Pathlib(path).write_text(self.to_string(geom), encoding="utf-8")


def _dedup_polygon_points(
        points: List[Tuple[float, float]],
        tol: float,
        ) -> List[Tuple[float, float]]:
    """Collapse coincident vertices in a closed-polygon outline.

    A ``ClosedPolygon`` traced from a chain walk (``edgepair.trace_line_polygons``)
    ends with its first vertex repeated (first == last); KiCad outlines can also
    carry sub-µm-coincident spur vertices.  For a G36/G37 region, every
    distinct turn matters but a repeated vertex is harmless, so the goal here is
    only to drop the closing duplicate (and any run of points closer than
    ``tol``) so the emitted outline has at least 3 clean vertices and does not
    draw a zero-length segment.  ``tol`` is in mm (the writer passes
    ``1/10**precision``, i.e. one least-significant Gerber coordinate step).
    """
    out: List[Tuple[float, float]] = []
    for i, (x, y) in enumerate(points):
        if i == len(points) - 1 and out:
            ox, oy = out[0]
            if abs(x - ox) <= tol and abs(y - oy) <= tol:
                # closing duplicate of the first vertex -> drop, region closes it
                continue
        if out:
            lx, ly = out[-1]
            if abs(x - lx) <= tol and abs(y - ly) <= tol:
                continue  # coincident with the previous vertex -> collapse
        out.append((x, y))
    return out

