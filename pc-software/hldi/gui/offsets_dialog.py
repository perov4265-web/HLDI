"""
Offsets dialog — port of "Окно настроек смещений".

Sets the camera↔laser optical-axis offset (``CamOffX``/``CamOffY``) and
provides the alignment buttons described in the readme:

* "Сброс"  - zero the table/carriage coordinates.
* "<-Уст"  - set coordinates to the offset for the *left* reference hole.
* "Уст->"  - set coordinates to the offset for the *right* reference hole.
* "Устан"  - set coordinates to the offset values.
* "Точка"  - fire the laser for the calibration dwell (default 500 ms).
* "Смещ"   - copy the current table/carriage coordinates into the offset
             fields (records the camera offset).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QSpinBox, QPushButton,
    QLabel, QGroupBox,
)

from ..core.config import DeviceConfig


class OffsetsDialog(QDialog):
    PROBE_MS = 500

    def __init__(self, config: DeviceConfig, controller=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Смещения")
        self.config = config
        self.controller = controller
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)

        box = QGroupBox("CAMERA ↔ LASER OFFSET (µm)")
        f = QFormLayout(box)
        self.off_x = QSpinBox(); self.off_x.setRange(-1_000_000, 1_000_000)
        self.off_y = QSpinBox(); self.off_y.setRange(-1_000_000, 1_000_000)
        self.off_x.setValue(config.cam_off_x)
        self.off_y.setValue(config.cam_off_y)
        f.addRow("X смещ", self.off_x)
        f.addRow("Y смещ", self.off_y)
        root.addWidget(box)

        # alignment buttons
        grid = QHBoxLayout()
        for label, slot in (("Сброс", self._reset),
                            ("<-Уст", self._set_left),
                            ("Уст->", self._set_right),
                            ("Устан", self._set_offset)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            grid.addWidget(b)
        root.addLayout(grid)

        grid2 = QHBoxLayout()
        b_point = QPushButton("Точка")
        b_point.clicked.connect(self._probe)
        b_grab = QPushButton("Смещ")
        b_grab.clicked.connect(self._grab_current)
        grid2.addWidget(b_point)
        grid2.addWidget(b_grab)
        root.addLayout(grid2)

        self.status = QLabel("")
        self.status.setObjectName("Caption")
        root.addWidget(self.status)

        save = QPushButton("Save")
        save.setObjectName("Accent")
        save.clicked.connect(self._save)
        root.addWidget(save)

    # -- helpers -------------------------------------------------------
    def _connected(self) -> bool:
        return bool(self.controller and self.controller.state.connected)

    def _reset(self) -> None:
        if self._connected():
            self.controller.move_to_xy(0, 0)
        self.status.setText("Coordinates reset to 0,0.")

    def _set_left(self) -> None:
        # left reference hole: align using the camera offset
        x, y = self.off_x.value(), self.off_y.value()
        if self._connected():
            self.controller.move_to_xy(-x, -y)
        self.status.setText("Aligned to left reference hole.")

    def _set_right(self) -> None:
        x, y = self.off_x.value(), self.off_y.value()
        rx = self.config.max_width_pcb - x
        if self._connected():
            self.controller.move_to_xy(rx, -y)
        self.status.setText("Aligned to right reference hole.")

    def _set_offset(self) -> None:
        if self._connected():
            self.controller.move_to_xy(self.off_x.value(), self.off_y.value())
        self.status.setText("Moved to offset position.")

    def _probe(self) -> None:
        # fire the laser for the calibration dwell, then off
        if self._connected():
            import threading
            self.controller.laser_power(self.config.pwr_limit)
            threading.Timer(self.PROBE_MS / 1000.0,
                            self.controller.laser_off).start()
        self.status.setText(f"Laser probe pulse {self.PROBE_MS} ms.")

    def _grab_current(self) -> None:
        if self._connected():
            st = self.controller.state
            self.off_x.setValue(int(st.x_um))
            self.off_y.setValue(int(st.y_um))
            self.status.setText("Captured current coordinates as offset.")

    def _save(self) -> None:
        self.config.cam_off_x = self.off_x.value()
        self.config.cam_off_y = self.off_y.value()
        self.config.save()
        self.accept()
