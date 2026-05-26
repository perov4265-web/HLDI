"""
Excellon drill-file parser.

Supports the "Добавить кернение" feature from the readme: an Excellon NC drill
file is loaded and, at each hole location, a small unexposed circle (the
``punchdia`` "Диам кернения") is cleared in the copper so the spot can be
hand-drilled.  This module just extracts the hole coordinates and diameters;
the clearing is applied to the :class:`~hldi.core.gerber.GerberImage` by
:func:`apply_punching`.

The parser covers the common Excellon subset: ``INCH``/``METRIC`` units,
tool definitions (``T01C0.8``), tool selection (``T01``) and coordinate hits
(``X..Y..``), with the same leading/trailing-zero handling as Gerber.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class Hole:
    x: float            # mm
    y: float            # mm
    diameter: float     # mm


@dataclass
class DrillData:
    holes: List[Hole] = field(default_factory=list)
    units: str = "mm"


class ExcellonParser:
    def __init__(self) -> None:
        self.tools: Dict[int, float] = {}      # tool number -> diameter (mm)
        self.data = DrillData()
        self._unit_scale = 1.0                 # to mm
        self._cur_dia = 0.0
        self._leading = True                   # omit leading zeros
        self._decimals = 4
        self._in_header = True

    def parse(self, text: str) -> DrillData:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            self._line(line)
        return self.data

    def parse_file(self, path: str) -> DrillData:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return self.parse(fh.read())

    def _line(self, line: str) -> None:
        up = line.upper()
        if up in ("M48", "%"):
            self._in_header = up == "M48"
            return
        if up.startswith("METRIC"):
            self._unit_scale = 1.0
            self.data.units = "mm"
            self._decimals = 3
            self._leading = "TZ" not in up
            return
        if up.startswith("INCH"):
            self._unit_scale = 25.4
            self.data.units = "in"
            self._decimals = 4
            self._leading = "TZ" not in up
            return
        # tool definition:  T01C0.800
        m = re.match(r"T(\d+)C([\d.]+)", up)
        if m:
            self.tools[int(m.group(1))] = float(m.group(2)) * self._unit_scale
            return
        # tool selection (no C):  T01
        m = re.match(r"T(\d+)$", up)
        if m:
            self._cur_dia = self.tools.get(int(m.group(1)), 0.0)
            return
        if up in ("M30", "M00"):
            return
        # coordinate hit
        if "X" in up or "Y" in up:
            self._hit(up)

    def _decode(self, raw: str) -> float:
        neg = raw.startswith("-")
        raw = raw.lstrip("+-")
        if "." in raw:
            val = float(raw)
        else:
            total = 2 + self._decimals
            raw = raw.rjust(total, "0") if self._leading else raw.ljust(total, "0")
            val = int(raw) / (10 ** self._decimals)
        val *= self._unit_scale
        return -val if neg else val

    def _hit(self, line: str) -> None:
        mx = re.search(r"X([+-]?[\d.]+)", line)
        my = re.search(r"Y([+-]?[\d.]+)", line)
        if not (mx or my):
            return
        x = self._decode(mx.group(1)) if mx else 0.0
        y = self._decode(my.group(1)) if my else 0.0
        self.data.holes.append(Hole(x=x, y=y, diameter=self._cur_dia))


def parse_excellon_file(path: str) -> DrillData:
    return ExcellonParser().parse_file(path)
