"""Live geometry preview canvas for the fabconvert GUI.

``GeometryCanvas`` paints a :class:`fabconvert.core.geometry.GeometrySet` (its
lines/arcs/circles/polygons/paths) fit-to-bounds with a margin and a bbox
rectangle for orientation.  The intermediate model is in
millimetres, **Y-down** — and a screen canvas is also Y-down — so we draw
directly with no axis flip, matching the library's own
``Arc.endpoints()`` convention.

Only imports PySide6 at module top.  This module is only imported after
:func:`fabconvert.gui._import_qt` has succeeded (from inside ``launch``),
so a missing PySide6 never produces a raw traceback from here.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen,
                            QPolygonF, QPainterPath)
from PySide6.QtWidgets import QWidget

from ..core.geometry import GeometrySet

# Visual constants.
_MARGIN = 24.0          # px padding around drawn content
_MIN_PEN_PX = 1.2
# Minimum rendered DIAMETER (px) for a filled pad/via circle, analogous to
# _MIN_PEN_PX for stroked primitives.  A real 0.3mm pad renders at its literal
# ~3.75px radius at typical zoom — a barely-visible translucent smudge — while
# stroked traces are floored to _MIN_PEN_PX.  Floor the filled pad's diameter
# (not radius) so small pads stay legible WITHOUT flattening visible size at
# high zoom (a 2mm pad still renders ~2x a 0.3mm pad once both clear the floor).
_PAD_MIN_PX = 4.0
_TEXT_PAD = 8


class GeometryCanvas(QWidget):
    """A widget that draws a ``GeometrySet`` fit to its size.

    Call :meth:`set_geometry` with a ``GeometrySet`` (or ``None`` to clear);
    the widget repaints and shows an empty-state hint when there's nothing
    to draw.
    """

    _FG = QColor("#1f6feb")      # draw color (light)
    _FG_DARK = QColor("#58a6ff")
    # Filled-pad/via colour — OPAQUE (alpha 255, not the alpha-70 fill used by
    # pour polygons) so pads read clearly on the canvas at the legible minimum
    # diameter below, instead of a faint translucent smudge.  Distinct hue from
    # the blue traces so a pad is visibly a pad, not a small trace blob.
    _PAD = QColor("#d68f00")        # warm amber (light)
    _PAD_DARK = QColor("#e3b341")
    # NB: QColor(name, alpha) is not a valid ctor in PySide6 — set alpha after.
    _BBOX = QColor(0, 0, 0, 90)
    _BBOX_DARK = QColor(255, 255, 255, 90)

    def __init__(self, title: str = "",
                 dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._geom: Optional[GeometrySet] = None
        self._title = title
        self._dark = dark
        self.setMinimumSize(320, 280)
        self.setAcceptDrops(False)
        # Cosmetic: subtle rounded card via stylesheet is handled at the app
        # level; here we just keep the background transparent so the card shows.

    # ---- public API -------------------------------------------------------

    def set_geometry(self, geom: Optional[GeometrySet]) -> None:
        self._geom = geom
        self.update()

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        # Title.
        if self._title:
            p.setPen(QPen(self._fg()))
            f = QFont()
            f.setPointSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(_TEXT_PAD, 6, w - 2 * _TEXT_PAD, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, self._title)

        geom = self._geom
        if geom is None or geom.is_empty:
            p.setPen(QPen(self._muted()))
            f = QFont()
            f.setPointSize(10)
            p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter,
                       "Drop a file or pick one to preview")
            return

        bounds = geom.bounds()
        if bounds is None:
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "(empty geometry)")
            return
        xmin, ymin, xmax, ymax = bounds
        gw, gh = (xmax - xmin) or 1.0, (ymax - ymin) or 1.0
        inner_w = w - 2 * _MARGIN
        inner_h = h - 2 * _MARGIN - 24  # leave room for title
        scale = min(inner_w / gw, inner_h / gh)

        # Logical→screen (Y-down → screen Y-down, direct).
        ox = (w - gw * scale) / 2.0 - xmin * scale
        oy_top = _MARGIN + 24
        oy = (oy_top + (inner_h - gh * scale) / 2.0) - ymin * scale

        def sx(x: float) -> float:
            return ox + x * scale

        def sy(y: float) -> float:
            return oy + y * scale

        # Bbox drawn before content, lightly, for orientation.
        self._draw_bbox(p, sx, sy, xmin, ymin, xmax, ymax)

        # Content.
        stroke_pen = self._stroke_pen()
        fill_brush = QBrush(self._fill())
        p.setBrush(Qt.NoBrush)

        for ln in geom.lines:
            self._draw_line(p, sx(ln.x0), sy(ln.y0), sx(ln.x1), sy(ln.y1),
                            ln.stroke_width, scale, stroke_pen)

        for a in geom.arcs:
            self._draw_arc(p, a, scale, sx, sy, stroke_pen)

        for c in geom.circles:
            self._draw_circle(p, c, scale, sx, sy, stroke_pen, fill_brush)

        for poly in geom.polygons:
            polyf = QPolygonF([QPointF(sx(x), sy(y)) for x, y in poly.points])
            p.setBrush(fill_brush)
            p.setPen(QPen(self._stroke_pen()))
            p.drawPolygon(polyf)
            p.setBrush(Qt.NoBrush)

        for path in geom.paths:
            self._draw_path(p, path, scale, sx, sy, stroke_pen, fill_brush)

        # Scale readout.
        p.setPen(QPen(self._muted()))
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(_TEXT_PAD, h - 18, w - 2 * _TEXT_PAD, 14),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"{gw:.2f} × {gh:.2f} mm · 1 px ≈ {1.0 / scale:.4f} mm")

    # ---- primitive draws --------------------------------------------------

    def _stroke_pen(self) -> QPen:
        pen = QPen(self._fg())
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _draw_line(self, p: QPainter, x0: float, y0: float, x1: float,
                   y1: float, sw: Optional[float], scale: float,
                   base_pen: QPen) -> None:
        wpx = (sw or 0.0) * scale
        pen = QPen(base_pen)
        pen.setWidthF(max(_MIN_PEN_PX, wpx))
        p.setPen(pen)
        p.drawLine(QLineF(x0, y0, x1, y1))

    def _draw_circle(self, p: QPainter, c, scale: float,
                     sx, sy, base_pen: QPen, fill: QBrush) -> None:
        cx, cy = sx(c.cx), sy(c.cy)
        if c.stroke_width is None:
            # filled pad/via: floor the rendered DIAMETER so a real 0.3mm pad
            # stays legible at typical zoom (analogous to _MIN_PEN_PX for
            # strokes), and fill OPAQUE so it reads as a pad rather than a faint
            # translucent smudge.  Geometry is untouched — only the paint.
            r = c.radius * scale
            d = max(_PAD_MIN_PX, 2.0 * r)
            p.setBrush(QBrush(self._pad()))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(cx - d / 2.0, cy - d / 2.0, d, d))
            p.setBrush(Qt.NoBrush)
        else:
            r = c.radius * scale
            rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            pen = QPen(base_pen)
            pen.setWidthF(max(_MIN_PEN_PX, c.stroke_width * scale))
            p.setPen(pen)
            p.drawEllipse(rect)

    def _draw_path(self, p: QPainter, path, scale: float, sx, sy,
                   base_pen: QPen, fill: QBrush) -> None:
        if not path.segments:
            return
        qp = QPainterPath()
        first = path.segments[0]
        qp.moveTo(sx(first[0]), sy(first[1]))
        for x, y in path.segments[1:]:
            qp.lineTo(sx(x), sy(y))
        if path.closed:
            qp.closeSubpath()
        if path.filled and not path.is_stroked():
            p.setBrush(fill)
            p.setPen(Qt.NoPen)
            p.drawPath(qp)
            p.setBrush(Qt.NoBrush)
        else:
            pen = QPen(base_pen)
            pen.setWidthF(max(_MIN_PEN_PX, (path.stroke_width or 0.0) * scale))
            p.setPen(pen)
            p.drawPath(qp)

    def _draw_arc(self, p: QPainter, a, scale: float, sx, sy,
                  base_pen: QPen) -> None:
        # Sample CCW from start_angle to end_angle (degrees) in steps <= ~5°.
        sweep = a.sweep_deg()
        if sweep <= 0:
            return
        n = max(2, int(math.ceil(sweep / 5.0)))
        pts = []
        for i in range(n + 1):
            t = math.radians(a.start_angle + sweep * i / n)
            px = a.cx + a.radius * math.cos(t)
            py = a.cy + a.radius * math.sin(t)
            pts.append(QPointF(sx(px), sy(py)))
        wpx = (a.stroke_width or 0.0) * scale
        pen = QPen(base_pen)
        pen.setWidthF(max(_MIN_PEN_PX, wpx))
        p.setPen(pen)
        for i in range(len(pts) - 1):
            p.drawLine(QLineF(pts[i], pts[i + 1]))

    # ---- decorations -------------------------------------------------------

    def _draw_bbox(self, p: QPainter, sx, sy, xmin: float, ymin: float,
                   xmax: float, ymax: float) -> None:
        pen = QPen(self._bbox())
        pen.setStyle(Qt.DashLine)
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rect = QRectF(QPointF(sx(xmin), sy(ymin)),
                      QPointF(sx(xmax), sy(ymax)))
        p.drawRect(rect)

    # ---- color helpers ----------------------------------------------------

    def _fg(self) -> QColor:
        return self._FG_DARK if self._dark else self._FG

    def _muted(self) -> QColor:
        return QColor("#9aa3af") if not self._dark else QColor("#6b7280")

    def _bbox(self) -> QColor:
        return self._BBOX_DARK if self._dark else self._BBOX

    def _fill(self) -> QColor:
        c = QColor(self._FG_DARK if self._dark else self._FG)
        c.setAlpha(70)
        return c

    def _pad(self) -> QColor:
        # Opaque (no alpha) — pads read clearly against the background and the
        # translucent pour fills.  See _PAD / _PAD_DARK above.
        return QColor(self._PAD_DARK if self._dark else self._PAD)
