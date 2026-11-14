"""Score tracked frames against a staged scene's ground truth.

Ground truth (``cv/eval/truth/*.yaml``) lists where each staged person is on the
floor, measured with tape in the scene frame; people stay in place for the
clip. Each frame, detected tracks with a floor position are matched one-to-one
to truth people within ``match_radius_m`` (Hungarian on distance). Without
positions (``--expected N`` only) only counts are scored.

Metrics (all measured, ``None`` when not computable):
- detection: % of frames each person is matched, and % with everyone matched
- ID switches: times a person's matched track ID changes (gaps ignored)
- mapping error: distance from the raw per-frame floor position to the truth
  point, and for the published (smoothed, deadbanded) position
- false positives: detected tracks matched to nobody
- posture agreement, when the truth lists a posture
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

POSTURES = {"standing", "sitting", "lying"}
_TRUTH_KEYS = {"scenario", "frame_id", "match_radius_m", "people", "notes"}
_PERSON_KEYS = {"id", "x_m", "y_m", "posture"}


@dataclass(frozen=True)
class TruthPerson:
    person_id: str
    x_m: float | None = None
    y_m: float | None = None
    posture: str | None = None

    @property
    def has_position(self) -> bool:
        return self.x_m is not None and self.y_m is not None


@dataclass(frozen=True)
class Truth:
    scenario: str
    frame_id: str | None
    people: tuple[TruthPerson, ...]
    match_radius_m: float = 1.0

    @property
    def positioned(self) -> bool:
        return bool(self.people) and all(p.has_position for p in self.people)


def parse_truth(data: Any) -> Truth:
    if not isinstance(data, dict):
        raise ValueError("truth file must be a mapping")
    unknown = set(data) - _TRUTH_KEYS
    if unknown:
        raise ValueError(f"truth: unknown keys {sorted(unknown)}")
    people = []
    for index, item in enumerate(data.get("people") or []):
        if not isinstance(item, dict) or set(item) - _PERSON_KEYS:
            raise ValueError(f"truth.people[{index}]: keys are {sorted(_PERSON_KEYS)}")
        posture = item.get("posture")
        if posture is not None and posture not in POSTURES:
            raise ValueError(f"truth.people[{index}].posture must be one of {sorted(POSTURES)}")
        x, y = item.get("x_m"), item.get("y_m")
        if (x is None) != (y is None):
            raise ValueError(f"truth.people[{index}]: give both x_m and y_m, or neither")
        people.append(TruthPerson(str(item.get("id") or f"P{index + 1}"),
                                  None if x is None else float(x), None if y is None else float(y),
                                  posture))
    if not people:
        raise ValueError("truth.people must list at least one person")
    return Truth(scenario=str(data.get("scenario") or "unlabeled"), frame_id=data.get("frame_id"),
                 people=tuple(people), match_radius_m=float(data.get("match_radius_m", 1.0)))


def count_truth(expected: int, scenario: str = "unlabeled") -> Truth:
    return Truth(scenario, None, tuple(TruthPerson(f"P{i + 1}") for i in range(expected)))


@dataclass
class _PersonStats:
    frames_matched: int = 0
    track_ids: list[str] = field(default_factory=list)
    switches: int = 0
    last_track: str | None = None
    last_seen_t: float | None = None
    longest_gap_s: float = 0.0
    errors_m: list[float] = field(default_factory=list)
    published_errors_m: list[float] = field(default_factory=list)
    offsets: list[tuple[float, float]] = field(default_factory=list)
    posture_total: int = 0
    posture_correct: int = 0


class Scorer:
    def __init__(self, truth: Truth):
        self.truth = truth
        self.frames = 0
        self.first_t: float | None = None
        self.last_t: float | None = None
        self.frames_all = 0
        self.frames_exact = 0
        self.detected_counts: list[int] = []
        self.false_positive_frames = 0
        self.false_positive_tracks: set[str] = set()
        self.track_ids: set[str] = set()
        self.people = {p.person_id: _PersonStats() for p in truth.people}

    def add_frame(self, t: float, tracks: Sequence[Any]) -> None:
        self.frames += 1
        self.first_t = t if self.first_t is None else self.first_t
        self.last_t = t
        detected = [track for track in tracks if track.detected]
        self.track_ids.update(track.track_id for track in detected)
        self.detected_counts.append(len(detected))
        expected = len(self.truth.people)
        self.frames_exact += len(detected) == expected
        if not self.truth.positioned:
            self.frames_all += len(detected) >= expected
            return

        mapped = [track for track in detected if track.map_xy is not None]
        matches = self._match(mapped)
        matched_tracks = {track.track_id for _, track in matches}
        for person, track in matches:
            self._record(person, track, t)
        self.frames_all += len(matches) == expected
        strays = [track.track_id for track in detected if track.track_id not in matched_tracks]
        if strays:
            self.false_positive_frames += 1
            self.false_positive_tracks.update(strays)

    def _match(self, tracks):
        from scipy.optimize import linear_sum_assignment

        people = list(self.truth.people)
        if not tracks:
            return []
        cost = np.array([[math.dist((p.x_m, p.y_m), track.map_xy) for track in tracks] for p in people])
        rows, cols = linear_sum_assignment(cost)
        return [(people[r], tracks[c]) for r, c in zip(rows, cols)
                if cost[r, c] <= self.truth.match_radius_m]

    def _record(self, person: TruthPerson, track, t: float) -> None:
        stats = self.people[person.person_id]
        if stats.last_seen_t is not None:
            stats.longest_gap_s = max(stats.longest_gap_s, t - stats.last_seen_t)
        stats.last_seen_t = t
        stats.frames_matched += 1
        if stats.last_track is not None and track.track_id != stats.last_track:
            stats.switches += 1
        if track.track_id not in stats.track_ids:
            stats.track_ids.append(track.track_id)
        stats.last_track = track.track_id
        dx, dy = track.map_xy[0] - person.x_m, track.map_xy[1] - person.y_m
        stats.errors_m.append(math.hypot(dx, dy))
        stats.offsets.append((dx, dy))
        if track.published_xy is not None:
            stats.published_errors_m.append(math.dist(track.published_xy, (person.x_m, person.y_m)))
        if person.posture is not None:
            stats.posture_total += 1
            stats.posture_correct += track.posture.label == person.posture

    def summary(self) -> dict[str, Any]:
        duration = (self.last_t - self.first_t) if self.frames > 1 else 0.0
        minutes = duration / 60.0 if duration > 0 else None
        expected = len(self.truth.people)
        result: dict[str, Any] = {
            "scenario": self.truth.scenario,
            "expected_people": expected,
            "ground_truth": "positions" if self.truth.positioned else "count",
            "frames": self.frames,
            "duration_s": round(duration, 1),
            "frames_all_detected_pct": _pct(self.frames_all, self.frames),
            "frames_exact_count_pct": _pct(self.frames_exact, self.frames),
            "mean_detected": (round(float(np.mean(self.detected_counts)), 2)
                              if self.detected_counts else None),
            "tracks_seen": len(self.track_ids),
            "extra_track_ids": max(0, len(self.track_ids) - expected),
        }
        if not self.truth.positioned:
            return result
        people = []
        errors, published, offsets = [], [], []
        switches = 0
        posture_total = posture_correct = 0
        for person in self.truth.people:
            stats = self.people[person.person_id]
            switches += stats.switches
            errors += stats.errors_m
            published += stats.published_errors_m
            offsets += stats.offsets
            posture_total += stats.posture_total
            posture_correct += stats.posture_correct
            people.append({
                "id": person.person_id,
                "posture_truth": person.posture,
                "detected_pct": _pct(stats.frames_matched, self.frames),
                "id_switches": stats.switches,
                "track_ids": len(stats.track_ids),
                "longest_gap_s": round(stats.longest_gap_s, 2) if stats.frames_matched else None,
                "error_cm_median": _cm(np.median(stats.errors_m)) if stats.errors_m else None,
                "error_cm_p90": _cm(np.percentile(stats.errors_m, 90)) if stats.errors_m else None,
                "posture_agreement_pct": _pct(stats.posture_correct, stats.posture_total),
            })
        bias = np.mean(offsets, axis=0) if offsets else None
        result.update({
            "match_radius_m": self.truth.match_radius_m,
            "id_switches": switches,
            "id_switches_per_min": round(switches / minutes, 2) if minutes else None,
            "false_positive_tracks": len(self.false_positive_tracks),
            "false_positive_frames_pct": _pct(self.false_positive_frames, self.frames),
            "mapping_error_cm_median": _cm(np.median(errors)) if errors else None,
            "mapping_error_cm_p90": _cm(np.percentile(errors, 90)) if errors else None,
            "mapping_error_cm_max": _cm(np.max(errors)) if errors else None,
            "mapping_bias_cm": [_cm(bias[0]), _cm(bias[1])] if bias is not None else None,
            "published_error_cm_median": _cm(np.median(published)) if published else None,
            "posture_agreement_pct": _pct(posture_correct, posture_total),
            "people": people,
        })
        return result


def _pct(part: int, total: int) -> float | None:
    return round(100.0 * part / total, 1) if total else None


def _cm(meters: float) -> float:
    return round(float(meters) * 100.0, 1)
