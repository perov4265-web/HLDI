"""
Device configuration dialog.

Port of ``INC/Option.forth`` (the "Конфигурация устройства" dialog).  Exposes
the :class:`~hldi.core.config.DeviceConfig` fields grouped by function and
writes them back on accept.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QSpinBox, QComboBox, QDialogButtonBox, QWidget, QGridLayout, QLabel,
)

from ..core.config import DeviceConfig


def _spin(lo, hi, val, suffix="") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    if suffix:
        s.setSuffix(suffix)
    s.setMaximumWidth(160)
    return s


class SettingsDialog(QDialog):
    BAUDS = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]

    def __init__(self, config: DeviceConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device configuration")
        self.config = config
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)

        grid = QGridLayout()
        grid.addWidget(self._serial_group(), 0, 0)
        grid.addWidget(self._workarea_group(), 0, 1)
        grid.addWidget(self._speeds_group(), 1, 0)
        grid.addWidget(self._motor_group(), 1, 1)
        grid.addWidget(self._motion_group(), 2, 0, 1, 2)
        root.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- groups --------------------------------------------------------
    def _serial_group(self) -> QGroupBox:
        g = QGroupBox("SERIAL LINK")
        f = QFormLayout(g)
        self.port_num = _spin(1, 64, self.config.num_com_port)
        self.baud = QComboBox()
        self.baud.addItems(str(b) for b in self.BAUDS)
        if self.config.speed_com in self.BAUDS:
            self.baud.setCurrentText(str(self.config.speed_com))
        f.addRow("COM port #", self.port_num)
        f.addRow("Baud rate", self.baud)
        return g

    def _workarea_group(self) -> QGroupBox:
        g = QGroupBox("WORK AREA")
        f = QFormLayout(g)
        self.max_w = _spin(1000, 2_000_000, self.config.max_width_pcb, " µm")
        self.max_h = _spin(1000, 2_000_000, self.config.max_height_pcb, " µm")
        self.over_x = _spin(0, 100000, self.config.over_pos_x, " µm")
        self.over_y = _spin(0, 100000, self.config.over_pos_y, " µm")
        f.addRow("Max width", self.max_w)
        f.addRow("Max height", self.max_h)
        f.addRow("Overtravel X", self.over_x)
        f.addRow("Overtravel Y", self.over_y)
        return g

    def _speeds_group(self) -> QGroupBox:
        g = QGroupBox("SPEEDS")
        f = QFormLayout(g)
        self.spd_jog = _spin(1, 100000, self.config.speed_jog_x)
        self.spd_jog_y = _spin(1, 100000, self.config.speed_jog_y)
        self.spd_f = _spin(1, 100000, self.config.speed_exp_f)
        self.spd_b = _spin(1, 100000, self.config.speed_exp_b)
        self.repeat = _spin(1, 20, self.config.repeat_cnt)
        self.accel = _spin(0, 50000, self.config.over_pos_x, " µm")
        f.addRow("X своб (idle)", self.spd_jog)
        f.addRow("Y своб (table)", self.spd_jog_y)
        f.addRow("X прям (print)", self.spd_f)
        f.addRow("X обр (return)", self.spd_b)
        f.addRow("Repeat count", self.repeat)
        f.addRow("Поле разг/торм", self.accel)
        return g

    def _motor_group(self) -> QGroupBox:
        g = QGroupBox("MOTOR / LASER / FIDUCIALS")
        f = QFormLayout(g)
        self.motor_pwr = _spin(0, 100, self.config.motor_pwr, " %")
        self.motor_min = _spin(0, 100, self.config.motor_min, " %")
        self.motor_max = _spin(0, 100, self.config.motor_max, " %")
        self.pwr_limit = _spin(0, 1000, self.config.pwr_limit)
        self.brake = _spin(0, 5000, self.config.brake_time, " ms")
        self.punch = _spin(0, 50000, self.config.punch_dia, " µm")
        self.mark_lo = _spin(0, 50000, self.config.mark_dia, " µm")
        self.mark_hi = _spin(0, 50000, self.config.mark_dia_end, " µm")
        f.addRow("Vдв motor power", self.motor_pwr)
        f.addRow("Vмин break-away", self.motor_min)
        f.addRow("Vмакс max safe", self.motor_max)
        f.addRow("Laser limit /10%", self.pwr_limit)
        f.addRow("Пауза усп (settle)", self.brake)
        f.addRow("Диам кернения", self.punch)
        f.addRow("Реп отв min", self.mark_lo)
        f.addRow("Реп отв max", self.mark_hi)
        return g

    def _motion_group(self) -> QGroupBox:
        g = QGroupBox("AXIS CALIBRATION  (steps ↔ microns)")
        f = QFormLayout(g)
        m = self.config.motion
        self.unit_x = _spin(1, 1_000_000, m.unit_x)
        self.unit_y = _spin(1, 1_000_000, m.unit_y)
        self.res_x = _spin(1, 100000, m.resolution_x)
        self.res_y = _spin(1, 100000, m.resolution_y)
        self.qual = _spin(1, 64, m.qual)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        for lbl, w in (("Unit X", self.unit_x), ("Res X", self.res_x),
                       ("Unit Y", self.unit_y), ("Res Y", self.res_y),
                       ("Qual", self.qual)):
            h.addWidget(QLabel(lbl))
            h.addWidget(w)
        f.addRow(row)
        return g

    # -- save ----------------------------------------------------------
    def _on_save(self) -> None:
        c = self.config
        c.num_com_port = self.port_num.value()
        c.speed_com = int(self.baud.currentText())
        c.max_width_pcb = self.max_w.value()
        c.max_height_pcb = self.max_h.value()
        c.over_pos_x = self.over_x.value()
        c.over_pos_y = self.over_y.value()
        c.speed_jog_x = self.spd_jog.value()
        c.speed_jog_y = self.spd_jog_y.value()
        c.speed_exp_f = self.spd_f.value()
        c.speed_exp_b = self.spd_b.value()
        c.repeat_cnt = self.repeat.value()
        c.over_pos_x = self.accel.value()
        c.motor_pwr = self.motor_pwr.value()
        c.motor_min = self.motor_min.value()
        c.motor_max = self.motor_max.value()
        c.pwr_limit = self.pwr_limit.value()
        c.brake_time = self.brake.value()
        c.punch_dia = self.punch.value()
        c.mark_dia = self.mark_lo.value()
        c.mark_dia_end = self.mark_hi.value()
        c.motion.unit_x = self.unit_x.value()
        c.motion.unit_y = self.unit_y.value()
        c.motion.resolution_x = self.res_x.value()
        c.motion.resolution_y = self.res_y.value()
        c.motion.qual = self.qual.value()
        c.save()
        self.accept()
