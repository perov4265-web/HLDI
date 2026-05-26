"""
Exposure engine.

Port of the exposure logic from ``INC/texposer.forth`` (the ``RunExposerOne``
shuttle loop) and the rasterising done in ``gerber.forth``.

The job is split in two stages, exactly as the original:

1. **Raster** the parsed :class:`~hldi.core.gerber.GerberImage` into a 1-bit
   bitmap at the configured resolution (``PixToPos`` / ``PosToPix`` set the
   pixel pitch).  Each raster row becomes one exposure line.

2. **Expose** by shuttling the laser caret across each row: for every line,
   repeat ``RepeatCNT`` times -> laser on, traverse forward at ``SpeedExpF``,
   laser off, return at ``SpeedExpB`` -> then advance the table in Y by one
   pixel pitch.  The loop is abortable (``fBrakeExp``).

Because the actual per-pixel laser modulation happens in firmware (the
``LaserMem`` / play-from-memory path), this engine drives the controller at
the line level and reports progress, which is what a host-side prototype
needs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..core.config import DeviceConfig
from ..core.gerber import GerberImage
from ..hardware.controller import Controller


@dataclass
class RasterBitmap:
    width: int                 # pixels
    height: int                # pixels (= number of scan lines)
    pitch_um: float            # micron per pixel
    origin_x_um: float
    origin_y_um: float
    rows: list                 # list[bytearray]; 1 byte per pixel (0/1)

    def row_has_data(self, y: int) -> bool:
        return any(self.rows[y])


def rasterize(image: GerberImage, dpi: int = 1000,
              negative: bool = False, mirror_x: bool = False,
              mirror_y: bool = False) -> RasterBitmap:
    """Rasterise a Gerber image to a 1-bit bitmap.

    ``dpi`` here is dots per inch of the laser; pitch = 25400 / dpi microns.
    The transform flags reproduce the main-window image buttons:
    ``negative`` ("негат") inverts the bitmap, ``mirror_x`` / ``mirror_y``
    ("X зерк" / "Y зерк") flip it about the respective axis.
    """
    pitch_um = 25400.0 / dpi
    min_x, min_y, max_x, max_y = image.bounds()
    # bounds are in mm -> microns
    min_x *= 1000.0
    min_y *= 1000.0
    max_x *= 1000.0
    max_y *= 1000.0
    width = max(1, int((max_x - min_x) / pitch_um) + 1)
    height = max(1, int((max_y - min_y) / pitch_um) + 1)
    rows = [bytearray(width) for _ in range(height)]

    def px(xu, yu):
        return (int((xu - min_x) / pitch_um), int((yu - min_y) / pitch_um))

    def set_disc(cx, cy, r_um):
        r = max(0, int(r_um / pitch_um))
        gx, gy = px(cx, cy)
        for dy in range(-r, r + 1):
            yy = gy + dy
            if 0 <= yy < height:
                row = rows[yy]
                for dx in range(-r, r + 1):
                    if dx * dx + dy * dy <= r * r:
                        xx = gx + dx
                        if 0 <= xx < width:
                            row[xx] = 1

    def set_rect(cx, cy, w_um, h_um):
        gx, gy = px(cx, cy)
        hw = max(0, int(w_um / pitch_um / 2))
        hh = max(0, int(h_um / pitch_um / 2))
        for yy in range(gy - hh, gy + hh + 1):
            if 0 <= yy < height:
                row = rows[yy]
                for xx in range(gx - hw, gx + hw + 1):
                    if 0 <= xx < width:
                        row[xx] = 1

    def draw_line(p0, p1, w_um):
        x0u, y0u = p0[0] * 1000.0, p0[1] * 1000.0
        x1u, y1u = p1[0] * 1000.0, p1[1] * 1000.0
        dist = ((x1u - x0u) ** 2 + (y1u - y0u) ** 2) ** 0.5
        steps = max(1, int(dist / pitch_um))
        for k in range(steps + 1):
            t = k / steps
            set_disc(x0u + (x1u - x0u) * t, y0u + (y1u - y0u) * t, w_um / 2.0)

    # flashes (pads)
    for f in image.flashes:
        cx, cy = f.x * 1000.0, f.y * 1000.0
        ap = f.aperture
        if ap.shape == "R":
            w = (ap.params[0] if ap.params else 0) * 1000.0
            h = (ap.params[1] if len(ap.params) > 1 else ap.params[0] if ap.params else 0) * 1000.0
            set_rect(cx, cy, w, h)
        else:
            set_disc(cx, cy, ap.size * 1000.0 / 2.0)

    # traces
    for t in image.traces:
        w_um = t.width * 1000.0
        for i in range(len(t.points) - 1):
            draw_line(t.points[i], t.points[i + 1], w_um)

    # regions (filled polygons) - scan-line fill
    for reg in image.regions:
        _fill_polygon(reg.points, rows, width, height, min_x, min_y, pitch_um)

    # macro shapes (composite pads) - exposure on sets pixels, off clears them
    for ms in image.macro_shapes:
        for poly, exposure in ms.polygons:
            _fill_polygon(poly, rows, width, height, min_x, min_y, pitch_um,
                          value=1 if exposure else 0)

    # image transforms (main-window buttons)
    if mirror_x:
        for r in rows:
            r.reverse()
    if mirror_y:
        rows.reverse()
    if negative:
        for r in rows:
            for i in range(len(r)):
                r[i] = 0 if r[i] else 1

    return RasterBitmap(width, height, pitch_um, min_x, min_y, rows)


def _fill_polygon(points, rows, width, height, min_x, min_y, pitch_um, value=1):
    pts = [((x * 1000.0 - min_x) / pitch_um, (y * 1000.0 - min_y) / pitch_um)
           for (x, y) in points]
    if len(pts) < 3:
        return
    ys = [p[1] for p in pts]
    y0 = max(0, int(min(ys)))
    y1 = min(height - 1, int(max(ys)))
    n = len(pts)
    for y in range(y0, y1 + 1):
        yc = y + 0.5
        xs = []
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            if (ay <= yc < by) or (by <= yc < ay):
                xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
        xs.sort()
        row = rows[y]
        for k in range(0, len(xs) - 1, 2):
            xa = max(0, int(xs[k]))
            xb = min(width - 1, int(xs[k + 1]))
            for xx in range(xa, xb + 1):
                row[xx] = value


class ExposureJob:
    """Runs an exposure on a background thread, abortable via :meth:`abort`."""

    def __init__(self, controller: Controller, config: DeviceConfig,
                 bitmap: RasterBitmap) -> None:
        self.controller = controller
        self.config = config
        self.bitmap = bitmap
        self._brake = threading.Event()        # fBrakeExp
        self._thread: Optional[threading.Thread] = None

    def abort(self) -> None:
        self._brake.set()

    def run(self, on_progress: Callable[[int, int], None] | None = None,
            on_done: Callable[[bool], None] | None = None) -> None:
        self._brake.clear()
        self._thread = threading.Thread(
            target=self._worker, args=(on_progress, on_done), daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # -- the shuttle loop (RunExposerOne / RunExposerBoth) -------------
    def _worker(self, on_progress, on_done):
        bmp = self.bitmap
        cfg = self.config
        ctl = self.controller
        repeat = max(1, cfg.repeat_cnt)
        completed = True

        # laser level resolved from the active "мс/К*" mode and limited by
        # the power limit (PwrLimit, in /10 % units).
        level = int(round(cfg.effective_power_fraction() * cfg.pwr_limit))
        settle_s = cfg.brake_time / 1000.0          # "Пауза усп"

        x_start = bmp.origin_x_um
        x_end = bmp.origin_x_um + bmp.width * bmp.pitch_um

        for line in range(bmp.height):
            if self._brake.is_set():
                completed = False
                break
            y_um = bmp.origin_y_um + line * bmp.pitch_um
            ctl.move_to_y(y_um)
            if settle_s:
                time.sleep(settle_s)                # let the table settle

            if bmp.row_has_data(line):
                for _ in range(repeat):
                    if self._brake.is_set():
                        completed = False
                        break
                    # forward pass — laser on
                    ctl.set_speed_x(cfg.speed_exp_f)
                    ctl.laser_power(level)
                    ctl.move_to_x(x_end)
                    if cfg.bidirectional:
                        # return pass also exposes (двунаправленная печать)
                        ctl.laser_power(level)
                        ctl.set_speed_x(cfg.speed_exp_f)
                        ctl.move_to_x(x_start)
                    else:
                        # one-direction print: laser off on the fast return
                        ctl.laser_off()
                        ctl.set_speed_x(cfg.speed_exp_b)
                        ctl.move_to_x(x_start)
                ctl.laser_off()
            if on_progress:
                on_progress(line + 1, bmp.height)

        ctl.laser_off()
        ctl.move_to_xy(x_start, bmp.origin_y_um)
        if on_done:
            on_done(completed)
