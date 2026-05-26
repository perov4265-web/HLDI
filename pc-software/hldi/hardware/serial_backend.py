"""
Real serial-port backend.

Faithful port of the COM-port handling in the original firmware/PC link.  All
protocol command logic now lives in :class:`ProtocolController`; this class
implements only the pyserial transport (open/close/read/write) plus the
v055 COM-port-number fix.
"""

from __future__ import annotations

import sys

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover - allows import without the dep
    serial = None

from ..core.config import DeviceConfig
from .protocol_controller import ProtocolController


class SerialController(ProtocolController):
    """Drives the physical HLDI controller over a serial port."""

    def __init__(self, config: DeviceConfig, port_name: str | None = None) -> None:
        super().__init__(config)
        self._port = None
        self._port_name = port_name or self._default_port_name(config)

    @staticmethod
    def _default_port_name(config: DeviceConfig) -> str:
        # NumComPort -> platform port name.
        # v055 fix: COM ports above 9 need the \\.\COMxx form on Windows.
        n = config.num_com_port
        if sys.platform.startswith("win"):
            return rf"\\.\COM{n}" if n > 9 else f"COM{n}"
        return f"/dev/ttyUSB{max(0, n - 1)}"

    # -- transport hooks ----------------------------------------------
    def _open_transport(self) -> bool:
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        self._port = serial.Serial(
            port=self._port_name,
            baudrate=self.config.speed_com,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=0.5,
        )
        self._port.reset_input_buffer()
        self._port.reset_output_buffer()
        return True

    def _close_transport(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            finally:
                self._port = None

    def _is_open(self) -> bool:
        return self._port is not None

    def _write(self, data: bytes) -> bool:
        if self._port is None:
            return False
        try:
            self._port.write(data)
            return True
        except Exception:
            return False

    def _read_bytes(self, timeout: float) -> bytes:
        if self._port is None:
            return b""
        try:
            self._port.timeout = timeout
            return self._port.read(256)
        except Exception:
            return b""
