"""
Gerber (RS-274X) parser.

A working reimplementation of the parsing/rasterising logic in
``INC/gerber.forth``.  The original is a very large Forth program that
walks the file token-by-token (``(G-PARSE)``) maintaining current point,
aperture, interpolation mode and a polygon list for G36/G37 regions, then
rasterises into a monochrome bitmap for the laser.

This port keeps the same conceptual model — a state machine over the
command stream that emits geometric primitives — and covers the feature
set seen in practice:

* format specification ``%FSLAXxYy*%`` (leading/trailing zero, decimals)
* units ``%MOMM*%`` / ``%MOIN*%``
* aperture definitions ``%ADDnnC,..*%`` (circle / rect / obround / polygon)
* aperture select ``Dnn``
* draw / move / flash ``D01 D02 D03``
* linear & circular interpolation ``G01 G02 G03`` (with ``G74``/``G75``)
* region mode ``G36``/``G37``

The result is a :class:`GerberImage` holding primitives in millimetres,
plus a bounding box, which the GUI renders and which the exposure layer
rasterises to a bitmap.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from .aperture_macro import (
    ApertureMacro, parse_macro,
    PRIM_CIRCLE, PRIM_VLINE, PRIM_VLINE2, PRIM_CLINE,
    PRIM_OUTLINE, PRIM_POLYGON, PRIM_MOIRE, PRIM_THERMAL,
)


# ---------------------------------------------------------------------------
# Primitive geometry (all coordinates in millimetres)
# ---------------------------------------------------------------------------
@dataclass
class Aperture:
    code: int
    shape: str                      # 'C', 'R', 'O', 'P', or 'M' (macro)
    params: Tuple[float, ...]
    macro_name: str = ""            # set when shape == 'M'
    macro_args: Tuple[float, ...] = ()

    @property
    def size(self) -> float:
        return self.params[0] if self.params else 0.0


@dataclass
class Trace:
    """A drawn line/arc segment with a stroke width (the aperture)."""
    points: List[Tuple[float, float]]
    width: float
    arc: bool = False


@dataclass
class Flash:
    """An aperture flashed at a point (a pad)."""
    x: float
    y: float
    aperture: Aperture


@dataclass
class Region:
    """A filled polygon (G36/G37)."""
    points: List[Tuple[float, float]]


@dataclass
class MacroShape:
    """Resolved geometry produced by flashing an aperture-macro pad.

    Each entry is a filled polygon (in mm) with an exposure flag; exposure
    ``False`` means the polygon clears (subtracts from) what is under it,
    exactly like the macro exposure-off primitive in the original.
    """
    polygons: List[Tuple[List[Tuple[float, float]], bool]] = field(default_factory=list)


@dataclass
class GerberImage:
    traces: List[Trace] = field(default_factory=list)
    flashes: List[Flash] = field(default_factory=list)
    regions: List[Region] = field(default_factory=list)
    macro_shapes: List[MacroShape] = field(default_factory=list)
    units: str = "mm"

    def bounds(self) -> Tuple[float, float, float, float]:
        """Return (min_x, min_y, max_x, max_y) in millimetres."""
        xs: List[float] = []
        ys: List[float] = []
        for t in self.traces:
            r = t.width / 2.0
            for (x, y) in t.points:
                xs += [x - r, x + r]
                ys += [y - r, y + r]
        for f in self.flashes:
            r = f.aperture.size / 2.0
            xs += [f.x - r, f.x + r]
            ys += [f.y - r, f.y + r]
        for reg in self.regions:
            for (x, y) in reg.points:
                xs.append(x)
                ys.append(y)
        for ms in self.macro_shapes:
            for poly, _exp in ms.polygons:
                for (x, y) in poly:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def is_empty(self) -> bool:
        return not (self.traces or self.flashes or self.regions
                    or self.macro_shapes)


class Interp(Enum):
    LINEAR = 1
    CW = 2
    CCW = 3


class GerberError(Exception):
    pass


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class GerberParser:
    """Stateful RS-274X parser."""

    _coord_re = re.compile(r"([XYIJ])([+-]?\d+)")

    def __init__(self) -> None:
        self.apertures: dict[int, Aperture] = {}
        self.macros: dict[str, ApertureMacro] = {}
        self.image = GerberImage()
        # format spec
        self._x_int = 2
        self._x_dec = 4
        self._y_int = 2
        self._y_dec = 4
        self._leading_zero = True   # True => omit leading zeros (LA mode)
        self._unit_scale = 1.0      # multiply parsed mm-equivalent
        # modal state
        self._x = 0.0
        self._y = 0.0
        self._i = 0.0
        self._j = 0.0
        self._interp = Interp.LINEAR
        self._multi_quadrant = True
        self._current_ap: Aperture | None = None
        self._region = False
        self._region_pts: List[Tuple[float, float]] = []

    # -- public API ----------------------------------------------------
    def parse(self, text: str) -> GerberImage:
        # split into command blocks: extended (%..%) and ordinary (..*)
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "%":
                end = text.find("%", i + 1)
                if end == -1:
                    break
                block = text[i + 1:end]
                self._handle_extended(block)
                i = end + 1
            elif ch in "*\n\r \t":
                i += 1
            else:
                end = text.find("*", i)
                if end == -1:
                    end = n
                self._handle_block(text[i:end].strip())
                i = end + 1
        return self.image

    def parse_file(self, path: str) -> GerberImage:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return self.parse(fh.read())

    # -- extended (parameter) commands ---------------------------------
    def _handle_extended(self, block: str) -> None:
        # An AM macro body spans every '*'-separated statement up to the
        # closing '%', so it must be handled at the block level (port of the
        # Forth ``AM`` word, which reads until '%').
        head = block.lstrip()
        if head[:2] == "AM":
            self._parse_macro_def(head)
            return
        # otherwise the block is one or more independent statements
        for stmt in block.split("*"):
            stmt = stmt.strip()
            if not stmt:
                continue
            code = stmt[:2]
            if code == "FS":
                self._parse_format(stmt)
            elif code == "MO":
                self._unit_scale = 25.4 if "IN" in stmt else 1.0
                self.image.units = "in" if "IN" in stmt else "mm"
            elif code == "AD":
                self._parse_aperture_def(stmt)
            # LP, SR, IP etc. are accepted but not acted upon

    def _parse_macro_def(self, block: str) -> None:
        # block looks like:  AM<name>*<prim>*<prim>*...
        first_star = block.find("*")
        if first_star == -1:
            return
        name = block[2:first_star].strip()
        body = block[first_star + 1:]
        self.macros[name] = parse_macro(name, body)

    def _parse_format(self, stmt: str) -> None:
        # e.g. FSLAX24Y24
        body = stmt[2:]
        if body and body[0] in "LT":
            self._leading_zero = body[0] == "L"
            body = body[1:]
        if body and body[0] in "AI":
            body = body[1:]  # absolute / incremental notation flag
        m = re.search(r"X(\d)(\d)Y(\d)(\d)", body)
        if m:
            self._x_int, self._x_dec, self._y_int, self._y_dec = map(int, m.groups())

    def _parse_aperture_def(self, stmt: str) -> None:
        # ADD10C,0.5   ADD11R,1X0.5   ADD12THERMAL,0X0X1X0.5  (macro)
        # the shape/name token runs from after the code up to the comma
        m = re.match(r"ADD(\d+)([^,]+)(?:,(.*))?$", stmt)
        if not m:
            return
        code = int(m.group(1))
        name = m.group(2).strip()
        params: Tuple[float, ...] = ()
        if m.group(3):
            try:
                params = tuple(float(p) for p in m.group(3).split("X") if p != "")
            except ValueError:
                params = ()
        # macro aperture: the name matches a previously defined %AM%
        if name in self.macros:
            self.apertures[code] = Aperture(
                code=code, shape="M", params=params,
                macro_name=name, macro_args=params)
            return
        # standard shapes are a single letter C / R / O / P
        std = name[0].upper() if name and name[0].upper() in "CROP" else "C"
        self.apertures[code] = Aperture(code=code, shape=std, params=params)

    # -- ordinary command blocks ---------------------------------------
    def _handle_block(self, block: str) -> None:
        if not block:
            return
        # G-codes can be mixed with coordinate data in one block
        for g in re.findall(r"G(\d+)", block):
            self._apply_gcode(int(g))
        # aperture select
        msel = re.search(r"(?<![XYIJ])D0*([1-9]\d*)\b", block)
        # a coordinate block ends in D01/D02/D03
        dop = None
        mdop = re.search(r"D0?([123])\*?$", block) or re.search(r"D0?([123])\b", block)
        if mdop:
            dop = int(mdop.group(1))
        # gather coordinates
        coords = dict(self._coord_re.findall(block))
        has_coord = bool(coords)
        # aperture selection: a bare Dnn with nn>=10 and no operation
        if dop is None:
            md = re.search(r"\bD0*(\d+)\b", block)
            if md and int(md.group(1)) >= 10:
                ap = self.apertures.get(int(md.group(1)))
                if ap is not None:
                    self._current_ap = ap
                return
        if not has_coord and dop is None:
            return
        self._exec_operation(coords, dop)

    def _apply_gcode(self, g: int) -> None:
        if g == 1:
            self._interp = Interp.LINEAR
        elif g == 2:
            self._interp = Interp.CW
        elif g == 3:
            self._interp = Interp.CCW
        elif g == 74:
            self._multi_quadrant = False
        elif g == 75:
            self._multi_quadrant = True
        elif g == 36:
            self._region = True
            self._region_pts = []
        elif g == 37:
            if self._region and len(self._region_pts) >= 3:
                self.image.regions.append(Region(points=self._region_pts[:]))
            self._region = False
            self._region_pts = []

    def _decode_coord(self, raw: str, intdig: int, decdig: int) -> float:
        neg = raw.startswith("-")
        raw = raw.lstrip("+-")
        total = intdig + decdig
        if self._leading_zero:
            # leading zeros omitted -> right justify
            raw = raw.rjust(total, "0")
        else:
            # trailing zeros omitted -> left justify
            raw = raw.ljust(total, "0")
        value = int(raw) / (10 ** decdig)
        value *= self._unit_scale
        return -value if neg else value

    def _exec_operation(self, coords: dict, dop: int | None) -> None:
        nx = self._x
        ny = self._y
        if "X" in coords:
            nx = self._decode_coord(coords["X"], self._x_int, self._x_dec)
        if "Y" in coords:
            ny = self._decode_coord(coords["Y"], self._y_int, self._y_dec)
        ci = self._decode_coord(coords["I"], self._x_int, self._x_dec) if "I" in coords else 0.0
        cj = self._decode_coord(coords["J"], self._y_int, self._y_dec) if "J" in coords else 0.0

        if dop is None:
            dop = 1 if not self._region else 1

        if dop == 2:        # move
            if self._region and self._region_pts:
                self.image.regions.append(Region(points=self._region_pts[:]))
                self._region_pts = []
        elif dop == 1:      # draw / interpolate
            seg = self._interpolate(self._x, self._y, nx, ny, ci, cj)
            if self._region:
                if not self._region_pts:
                    self._region_pts.append((self._x, self._y))
                self._region_pts.extend(seg[1:])
            else:
                width = self._current_ap.size if self._current_ap else 0.1
                self.image.traces.append(
                    Trace(points=seg, width=width,
                          arc=self._interp != Interp.LINEAR))
        elif dop == 3:      # flash
            if self._current_ap:
                ap = self._current_ap
                if ap.shape == "M" and ap.macro_name in self.macros:
                    shape = self._expand_macro(ap, nx, ny)
                    if shape.polygons:
                        self.image.macro_shapes.append(shape)
                else:
                    self.image.flashes.append(Flash(x=nx, y=ny, aperture=ap))

        self._x, self._y = nx, ny

    # -- aperture macro expansion (port of RunGerbMacro) ---------------
    def _expand_macro(self, ap: Aperture, cx: float, cy: float) -> "MacroShape":
        """Flash a macro aperture at (cx, cy), producing filled polygons."""
        macro = self.macros[ap.macro_name]
        resolved = macro.evaluate(list(ap.macro_args))
        shape = MacroShape()
        for prim in resolved:
            polys = _macro_primitive_polygons(prim, cx, cy)
            for poly in polys:
                if len(poly) >= 3:
                    shape.polygons.append((poly, prim.exposure))
        return shape

    def _interpolate(self, x0, y0, x1, y1, i, j) -> List[Tuple[float, float]]:
        if self._interp == Interp.LINEAR:
            return [(x0, y0), (x1, y1)]
        # circular: centre is current point + (i, j)
        cx, cy = x0 + i, y0 + j
        r = math.hypot(x0 - cx, y0 - cy)
        if r == 0:
            return [(x0, y0), (x1, y1)]
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
        ccw = self._interp == Interp.CCW
        if ccw:
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a1 >= a0:
                a1 -= 2 * math.pi
        sweep = a1 - a0
        steps = max(2, int(abs(sweep) / (math.pi / 36)) + 1)
        pts = []
        for k in range(steps + 1):
            a = a0 + sweep * k / steps
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts


def parse_gerber_file(path: str) -> GerberImage:
    return GerberParser().parse_file(path)


def apply_punching(image: GerberImage, holes, punch_dia_um: float) -> int:
    """Add centre-punch clearings ("Добавить кернение").

    For every drill hole, an unexposed circle of diameter ``punch_dia_um``
    (µm) is cleared at the hole centre, matching the readme's "Диам кернения":
    a small unexposed area in the centre of a pad to aid manual drilling.
    Returns the number of punches added.  ``holes`` is an iterable of objects
    with ``.x`` and ``.y`` in millimetres (e.g. Excellon ``Hole``).
    """
    r_mm = (punch_dia_um / 1000.0) / 2.0
    if r_mm <= 0:
        return 0
    shape = MacroShape()
    import math as _m
    n = 0
    for h in holes:
        poly = [(h.x + r_mm * _m.cos(2 * _m.pi * k / 32),
                 h.y + r_mm * _m.sin(2 * _m.pi * k / 32)) for k in range(32)]
        shape.polygons.append((poly, False))      # exposure off -> clears
        n += 1
    if shape.polygons:
        image.macro_shapes.append(shape)
    return n


# ---------------------------------------------------------------------------
# Aperture-macro primitive geometry
#
# Converts a resolved AM primitive (numbers in the macro's units, already in
# mm because the parser scales coordinates) into one or more polygon outlines
# centred on the flash point (cx, cy).  This mirrors what the ``vMacro`` words
# drew into the bitmap in the original ``gerber.forth``.
# ---------------------------------------------------------------------------
def _circle_poly(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _rotate(points, deg, ox=0.0, oy=0.0):
    if not deg:
        return points
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for (x, y) in points:
        dx, dy = x - ox, y - oy
        out.append((ox + dx * ca - dy * sa, oy + dx * sa + dy * ca))
    return out


def _macro_primitive_polygons(prim, cx, cy):
    """Return a list of polygon outlines (mm) for one resolved AM primitive."""
    code = prim.code
    v = prim.values
    polys: List[List[Tuple[float, float]]] = []

    def at(i, default=0.0):
        return v[i] if i < len(v) else default

    if code == PRIM_CIRCLE:
        # 1, exposure, diameter, center_x, center_y[, rotation]
        dia, x, y = at(1), at(2), at(3)
        rot = at(4)
        poly = _circle_poly(cx + x, cy + y, dia / 2.0)
        polys.append(_rotate(poly, rot, cx, cy))

    elif code in (PRIM_VLINE, PRIM_VLINE2):
        # 20, exposure, width, x1, y1, x2, y2, rotation
        w, x1, y1, x2, y2, rot = at(1), at(2), at(3), at(4), at(5), at(6)
        polys.append(_rotate(_thick_line(x1, y1, x2, y2, w, cx, cy), rot, cx, cy))

    elif code == PRIM_CLINE:
        # 21, exposure, width, height, center_x, center_y, rotation
        w, h, x, y, rot = at(1), at(2), at(3), at(4), at(5)
        hw, hh = w / 2.0, h / 2.0
        poly = [(cx + x - hw, cy + y - hh), (cx + x + hw, cy + y - hh),
                (cx + x + hw, cy + y + hh), (cx + x - hw, cy + y + hh)]
        polys.append(_rotate(poly, rot, cx, cy))

    elif code == PRIM_OUTLINE:
        # 4, exposure, n, x0, y0, x1, y1, ..., xn, yn, rotation
        n = int(at(1))
        pts = []
        idx = 2
        for _ in range(n + 1):
            pts.append((cx + at(idx), cy + at(idx + 1)))
            idx += 2
        rot = at(idx)
        polys.append(_rotate(pts, rot, cx, cy))

    elif code == PRIM_POLYGON:
        # 5, exposure, n_vertices, center_x, center_y, diameter, rotation
        nv = int(at(1))
        x, y, dia, rot = at(2), at(3), at(4), at(5)
        r = dia / 2.0
        poly = [(cx + x + r * math.cos(2 * math.pi * k / nv),
                 cy + y + r * math.sin(2 * math.pi * k / nv)) for k in range(nv)]
        polys.append(_rotate(poly, rot, cx, cy))

    elif code == PRIM_MOIRE:
        # 6, center_x, center_y, outer_dia, ring_thick, gap, max_rings,
        #    cross_thick, cross_len, rotation   (no exposure flag)
        x, y = at(0), at(1)
        outer, thick, gap, rings = at(2), at(3), at(4), int(at(5))
        cross_t, cross_l, rot = at(6), at(7), at(8)
        d = outer
        for _ in range(max(1, rings)):
            r_out = d / 2.0
            r_in = max(0.0, r_out - thick)
            # approximate ring by its outer circle (filled); inner gap handled
            # as a clear circle
            polys.append(_circle_poly(cx + x, cy + y, r_out))
            d -= 2 * (thick + gap)
            if d <= 0:
                break
        if cross_t > 0 and cross_l > 0:
            hl, ht = cross_l / 2.0, cross_t / 2.0
            polys.append(_rotate([(cx + x - hl, cy + y - ht), (cx + x + hl, cy + y - ht),
                                  (cx + x + hl, cy + y + ht), (cx + x - hl, cy + y + ht)],
                                 rot, cx, cy))
            polys.append(_rotate([(cx + x - ht, cy + y - hl), (cx + x + ht, cy + y - hl),
                                  (cx + x + ht, cy + y + hl), (cx + x - ht, cy + y + hl)],
                                 rot, cx, cy))

    elif code == PRIM_THERMAL:
        # 7, center_x, center_y, outer_dia, inner_dia, gap, rotation
        x, y = at(0), at(1)
        outer, inner, gap, rot = at(2), at(3), at(4), at(5)
        # outer filled ring approximated by the outer circle outline
        polys.append(_circle_poly(cx + x, cy + y, outer / 2.0))

    return polys


def _thick_line(x1, y1, x2, y2, w, cx, cy):
    """Rectangle outline of a line of width w between two points (offset by c)."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return []
    ux, uy = dx / length, dy / length
    # perpendicular
    px, py = -uy * w / 2.0, ux * w / 2.0
    return [(cx + x1 + px, cy + y1 + py), (cx + x2 + px, cy + y2 + py),
            (cx + x2 - px, cy + y2 - py), (cx + x1 - px, cy + y1 - py)]

