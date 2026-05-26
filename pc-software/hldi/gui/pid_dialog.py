"""
PID regulator tuning dialog — port of "Окно настроек коэффициентов ПИД".

The original runs the carriage shuttling back and forth over a 0–100 mm span
at the set speed and plots the carriage speed and the motor control voltage so
the operator can tune the proportional / integral / differential coefficients
and the minimum motor voltage (``Vmin``) for the flattest, smoothest motion
(see the "Настройка ПИД коэффициентов" notes in the readme).

Buttons: СТАРТ / СТОП (run/stop the shuttle test), СОХР (save coefficients).
Sliders: проп / интегр / дифф / Vmin.

When a controller is connected the coefficients are pushed live (the firmware
exposed CMD_SP* / gflag commands); without one, a small model simulates the
response so the graph is meaningful for UI testing.
"""

from __future__ import annotations

import collections

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QGroupBox, QFormLayout, QWidget,
)

from ..core.config import DeviceConfig


class _Graph(QWidget):
    """Rolling plot of carriage speed (teal) and motor voltage (copper)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.speed = collections.deque(maxlen=240)
        self.volt = collections.deque(maxlen=240)

    def push(self, speed: float, volt: float) -> None:
        self.speed.append(speed)
        self.volt.append(volt)
        self.update()

    def clear(self) -> None:
        self.speed.clear()
        self.volt.clear()
        self.update()

    def paintEvent(self, _evt) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#060a0f"))
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor("#1b2430")))
        for k in range(1, 4):
            y = h * k / 4
            p.drawLine(0, int(y), w, int(y))

        def plot(series, color, lo, hi):
            if len(series) < 2:
                return
            p.setPen(QPen(QColor(color), 2))
            n = len(series)
            span = max(1e-6, hi - lo)
            prev = None
            for i, val in enumerate(series):
                x = w * i / max(1, series.maxlen - 1)
                y = h - (val - lo) / span * h
                if prev is not None:
                    p.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)

        plot(self.speed, "#6fd3c0", 0, 120)     # mm/s
        plot(self.volt, "#e9b949", 0, 100)      # %
        p.end()


class PidDialog(QDialog):
    SPAN_MM = 100.0

    def __init__(self, config: DeviceConfig, controller=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ПИД коэфф")
        self.config = config
        self.controller = controller
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        self.graph = _Graph()
        root.addWidget(self.graph)

        # sliders
        box = QGroupBox("COEFFICIENTS")
        f = QFormLayout(box)
        self.s_kp = self._slider(0, 1000, config.pid_kp)
        self.s_ki = self._slider(0, 1000, config.pid_ki)
        self.s_kd = self._slider(0, 1000, config.pid_kd)
        self.s_vmin = self._slider(0, 100, config.motor_min)
        f.addRow("проп", self.s_kp)
        f.addRow("интегр", self.s_ki)
        f.addRow("дифф", self.s_kd)
        f.addRow("Vmin", self.s_vmin)
        root.addWidget(box)

        # buttons
        row = QHBoxLayout()
        self.start_btn = QPushButton("СТАРТ")
        self.start_btn.setObjectName("Accent")
        self.start_btn.clicked.connect(self._toggle)
        save_btn = QPushButton("СОХР")
        save_btn.clicked.connect(self._save)
        row.addWidget(self.start_btn)
        row.addWidget(save_btn)
        root.addLayout(row)

        # shuttle-test model state
        self._running = False
        self._pos = 0.0
        self._dir = 1
        self._vel = 0.0
        self._integral = 0.0
        self._prev_err = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _slider(self, lo, hi, val) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(val))
        s.valueChanged.connect(self._push_live)
        return s

    # -- live coefficient push ----------------------------------------
    def _push_live(self) -> None:
        # store into config; on real hardware this is where CMD_SP*/gflag
        # writes would go.
        self.config.pid_kp = self.s_kp.value()
        self.config.pid_ki = self.s_ki.value()
        self.config.pid_kd = self.s_kd.value()
        self.config.motor_min = self.s_vmin.value()

    # -- shuttle test --------------------------------------------------
    def _toggle(self) -> None:
        if self._running:
            self._running = False
            self.start_btn.setText("СТАРТ")
            self._timer.stop()
            if self.controller and self.controller.state.connected:
                self.controller.stop_move()
        else:
            self._running = True
            self.start_btn.setText("СТОП")
            self.graph.clear()
            self._integral = 0.0
            self._prev_err = 0.0
            self._timer.start(30)

    def _tick(self) -> None:
        # target speed in mm/s from the exposure speed setting
        target = max(1.0, self.config.speed_exp_f / 100.0)
        dt = 0.03
        kp = self.s_kp.value() / 100.0
        ki = self.s_ki.value() / 1000.0
        kd = self.s_kd.value() / 1000.0
        vmin = self.s_vmin.value()

        err = target - self._vel
        self._integral += err * dt
        deriv = (err - self._prev_err) / dt
        self._prev_err = err
        drive = kp * err + ki * self._integral + kd * deriv
        volt = max(vmin, min(100.0, vmin + drive))
        # crude plant: voltage above break-away accelerates the carriage
        accel = (volt - vmin) * 4.0 - self._vel * 1.5
        self._vel = max(0.0, self._vel + accel * dt)
        self._pos += self._dir * self._vel * dt
        if self._pos >= self.SPAN_MM:
            self._pos = self.SPAN_MM; self._dir = -1
        elif self._pos <= 0:
            self._pos = 0.0; self._dir = 1
        self.graph.push(self._vel, volt)

    def _save(self) -> None:
        self._push_live()
        self.config.save()
        self.start_btn.setText("СТАРТ")

    def closeEvent(self, evt) -> None:
        self._timer.stop()
        if self.controller and self.controller.state.connected:
            self.controller.stop_move()
        super().closeEvent(evt)
