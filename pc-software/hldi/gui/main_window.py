"""
Main application window.

The modernised interface replacing the original Win32 console + dialogs.
Layout:

    +-----------------------------------------------------------+
    |  HLDI control            [● status]   [Settings]          |
    +---------------------------+-------------------------------+
    |                           |  CONNECTION                   |
    |                           |   backend / port / connect    |
    |      Gerber viewer        |  POSITION (live readout)      |
    |      (zoom / pan)         |  JOG  (X/Y nudge + go-to)     |
    |                           |  EXPOSURE (load, run, abort)  |
    |                           |  LOG                          |
    +---------------------------+-------------------------------+
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QFileDialog,
    QPlainTextEdit, QProgressBar, QFormLayout, QFrame, QMessageBox, QTabWidget,
    QSlider, QCheckBox, QMenuBar,
)

from ..core.config import DeviceConfig
from ..core.gerber import parse_gerber_file, GerberImage
from ..core.exposure import rasterize, ExposureJob
from ..hardware.controller import Controller
from ..hardware.simulator import SimController
from ..hardware.serial_backend import SerialController
from .gerber_view import GerberView
from .camera_panel import CameraPanel
from .settings_dialog import SettingsDialog
from .offsets_dialog import OffsetsDialog
from .pid_dialog import PidDialog
from .theme import status_dot


class _Bridge(QObject):
    """Marshals worker-thread callbacks back onto the GUI thread."""
    progress = Signal(int, int)
    done = Signal(bool)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HLDI — Laser Direct Imaging")
        self.resize(1180, 720)

        self.config = DeviceConfig.load()
        self.controller: Controller | None = None
        self.image: GerberImage | None = None
        self.job: ExposureJob | None = None
        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)

        self._build_ui()

        # live position poll
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_state)
        self._timer.start(250)

        self.log("Ready. Select a backend and connect.")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(10)

        self._build_menu()
        self._build_shortcuts()
        root.addLayout(self._header())

        body = QHBoxLayout()
        body.setSpacing(12)
        self.view = GerberView()
        self.camera = CameraPanel(self.config)
        self.camera.log.connect(self.log)
        self.camera.offset_changed.connect(
            lambda x, y: self.log(f"Camera offset set to X {x}, Y {y} px"))

        self.tabs = QTabWidget()
        self.tabs.addTab(self.view, "Gerber")
        self.tabs.addTab(self.camera, "Camera")
        body.addWidget(self.tabs, 3)
        body.addWidget(self._sidebar(), 2)
        root.addLayout(body, 1)

        self.statusBar().showMessage("Disconnected")

    def _header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        title = QLabel("HLDI  control")
        title.setObjectName("H1")
        h.addWidget(title)
        h.addStretch(1)
        self.status_label = QLabel("●  offline")
        self.status_label.setStyleSheet(f"color:{status_dot(False)};")
        h.addWidget(self.status_label)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._open_settings)
        h.addWidget(settings_btn)
        return h

    # ------------------------------------------------------------------
    # menu & hotkeys (port of the readme's Меню and "горячие клавиши")
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_files = bar.addMenu("Файлы")
        m_files.addAction("Новый Gerber", self._load_gerber)
        m_files.addAction("Добавить Gerber", self._add_gerber)
        m_files.addAction("Добавить кернение", self._add_punching)

        m_dbg = bar.addMenu("Отладка")
        m_dbg.addAction("Обновление прогр МК", self._update_firmware)

        m_set = bar.addMenu("Настройки")
        m_set.addAction("Общие", self._open_settings)
        m_set.addAction("Смещения", self._open_offsets)
        m_set.addAction("ПИД коэфф", self._open_pid)
        m_set.addSeparator()
        m_set.addAction("Вспышка лазера", self._laser_flash)
        m_set.addAction("Включить лазер…", self._laser_on)
        m_set.addAction("Выключить лазер", self._laser_off)

    def _build_shortcuts(self) -> None:
        def sc(keys, slot):
            s = QShortcut(QKeySequence(keys), self)
            s.activated.connect(slot)
            return s

        # F1/F2/F3 — coordinate presets
        sc("F1", lambda: self._goto_um(0, 0))
        sc("F2", self._goto_left_ref)
        sc("F3", self._goto_right_ref)
        sc("F9", self._update_firmware)

        # 1..0 — carriage speed presets 100..1000 mm/s
        for i, key in enumerate("1234567890"):
            sc(key, lambda v=(i + 1) * 100: self._set_speed_preset(v))
        # Ctrl+1..0 — free-move speed presets
        free = [75, 100, 128, 166, 215, 280, 360, 470, 615, 800]
        for i, key in enumerate("1234567890"):
            sc(f"Ctrl+{key}", lambda v=free[i]: self._set_jogspeed_preset(v))

        # arrows — jog; modifiers change the step (1/4, 10/40, 100/400 steps)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0),
                            ("Up", 0, 1), ("Down", 0, -1)):
            sc(key, lambda dx=dx, dy=dy: self._jog_steps(dx, dy, 1))
            sc(f"Shift+{key}", lambda dx=dx, dy=dy: self._jog_steps(dx, dy, 0.25))
            sc(f"Ctrl+{key}", lambda dx=dx, dy=dy: self._jog_steps(dx, dy, 10))
            sc(f"Ctrl+Shift+{key}", lambda dx=dx, dy=dy: self._jog_steps(dx, dy, 100))

        # board corners
        sc("End", lambda: self._goto_um(0, 0))
        sc("Home", lambda: self._goto_um(0, self.config.max_height_pcb))
        sc("PgDown", lambda: self._goto_um(self.config.max_width_pcb, 0))
        sc("PgUp", lambda: self._goto_um(self.config.max_width_pcb,
                                         self.config.max_height_pcb))

    def _sidebar(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        col.addWidget(self._connection_group())
        col.addWidget(self._position_group())
        col.addWidget(self._jog_group())
        col.addWidget(self._exposure_group())
        col.addWidget(self._log_group(), 1)
        return w

    def _connection_group(self) -> QGroupBox:
        g = QGroupBox("CONNECTION")
        f = QFormLayout(g)
        self.backend_box = QComboBox()
        self.backend_box.addItems(
            ["Simulator", "Serial (USB)", "WiFi (network)", "Bluetooth (BLE)"])
        self.port_edit = QComboBox()
        self.port_edit.setEditable(True)
        self._refresh_ports()

        self.discover_btn = QPushButton("Scan for devices")
        self.discover_btn.clicked.connect(self._discover_devices)
        self.device_box = QComboBox()      # discovered WiFi/BLE devices
        self.device_box.setEnabled(False)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("Accent")
        self.connect_btn.clicked.connect(self._toggle_connect)
        f.addRow("Backend", self.backend_box)
        f.addRow("Port", self.port_edit)
        f.addRow(self.discover_btn)
        f.addRow("Device", self.device_box)
        f.addRow(self.connect_btn)
        self.backend_box.currentIndexChanged.connect(self._on_backend_changed)
        self.port_edit.setEnabled(False)
        self._found_devices = []
        return g

    def _on_backend_changed(self, i: int) -> None:
        # 0 sim, 1 serial, 2 wifi, 3 ble
        self.port_edit.setEnabled(i == 1)
        wireless = i in (2, 3)
        self.discover_btn.setEnabled(wireless)
        self.device_box.setEnabled(wireless and self.device_box.count() > 0)

    def _position_group(self) -> QGroupBox:
        g = QGroupBox("POSITION")
        v = QVBoxLayout(g)
        self.pos_label = QLabel("X  0.000    Y  0.000  mm")
        self.pos_label.setObjectName("Mono")
        v.addWidget(self.pos_label)
        self.laser_label = QLabel("laser  off")
        self.laser_label.setObjectName("Caption")
        v.addWidget(self.laser_label)
        return g

    def _jog_group(self) -> QGroupBox:
        g = QGroupBox("JOG  /  GO TO")
        grid = QGridLayout(g)
        self.step_box = QSpinBox()
        self.step_box.setRange(1, 50000)
        self.step_box.setValue(1000)
        self.step_box.setSuffix(" µm")
        grid.addWidget(QLabel("step"), 0, 0)
        grid.addWidget(self.step_box, 0, 1, 1, 2)

        yp = QPushButton("Y +"); ym = QPushButton("Y −")
        xp = QPushButton("X +"); xm = QPushButton("X −")
        yp.clicked.connect(lambda: self._jog(0, +1))
        ym.clicked.connect(lambda: self._jog(0, -1))
        xp.clicked.connect(lambda: self._jog(+1, 0))
        xm.clicked.connect(lambda: self._jog(-1, 0))
        grid.addWidget(yp, 1, 1)
        grid.addWidget(xm, 2, 0)
        grid.addWidget(xp, 2, 2)
        grid.addWidget(ym, 3, 1)

        stop = QPushButton("STOP")
        stop.setObjectName("Danger")
        stop.clicked.connect(self._stop)
        grid.addWidget(stop, 2, 1)

        self.goto_x = QDoubleSpinBox(); self.goto_x.setRange(0, 2000); self.goto_x.setSuffix(" mm")
        self.goto_y = QDoubleSpinBox(); self.goto_y.setRange(0, 2000); self.goto_y.setSuffix(" mm")
        go = QPushButton("Go")
        go.clicked.connect(self._goto)
        grid.addWidget(self.goto_x, 4, 0)
        grid.addWidget(self.goto_y, 4, 1)
        grid.addWidget(go, 4, 2)
        return g

    def _exposure_group(self) -> QGroupBox:
        g = QGroupBox("EXPOSURE")
        v = QVBoxLayout(g)

        row = QHBoxLayout()
        load = QPushButton("Load Gerber…")
        load.clicked.connect(self._load_gerber)
        add = QPushButton("Add…")
        add.setToolTip("Add another Gerber on top, or add centre-punching")
        add.clicked.connect(self._show_add_menu)
        row.addWidget(load)
        row.addWidget(add)
        row.addWidget(QLabel("DPI"))
        self.dpi_box = QSpinBox()
        self.dpi_box.setRange(100, 4000)
        self.dpi_box.setValue(1000)
        row.addWidget(self.dpi_box)
        v.addLayout(row)

        # image transforms: негат / X зерк / Y зерк
        trow = QHBoxLayout()
        self.cb_negative = QCheckBox("негат")
        self.cb_mirror_x = QCheckBox("X зерк")
        self.cb_mirror_y = QCheckBox("Y зерк")
        for cb, attr in ((self.cb_negative, "negative"),
                         (self.cb_mirror_x, "mirror_x"),
                         (self.cb_mirror_y, "mirror_y")):
            cb.setChecked(getattr(self.config, attr))
            cb.toggled.connect(self._on_transform_changed)
            trow.addWidget(cb)
        v.addLayout(trow)

        # laser power mode "мс/К*" + live sliders
        prow = QHBoxLayout()
        self.power_mode_btn = QPushButton("мс/К*")
        self.power_mode_btn.setCheckable(True)
        self.power_mode_btn.setChecked(self.config.power_mode == 1)
        self.power_mode_btn.clicked.connect(self._toggle_power_mode)
        self.power_mode_label = QLabel()
        prow.addWidget(self.power_mode_btn)
        prow.addWidget(self.power_mode_label, 1)
        v.addLayout(prow)

        v.addWidget(QLabel("Скорость эксп, мм/с"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(1, 200)
        self.speed_slider.setValue(min(200, max(1, self.config.speed_exp_f // 100)))
        self.speed_slider.valueChanged.connect(self._on_speed_slider)
        v.addWidget(self.speed_slider)

        v.addWidget(QLabel("Огр. мощности лазера"))
        self.power_slider = QSlider(Qt.Horizontal)
        self.power_slider.setRange(0, 1000)
        self.power_slider.setValue(self.config.pwr_limit)
        self.power_slider.valueChanged.connect(self._on_power_slider)
        v.addWidget(self.power_slider)

        row2 = QHBoxLayout()
        self.run_btn = QPushButton("ЭКСПОН")
        self.run_btn.setObjectName("Accent")
        self.run_btn.clicked.connect(self._run_exposure)
        self.run_btn.setEnabled(False)
        self.abort_btn = QPushButton("СТОП")
        self.abort_btn.setObjectName("Danger")
        self.abort_btn.clicked.connect(self._abort_exposure)
        self.abort_btn.setEnabled(False)
        row2.addWidget(self.run_btn)
        row2.addWidget(self.abort_btn)
        v.addLayout(row2)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        v.addWidget(self.progress)
        self._update_power_label()
        return g

    def _log_group(self) -> QGroupBox:
        g = QGroupBox("LOG")
        v = QVBoxLayout(g)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        v.addWidget(self.log_view)
        return g

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _refresh_ports(self) -> None:
        self.port_edit.clear()
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        if not ports:
            import sys
            ports = ["COM1"] if sys.platform.startswith("win") else ["/dev/ttyUSB0"]
        self.port_edit.addItems(ports)

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def _toggle_connect(self) -> None:
        if self.controller and self.controller.state.connected:
            self.controller.close()
            self.controller = None
            self.connect_btn.setText("Connect")
            self.run_btn.setEnabled(False)
            self._set_status(False)
            self.log("Disconnected.")
            return

        idx = self.backend_box.currentIndex()
        if idx == 0:
            self.controller = SimController(self.config)
            self.log("Using simulator backend.")
        elif idx == 1:
            port = self.port_edit.currentText().strip()
            self.controller = SerialController(self.config, port_name=port)
            self.log(f"Opening serial port {port} @ {self.config.speed_com} baud…")
        elif idx == 2:
            dev = self._selected_device("wifi")
            if dev is None:
                self.log("No WiFi device selected — press Scan first.")
                return
            from ..hardware.network_backend import NetworkController
            self.controller = NetworkController(self.config, dev.address, dev.port)
            self.log(f"Connecting to {dev.name} at {dev.address}:{dev.port}…")
        elif idx == 3:
            dev = self._selected_device("ble")
            if dev is None:
                self.log("No Bluetooth device selected — press Scan first.")
                return
            from ..hardware.ble_backend import BleController, HAVE_BLEAK
            if not HAVE_BLEAK:
                self.log("Bluetooth needs the 'bleak' package (pip install bleak).")
                return
            self.controller = BleController(self.config, dev.address)
            self.log(f"Connecting to {dev.name} over BLE ({dev.address})…")

        try:
            ok = self.controller.open()
        except Exception as exc:
            ok = False
            self.log(f"Error: {exc}")

        if ok:
            self.connect_btn.setText("Disconnect")
            self._set_status(True)
            self.run_btn.setEnabled(self.image is not None)
            self.log("Controller connected.")
        else:
            self.controller = None
            self._set_status(False)
            self.log("Connection failed.")

    def _selected_device(self, transport: str):
        i = self.device_box.currentIndex()
        if 0 <= i < len(self._found_devices):
            dev = self._found_devices[i]
            if dev.transport == transport:
                return dev
        # fall back to the first device of the right transport
        for dev in self._found_devices:
            if dev.transport == transport:
                return dev
        return None

    def _discover_devices(self) -> None:
        from ..hardware import discovery
        self.discover_btn.setEnabled(False)
        self.discover_btn.setText("Scanning…")
        self.log("Scanning for HLDI devices (WiFi + Bluetooth)…")
        QApplication.processEvents()
        try:
            want_ble = self.backend_box.currentIndex() == 3
            devices = discovery.discover_wifi(timeout=2.0)
            if want_ble:
                devices += discovery.discover_ble(timeout=4.0)
        except Exception as exc:
            devices = []
            self.log(f"Discovery error: {exc}")
        self._found_devices = devices
        self.device_box.clear()
        for d in devices:
            self.device_box.addItem(d.label())
        self.device_box.setEnabled(bool(devices))
        self.discover_btn.setEnabled(True)
        self.discover_btn.setText("Scan for devices")
        if devices:
            self.log(f"Found {len(devices)} device(s).")
        else:
            self.log("No devices found. Check power, WiFi config and range.")


    def _set_status(self, connected: bool) -> None:
        self.status_label.setText("●  online" if connected else "●  offline")
        self.status_label.setStyleSheet(f"color:{status_dot(connected)};")
        self.statusBar().showMessage("Connected" if connected else "Disconnected")

    # ------------------------------------------------------------------
    # motion
    # ------------------------------------------------------------------
    def _require_ctl(self) -> bool:
        if not (self.controller and self.controller.state.connected):
            self.log("Not connected.")
            return False
        return True

    def _jog(self, sx: int, sy: int) -> None:
        if not self._require_ctl():
            return
        d = self.step_box.value()
        st = self.controller.state
        self.controller.move_to_xy(st.x_um + sx * d, st.y_um + sy * d)
        self._poll_state()

    def _goto(self) -> None:
        if not self._require_ctl():
            return
        self.controller.move_to_xy(self.goto_x.value() * 1000,
                                   self.goto_y.value() * 1000)
        self._poll_state()

    def _stop(self) -> None:
        if self.controller:
            self.controller.stop_move()
            self.controller.laser_off()
            self.log("STOP — motion halted, laser off.")

    def _poll_state(self) -> None:
        if not (self.controller and self.controller.state.connected):
            return
        st = self.controller.read_state()
        self.pos_label.setText(f"X  {st.x_um/1000:.3f}    Y  {st.y_um/1000:.3f}  mm")
        self.laser_label.setText(
            f"laser  {st.laser_power/10:.0f} %" if st.laser_power else "laser  off")
        self.view.set_machine_pos(st.x_um, st.y_um)
        self.camera.set_machine_pos(st.x_um, st.y_um)

    # ------------------------------------------------------------------
    # gerber / exposure
    # ------------------------------------------------------------------
    def _load_gerber(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Gerber file", "",
            "Gerber (*.gbr *.ger *.art *.pho *.gtl *.gbl *.txt);;All files (*)")
        if not path:
            return
        try:
            self.image = parse_gerber_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Gerber", f"Failed to parse:\n{exc}")
            return
        self._after_image_change(f"Loaded {path}")

    def _show_add_menu(self) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        a1 = menu.addAction("Добавить Gerber (overlay)")
        a2 = menu.addAction("Добавить кернение (Excellon)…")
        a1.triggered.connect(self._add_gerber)
        a2.triggered.connect(self._add_punching)
        menu.exec(self.cursor().pos() if hasattr(self, "cursor")
                  else self.mapToGlobal(self.rect().center()))

    def _add_gerber(self) -> None:
        if self.image is None:
            self._load_gerber()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Gerber file (overlay)", "",
            "Gerber (*.gbr *.ger *.art *.pho *.gtl *.gbl *.txt);;All files (*)")
        if not path:
            return
        try:
            overlay = parse_gerber_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Gerber", f"Failed to parse:\n{exc}")
            return
        # objects are drawn over the existing image (readme: "Добавить Gerber")
        self.image.traces += overlay.traces
        self.image.flashes += overlay.flashes
        self.image.regions += overlay.regions
        self.image.macro_shapes += overlay.macro_shapes
        self._after_image_change(f"Added overlay {path}")

    def _add_punching(self) -> None:
        if self.image is None:
            QMessageBox.information(self, "Кернение",
                                    "Load a Gerber image first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Excellon drill file", "",
            "Excellon (*.drl *.txt *.exc *.nc);;All files (*)")
        if not path:
            return
        try:
            from ..core.excellon import parse_excellon_file
            from ..core.gerber import apply_punching
            drill = parse_excellon_file(path)
            n = apply_punching(self.image, drill.holes, self.config.punch_dia)
        except Exception as exc:
            QMessageBox.warning(self, "Кернение", f"Failed:\n{exc}")
            return
        self._after_image_change(
            f"Added {n} centre-punches (Ø{self.config.punch_dia/1000:.2f} mm)")

    def _after_image_change(self, msg: str) -> None:
        self.view.set_image(self.image)
        b = self.image.bounds()
        self.log(msg)
        self.log(f"  {len(self.image.traces)} traces, "
                 f"{len(self.image.flashes)} pads, "
                 f"{len(self.image.regions)} regions, "
                 f"{len(self.image.macro_shapes)} macro/punch; "
                 f"size {b[2]-b[0]:.1f}×{b[3]-b[1]:.1f} mm")
        self.run_btn.setEnabled(
            self.controller is not None and self.controller.state.connected)

    # -- transforms & power -------------------------------------------
    def _on_transform_changed(self) -> None:
        self.config.negative = self.cb_negative.isChecked()
        self.config.mirror_x = self.cb_mirror_x.isChecked()
        self.config.mirror_y = self.cb_mirror_y.isChecked()

    def _toggle_power_mode(self) -> None:
        self.config.power_mode = 1 if self.power_mode_btn.isChecked() else 0
        # recompute one into the other, as the readme describes
        if self.config.power_mode == 1:
            self.config.pixel_ms = self.config.power_to_ms()
        else:
            self.config.power_k = self.config.ms_to_power()
        self._update_power_label()

    def _update_power_label(self) -> None:
        if self.config.power_mode == 1:
            self.power_mode_label.setText(
                f"мс: {self.config.pixel_ms:.3f} ms/pixel")
        else:
            self.power_mode_label.setText(
                f"К*: {self.config.power_k:.3f} of max")

    def _on_speed_slider(self, v: int) -> None:
        self.config.speed_exp_f = v * 100        # mm/s shown ×100 internally
        self._update_power_label()               # dwell depends on speed

    def _on_power_slider(self, v: int) -> None:
        self.config.pwr_limit = v
        if self.controller and self.controller.state.connected:
            # live laser-limit regulation while focusing/exposing
            pass

    def _run_exposure(self) -> None:
        if not self._require_ctl() or self.image is None:
            return
        self.log("Rasterising…")
        bmp = rasterize(self.image, dpi=self.dpi_box.value(),
                        negative=self.config.negative,
                        mirror_x=self.config.mirror_x,
                        mirror_y=self.config.mirror_y)
        self.log(f"  {bmp.width}×{bmp.height} px @ {bmp.pitch_um:.1f} µm/px")
        self.job = ExposureJob(self.controller, self.config, bmp)
        self.progress.setMaximum(bmp.height)
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)
        self.log("Exposure started.")
        self.job.run(
            on_progress=lambda c, t: self.bridge.progress.emit(c, t),
            on_done=lambda ok: self.bridge.done.emit(ok))

    def _abort_exposure(self) -> None:
        if self.job:
            self.job.abort()
            self.log("Abort requested…")

    def _on_progress(self, cur: int, total: int) -> None:
        self.progress.setValue(cur)

    def _on_done(self, completed: bool) -> None:
        self.abort_btn.setEnabled(False)
        self.run_btn.setEnabled(True)
        self.log("Exposure complete." if completed else "Exposure aborted.")
        self._poll_state()

    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            self.log("Configuration saved.")
            self._refresh_ports()
            self._sync_controls_from_config()

    def _open_offsets(self) -> None:
        dlg = OffsetsDialog(self.config, self.controller, self)
        if dlg.exec():
            self.log("Offsets saved.")

    def _open_pid(self) -> None:
        PidDialog(self.config, self.controller, self).exec()

    def _sync_controls_from_config(self) -> None:
        self.cb_negative.setChecked(self.config.negative)
        self.cb_mirror_x.setChecked(self.config.mirror_x)
        self.cb_mirror_y.setChecked(self.config.mirror_y)
        self.power_slider.setValue(self.config.pwr_limit)
        self.speed_slider.setValue(min(200, max(1, self.config.speed_exp_f // 100)))
        self._update_power_label()

    # -- laser actions (Настройки menu) -------------------------------
    def _laser_flash(self) -> None:
        if not self._require_ctl():
            return
        import threading
        self.controller.laser_power(self.config.pwr_limit)
        threading.Timer(0.5, self.controller.laser_off).start()
        self.log("Laser flash 500 ms (calibration).")

    def _laser_on(self) -> None:
        if not self._require_ctl():
            return
        level = int(round(self.config.effective_power_fraction()
                          * self.config.pwr_limit))
        self.controller.laser_power(level)
        self.log(f"Laser on at level {level} (focusing).")

    def _laser_off(self) -> None:
        if self.controller:
            self.controller.laser_off()
            self.log("Laser off.")

    def _update_firmware(self) -> None:
        # AVR bootloader flashing is not part of this prototype.
        QMessageBox.information(
            self, "Обновление прогр МК",
            "Firmware flashing (AVR bootloader) is not implemented in this "
            "prototype.\nThe CMD_BOOT_* protocol commands are defined in "
            "protocol.py as the extension point.")

    # -- navigation / hotkey targets ----------------------------------
    def _goto_um(self, x_um: float, y_um: float) -> None:
        if not self._require_ctl():
            return
        self.controller.move_to_xy(x_um, y_um)
        self._poll_state()

    def _goto_left_ref(self) -> None:
        self._goto_um(-self.config.cam_off_x, -self.config.cam_off_y)

    def _goto_right_ref(self) -> None:
        self._goto_um(self.config.max_width_pcb - self.config.cam_off_x,
                      -self.config.cam_off_y)

    def _set_speed_preset(self, v: int) -> None:
        self.config.speed_exp_f = v
        self.speed_slider.setValue(min(200, max(1, v // 100)))
        if self.controller and self.controller.state.connected:
            self.controller.set_speed_x(v)
        self.log(f"Carriage speed {v} mm/s.")

    def _set_jogspeed_preset(self, v: int) -> None:
        self.config.speed_jog_x = v
        self.log(f"Free-move speed {v}.")

    def _jog_steps(self, dx: int, dy: int, step_mult: float) -> None:
        if not self._require_ctl():
            return
        # base step is one full step; modifiers scale it (1/4, 10, 100)
        base = self.step_box.value()
        d = base * step_mult
        st = self.controller.state
        self.controller.move_to_xy(st.x_um + dx * d, st.y_um + dy * d)
        self._poll_state()

    def closeEvent(self, evt) -> None:
        if self.job:
            self.job.abort()
        self.camera.shutdown()
        if self.controller:
            self.controller.close()
        super().closeEvent(evt)
