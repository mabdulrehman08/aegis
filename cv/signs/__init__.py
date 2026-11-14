"""Observed signs per track: posture, movement, command response, eyes, breathing, blood.

Every sign is an observation with a confidence and an explicit ``not_testable``
/ ``not_tested``. Nothing here is a diagnosis, injury type, severity or
priority; the backend may turn the signs into a *suggested* priority that a
medic confirms (see cv/CONTRACT_PROPOSAL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from cv.settings import MODELS_DIR, SignsSettings, resolve
from cv.signs.blood import BloodEstimator, BloodState
from cv.signs.breathing import BreathingEstimator, BreathingState
from cv.signs.eyes import EyeEstimator, EyeState, FaceScorer
from cv.signs.motion import MotionEstimator, MotionState

SCHEMA = "aegis-cv-signs/0.1"


@dataclass
class _TrackSigns:
    motion: MotionState = field(default_factory=MotionState)
    blood: BloodState = field(default_factory=BloodState)
    eyes: EyeState = field(default_factory=EyeState)
    breathing: BreathingState = field(default_factory=BreathingState)


class SignsEstimator:
    def __init__(self, settings: SignsSettings, iso: Callable[[float], str],
                 keypoint_min_conf: float = 0.5, face_scorer: FaceScorer | None = None):
        self.settings = settings
        self.iso = iso
        self.motion = MotionEstimator(settings, keypoint_min_conf)
        self.blood = BloodEstimator(settings, keypoint_min_conf)
        self.breathing = BreathingEstimator(settings, keypoint_min_conf)
        self.eyes = EyeEstimator(settings, face_scorer or FaceScorer(resolve(settings.eyes_model, MODELS_DIR)),
                                 keypoint_min_conf)
        self.states: dict[str, _TrackSigns] = {}
        self.command_t: float | None = None

    def mark_command(self, t: float) -> None:
        """The operator just gave a spoken command ("raise your hand if you hear me")."""
        self.command_t = t
        for state in self.states.values():
            self.motion.mark_command(state.motion, t)

    def update(self, image, tracks: Sequence, t: float) -> None:
        live = {track.track_id for track in tracks}
        for track_id in list(self.states):
            if track_id not in live:
                del self.states[track_id]
        for track in tracks:
            if not track.detected:
                continue
            state = self.states.setdefault(track.track_id, _TrackSigns())
            sample = self.motion.sample(track.bbox, track.keypoints, track.keypoint_conf, t)
            self.motion.update(state.motion, sample)
            still = self.motion.movement(state.motion, t)["value"] == "still"
            if self.settings.breathing:
                self.breathing.update(state.breathing, image, track.keypoints, track.keypoint_conf, still, t)
            if self.settings.eyes and self.eyes.due(state.eyes, t):
                self.eyes.update(state.eyes, image, track.keypoints, track.keypoint_conf, t)
            if self.settings.blood and self.blood.due(state.blood, t):
                self.blood.update(state.blood, image, track.bbox, track.keypoints, track.keypoint_conf, t)

    def snapshot(self, track, t: float) -> dict | None:
        state = self.states.get(track.track_id)
        if state is None:
            return None
        signs = {
            "schema": SCHEMA,
            "window_s": self.settings.window_s,
            "posture": {"value": track.posture.label, "confidence": round(track.posture.confidence, 2)},
            "movement": self.motion.movement(state.motion, t),
            "response": self.motion.response(state.motion, t),
        }
        if state.motion.command_t is not None:
            signs["response"]["command_at"] = self.iso(state.motion.command_t)
        if self.settings.eyes:
            signs["eyes"] = self.eyes.value(state.eyes, t)
        if self.settings.breathing:
            signs["breathing"] = self.breathing.value(state.breathing, t)
        if self.settings.blood:
            signs["visible_blood"] = self.blood.value(state.blood, t)
        return signs

    def listening(self, track_id: str) -> bool:
        state = self.states.get(track_id)
        return state is not None and state.motion.response == "pending"


def describe(signs: dict | None, listening: bool = False) -> list[str]:
    """Short overlay lines for one track's signs."""
    if not signs:
        return []
    movement = signs["movement"]
    move = (f"still {movement['still_for_s']:.0f}s" if movement["value"] == "still"
            else movement["value"].replace("_", " "))
    response = "listening..." if listening else signs["response"]["value"].replace("_", " ")
    lines = [f"{move} | response: {response}"]
    parts = []
    if "eyes" in signs:
        parts.append(f"eyes {signs['eyes']['value'].replace('_', ' ')}")
    if "breathing" in signs:
        breathing = signs["breathing"]
        parts.append(f"breath {breathing['rate_bpm']:.0f}/min" if breathing.get("rate_bpm")
                     else f"breath {breathing['value'].replace('_', ' ')}")
    if parts:
        lines.append(" | ".join(parts))
    blood = signs.get("visible_blood")
    if blood:
        lines.append("blood: " + (", ".join(blood["regions"]) if blood["value"] == "detected"
                                  else blood["value"].replace("_", " ")))
    return lines
