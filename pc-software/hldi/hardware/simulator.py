"""
In-process controller simulator.

Implements the same :class:`~hldi.hardware.controller.Controller` interface
as the real serial backend, but models the machine state in memory so the
application is fully usable (and testable) without any hardware attached.

Movements update the simulated position immediately and report a short
travel time so the GUI's "moving" indicator behaves realistically.
"""

from __future__ import annotations

import time

from ..core.config import DeviceConfig
from .controller import Controller, MachineState


class SimController(Controller):
    """A virtual HLDI controller."""

    def __init__(self, config: DeviceConfig) -> None:
        super().__init__(config)
        self._open = False
        self._speed_x = config.speed_jog_x
        self._speed_y = config.speed_jog_x

    def open(self) -> bool:
        self._open = True
        self.state.connected = True
        return True

    def close(self) -> None:
        self._open = False
        self.state.connected = False

    def check_connect(self) -> bool:
        return self._open

    # -- motion --------------------------------------------------------
    def move_to_x(self, x_um: float) -> bool:
        x_um = self._clamp(x_um, self.config.max_width_pcb)
        self.state.x_um = x_um
        return True

    def move_to_y(self, y_um: float) -> bool:
        y_um = self._clamp(y_um, self.config.max_height_pcb)
        self.state.y_um = y_um
        return True

    def step_x(self, steps: int) -> bool:
        self.state.x_um += self.config.motion.steps_to_pos_x(steps)
        return True

    def step_y(self, steps: int) -> bool:
        self.state.y_um += self.config.motion.steps_to_pos_y(steps)
        return True

    def stop_move(self) -> None:
        self.state.moving = False

    # -- speed ---------------------------------------------------------
    def set_speed_x(self, v: int) -> bool:
        self._speed_x = v
        return True

    def set_speed_y(self, v: int) -> bool:
        self._speed_y = v
        return True

    # -- laser ---------------------------------------------------------
    def laser_power(self, n: int) -> None:
        self.state.laser_power = max(0, min(int(n), self.config.pwr_limit))

    # -- state ---------------------------------------------------------
    def read_state(self) -> MachineState:
        return self.state

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _clamp(v: float, hi: float) -> float:
        return max(0.0, min(v, hi))
