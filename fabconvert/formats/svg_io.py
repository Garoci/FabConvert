"""SVG reader / writer for the fabconvert intermediate model.

SVG is already Y-down, like the intermediate, so an SvgReader's
:class:`~fabconvert.alignment.Alignment` has ``y_flip=False`` and
``unit_scale`` parsed from the width/height unit suffix (KiCad SVGs are mm →
1:1).  Coordinates are used as-is after the Alignment scale, never flipped.

The reader recognises the KiCad export convention the original project's
pipeline was built against:

  * Stroked centreline ``<path>``s carry ``stroke-width`` → :class:`Path`
    with ``stroke_width`` (a *trace*, not an outline — golden rule #5).
  * Filled ``<circle>``s (``fill`` non-none, no stroke or stroke none) →
    :class:`Circle` (a *pad/via*, no stroke width).
  * Filled ``<path>``s / ``<polygon>``s → :class:`ClosedPolygon` (an outline
    copper region).
  * ``<line>`` / ``<rect>`` / ``<ellipse>`` parsed too, for completeness.

The writer emits that same KiCad convention: ``fill="none" stroke="#000000"
stroke-width=<mm>`` centreline ``<path>``s for traces, filled ``<circle>``s for
pads, filled outline ``<path>``s for polygons.  A viewBox plus width/height in
mm wraps everything, so a round trip lands at the same geometry.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
import pathlib
from typing import List, Optional, Tuple, Union

from ..core.geometry import (
    Arc, Circle, ClosedPolygon, GeometrySet, Line, Path,
)
from ..alignment import Alignment
from ..alignment.detect import svg_alignment_from_viewbox

# NB: ``pathlib.Path`` is deliberately aliased as ``Pathlib`` because
# ``core.geometry.Path`` (the stroked-centreline dataclass) shadows the bare
# name ``Path`` in this module.  Use Pathlib for filesystem paths.
Pathlib = pathlib.Path

_SVG_NS = "http://www.w3.org/2000/svg"
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_len(attr: Optional[str]) -> Optional[float]:
    """Parse an SVG length attr, stripping 'mm'/'px'/'in' suffixes."""
    if attr is None:
        return None
    s = attr.strip()
    if not s:
        return None
    # strip trailing unit suffix
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _unit_suffix(attr: Optional[str]) -> Optional[str]:
    if attr is None:
        return None
    s = attr.strip().lower()
    if s.endswith("mm"):
        return "mm"
    if s.endswith("px") or s.endswith(""):
        return None  # user units / px
    if s.endswith("in") or s.endswith("inch"):
        return "in"
    return None


def _style_dict(style_attr: Optional[str]) -> dict:
    """Parse a CSS-style ``style="..."`` attribute into a dict."""
    if not style_attr:
        return {}
    out = {}
    for decl in style_attr.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_path_d(d: str) -> List[Tuple[str, Tuple[float, ...]]]:
    """Tokenise an SVG path ``d`` string into ``[(cmd, (params...)), ...]``.

    A minimalising tokenizer: command letters plus their following numeric
    params.  Repeated implicit coordinates (``M x y x y ...``) are emitted as
    separate ``L`` tuples following the leading ``M``.
    """
    tokens = re.findall(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    out: List[Tuple[str, Tuple[float, ...]]] = []
    i = 0
    param_counts = {
        "M": 2, "m": 2, "L": 2, "l": 2, "H": 1, "h": 1, "V": 1, "v": 1,
        "C": 6, "c": 6, "S": 4, "s": 4, "Q": 4, "q": 4, "T": 2, "t": 2,
        "A": 7, "a": 7, "Z": 0, "z": 0,
    }
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            count = param_counts.get(cmd, 0)
            i += 1
            if count == 0:
                out.append((cmd, ()))
                continue
            # Read repeated param groups until the next command letter.
            while i < len(tokens) and not tokens[i].isalpha():
                params: List[float] = []
                for _ in range(count):
                    if i >= len(tokens) or tokens[i].isalpha():
                        break
                    params.append(float(tokens[i])); i += 1
                if len(params) < count:
                    break
                out.append((cmd, tuple(params)))
                # After the first M/m pair, repeats are implicit L/l.
                if cmd == "M":
                    cmd = "L"
                elif cmd == "m":
                    cmd = "l"
        else:
            i += 1  # stray number; ignore
    return out


def _path_points(d: str) -> Tuple[List[Tuple[float, float]],
                                  Optional[Tuple[Arc, ...]]]:
    """Flatten a path ``d`` into absolute (x, y) points collecting any Arcs.

    Returns (points, arcs).  `points` is the polyline of vertex visits (the
    centreline for a stroked path; the outline for a filled path).  `arcs` is
    None when the path had no arc commands; otherwise the arcs sampled.
    Curves are flattened to their endpoints only (adequate for PCB centreline
    round-trip at micron tolerance, since KiCad SVGs use straight segments).
    """
    cmds = _parse_path_d(d)
    pts: List[Tuple[float, float]] = []
    arcs: List[Arc] = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    for cmd, params in cmds:
        if cmd in ("M", "L"):
            cur = (params[0], params[1]); start = cur if cmd == "M" else start
            pts.append(cur)
        elif cmd in ("m", "l"):
            cur = (cur[0] + params[0], cur[1] + params[1])
            if cmd == "m":
                start = cur
            pts.append(cur)
        elif cmd == "H":
            cur = (params[0], cur[1]); pts.append(cur)
        elif cmd == "h":
            cur = (cur[0] + params[0], cur[1]); pts.append(cur)
        elif cmd == "V":
            cur = (cur[0], params[0]); pts.append(cur)
        elif cmd == "v":
            cur = (cur[0], cur[1] + params[0]); pts.append(cur)
        elif cmd in ("C", "S", "Q", "T"):
            # Straight-line approximation: jump to the final endpoint only.
            cur = (params[-2], params[-1]); pts.append(cur)
        elif cmd in ("c", "s", "q", "t"):
            cur = (cur[0] + params[-2], cur[1] + params[-1]); pts.append(cur)
        elif cmd in ("A", "a"):
            rx, ry, _xrot, _large, sweep, x, y = params
            if cmd == "a":
                x, y = cur[0] + x, cur[1] + y
            # Approximate arc by its chord; record an Arc primitive too.
            r = max(abs(rx), abs(ry))
            if r > 0:
                ang0 = math.degrees(math.atan2(cur[1] - (y - 0), cur[0] - (x - 0)))
            arcs.append(Arc(cx=(cur[0] + x) / 2.0, cy=(cur[1] + y) / 2.0,
                            radius=r, start_angle=0.0, end_angle=0.0,
                            stroke_width=None))
            cur = (x, y); pts.append(cur)
        elif cmd in ("Z", "z"):
            pts.append(start); cur = start
    return pts, (tuple(arcs) if arcs else None)


class SvgReader:
    """Read an SVG file into the intermediate model (mm, Y-down).

    The reader is convention-aware (KiCad SVGs group stroked and filled
    primitives separately with shared ``<g style="...">`` attributes) but
    also reads inline attributes.  Inherited style cascades from ancestor
    ``<g>`` elements.
    """

    def __init__(self, source: Union[str, Path, bytes, str]):
        # Accept a path or a raw SVG string/bytes.
        self._source = source
        self.alignment: Optional[Alignment] = None
        self.geometry = GeometrySet()

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "SvgReader":
        r = cls(Pathlib(path).read_bytes())
        r._parse()
        return r

    @classmethod
    def from_string(cls, svg_text: str) -> "SvgReader":
        r = cls(svg_text.encode("utf-8"))
        r._parse()
        return r

    # --- the heavy part ----------------------------------------------------

    def _parse(self) -> None:
        data = self._source
        if isinstance(data, (bytes, bytearray)):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        root = ET.fromstring(text)

        # --- Alignment: viewBox present? unit suffix on width/height? -----
        vb_attr = root.get("viewBox")
        vb = None
        if vb_attr:
            parts = vb_attr.replace(",", " ").split()
            if len(parts) == 4:
                try:
                    vb = tuple(float(p) for p in parts)
                except ValueError:
                    vb = None
        self.alignment = svg_alignment_from_viewbox(
            vb, root.get("width"), root.get("height"))
        sc = self.alignment.unit_scale  # 1.0 for mm SVGs

        # --- Walk the tree with inherited style --------------------------
        self._walk(root, style={}, sc=sc)

    def _walk(self, elt: ET.Element, style: dict, sc: float) -> None:
        """Depth-first walk merging style from <g> ancestors onto children.

        Hard-won lesson (this was the SVG empty-parse bug): a container can be
        the root ``<svg>`` element (or an unknown element), NOT just ``<g>``.
        The first version only recursed for ``<g>`` and so fell straight through
        the root ``<svg>`` into the geometry-classify branch and returned without
        ever visiting its children — yielding ``stroked=0 circles=0`` on every
        KiCad SVG.  Containers here are therefore: ``<g>``, ``<svg>``, and any
        element whose tag isn't one of the recognised geometry leaves.  We always
        recurse into a container's children carrying the merged style.
        """
        tag = _strip_ns(elt.tag)
        if tag in ("style", "defs", "title", "desc", "metadata"):
            return
        # Merge inherited style with this element's own style attributes.
        # Presentation attributes (fill/stroke/...) and the ``style`` attribute
        # both contribute; own attributes override inherited ones (CSS spec).
        local = dict(style)
        local.update(_style_dict(elt.get("style")))
        for k in ("fill", "stroke", "stroke-width"):
            v = elt.get(k)
            if v is not None:
                local[k] = v

        is_container = tag in ("g", "svg", "a", "symbol", "use", "marker",
                              "pattern", "clipPath", "mask", "switch", "defs")
        is_geometry = tag in (
            "circle", "ellipse", "line", "rect", "polygon", "polyline",
            "path", "image", "text")
        # An unknown tag that has children is a container too (be permissive).
        if not is_container and not is_geometry and len(list(elt)) > 0:
            is_container = True

        if is_container:
            for child in elt:
                self._walk(child, local, sc)
            # A container can ALSO carry geometry (e.g. a <use> with its own
            # geometric footprint), but the KiCad SVGs don't — recurse only.
            return

        # --- Geometry leaf: own attributes already folded into local. ---
        # Re-read presentation attrs directly on the leaf (they may have been
        # set here and not on a parent <g>).
        own = {**local, **_style_dict(elt.get("style"))}
        for k in ("fill", "stroke", "stroke-width", "stroke-linecap"):
            v = elt.get(k)
            if v is not None:
                own[k] = v

        fill = own.get("fill")
        stroke = own.get("stroke")
        sw = own.get("stroke-width")
        sw_mm = _parse_len(sw)  # in SVG user units; scale by Alignment
        if sw_mm is not None:
            sw_mm *= sc

        # Classify: pad (filled circle/ellipse) vs trace (stroked) vs outline.
        stroked = (stroke is not None and stroke.lower() != "none")
        filled = (fill is not None and fill.lower() != "none")

        if tag == "circle":
            cx = _parse_len(elt.get("cx")) or 0.0
            cy = _parse_len(elt.get("cy")) or 0.0
            r = _parse_len(elt.get("r")) or 0.0
            cx *= sc; cy *= sc; r *= sc
            if stroked and sw_mm is not None:
                # stroked ring (rare) -> Circle with width
                self.geometry.circles.append(
                    Circle(cx=cx, cy=cy, radius=r, stroke_width=sw_mm))
            else:
                self.geometry.circles.append(
                    Circle(cx=cx, cy=cy, radius=r, stroke_width=None))
        elif tag == "ellipse":
            cx = _parse_len(elt.get("cx")) or 0.0
            cy = _parse_len(elt.get("cy")) or 0.0
            rx = _parse_len(elt.get("rx")) or 0.0
            ry = _parse_len(elt.get("ry")) or 0.0
            cx *= sc; cy *= sc; rx *= sc; ry *= sc
            if stroked and sw_mm is not None:
                # approximate by a circle of the average radius, carrying width
                self.geometry.circles.append(Circle(
                    cx=cx, cy=cy, radius=(rx + ry) / 2.0, stroke_width=sw_mm))
            else:
                self.geometry.circles.append(Circle(
                    cx=cx, cy=cy, radius=(rx + ry) / 2.0, stroke_width=None))
        elif tag == "line":
            x1 = (_parse_len(elt.get("x1")) or 0.0) * sc
            y1 = (_parse_len(elt.get("y1")) or 0.0) * sc
            x2 = (_parse_len(elt.get("x2")) or 0.0) * sc
            y2 = (_parse_len(elt.get("y2")) or 0.0) * sc
            self.geometry.lines.append(Line(
                x0=x1, y0=y1, x1=x2, y1=y2,
                stroke_width=(sw_mm if stroked else None)))
        elif tag == "rect":
            x = (_parse_len(elt.get("x")) or 0.0) * sc
            y = (_parse_len(elt.get("y")) or 0.0) * sc
            w = (_parse_len(elt.get("width")) or 0.0) * sc
            h = (_parse_len(elt.get("height")) or 0.0) * sc
            self.geometry.polygons.append(ClosedPolygon(points=[
                (x, y), (x + w, y), (x + w, y + h), (x, y + h)]))
        elif tag in ("polygon", "polyline"):
            pts_str = elt.get("points", "")
            nums = [float(t) for t in _NUM_RE.findall(pts_str)]
            pts = [(nums[i] * sc, nums[i + 1] * sc)
                   for i in range(0, len(nums) - 1, 2)]
            if stroked and sw_mm is not None:
                self.geometry.paths.append(Path(
                    segments=pts, stroke_width=sw_mm,
                    closed=(tag == "polygon"), filled=False))
            elif filled and tag == "polygon":
                self.geometry.polygons.append(ClosedPolygon(points=pts))
            else:
                self.geometry.paths.append(Path(
                    segments=pts, stroke_width=None, closed=(tag == "polygon"),
                    filled=False))
        elif tag == "path":
            pts, arcs = _path_points(elt.get("d", ""))
            pts = [(p[0] * sc, p[1] * sc) for p in pts]
            if arcs:
                for a in arcs:
                    # scale arc coords too
                    self.geometry.arcs.append(Arc(
                        cx=a.cx * sc, cy=a.cy * sc, radius=a.radius * sc,
                        start_angle=a.start_angle, end_angle=a.end_angle,
                        stroke_width=sw_mm if stroked else None))
            if len(pts) >= 2:
                if stroked and sw_mm is not None:
                    self.geometry.paths.append(Path(
                        segments=pts, stroke_width=sw_mm,
                        closed=False, filled=False))
                elif filled:
                    self.geometry.polygons.append(ClosedPolygon(points=pts))
                else:
                    # Unstyled path — record as zero-width centreline so it
                    # isn't silently dropped (it might be a boundary outline).
                    self.geometry.paths.append(Path(
                        segments=pts, stroke_width=None,
                        closed=False, filled=True))


class SvgWriter:
    """Write the intermediate model back to KiCad-style SVG (mm, Y-down).

    Traces (``Path``/``Line`` with stroke_width) become stroked centreline
    ``<path>``s carrying ``stroke-width``.  Pads (``Circle`` with no width)
    become filled ``<circle>``s.  Polygons become filled outline ``<path>``s.
    A padded viewBox in mm wraps everything exactly as the original
    ``GerberConverter``/``DxfConverter._emit_gerber_svg`` did.
    """

    def __init__(self, alignment: Optional[Alignment] = None):
        # SVG is Y-down like the intermediate; if a Y-flip is supplied we
        # honour it (e.g. emitting back into a Y-up SVG that lacks it), but the
        # default no-flip is the common case.
        self.alignment = alignment or Alignment.mm(y_flip=False)

    def to_string(self, geom: GeometrySet) -> str:
        root = ET.Element(f"{{{_SVG_NS}}}svg")
        root.set("xmlns", _SVG_NS)
        root.set("version", "1.1")

        # bbox in intermediate mm
        bb = geom.bounds()
        if bb is None:
            root.set("width", "1mm")
            root.set("height", "1mm")
            root.set("viewBox", "0 0 1 1")
            return '<?xml version="1.0" encoding="utf-8"?>\n' + \
                   ET.tostring(root, encoding="unicode")

        x0, y0, x1, y1 = bb
        w = max(x1 - x0, 1e-6); h = max(y1 - y0, 1e-6)
        pad = max(w, h) * 0.02
        pad = max(pad, 0.1)
        vb_x = x0 - pad; vb_y = y0 - pad
        vb_w = w + 2 * pad; vb_h = h + 2 * pad
        root.set("width", f"{vb_w:.6f}mm")
        root.set("height", f"{vb_h:.6f}mm")
        root.set("viewBox", f"{vb_x:.6f} {vb_y:.6f} {vb_w:.6f} {vb_h:.6f}")

        af = self.alignment
        def Y(y: float) -> float:
            return af.flip_intermediate_y(y)

        # --- Traces: stroked centreline paths (carrying their own width) ---
        # A shared <g> groups the stroke ones; each path also carries its own
        # attributes (so a downstream recolor keeps the width — mirrors the
        # original convention exactly).
        for p in geom.paths:
            if not p.segments or len(p.segments) < 2:
                continue
            if p.is_stroked():
                d_parts = [f"M {p.segments[0][0]:.6f} {Y(p.segments[0][1]):.6f}"]
                for (x, y) in p.segments[1:]:
                    d_parts.append(f"L {x:.6f} {Y(y):.6f}")
                el = ET.SubElement(root, f"{{{_SVG_NS}}}path")
                el.set("d", " ".join(d_parts))
                el.set("fill", "none")
                el.set("stroke", "#000000")
                el.set("stroke-width", f"{(p.stroke_width or 0.0):.6f}")
                el.set("stroke-linecap", "round")
                el.set("stroke-linejoin", "round")
            elif p.filled:
                d_parts = [f"M {p.segments[0][0]:.6f} {Y(p.segments[0][1]):.6f}"]
                for (x, y) in p.segments[1:]:
                    d_parts.append(f"L {x:.6f} {Y(y):.6f}")
                if p.closed:
                    d_parts.append("Z")
                el = ET.SubElement(root, f"{{{_SVG_NS}}}path")
                el.set("d", " ".join(d_parts))
                el.set("fill", "#000000")
                el.set("stroke", "none")

        # --- Stroked lines carrying width → centreline paths (single-seg) --
        for ln in geom.lines:
            if ln.stroke_width is not None:
                el = ET.SubElement(root, f"{{{_SVG_NS}}}path")
                el.set("d", f"M {ln.x0:.6f} {Y(ln.y0):.6f} L {ln.x1:.6f} {Y(ln.y1):.6f}")
                el.set("fill", "none")
                el.set("stroke", "#000000")
                el.set("stroke-width", f"{ln.stroke_width:.6f}")
                el.set("stroke-linecap", "round")
            # outline edges (stroke_width None) are emitted via polygons only

        # --- Polygons: filled outline paths ---
        for poly in geom.polygons:
            if len(poly.points) < 3:
                continue
            d_parts = [f"M {poly.points[0][0]:.6f} {Y(poly.points[0][1]):.6f}"]
            for (x, y) in poly.points[1:]:
                d_parts.append(f"L {x:.6f} {Y(y):.6f}")
            d_parts.append("Z")
            el = ET.SubElement(root, f"{{{_SVG_NS}}}path")
            el.set("d", " ".join(d_parts))
            el.set("fill", "#000000")
            el.set("stroke", "none")
            el.set("fill-rule", "evenodd")

        # --- Arcs: stroked centreline (chord approximation) ---
        for a in geom.arcs:
            p0, p1 = a.endpoints()
            if a.stroke_width is not None:
                el = ET.SubElement(root, f"{{{_SVG_NS}}}path")
                el.set("d", f"M {p0[0]:.6f} {Y(p0[1]):.6f} L {p1[0]:.6f} {Y(p1[1]):.6f}")
                el.set("fill", "none")
                el.set("stroke", "#000000")
                el.set("stroke-width", f"{a.stroke_width:.6f}")
                el.set("stroke-linecap", "round")

        # --- Circles: pads (filled) or stroked rings ---
        for c in geom.circles:
            el = ET.SubElement(root, f"{{{_SVG_NS}}}circle")
            el.set("cx", f"{c.cx:.6f}")
            el.set("cy", f"{Y(c.cy):.6f}")
            el.set("r", f"{c.radius:.6f}")
            if c.stroke_width is not None:
                el.set("fill", "none")
                el.set("stroke", "#000000")
                el.set("stroke-width", f"{c.stroke_width:.6f}")
            else:
                el.set("fill", "#000000")
                el.set("stroke", "none")

        return '<?xml version="1.0" encoding="utf-8"?>\n' + \
               ET.tostring(root, encoding="unicode")

    def to_file(self, geom: GeometrySet, path: Union[str, Pathlib]) -> None:
        Pathlib(path).write_text(self.to_string(geom), encoding="utf-8")
