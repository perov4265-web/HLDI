"""
Device configuration model.

Port of the ``SCNF`` structure from ``INC/common.forth``.  The original stored
these values in the Windows registry under ``software\\HLDI\\`` (see
``Main`` in ``hldi.forth``); here they are persisted to a JSON file in the
user's config directory, which is portable across platforms.

All linear quantities are kept in microns (the firmware's internal unit), and
the step/position conversions mirror ``hardctrl.forth`` (``PosToSteps`` /
``XToPos``): ``steps = pos_um * resolution * qual / unit``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class MotionConfig:
    """Per-axis calibration used by the step<->micron conversions.

    Defaults follow the readme: the carriage encoder is 600 dots per 25400 µm
    (one inch — typical of the donor printers; some are 720), and the table is
    a 1.8°/step motor on a 1 mm-pitch screw → 400 steps per 1000 µm.

    The v056 changelog noted error *accumulating* when converting position →
    steps → position repeatedly.  To match that fix, the inverse conversion
    rounds to the nearest micron so a value never drifts on round-trips.
    """

    unit_x: int = 25400         # microns over which resolution_x dots occur
    unit_y: int = 1000          # microns over which resolution_y steps occur
    resolution_x: int = 600     # encoder dots (ResolutionX)
    resolution_y: int = 400     # motor steps   (ResolutionY)
    qual: int = 1               # sub-step quality multiplier (Qual / "Увел точ")

    def pos_to_steps_x(self, pos_um: float) -> int:
        return round(pos_um * self.resolution_x * self.qual / self.unit_x)

    def pos_to_steps_y(self, pos_um: float) -> int:
        return round(pos_um * self.resolution_y * self.qual / self.unit_y)

    def steps_to_pos_x(self, steps: int) -> float:
        return round(steps * self.unit_x / (self.resolution_x * self.qual))

    def steps_to_pos_y(self, steps: int) -> float:
        return round(steps * self.unit_y / (self.resolution_y * self.qual))


@dataclass
class DeviceConfig:
    """Mirror of the SCNF configuration block."""

    # --- serial link ---
    num_com_port: int = 1           # NumComPort
    speed_com: int = 115200         # SpeedCom (baud)

    # --- exposure / motion speeds (mm/s unless noted) ---
    speed_jog_x: int = 5000         # SpeedJogX  ("X своб" — idle carriage moves)
    speed_exp_f: int = 3000         # SpeedExpF  ("X прям" — print speed)
    speed_exp_b: int = 8000         # SpeedExpB  ("X обр"  — return, reserved)
    speed_jog_y: int = 4000         # "Y своб" — table stepper speed (steps/s)
    repeat_cnt: int = 1             # RepeatCNT ("Повт/Одн" — reserved)
    bidirectional: bool = True      # двунаправленная печать (readme: only mode now)

    # --- laser power mode ("мс/К*") ---
    # mode 0 = "К*": fraction of max power (0.001..1.0)
    # mode 1 = "мс": exposure time per pixel in ms (0.001..1.0)
    # the two are recomputed from each other using the current exposure speed.
    power_mode: int = 0             # 0 = K*, 1 = ms
    power_k: float = 0.5            # K* coefficient
    pixel_ms: float = 0.1           # exposure time per pixel, ms

    # --- image transforms (main-window buttons) ---
    negative: bool = False          # "негат"
    mirror_x: bool = False          # "X зерк"
    mirror_y: bool = False          # "Y зерк"

    # --- work area (microns) ---
    max_width_pcb: int = 300000     # MaxWidthPCB
    max_height_pcb: int = 400000    # MaxHeightPCB
    over_pos_x: int = 1000          # OverPosX  ("Поле разг/торм" — accel/brake run-up)
    over_pos_y: int = 1000          # OverPosY
    offs_pos: int = 0               # OffsPos

    # --- camera alignment ---
    cam_off_x: int = 0              # CamOffX  (camera↔laser optical-axis offset, µm)
    cam_off_y: int = 0              # CamOffY
    cam_res_x: int = 640            # CamResX
    cam_res_y: int = 480            # CamResY
    cross_alpha: int = 128          # CrossAlpha (reticle transparency 0..255)

    # --- fiducials / drilling ---
    punch_dia: int = 1000           # punchdia ("Диам кернения" — unexposed centre)
    # reference-hole diameter is matched as a *range* (v056 fix: avoids a
    # mismatch when converting inch↔metric), markdia..markdiaend.
    mark_dia: int = 500             # markdia    (range low)
    mark_dia_end: int = 600         # markdiaend (range high)

    # --- motor / laser limits ---
    motor_pwr: int = 80             # MotorPWR  ("Vдв" — driver supply, %)
    motor_min: int = 0              # MotorMin  ("Vмин" — break-away voltage)
    motor_max: int = 100            # MotorMax  ("Vмакс" — max safe voltage)
    pwr_limit: int = 100            # PwrLimit  (laser power limit, /10 %)
    brake_time: int = 50            # BrakeTime ("Пауза усп" — table settle, ms)

    # --- PID speed regulator (ПИД коэфф) — defaults from firmware tab_cnf ---
    pid_kp: int = 525               # proportional ("проп",  kprp)
    pid_ki: int = 7                 # integral     ("интегр", kint)
    pid_kd: int = 1                 # differential ("дифф",   kdif)
    pid_kcom: int = 1000            # common divisor (kcom)

    # --- file references ---
    name_file_cross: str = ""       # NameFileCross ("Рисунок прицела" BMP)
    name_file_cam: str = ""         # NameFileCam   ("Устройство видео")
    name_fast_fw: str = ""          # NameFastFW    (F9 quick-flash firmware)

    motion: MotionConfig = field(default_factory=MotionConfig)

    # ------------------------------------------------------------------
    # laser power mode ("мс/К*")
    #
    # The readme: switching the "мс/К*" button recomputes one into the other
    # depending on the current exposure speed.  One pixel is one dot of the
    # carriage resolution, so the time the laser dwells on a pixel is
    #     pixel_ms = pixel_pitch_mm / speed_mm_s * 1000
    # and K* (fraction of full power) trades off against that dwell — a longer
    # dwell needs less power for the same delivered energy, so we treat their
    # product (energy) as the invariant when converting.
    # ------------------------------------------------------------------
    def pixel_pitch_mm(self) -> float:
        """Carriage pixel pitch in mm (unit / resolution)."""
        m = self.motion
        return (m.unit_x / (m.resolution_x * max(1, m.qual))) / 1000.0

    def nominal_pixel_ms(self) -> float:
        """Dwell time per pixel at the current forward exposure speed."""
        speed = max(1, self.speed_exp_f)            # mm/s
        return self.pixel_pitch_mm() / speed * 1000.0

    def power_to_ms(self) -> float:
        """Convert the K* coefficient to an equivalent pixel time (ms)."""
        return self.nominal_pixel_ms() * max(0.001, min(self.power_k, 1.0))

    def ms_to_power(self) -> float:
        """Convert the pixel-time setting back to a K* coefficient."""
        nom = self.nominal_pixel_ms()
        if nom <= 0:
            return 0.0
        return max(0.001, min(self.pixel_ms / nom, 1.0))

    def effective_power_fraction(self) -> float:
        """The 0..1 laser power fraction the active mode resolves to."""
        return self.power_k if self.power_mode == 0 else self.ms_to_power()

    def is_reference_hole(self, dia_um: float) -> bool:
        """True if a hole/pad diameter falls in the reference-hole range."""
        lo, hi = sorted((self.mark_dia, self.mark_dia_end))
        return lo <= dia_um <= hi

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    @staticmethod
    def default_path() -> Path:
        base = Path.home() / ".config" / "hldi"
        base.mkdir(parents=True, exist_ok=True)
        return base / "config.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "DeviceConfig":
        path = path or cls.default_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        motion = MotionConfig(**data.pop("motion", {}))
        # ignore unknown keys so the file survives version changes
        known = {f for f in cls.__dataclass_fields__ if f != "motion"}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(motion=motion, **clean)

    def save(self, path: Path | None = None) -> None:
        path = path or self.default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
