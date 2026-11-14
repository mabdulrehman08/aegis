import numpy as np

from cv.camera import Camera
from cv.settings import CameraSettings


class FakeCapture:
    def __init__(self, frames, opened=True):
        self.frames = list(frames)
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def release(self):
        self.released = True

    def get(self, _prop):
        return 0.0


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_camera_reconnects_after_it_stops_delivering_frames():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    captures = [FakeCapture([image]), FakeCapture([], opened=False), FakeCapture([image, image])]
    clock = Clock()
    camera = Camera(CameraSettings(reconnect_after_s=1.0, retry_interval_s=0.5), clock=clock,
                    sleep=clock.sleep, opener=lambda: captures.pop(0))

    assert camera.read() is not None and camera.connected
    frames = []
    for _ in range(500):          # never raises while disconnected
        frame = camera.read()
        if frame is not None:
            frames.append(frame)
        if len(frames) == 2:
            break
    assert len(frames) == 2
    assert camera.reconnects == 2  # one failed reopen, one successful
    assert [f.index for f in frames] == [1, 2]
    assert frames[0].t >= 1.0      # monotonic time advanced through the outage


def test_file_source_finishes_instead_of_reconnecting():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    clock = Clock()
    camera = Camera(CameraSettings(), source="clip.mp4", pace=False, clock=clock, sleep=clock.sleep,
                    opener=lambda: FakeCapture([image]))
    assert camera.read() is not None
    assert camera.read() is None and camera.finished
    assert camera.read() is None
