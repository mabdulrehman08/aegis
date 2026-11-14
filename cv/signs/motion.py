"""Movement, stillness and response to a spoken command, from pose keypoints.

Keypoints are compared with their position ``motion_lag_s`` ago rather than the
previous frame, so per-frame detector jitter does not add up to "movement".
Displacements are divided by the body size, so the same threshold works near and
far from the camera.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import LIMBS, body_scale


@dataclass
class Sample:
    t: float
    keypoints: np.ndarray | None
    keypoint_conf: np.ndarray | None
    center: np.ndarray
    scale: float


def displacement(a: Sample, b: Sample, indices=None, min_conf: float = 0.5,
                 top: int | None = None) -> float | None:
    """Normalized displacement between two samples.

    By default the 75th percentile over keypoints (whole-body motion). ``top=k``
    takes the k-th largest instead, so one raised hand (wrist and elbow) counts
    while a single jittering keypoint does not.
    """
    scale = max(a.scale, b.scale)
    if a.keypoints is not None and b.keypoints is not None:
        both = (a.keypoint_conf >= min_conf) & (b.keypoint_conf >= min_conf)
        if indices is not None:
            chosen = np.zeros_like(both)
            chosen[list(indices)] = True
            both &= chosen
        if both.sum() >= 2:
            moved = np.linalg.norm(a.keypoints[both] - b.keypoints[both], axis=1)
            if top is not None:
                return float(np.sort(moved)[-min(top, len(moved))]) / scale
            return float(np.percentile(moved, 75)) / scale
        if indices is not None:
            return None
    return float(np.linalg.norm(a.center - b.center)) / scale


@dataclass
class MotionState:
    first_t: float | None = None
    last_motion_t: float | None = None
    last_measured_t: float | None = None
    level: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=120))
    command_t: float | None = None
    command_ref: Sample | None = None
    command_seen_s: float = 0.0
    command_last_t: float | None = None
    response: str = "not_tested"
    response_peak: float = 0.0


class MotionEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    def sample(self, bbox, keypoints, keypoint_conf, t: float) -> Sample:
        x1, y1, x2, y2 = bbox
        return Sample(t, None if keypoints is None else np.asarray(keypoints, dtype=float),
                      None if keypoint_conf is None else np.asarray(keypoint_conf, dtype=float),
                      np.array([(x1 + x2) / 2, (y1 + y2) / 2]),
                      body_scale(bbox, keypoints, keypoint_conf, self.min_conf))

    def update(self, state: MotionState, sample: Sample) -> None:
        settings = self.settings
        t = sample.t
        state.first_t = t if state.first_t is None else state.first_t
        reference = next((s for s in reversed(state.history) if t - s.t >= settings.motion_lag_s), None)
        state.history.append(sample)
        if reference is not None:
            moved = displacement(sample, reference, min_conf=self.min_conf)
            if moved is not None:
                limb = displacement(sample, reference, indices=LIMBS, min_conf=self.min_conf, top=2) or 0.0
                # One limb moving a lot is movement too, on the body-motion scale.
                state.level = max(moved, limb * settings.move_threshold / settings.response_threshold)
                state.last_measured_t = t
                if state.level >= settings.move_threshold:
                    state.last_motion_t = t
        if state.command_t is not None and state.response == "pending":
            self._update_response(state, sample)

    def mark_command(self, state: MotionState, t: float) -> None:
        state.command_t = t
        state.command_ref = state.history[-1] if state.history else None
        state.command_seen_s = 0.0
        state.command_last_t = None
        state.response = "pending"
        state.response_peak = 0.0

    def _update_response(self, state: MotionState, sample: Sample) -> None:
        settings = self.settings
        if state.command_ref is None:
            state.command_ref = sample
        if state.command_last_t is not None:
            state.command_seen_s += min(0.5, sample.t - state.command_last_t)
        state.command_last_t = sample.t
        moved = displacement(sample, state.command_ref, indices=LIMBS, min_conf=self.min_conf, top=2)
        if moved is not None:
            state.response_peak = max(state.response_peak, moved)
            if moved >= settings.response_threshold:
                state.response = "responded"

    def movement(self, state: MotionState, t: float) -> dict:
        if state.last_measured_t is None or t - state.last_measured_t > 1.0:
            return {"value": "not_testable", "confidence": 0.0}
        since = state.last_motion_t if state.last_motion_t is not None else state.first_t
        still_for = max(0.0, t - since)
        if still_for >= self.settings.still_after_s:
            margin = 1.0 - min(1.0, state.level / self.settings.move_threshold)
            return {"value": "still", "confidence": round(0.5 + 0.5 * margin, 2),
                    "still_for_s": round(still_for, 1)}
        return {"value": "moving", "confidence": round(min(1.0, 0.5 + state.level), 2),
                "still_for_s": 0.0}

    def response(self, state: MotionState, t: float) -> dict:
        if state.command_t is None:
            return {"value": "not_tested", "confidence": 0.0}
        if state.response == "pending" and t - state.command_t >= self.settings.command_window_s:
            seen = state.command_seen_s / self.settings.command_window_s
            state.response = "no_response" if seen >= 0.5 else "not_tested"
        if state.response == "responded":
            confidence = min(1.0, 0.6 + state.response_peak)
        elif state.response == "no_response":
            seen = min(1.0, state.command_seen_s / self.settings.command_window_s)
            confidence = 0.4 + 0.4 * seen
        elif state.response == "pending":
            return {"value": "not_tested", "confidence": 0.0}
        else:
            confidence = 0.0
        return {"value": state.response, "confidence": round(confidence, 2)}
