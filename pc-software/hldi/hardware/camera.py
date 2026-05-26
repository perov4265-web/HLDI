"""
Alignment camera.

Cross-platform port of ``INC/capture.forth``.  The original used DirectShow
(building a capture + VMR9 render graph, enumerating devices via
``СписокУстройствЗахвата``, overlaying an alignment crosshair "прицел" with
``Нарисовать_прицел_в_памяти`` and showing coordinates) to align the PCB to
the machine, using the ``CamOffX/CamOffY`` offsets from the configuration.

DirectShow is Windows-only, so this port uses OpenCV (``cv2``) as the capture
backend, which works on Windows, Linux and macOS, while keeping the same
behaviour the operator relied on:

* :meth:`CameraDevice.list_devices` - enumerate cameras (was the DirectShow
  device enumerator).
* :meth:`open` / :meth:`close` / :meth:`read_frame` - run the capture graph.
* :func:`draw_crosshair` - overlay the alignment reticle and position text,
  the port of ``Нарисовать_прицел_в_памяти`` + ``Показать_координаты``.

OpenCV is an optional dependency; if it is not installed the rest of the
application still runs and the camera panel reports that it is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

try:
    import cv2  # OpenCV
    import numpy as np
    HAVE_CV2 = True
except Exception:                       # pragma: no cover
    cv2 = None
    np = None
    HAVE_CV2 = False


@dataclass
class CameraInfo:
    index: int
    name: str


class CameraError(Exception):
    pass


class CameraDevice:
    """A single capture device, wrapping an OpenCV ``VideoCapture``."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480) -> None:
        self.index = index
        self.width = width          # VideoW
        self.height = height        # VideoH
        self._cap = None

    # -- enumeration (port of СписокУстройствЗахвата) ------------------
    @staticmethod
    def list_devices(max_probe: int = 8) -> List[CameraInfo]:
        """Probe for available cameras.

        OpenCV has no portable name enumeration, so devices are probed by
        index; the original DirectShow code likewise returned an indexed list
        with friendly names where available.
        """
        if not HAVE_CV2:
            return []
        found: List[CameraInfo] = []
        for i in range(max_probe):
            cap = cv2.VideoCapture(i)
            ok = cap.isOpened()
            cap.release()
            if ok:
                found.append(CameraInfo(index=i, name=f"Camera {i}"))
        return found

    # -- lifecycle -----------------------------------------------------
    def open(self) -> bool:
        if not HAVE_CV2:
            raise CameraError("OpenCV (cv2) is not installed")
        self._cap = cv2.VideoCapture(self.index)
        if not self._cap.isOpened():
            self._cap = None
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # update to the resolution the device actually gave us
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
        return True

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def read_frame(self):
        """Return the latest frame as an RGB numpy array, or None."""
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def grab_still(self, path: str) -> bool:
        """Capture a single frame to a file (port of the camera snapshot)."""
        frame = self.read_frame()
        if frame is None:
            return False
        cv2.imwrite(path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        return True


def draw_crosshair(frame, off_x: int = 0, off_y: int = 0,
                   pos_text: str | None = None, alpha: int = 255):
    """Overlay the alignment reticle and position text on a frame.

    Port of ``Нарисовать_прицел_в_памяти`` (cross through the centre, offset
    by the camera offset ``CamOffX``/``CamOffY``) and ``Показать_координаты``
    (machine position text in the corner).  ``off_x``/``off_y`` are in pixels
    and ``alpha`` (0..255) is the reticle transparency ("Прозрачность").
    Returns the frame (modified in place when OpenCV is present).
    """
    if not HAVE_CV2 or frame is None:
        return frame
    h, w = frame.shape[:2]
    cx, cy = w // 2 + off_x, h // 2 + off_y
    color = (233, 185, 73)          # copper, matching the UI accent (RGB)
    overlay = frame.copy()
    cv2.line(overlay, (0, cy), (w, cy), color, 1)
    cv2.line(overlay, (cx, 0), (cx, h), color, 1)
    cv2.circle(overlay, (cx, cy), 18, (255, 92, 87), 1)
    a = max(0.0, min(alpha / 255.0, 1.0))
    cv2.addWeighted(overlay, a, frame, 1 - a, 0, frame)
    if pos_text:
        cv2.putText(frame, pos_text, (10, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                    cv2.LINE_AA)
    return frame
