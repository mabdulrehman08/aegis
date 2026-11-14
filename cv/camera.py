"""Webcam or video-file frame source that never raises in the video loop.

A live camera that stops delivering frames is released and reopened every
``retry_interval_s``; ``read()`` returns ``None`` meanwhile. A file source is
paced to its FPS by default (``fps_override``, e.g. the measured FPS from a
recording's sidecar, wins over the container value) so time-based logic behaves
as it would live. ``loop`` restarts a file at its end, keeping the pacing continuous.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from cv.settings import CameraSettings

log = logging.getLogger(__name__)


@dataclass
class Frame:
    image: Any
    t: float        # monotonic capture time (s)
    index: int


class Camera:
    def __init__(self, settings: CameraSettings, source: str | None = None, *, pace: bool = True,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 opener: Callable[[], Any] | None = None,
                 fps_override: float | None = None, loop: bool = False):
        self.settings = settings
        self.source = source
        self.pace = pace
        self.fps_override = fps_override
        self.loop = loop
        self.loops = 0
        self._frames_this_pass = 0
        self._clock = clock
        self._sleep = sleep
        self._opener = opener or self._open_cv
        self._cap: Any = None
        self._failing_since: float | None = None
        self._next_retry = 0.0
        self._file_fps = 0.0
        self._file_start: float | None = None
        self.index = 0
        self.status = "closed"     # connected / reconnecting / finished / closed
        self.reconnects = 0
        self.resolution: tuple[int, int] | None = None

    @property
    def label(self) -> str:
        return self.source or f"camera:{self.settings.index}"

    @property
    def is_file(self) -> bool:
        return self.source is not None

    @property
    def finished(self) -> bool:
        return self.status == "finished"

    @property
    def connected(self) -> bool:
        return self.status == "connected"

    def _open_cv(self) -> Any:
        import cv2

        if self.source:
            return cv2.VideoCapture(self.source)
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.settings.index, backend)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def open(self) -> bool:
        self._release_capture()
        try:
            capture = self._opener()
        except Exception as error:  # a broken backend must not crash the loop
            log.warning("Opening %s failed: %s", self.label, error)
            capture = None
        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            if self.is_file:
                log.error("Cannot open video file %s", self.source)
                self.status = "finished"
            else:
                self.status = "reconnecting"
                self._next_retry = self._clock() + self.settings.retry_interval_s
            return False
        self._cap = capture
        self.status = "connected"
        self._failing_since = None
        if self.is_file:
            import cv2

            self._frames_this_pass = 0
            fps = self.fps_override or float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            self._file_fps = fps if 1.0 <= fps <= 240.0 else 30.0
        return True

    @property
    def file_fps(self) -> float:
        return self._file_fps

    def read(self) -> Frame | None:
        if self.finished:
            return None
        if self._cap is None:
            if self._clock() >= self._next_retry:
                if self.status == "reconnecting":
                    self.reconnects += 1
                self.open()
            if self._cap is None:
                self._sleep(0.02)
                return None
        try:
            ok, image = self._cap.read()
        except Exception as error:
            log.warning("Reading %s failed: %s", self.label, error)
            ok, image = False, None
        now = self._clock()
        if ok and image is not None:
            self._failing_since = None
            self.resolution = (int(image.shape[1]), int(image.shape[0]))
            if self.is_file and self.pace:
                if self._file_start is None:
                    self._file_start = now
                delay = self._file_start + self.index / self._file_fps - now
                if delay > 0:
                    self._sleep(delay)
                    now = self._clock()
            frame = Frame(image=image, t=now, index=self.index)
            self.index += 1
            self._frames_this_pass += 1
            return frame
        if self.is_file:
            self._release_capture()
            if self.loop and self._frames_this_pass > 0 and self.open():
                self.loops += 1
                return None
            self.status = "finished"
            return None
        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since >= self.settings.reconnect_after_s:
            log.warning("%s stopped delivering frames; reconnecting", self.label)
            self._release_capture()
            self.status = "reconnecting"
            self._next_retry = now
        else:
            self._sleep(0.01)
        return None

    def _release_capture(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def release(self) -> None:
        self._release_capture()
        if not self.finished:
            self.status = "closed"
