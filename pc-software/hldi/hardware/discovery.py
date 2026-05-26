"""
Automatic HLDI device discovery over WiFi and Bluetooth.

Mirrors the firmware discovery protocol (``firmware/.../hldi_discovery.h``):

* **WiFi**: broadcasts the probe :data:`HLDI_PROBE` to UDP port
  :data:`HLDI_DISC_PORT`; controllers reply (and also beacon unsolicited) with
  a one-line JSON record describing themselves.  Found devices expose a TCP
  command port.
* **Bluetooth LE**: scans for peripherals advertising the HLDI service UUID /
  name prefix; commands then flow over a Nordic-UART-style characteristic pair.

Both scans are best-effort and degrade gracefully when a transport or its
optional dependency (``bleak`` for BLE) is unavailable.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import List

# ---- discovery constants (must match firmware hldi_discovery.h) ----
HLDI_DISC_PORT = 50505
HLDI_TCP_PORT = 3333
HLDI_PROBE = b"HLDI?WHO"
HLDI_BEACON_KEY = "hldi"

HLDI_BLE_SERVICE_UUID = "6e40a001-b5a3-f393-e0a9-e50e24dcca9e"
HLDI_BLE_RX_UUID = "6e40a002-b5a3-f393-e0a9-e50e24dcca9e"   # PC -> device
HLDI_BLE_TX_UUID = "6e40a003-b5a3-f393-e0a9-e50e24dcca9e"   # device -> PC
HLDI_BLE_NAME_PREFIX = "HLDI-"


@dataclass
class FoundDevice:
    transport: str          # "wifi" | "ble"
    name: str
    address: str            # IP (wifi) or BLE address
    port: int = 0           # TCP port for wifi
    version: int = 0
    mac: str = ""

    def label(self) -> str:
        where = (f"{self.address}:{self.port}" if self.transport == "wifi"
                 else self.address)
        return f"{self.name}  [{self.transport}]  {where}"


# ---------------------------------------------------------------------------
# WiFi discovery (UDP broadcast probe + listen for beacons)
# ---------------------------------------------------------------------------
def discover_wifi(timeout: float = 2.0) -> List[FoundDevice]:
    found: dict[str, FoundDevice] = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.4)
        # send the probe to the broadcast address
        try:
            sock.sendto(HLDI_PROBE, ("255.255.255.255", HLDI_DISC_PORT))
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            dev = _parse_beacon(data, addr[0])
            if dev:
                found[dev.address] = dev
        sock.close()
    except OSError:
        pass
    return list(found.values())


def _parse_beacon(data: bytes, src_ip: str) -> FoundDevice | None:
    try:
        obj = json.loads(data.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or HLDI_BEACON_KEY not in obj:
        return None
    return FoundDevice(
        transport="wifi",
        name=str(obj.get("name", "HLDI")),
        address=str(obj.get("ip", src_ip)),
        port=int(obj.get("port", HLDI_TCP_PORT)),
        version=int(obj.get("ver", 0)),
        mac=str(obj.get("mac", "")),
    )


# ---------------------------------------------------------------------------
# BLE discovery (scan for the HLDI service / name prefix)
# ---------------------------------------------------------------------------
def discover_ble(timeout: float = 4.0) -> List[FoundDevice]:
    try:
        import asyncio
        from bleak import BleakScanner
    except ImportError:
        return []

    async def _scan():
        out: List[FoundDevice] = []
        devices = await BleakScanner.discover(timeout=timeout,
                                              return_adv=True)
        for dev, adv in devices.values():
            name = adv.local_name or dev.name or ""
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if (HLDI_BLE_SERVICE_UUID in uuids or
                    name.startswith(HLDI_BLE_NAME_PREFIX)):
                out.append(FoundDevice(transport="ble", name=name or "HLDI",
                                       address=dev.address))
        return out

    try:
        return asyncio.run(_scan())
    except Exception:
        return []


def discover_all(wifi_timeout: float = 2.0,
                 ble_timeout: float = 4.0) -> List[FoundDevice]:
    """Discover devices over every available transport."""
    devices = discover_wifi(wifi_timeout)
    devices += discover_ble(ble_timeout)
    return devices
