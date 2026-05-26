"""
Hardware controller abstraction.

:class:`Controller` is the common interface the GUI talks to.  Two backends
implement it:

* :class:`~hldi.hardware.serial_backend.SerialController` - drives the real
  device over a COM port using the protocol in :mod:`hldi.core.protocol`,
  faithfully reproducing the command words from ``INC/hardctrl.forth``.
* :class:`~hldi.hardware.simulator.SimController` - an in-process simulator
  that models position, laser power and connection state so the whole
  application can run and be tested without hardware.

High-level motion is expressed in microns; conversion to encoder steps is
done with :class:`~hldi.core.config.MotionConfig` exactly as ``PosToSteps``
did in the Forth source.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.config import DeviceConfig


@dataclass
class MachineState:
    """Snapshot of the controller state (port of the SINFO/state buffer)."""
    x_um: float = 0.0
    y_um: float = 0.0
    laser_power: int = 0        # 0..1000  (n/10 %)
    moving: bool = False
    connected: bool = False


def encode_value(n: int) -> bytes:
    """Encode a signed 32-bit value as the firmware expects (little-endian).

    Mirrors ``SendValCMD`` which pushes a cell (32-bit) payload.
    """
    return struct.pack("<i", int(n))


class Controller(ABC):
    """Abstract machine controller."""

    def __init__(self, config: DeviceConfig) -> None:
        self.config = config
        self.state = MachineState()

    # -- lifecycle -----------------------------------------------------
    @abstractmethod
    def open(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def check_connect(self) -> bool:
        """Port of ``_CheckConnect`` - ping with ACK up to 3 times."""

    # -- motion (microns) ---------------------------------------------
    @abstractmethod
    def move_to_x(self, x_um: float) -> bool: ...

    @abstractmethod
    def move_to_y(self, y_um: float) -> bool: ...

    def move_to_xy(self, x_um: float, y_um: float) -> bool:
        """Port of ``MoveToXY`` - Y first, then X."""
        if self.move_to_y(y_um):
            return self.move_to_x(x_um)
        return False

    @abstractmethod
    def step_x(self, steps: int) -> bool: ...

    @abstractmethod
    def step_y(self, steps: int) -> bool: ...

    @abstractmethod
    def stop_move(self) -> None:
        """Port of ``StopMove``."""

    # -- speed ---------------------------------------------------------
    @abstractmethod
    def set_speed_x(self, v: int) -> bool: ...

    @abstractmethod
    def set_speed_y(self, v: int) -> bool: ...

    # -- laser ---------------------------------------------------------
    @abstractmethod
    def laser_power(self, n: int) -> None:
        """Port of ``LaserValExp`` - set laser to n/10 %."""

    def laser_off(self) -> None:
        """Port of ``LaserOff``."""
        self.laser_power(0)

    # -- state ---------------------------------------------------------
    @abstractmethod
    def read_state(self) -> MachineState: ...
