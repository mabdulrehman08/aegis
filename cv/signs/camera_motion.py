"""Camera motion from the background, for body-worn and handheld cameras.

Corners are found outside the people's boxes in a downscaled gray frame and
followed into the next frame with pyramidal Lucas-Kanade. A RANSAC similarity
fit gives the frame-to-frame rotation and the shift of the frame center, in
pixels at 1280 px width. The signed shifts are summed over ``CHUNK_S`` chunks
(tracking noise cancels, real motion adds up) and the fastest chunk of the last
``camera_window_s`` sets the state:

- ``still``: at most ``camera_still_px_s`` (tripod, or the wearer holding still)
- ``steady``: at most ``camera_steady_px_s`` (body sway, a handheld phone)
- ``moving``: faster (walking, turning)
- ``unknown``: too few background points, e.g. a casualty filling the frame

The cumulative rotation (roll) lets movement be measured in a body frame that
does not turn with the camera.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

WIDTH = 320
REFERENCE_WIDTH = 1280
MIN_POINTS = 12
CHUNK_S = 0.25
STATES = ("still", "steady", "moving", "unknown")


@dataclass
class CameraMotionState:
    previous: np.ndarray | None = None
    previous_t: float | None = None
    steps: deque = field(default_factory=deque)     # (t, dt, (dx, dy) at 1280 width or None)
    roll_deg: float = 0.0
    state: str = "unknown"
    speed_px_s: float | None = None
    last_shift_px: tuple[float, float] | None = None   # latest frame-to-frame shift, image pixels


class CameraMotionEstimator:
    def __init__(self, settings):
        self.settings = settings

    def update(self, state: CameraMotionState, image_bgr, boxes, t: float) -> str:
        import cv2

        height, width = image_bgr.shape[:2]
        factor = WIDTH / width
        gray = cv2.cvtColor(cv2.resize(image_bgr, (WIDTH, max(8, round(height * factor)))),
                            cv2.COLOR_BGR2GRAY)
        if state.previous is not None and state.previous.shape == gray.shape and t > state.previous_t:
            shift, roll = measure(state.previous, gray, boxes, factor)
            scale = REFERENCE_WIDTH / WIDTH
            state.steps.append((t, t - state.previous_t,
                                None if shift is None else (shift[0] * scale, shift[1] * scale)))
            state.last_shift_px = None if shift is None else (shift[0] / factor, shift[1] / factor)
            state.roll_deg += roll
        else:
            state.last_shift_px = None
        state.previous, state.previous_t = gray, t
        while state.steps and t - state.steps[0][0] > self.settings.camera_window_s:
            state.steps.popleft()
        state.state, state.speed_px_s = classify(state.steps, self.settings)
        return state.state

    @staticmethod
    def value(state: CameraMotionState) -> dict:
        result = {"value": state.state}
        if state.speed_px_s is not None:
            result["speed_px_s"] = round(state.speed_px_s, 1)
        return result


def measure(previous, current, boxes, factor: float):
    """(center shift (dx, dy) in downscaled pixels or None if unknown, rotation in degrees)."""
    import cv2

    mask = np.full(previous.shape, 255, dtype=np.uint8)
    for x1, y1, x2, y2 in boxes:
        mask[max(0, int(y1 * factor)):int(math.ceil(y2 * factor)) + 1,
             max(0, int(x1 * factor)):int(math.ceil(x2 * factor)) + 1] = 0
    corners = cv2.goodFeaturesToTrack(previous, maxCorners=200, qualityLevel=0.01,
                                      minDistance=8, mask=mask, blockSize=5)
    if corners is None or len(corners) < MIN_POINTS:
        return None, 0.0
    tracked, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, corners, None,
                                                  winSize=(21, 21), maxLevel=3)
    ok = status.reshape(-1) == 1
    if ok.sum() < MIN_POINTS:
        return None, 0.0
    matrix, inliers = cv2.estimateAffinePartial2D(corners.reshape(-1, 2)[ok], tracked.reshape(-1, 2)[ok],
                                                  method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if matrix is None or inliers is None or inliers.sum() < MIN_POINTS:
        return None, 0.0
    center = np.array([previous.shape[1] / 2, previous.shape[0] / 2, 1.0])
    dx, dy = matrix @ center - center[:2]
    return (float(dx), float(dy)), math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))


def classify(steps, settings) -> tuple[str, float | None]:
    """State and the fastest chunk speed (px/s at 1280 width) over the window."""
    speeds, chunk_t, dx, dy, start = [], 0.0, 0.0, 0.0, None
    for t, dt, shift in steps:
        if shift is None:
            continue
        start = t - dt if start is None else start
        chunk_t, dx, dy = chunk_t + dt, dx + shift[0], dy + shift[1]
        if t - start >= CHUNK_S:
            speeds.append(math.hypot(dx, dy) / chunk_t)
            chunk_t, dx, dy, start = 0.0, 0.0, 0.0, None
    if chunk_t > 0 and (not speeds or chunk_t >= CHUNK_S / 2):
        speeds.append(math.hypot(dx, dy) / chunk_t)
    level = max(speeds) if speeds else None
    if level is not None and level > settings.camera_steady_px_s:
        return "moving", level
    if level is None or sum(1 for *_, shift in steps if shift is None) > 0.1 * len(steps):
        return "unknown", level
    if level <= settings.camera_still_px_s:
        return "still", level
    if level <= settings.camera_steady_px_s:
        return "steady", level
    return "moving", level
