"""
Camera alignment panel.

The GUI counterpart of ``capture.forth``: a live video preview with the
alignment crosshair overlaid, device selection, start/stop, snapshot, and a
"set offset" action that records the camera-to-tool offset
(``CamOffX``/``CamOffY``) into the device configuration — the alignment
workflow the original window served.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel,
    QGroupBox, QFileDialog,
)

from ..core.config import DeviceConfig
from ..hardware.camera import CameraDevice, CameraInfo, draw_crosshair, HAVE_CV2


class CameraPanel(QWidget):
    """Live alignment camera with crosshair overlay."""

    offset_changed = Signal(int, int)        # emitted on "set offset"
    log = Signal(str)

    def __init__(self, config: DeviceConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.device: CameraDevice | None = None
        self._machine_pos = (0.0, 0.0)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- UI ------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("ALIGNMENT CAMERA")
        v = QVBoxLayout(box)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet(
            "background:#060a0f; border:1px solid #1b2430; border-radius:6px;")
        self.preview.setText("camera stopped" if HAVE_CV2
                             else "OpenCV not installed —\ncamera unavailable")
        v.addWidget(self.preview)

        row = QHBoxLayout()
        self.device_box = QComboBox()
        self.refresh_btn = QPushButton("Scan")
        self.refresh_btn.clicked.connect(self._scan)
        row.addWidget(self.device_box, 1)
        row.addWidget(self.refresh_btn)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("Accent")
        self.start_btn.clicked.connect(self._toggle)
        self.snap_btn = QPushButton("Snapshot")
        self.snap_btn.clicked.connect(self._snapshot)
        self.align_btn = QPushButton("Set offset")
        self.align_btn.clicked.connect(self._set_offset)
        for b in (self.start_btn, self.snap_btn, self.align_btn):
            row2.addWidget(b)
        v.addLayout(row2)

        self.offset_label = QLabel(
            f"offset  X {self.config.cam_off_x}  Y {self.config.cam_off_y} px")
        self.offset_label.setObjectName("Caption")
        v.addWidget(self.offset_label)

        root.addWidget(box)
        self._set_running(False)
        self.snap_btn.setEnabled(False)
        self.align_btn.setEnabled(False)
        if HAVE_CV2:
            self._scan()

    # -- machine position feed (for the coordinate overlay) ------------
    def set_machine_pos(self, x_um: float, y_um: float) -> None:
        self._machine_pos = (x_um, y_um)

    # -- device handling -----------------------------------------------
    def _scan(self) -> None:
        self.device_box.clear()
        if not HAVE_CV2:
            self.log.emit("OpenCV not installed; camera disabled.")
            return
        devices = CameraDevice.list_devices()
        if not devices:
            self.device_box.addItem("No camera found", -1)
            self.start_btn.setEnabled(False)
            self.log.emit("No camera devices found.")
            return
        for d in devices:
            self.device_box.addItem(d.name, d.index)
        self.start_btn.setEnabled(True)
        self.log.emit(f"Found {len(devices)} camera(s).")

    def _toggle(self) -> None:
        if self.device and self.device.is_open:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        idx = self.device_box.currentData()
        if idx is None or idx < 0:
            return
        self.device = CameraDevice(index=int(idx),
                                   width=self.config.cam_res_x,
                                   height=self.config.cam_res_y)
        try:
            ok = self.device.open()
        except Exception as exc:
            self.log.emit(f"Camera error: {exc}")
            ok = False
        if ok:
            self._set_running(True)
            self.snap_btn.setEnabled(True)
            self.align_btn.setEnabled(True)
            self._timer.start(33)            # ~30 fps
            self.log.emit("Camera started.")
        else:
            self.device = None
            self.log.emit("Failed to open camera.")

    def _stop(self) -> None:
        self._timer.stop()
        if self.device:
            self.device.close()
            self.device = None
        self._set_running(False)
        self.snap_btn.setEnabled(False)
        self.align_btn.setEnabled(False)
        self.preview.setText("camera stopped")
        self.log.emit("Camera stopped.")

    def _set_running(self, running: bool) -> None:
        self.start_btn.setText("Stop" if running else "Start")

    # -- frame loop ----------------------------------------------------
    def _tick(self) -> None:
        if not (self.device and self.device.is_open):
            return
        frame = self.device.read_frame()
        if frame is None:
            return
        x_um, y_um = self._machine_pos
        text = f"X {x_um/1000:.2f}  Y {y_um/1000:.2f} mm"
        draw_crosshair(frame, self.config.cam_off_x, self.config.cam_off_y,
                       text, self.config.cross_alpha)
        h, w = frame.shape[:2]
        img = QImage(frame.data, w, h, 3 * w, QImage.Format_RGB888)
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(
            self.preview.width(), self.preview.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # -- actions -------------------------------------------------------
    def _snapshot(self) -> None:
        if not (self.device and self.device.is_open):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save snapshot", "snapshot.png", "PNG image (*.png)")
        if path and self.device.grab_still(path):
            self.log.emit(f"Snapshot saved: {path}")

    def _set_offset(self) -> None:
        # In a real alignment the operator centres a known fiducial under the
        # reticle; the current reticle offset is then stored as the cam->tool
        # offset.  Here we persist whatever offset is configured.
        self.config.save()
        self.offset_changed.emit(self.config.cam_off_x, self.config.cam_off_y)
        self.offset_label.setText(
            f"offset  X {self.config.cam_off_x}  Y {self.config.cam_off_y} px")
        self.log.emit("Camera offset stored to configuration.")

    def shutdown(self) -> None:
        self._stop()
