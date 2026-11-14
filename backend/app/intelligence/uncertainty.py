"""Deterministic temporal confidence and freshness model for CV observations.

This module turns one shared ``CVObservation`` (or its absence) into an
explainable ``UncertaintyReport`` at a caller-supplied instant. It is pure:
no clock reads, no I/O, no mutation, and identical inputs give identical
reports.

Semantics
---------
* Freshness is measured twice: from the report (``observed_at``) and from the
  last actual detection (``track.last_seen_at``; ``observed_at`` when there is
  no track). A stamp later than ``now`` is future-dated. A stamp is stale once
  ``now >= stamp + freshness_seconds``. The legacy solver treats the exact
  boundary instant as fresh; this model treats it as stale, so it can only be
  stricter and ``next_transition_at`` is the instant at which the change has
  already happened, never an instant that must be re-evaluated forever.
* ``raw_confidence`` is the detector value, preserved unchanged.
  ``ranking_score`` halves every ``confidence_half_life_seconds`` of detection
  age. It is a deterministic ranking heuristic, NOT a calibrated probability
  and carries no clinical or medical meaning. It gates eligibility only when
  ``decay_gates_eligibility`` is enabled, and then only by tightening.
* Positions are never extrapolated. ``last_reported_position`` is exactly the
  reported coordinate, or the authoritative human-known position when no CV
  observation exists.
* Occluded and lost tracks are never eligible for new assignments. Stale,
  future-dated, low-confidence and position-mismatched observations are
  excluded with the same reason names the legacy solver uses.
* A missing CV observation is not an exclusion when an authoritative
  human-known position exists (``location_evidence="human_known"``). Without
  one the location is unknown and the casualty is excluded.

With the default policy the eligible set is a subset of the legacy solver's
eligible set; it differs only at the exact freshness boundary instant.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
import math
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from backend.app.models import CVObservation, Position

LocationBasis = Literal["calibrated_world", "zone_estimate"]
LocationState = Literal["observed", "estimated", "unknown"]
LocationEvidence = Literal[
    "cv_calibrated_world", "cv_zone_estimate", "cv_unspecified", "human_known", "none",
]

_EVIDENCE: dict[str | None, LocationEvidence] = {
    "calibrated_world": "cv_calibrated_world",
    "zone_estimate": "cv_zone_estimate",
    None: "cv_unspecified",
}


class UncertaintyPolicy(BaseModel):
    """Validated thresholds. Defaults mirror the MVP solver (30 s, 0.5, 0.25 m).

    ``decay_gates_eligibility`` and ``allow_zone_estimates=False`` are opt-in
    tightenings; no setting can make an observation eligible that the legacy
    solver with the same thresholds would exclude.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    freshness_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 30
    min_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.5
    position_tolerance_m: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.25
    confidence_half_life_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 30
    decay_gates_eligibility: bool = False
    allow_zone_estimates: bool = True


