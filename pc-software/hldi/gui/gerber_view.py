"""
Gerber viewer widget.

Replaces the original Win32 bitmap-DC viewer (the painting code in
``main.forth`` / ``gerber.forth``) with a zoomable, pannable QWidget that
draws the parsed primitives directly.  Also overlays the current machine
position as a crosshair.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath
from PySide6.QtWidgets import QWidget

from ..core.gerber import GerberImage


class GerberView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.image: GerberImage | None = None
        self._scale = 4.0          # pixels per mm
        self._offset = QPointF(40, 40)
        self._panning = False
        self._last = QPointF()
        self.machine_x_um = 0.0
        self.machine_y_um = 0.0
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setBackgroundRole()  # placeholder; styled via paint
        self.setAutoFillBackground(False)

    def setBackgroundRole(self, *_):  # override to no-op; we paint our own bg
        pass

    # -- data ----------------------------------------------------------
    def set_image(self, image: GerberImage | None) -> None:
        self.image = image
        if image and not image.is_empty:
            self.fit()
        self.update()

    def set_machine_pos(self, x_um: float, y_um: float) -> None:
        self.machine_x_um = x_um
        self.machine_y_um = y_um
        self.update()

    def fit(self) -> None:
        if not self.image or self.image.is_empty:
            return
        min_x, min_y, max_x, max_y = self.image.bounds()
        w = max(0.1, max_x - min_x)
        h = max(0.1, max_y - min_y)
        sx = (self.width() - 80) / w
        sy = (self.height() - 80) / h
        self._scale = max(0.5, min(sx, sy))
        # centre
        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        self._offset = QPointF(
            self.width() / 2 - cx * self._scale,
            self.height() / 2 + cy * self._scale,
        )
        self.update()

    # -- coordinate mapping (mm -> screen); Y is flipped --------------
    def _to_screen(self, x_mm: float, y_mm: float) -> QPointF:
        return QPointF(self._offset.x() + x_mm * self._scale,
                       self._offset.y() - y_mm * self._scale)

    # -- painting ------------------------------------------------------
    def paintEvent(self, _evt) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#0d1117"))
        self._draw_grid(p)

        if self.image:
            copper = QColor("#e9b949")          # warm copper/gold
            bg = QColor("#0d1117")
            p.setBrush(QBrush(copper))
            p.setPen(Qt.NoPen)

            # macro shapes (composite pads): exposure-on adds copper,
            # exposure-off clears back to background
            for ms in self.image.macro_shapes:
                for poly, exposure in ms.polygons:
                    path = QPainterPath()
                    path.moveTo(self._to_screen(*poly[0]))
                    for pt in poly[1:]:
                        path.lineTo(self._to_screen(*pt))
                    path.closeSubpath()
                    p.setBrush(QBrush(copper if exposure else bg))
                    p.drawPath(path)
            p.setBrush(QBrush(copper))

            # regions (filled)
            for reg in self.image.regions:
                path = QPainterPath()
                if reg.points:
                    path.moveTo(self._to_screen(*reg.points[0]))
                    for pt in reg.points[1:]:
                        path.lineTo(self._to_screen(*pt))
                    path.closeSubpath()
                p.drawPath(path)

            # traces (stroked)
            for t in self.image.traces:
                pen = QPen(copper)
                pen.setWidthF(max(1.0, t.width * self._scale))
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                for i in range(len(t.points) - 1):
                    p.drawLine(self._to_screen(*t.points[i]),
                               self._to_screen(*t.points[i + 1]))

            # flashes (pads)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(copper))
            for f in self.image.flashes:
                c = self._to_screen(f.x, f.y)
                ap = f.aperture
                if ap.shape == "R":
                    w = (ap.params[0] if ap.params else 0) * self._scale
                    h = ((ap.params[1] if len(ap.params) > 1 else ap.params[0])
                         if ap.params else 0) * self._scale
                    p.drawRect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h))
                else:
                    r = ap.size * self._scale / 2
                    p.drawEllipse(c, r, r)

        self._draw_crosshair(p)
        self._draw_hud(p)
        p.end()

    def _draw_grid(self, p: QPainter) -> None:
        pen = QPen(QColor("#1b2430"))
        pen.setWidth(1)
        p.setPen(pen)
        step = 10 * self._scale          # 10 mm grid
        if step < 8:
            step *= 5
        x = self._offset.x() % step
        while x < self.width():
            p.drawLine(int(x), 0, int(x), self.height())
            x += step
        y = self._offset.y() % step
        while y < self.height():
            p.drawLine(0, int(y), self.width(), int(y))
            y += step

    def _draw_crosshair(self, p: QPainter) -> None:
        c = self._to_screen(self.machine_x_um / 1000.0, self.machine_y_um / 1000.0)
        pen = QPen(QColor("#ff5c57"))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(c.x()) - 12, int(c.y()), int(c.x()) + 12, int(c.y()))
        p.drawLine(int(c.x()), int(c.y()) - 12, int(c.x()), int(c.y()) + 12)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 6, 6)

    def _draw_hud(self, p: QPainter) -> None:
        p.setPen(QColor("#6b7785"))
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(10, self.height() - 12,
                   f"scale {self._scale:.1f} px/mm    "
                   f"head  X {self.machine_x_um/1000:.2f}  Y {self.machine_y_um/1000:.2f} mm")

    # -- interaction ---------------------------------------------------
    def wheelEvent(self, evt) -> None:
        factor = 1.15 if evt.angleDelta().y() > 0 else 1 / 1.15
        # zoom about cursor
        cur = evt.position()
        before_x = (cur.x() - self._offset.x()) / self._scale
        before_y = (self._offset.y() - cur.y()) / self._scale
        self._scale = max(0.2, min(self._scale * factor, 200.0))
        self._offset = QPointF(cur.x() - before_x * self._scale,
                               cur.y() + before_y * self._scale)
        self.update()

    def mousePressEvent(self, evt) -> None:
        if evt.button() == Qt.LeftButton:
            self._panning = True
            self._last = evt.position()

    def mouseMoveEvent(self, evt) -> None:
        if self._panning:
            delta = evt.position() - self._last
            self._offset += delta
            self._last = evt.position()
            self.update()

    def mouseReleaseEvent(self, evt) -> None:
        self._panning = False
