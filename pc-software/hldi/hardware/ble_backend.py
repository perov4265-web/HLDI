"""
Bluetooth LE controller backend.

Talks to an HLDI controller over the BLE Nordic-UART-style characteristics
advertised by the firmware.  ``bleak`` is async, so a private asyncio event
loop runs on its own thread; the synchronous :class:`ProtocolController`
transport methods marshal onto it.  Notifications from the device are pushed
into a byte queue that :meth:`_read_bytes` drains.

``bleak`` is an optional dependency; if it is not installed the BLE backend
is simply unavailable (``open`` returns False).
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque

from ..core.config import DeviceConfig
from .protocol_controller import ProtocolController
from .discovery import HLDI_BLE_RX_UUID, HLDI_BLE_TX_UUID

try:
    from bleak import BleakClient
    HAVE_BLEAK = True
except ImportError:  # pragma: no cover
    BleakClient = None
    HAVE_BLEAK = False


class BleController(ProtocolController):
    def __init__(self, config: DeviceConfig, address: str) -> None:
        super().__init__(config)
        self.address = address
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._inbox: deque[int] = deque()
        self._inbox_lock = threading.Lock()

    # -- event loop thread --------------------------------------------
    def _start_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        daemon=True)
        self._thread.start()

    def _run(self, coro, timeout: float = 5.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # -- transport hooks ----------------------------------------------
    def _open_transport(self) -> bool:
        if not HAVE_BLEAK:
            return False
        if self._loop is None:
            self._start_loop()

        async def _connect():
            client = BleakClient(self.address)
            await client.connect()

            def _notify(_handle, data: bytearray):
                with self._inbox_lock:
                    self._inbox.extend(data)

            await client.start_notify(HLDI_BLE_TX_UUID, _notify)
            return client

        try:
            self._client = self._run(_connect(), timeout=10.0)
            return self._client is not None and self._client.is_connected
        except Exception:
            return False

    def _close_transport(self) -> None:
        if self._client is not None and self._loop is not None:
            try:
                self._run(self._client.disconnect(), timeout=5.0)
            except Exception:
                pass
            self._client = None

    def _is_open(self) -> bool:
        return self._client is not None

    def _write(self, data: bytes) -> bool:
        if self._client is None:
            return False
        try:
            self._run(self._client.write_gatt_char(HLDI_BLE_RX_UUID,
                                                   bytes(data), response=False))
            return True
        except Exception:
            return False

    def _read_bytes(self, timeout: float) -> bytes:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._inbox_lock:
                if self._inbox:
                    out = bytes(self._inbox)
                    self._inbox.clear()
                    return out
            time.sleep(0.005)
        return b""
