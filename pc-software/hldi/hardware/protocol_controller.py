"""
Transport-agnostic protocol controller.

Factors the command logic (framing, ACK + retry, motion / laser / state
commands, the INFO->READ status path) out of the serial backend so it can be
reused unchanged over WiFi (TCP) and Bluetooth.  A concrete backend only has
to implement the byte transport: :meth:`_write`, :meth:`_read_bytes`,
:meth:`_open_transport`, :meth:`_close_transport`.
"""

from __future__ import annotations

import struct
import threading
import time

from ..core.config import DeviceConfig
from ..core.protocol import Cmd, Packet, ProtocolError, MAX_RETRIES, CHAREND, RSHDR_SIZE
from .controller import Controller, MachineState, encode_value


class ProtocolController(Controller):
    """A Controller that speaks the binary protocol over any byte transport."""

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__(config)
        self._lock = threading.RLock()
        self.err_count = 0
        self._sbuf_addr = None
        self._rx = bytearray()

    # -- transport hooks (implemented by concrete backends) ------------
    def _open_transport(self) -> bool:
        raise NotImplementedError

    def _close_transport(self) -> None:
        raise NotImplementedError

    def _write(self, data: bytes) -> bool:
        raise NotImplementedError

    def _read_bytes(self, timeout: float) -> bytes:
        """Return whatever bytes are available within ``timeout`` (may be b'')."""
        raise NotImplementedError

    def _is_open(self) -> bool:
        raise NotImplementedError

    # -- lifecycle -----------------------------------------------------
    def open(self) -> bool:
        try:
            if not self._open_transport():
                self.state.connected = False
                return False
        except Exception:
            self.state.connected = False
            return False
        self.state.connected = self.check_connect()
        return self.state.connected

    def close(self) -> None:
        with self._lock:
            try:
                self._close_transport()
            finally:
                self.state.connected = False

    # -- framed read ---------------------------------------------------
    def _read_frame(self, timeout: float = 0.5) -> Packet | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self._read_bytes(0.1)
            if chunk:
                self._rx.extend(chunk)
                while CHAREND in self._rx:
                    end = self._rx.index(CHAREND) + 1
                    frame = bytes(self._rx[:end])
                    del self._rx[:end]
                    if len(frame) >= RSHDR_SIZE:
                        try:
                            return Packet.decode(frame)
                        except ProtocolError:
                            continue
        return None

    # -- command helpers (RepSendAndACK) -------------------------------
    def _send_and_ack(self, cid: int, payload: bytes = b"") -> bool:
        pkt = Packet(cid=cid, payload=payload).encode()
        for _ in range(MAX_RETRIES):
            if not self._write(pkt):
                self.err_count += 1
                if not self._auto_reconnect():
                    return False
                continue
            ack = self._read_frame()
            if ack is not None and ack.cid == Cmd.ACK:
                return True
            self.err_count += 1
        return False

    def _send_only(self, cid: int) -> bool:
        with self._lock:
            return self._send_and_ack(cid, b"")

    def _send_val(self, n: int, cid: int) -> bool:
        with self._lock:
            return self._send_and_ack(cid, encode_value(n))

    # -- automatic reconnect ------------------------------------------
    def _auto_reconnect(self) -> bool:
        """Best-effort reconnect after a transport write failure."""
        try:
            self._close_transport()
        except Exception:
            pass
        try:
            if self._open_transport():
                self._rx.clear()
                return True
        except Exception:
            pass
        self.state.connected = False
        return False

    # -- connection check ----------------------------------------------
    def check_connect(self) -> bool:
        for _ in range(3):
            if self._send_only(Cmd.ACK):
                return True
        return False

    # -- motion --------------------------------------------------------
    def move_to_x(self, x_um: float) -> bool:
        steps = self.config.motion.pos_to_steps_x(x_um)
        ok = self._send_val(steps, Cmd.MVX)
        if ok:
            self.state.x_um = x_um
        return ok

    def move_to_y(self, y_um: float) -> bool:
        steps = self.config.motion.pos_to_steps_y(y_um)
        ok = self._send_val(steps, Cmd.MVY)
        if ok:
            self.state.y_um = y_um
        return ok

    def step_x(self, steps: int) -> bool:
        return self._send_val(steps, Cmd.STX)

    def step_y(self, steps: int) -> bool:
        return self._send_val(steps, Cmd.STY)

    def stop_move(self) -> None:
        self._send_only(Cmd.MVS)
        self.state.moving = False

    def set_speed_x(self, v: int) -> bool:
        return self._send_val(v, Cmd.SPX)

    def set_speed_y(self, v: int) -> bool:
        return self._send_val(v, Cmd.SPY)

    def laser_power(self, n: int) -> None:
        n = max(0, min(int(n), self.config.pwr_limit))
        self._send_val(n, Cmd.LSR)
        self.state.laser_power = n

    # -- state (INFO -> READ SINFO) ------------------------------------
    def _query_info(self):
        pkt = Packet(cid=Cmd.INFO).encode()
        with self._lock:
            if not self._write(pkt):
                return None
            resp = self._read_frame()
        if resp is None or resp.cid != Cmd.INFO or len(resp.payload) < 16:
            return None
        pcnf, lbuf, sbuf = struct.unpack_from("<III", resp.payload, 0)
        self._sbuf_addr = sbuf
        return sbuf

    def _read_block(self, addr: int, length: int) -> bytes | None:
        payload = struct.pack("<II", addr, length)
        pkt = Packet(cid=Cmd.READ, payload=payload).encode()
        with self._lock:
            if not self._write(pkt):
                return None
            resp = self._read_frame()
        if resp is None or resp.cid != Cmd.READ:
            return None
        return resp.payload

    def read_state(self) -> MachineState:
        if self._sbuf_addr is None:
            if self._query_info() is None:
                return self.state
        block = self._read_block(self._sbuf_addr, 16)
        if block and len(block) >= 16:
            dx, dy, sp, st = struct.unpack_from("<iiiI", block, 0)
            self.state.x_um = self.config.motion.steps_to_pos_x(dx)
            self.state.y_um = self.config.motion.steps_to_pos_y(dy)
            self.state.moving = bool(st & 0x3)
        return self.state
