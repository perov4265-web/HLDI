"""
WiFi (TCP) controller backend.

Connects to an HLDI controller's TCP command port (found via
:mod:`hldi.hardware.discovery`) and runs the binary protocol over the socket.
All command logic is inherited from :class:`ProtocolController`; this class
only implements the socket transport, including reconnect.
"""

from __future__ import annotations

import socket

from ..core.config import DeviceConfig
from .protocol_controller import ProtocolController
from .discovery import HLDI_TCP_PORT


class NetworkController(ProtocolController):
    def __init__(self, config: DeviceConfig, host: str,
                 port: int = HLDI_TCP_PORT) -> None:
        super().__init__(config)
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None

    def _open_transport(self) -> bool:
        self._sock = socket.create_connection((self.host, self.port),
                                              timeout=3.0)
        self._sock.settimeout(0.1)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True

    def _close_transport(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _is_open(self) -> bool:
        return self._sock is not None

    def _write(self, data: bytes) -> bool:
        if self._sock is None:
            return False
        try:
            self._sock.sendall(data)
            return True
        except OSError:
            return False

    def _read_bytes(self, timeout: float) -> bytes:
        if self._sock is None:
            return b""
        self._sock.settimeout(timeout)
        try:
            return self._sock.recv(256)
        except socket.timeout:
            return b""
        except OSError:
            return b""