class UncertaintyReport(BaseModel):
    """Per-casualty temporal uncertainty diagnostics at ``evaluated_at``.

    ``eligible_for_new_assignment`` is true exactly when ``reasons`` is empty.
    ``next_transition_at`` is the earliest instant after ``evaluated_at`` at
    which ``reasons`` change by time alone, or ``None`` if they never will.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: AwareDatetime
    observation_id: str | None
    track_id: str | None
    track_state: Literal["visible", "occluded", "lost"] | None
    location_state: LocationState
    location_evidence: LocationEvidence
    last_reported_position: Position | None
    raw_confidence: float | None
    ranking_score: float | None
    report_age_seconds: float | None
    detection_age_seconds: float | None
    reasons: tuple[str, ...]
    eligible_for_new_assignment: bool
    next_transition_at: AwareDatetime | None


DEFAULT_POLICY = UncertaintyPolicy()


def _shift(instant: datetime, seconds: float) -> datetime | None:
    """Return ``instant + seconds`` or ``None`` when it is beyond representable time."""
    try:
        return instant + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def _classify(stamp: datetime, now: datetime,
              policy: UncertaintyPolicy) -> tuple[str | None, list[datetime]]:
    """Classify one stamp as future_dated, stale or fresh (None) plus its upcoming changes."""
    stale_at = _shift(stamp, policy.freshness_seconds)
    if now < stamp:
        status = "future_dated"
    elif stale_at is not None and now >= stale_at:
        status = "stale"
    else:
        status = None
    return status, [instant for instant in (stamp, stale_at) if instant is not None and instant > now]


def _decay_cutoff(detected_at: datetime, confidence: float,
                  policy: UncertaintyPolicy) -> datetime | None:
    """Instant from which the ranking score is below ``min_confidence``."""
    if policy.min_confidence == 0 or confidence < policy.min_confidence:
        return None
    seconds = policy.confidence_half_life_seconds * math.log2(confidence / policy.min_confidence)
    return _shift(detected_at, seconds)


def _mismatched(reported: Position, known: Position, tolerance_m: float) -> bool:
    return (reported.frame_id != known.frame_id
            or math.hypot(reported.x_m - known.x_m, reported.y_m - known.y_m) > tolerance_m)


def evaluate_uncertainty(
    observation: CVObservation | None,
    now: datetime,
    policy: UncertaintyPolicy | None = None,
    *,
    location_basis: LocationBasis | None = None,
    authoritative_position: Position | None = None,
) -> UncertaintyReport:
    """Evaluate one casualty's CV evidence at the timezone-aware instant ``now``.

    ``location_basis`` is the camera provenance of the observation's position
    (``None`` when unspecified). ``authoritative_position`` is the human-known
    scenario position; it is the fallback location when ``observation`` is
    ``None`` and the reference for the position-mismatch check otherwise.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    policy = policy or DEFAULT_POLICY
    if observation is None:
        known = authoritative_position is not None
        return UncertaintyReport(
            evaluated_at=now, observation_id=None, track_id=None, track_state=None,
            location_state="observed" if known else "unknown",
            location_evidence="human_known" if known else "none",
            last_reported_position=authoritative_position,
            raw_confidence=None, ranking_score=None,
            report_age_seconds=None, detection_age_seconds=None,
            reasons=() if known else ("location_unknown",),
            eligible_for_new_assignment=known, next_transition_at=None,
        )

    track = observation.track
    detected_at = track.last_seen_at if track is not None else observation.observed_at
    report_age = (now - observation.observed_at).total_seconds()
    detection_age = (now - detected_at).total_seconds()
    reasons: list[str] = []
    report_status, upcoming = _classify(observation.observed_at, now, policy)
    if report_status is not None:
        reasons.append(f"observation_{report_status}")
    if observation.confidence < policy.min_confidence:
        reasons.append("observation_low_confidence")
    if authoritative_position is not None and _mismatched(
            observation.position, authoritative_position, policy.position_tolerance_m):
        reasons.append("observation_position_mismatch")
    track_status = None
    if track is not None:
        if track.state != "visible":
            reasons.append(f"track_{track.state}")
        track_status, track_upcoming = _classify(track.last_seen_at, now, policy)
        upcoming.extend(track_upcoming)
        if track_status is not None:
            reasons.append(f"track_{track_status}")
    if policy.decay_gates_eligibility:
        cutoff = _decay_cutoff(detected_at, observation.confidence, policy)
        if cutoff is not None and now >= cutoff:
            reasons.append("decayed_confidence_below_threshold")
        elif cutoff is not None:
            upcoming.append(cutoff)
    if location_basis == "zone_estimate" and not policy.allow_zone_estimates:
        reasons.append("zone_estimate_not_allowed")

    track_state = track.state if track is not None else None
    if report_status is not None or track_status is not None or track_state == "lost":
        location_state: LocationState = "unknown"
    elif track_state == "occluded" or location_basis == "zone_estimate":
        location_state = "estimated"
    else:
        location_state = "observed"
    ranking_score = observation.confidence * 0.5 ** (
        max(detection_age, 0.0) / policy.confidence_half_life_seconds)
    return UncertaintyReport(
        evaluated_at=now, observation_id=observation.id,
        track_id=track.track_id if track is not None else None, track_state=track_state,
        location_state=location_state, location_evidence=_EVIDENCE[location_basis],
        last_reported_position=observation.position,
        raw_confidence=observation.confidence, ranking_score=ranking_score,
        report_age_seconds=report_age, detection_age_seconds=detection_age,
        reasons=tuple(reasons), eligible_for_new_assignment=not reasons,
        next_transition_at=min(upcoming) if upcoming else None,
    )


def assignment_exclusions(reports: Mapping[str, UncertaintyReport]) -> dict[str, list[str]]:
    """Casualty ID to exclusion reasons for every ineligible report, sorted by ID."""
    return {casualty_id: list(report.reasons) for casualty_id, report in sorted(reports.items())
            if not report.eligible_for_new_assignment}


def earliest_transition(reports: Iterable[UncertaintyReport]) -> datetime | None:
    """Earliest ``next_transition_at`` across reports, or ``None``."""
    instants = [report.next_transition_at for report in reports
                if report.next_transition_at is not None]
    return min(instants) if instants else None
