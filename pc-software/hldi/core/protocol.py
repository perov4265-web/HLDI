"""
HLDI controller serial protocol.

This is a faithful port of the framing and checksum used by the controller
firmware (``HARD/FIRMWARE/SRC/comport.c`` ``SendBlock`` / ``prot.c``
``CHECK_INPUT_BIN``) and the shared header ``hldi.h`` by AlphaCrow.

The firmware is the single source of truth for these values; this module is
verified byte-for-byte against it.

Frame layout (as produced by the firmware ``SendBlock`` and accepted by
``CHECK_INPUT_BIN``)::

    +--------+--------+===========+--------+--------+--------+--------+
    | CBEG   | CID    |  PAYLOAD  | LEN    | KS lo  | KS hi  | CEND   |
    +--------+--------+===========+--------+--------+--------+--------+
      0xD6     id        n bytes    n+6      checksum (16)    0xE7

* ``LEN`` = payload length + ``RSHDR_SIZE`` (6).
* ``KS``  = ``(CBEG + CID + LEN + sum(payload)) & 0xFFFF``, little-endian.
* The length byte is transmitted **after** the payload (a quirk of the
  original ``SendBlock`` that this port reproduces exactly).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Framing constants (from the firmware hldi.h)
# ---------------------------------------------------------------------------
CHARBEG = 0xD6
CHAREND = 0xE7
RSHDR_SIZE = 6          # cbeg + cid + len + ks[2] + cend
KS_LEN = 2

#: Maximum useful payload per packet (``MAXLENRS``).
MAXLENRS = 112
#: Command receive buffer half-size (``SIZEBUFRX``).
SIZEBUFRX = 128

#: Firmware version this protocol targets.
HLDI_VERSION = 36


# ---------------------------------------------------------------------------
# Command identifiers (sequential 0..17, from hldi.h / prot.c TabExecCMD[])
# ---------------------------------------------------------------------------
class Cmd:
    ACK = 0     # acknowledge
    INFO = 1    # send MCU info block
    CONF = 2    # reconfigure from CONFIG block
    BOOT = 3    # reboot (firmware update)
    READ = 4    # read a RAM block to the PC
    WRITE = 5   # write a RAM block from the PC
    MVS = 6     # stop motion
    MVX = 7     # move carriage to absolute position (microns)
    MVY = 8     # move table to absolute position (microns)
    STX = 9     # step carriage by N encoder steps
    STY = 10    # step table by N motor steps
    SPX = 11    # set carriage speed
    SPY = 12    # set table speed
    LSR = 13    # laser control
    LPTR = 14   # set active exposure buffer pointer
    AXSX = 15   # set carriage coordinate X
    AXSY = 16   # set table coordinate Y
    DBG = 17    # debug enable/disable
    COUNT = 18

    # legacy aliases kept so existing call sites stay valid
    COM_STATE = INFO


def checksum(cbeg: int, cid: int, len_field: int, payload: bytes) -> int:
    """16-bit checksum matching the firmware ``SendBlock`` accumulation."""
    return (cbeg + cid + len_field + sum(payload)) & 0xFFFF


@dataclass
class Packet:
    """A protocol packet."""

    cid: int
    payload: bytes = b""

    def encode(self) -> bytes:
        """Build the on-wire frame (port of ``SendBlock``)."""
        payload = bytes(self.payload)
        len_field = (len(payload) + RSHDR_SIZE) & 0xFF
        ks = checksum(CHARBEG, self.cid & 0xFF, len_field, payload)
        return (bytes((CHARBEG, self.cid & 0xFF)) + payload +
                bytes((len_field,)) + struct.pack("<H", ks) +
                bytes((CHAREND,)))

    @classmethod
    def decode(cls, raw: bytes) -> "Packet":
        """Parse a complete frame (port of ``CHECK_INPUT_BIN`` validation)."""
        if len(raw) < RSHDR_SIZE:
            raise ProtocolError("frame too short")
        if raw[-1] != CHAREND:
            raise ProtocolError(f"bad end marker 0x{raw[-1]:02X}")
        # the length byte sits 4 bytes before the end (len, ks, ks, cend)
        len_field = raw[-4]
        if not (RSHDR_SIZE <= len_field < SIZEBUFRX):
            raise ProtocolError("bad length field")
        pkt = raw[-len_field:]            # exact packet, starting at CBEG
        if pkt[0] != CHARBEG:
            raise ProtocolError(f"bad start marker 0x{pkt[0]:02X}")
        n = len_field - 4                # bytes covered by checksum (incl cbeg)
        ks = sum(pkt[0:n + 1]) & 0xFFFF
        got = pkt[n + 1] | (pkt[n + 2] << 8)
        if ks != got:
            raise ProtocolError(
                f"checksum mismatch: computed 0x{ks:04X}, got 0x{got:04X}")
        cid = pkt[1]
        payload = pkt[2:2 + (len_field - RSHDR_SIZE)]
        return cls(cid=cid, payload=payload)


class ProtocolError(Exception):
    """Raised on framing or checksum errors."""


#: The PC side retries each command waiting for an ACK; the original PC program
#: used 5 attempts (``RepSendAndACK``).
MAX_RETRIES = 5
