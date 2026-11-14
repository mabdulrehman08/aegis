"""Temporal uncertainty model: freshness, confidence, location evidence, legacy parity."""

from datetime import datetime, timedelta, timezone
import itertools
import json
import math
from pathlib import Path

from pydantic import ValidationError
import pytest

from backend.app.intelligence import (
    UncertaintyPolicy,
    UncertaintyReport,
    assignment_exclusions,
    earliest_transition,
    evaluate_uncertainty,
)
from backend.app.models import Casualty, CVObservation, CVTrack, Position, Scenario
from optimization.models import OptimizationRequest
from optimization.solver import _observation_reasons
from scripts import evaluate_uncertainty as harness

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
HOME = Position(frame_id="world", x_m=4.5, y_m=2.5)
STALE = ("observation_stale", "track_stale")


def at(seconds):
    return NOW + timedelta(seconds=seconds)


def observation(observed=0, seen=None, confidence=0.9, state="visible", position=HOME):
    track = None
    if state is not None:
        last_seen = at(observed if seen is None else seen)
        track = CVTrack(track_id="track-1", state=state,
                        first_seen_at=min(last_seen, at(-300)), last_seen_at=last_seen)
    return CVObservation(id="obs-1", source="cv", observed_at=at(observed), position=position,
                         kind="person_detected", confidence=confidence, track=track)


def evaluate(obs, offset=0, policy=None, **kwargs):
    kwargs.setdefault("authoritative_position", HOME)
    return evaluate_uncertainty(obs, at(offset), policy, **kwargs)


def legacy(obs, offset, policy=UncertaintyPolicy()):
    scenario = Scenario(
        schema_version="1.0", id="s", name="s", synthetic=True, frame_id="world", created_at=NOW,
        casualties=[Casualty(id="c", position=HOME, medical_assessment=None)], responders=[])
    request = OptimizationRequest(
        scenario=scenario, now=at(offset), observations={} if obs is None else {"c": obs},
        freshness_seconds=policy.freshness_seconds, min_confidence=policy.min_confidence,
        position_tolerance_m=policy.position_tolerance_m)
    return _observation_reasons(scenario.casualties[0], request)


def test_default_policy_mirrors_solver_thresholds():
    policy = UncertaintyPolicy()
    request_fields = OptimizationRequest.model_fields
    assert policy.freshness_seconds == request_fields["freshness_seconds"].default == 30
    assert policy.min_confidence == request_fields["min_confidence"].default == 0.5
    assert policy.position_tolerance_m == request_fields["position_tolerance_m"].default == 0.25
    assert policy.decay_gates_eligibility is False
    assert policy.allow_zone_estimates is True


@pytest.mark.parametrize("bad", [
    {"freshness_seconds": -1}, {"freshness_seconds": math.inf}, {"min_confidence": 1.5},
    {"min_confidence": math.nan}, {"position_tolerance_m": -0.1},
    {"confidence_half_life_seconds": 0}, {"unknown": 1},
])
def test_policy_rejects_invalid_values(bad):
    with pytest.raises(ValidationError):
        UncertaintyPolicy(**bad)


def test_policy_and_report_are_immutable():
    report = evaluate(observation())
    with pytest.raises(ValidationError):
        UncertaintyPolicy().min_confidence = 0.1
    with pytest.raises(ValidationError):
        report.eligible_for_new_assignment = False


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_uncertainty(observation(), datetime(2026, 11, 13, 12))


def test_fresh_visible_detection_is_observed_and_eligible():
    report = evaluate(observation(), 10, location_basis="calibrated_world")
    assert isinstance(report, UncertaintyReport)
    assert report.eligible_for_new_assignment and report.reasons == ()
    assert report.location_state == "observed"
    assert report.location_evidence == "cv_calibrated_world"
    assert report.last_reported_position == HOME
    assert report.observation_id == "obs-1" and report.track_id == "track-1"
    assert report.track_state == "visible"
    assert report.report_age_seconds == report.detection_age_seconds == 10
    assert report.next_transition_at == at(30)
    assert report.evaluated_at == at(10)


def test_raw_confidence_is_preserved_and_ranking_score_halves_per_half_life():
    fresh, aged = evaluate(observation(), 0), evaluate(observation(), 30)
    assert fresh.raw_confidence == aged.raw_confidence == 0.9
    assert fresh.ranking_score == pytest.approx(0.9)
    assert aged.ranking_score == pytest.approx(0.45)
    assert aged.ranking_score < fresh.ranking_score
    custom = evaluate(observation(), 10, UncertaintyPolicy(confidence_half_life_seconds=10))
    assert custom.ranking_score == pytest.approx(0.45)


def test_confidence_threshold_is_inclusive():
    assert evaluate(observation(confidence=0.5)).eligible_for_new_assignment
    report = evaluate(observation(confidence=0.4999))
    assert report.reasons == ("observation_low_confidence",)
    assert report.location_state == "observed"


def test_freshness_boundary_is_conservative_and_settles():
    assert evaluate(observation(), 29.999999).eligible_for_new_assignment
    before = evaluate(observation(), 29.999999)
    assert before.next_transition_at == at(30)
    boundary = evaluate_uncertainty(observation(), before.next_transition_at,
                                    authoritative_position=HOME)
    assert boundary.reasons == STALE
    assert boundary.location_state == "unknown"
    assert boundary.next_transition_at is None
    assert legacy(observation(), 30) == []
    assert legacy(observation(), 30.000001) == list(STALE)


def test_future_dated_report_and_detection_are_excluded_until_their_stamp():
    report = evaluate(observation(observed=10), 0)
    assert report.reasons == ("observation_future_dated", "track_future_dated")
    assert report.location_state == "unknown"
    assert report.report_age_seconds == report.detection_age_seconds == -10
    assert report.next_transition_at == at(10)
    arrived = evaluate(observation(observed=10), 10)
    assert arrived.eligible_for_new_assignment
    assert arrived.next_transition_at == at(40)


def test_age_is_measured_from_both_report_and_last_detection():
    obs = observation(observed=0, seen=-25)
    report = evaluate(obs, 0)
    assert report.report_age_seconds == 0 and report.detection_age_seconds == 25
    assert report.eligible_for_new_assignment
    assert report.next_transition_at == at(5)
    assert report.ranking_score == pytest.approx(0.9 * 0.5 ** (25 / 30))
    later = evaluate(obs, 5)
    assert later.reasons == ("track_stale",)
    assert later.location_state == "unknown"
    assert later.next_transition_at == at(30)


@pytest.mark.parametrize("state,location_state", [("occluded", "estimated"), ("lost", "unknown")])
def test_occluded_and_lost_tracks_are_never_newly_assignable(state, location_state):
    for policy in (UncertaintyPolicy(), UncertaintyPolicy(min_confidence=0, freshness_seconds=1e6)):
        report = evaluate(observation(state=state, confidence=1.0), 0, policy)
        assert not report.eligible_for_new_assignment
        assert f"track_{state}" in report.reasons
        assert report.location_state == location_state
        assert report.last_reported_position == HOME


def test_recovery_after_loss_restores_eligibility_from_new_report():
    assert not evaluate(observation(state="lost"), 0).eligible_for_new_assignment
    assert evaluate(observation(observed=5, state="occluded"), 5).reasons == ("track_occluded",)
    recovered = evaluate(observation(observed=10), 10)
    assert recovered.eligible_for_new_assignment and recovered.location_state == "observed"


def test_observation_without_track_uses_report_time_for_detection():
    report = evaluate(observation(state=None), 12, location_basis=None)
    assert report.track_id is None and report.track_state is None
    assert report.detection_age_seconds == report.report_age_seconds == 12
    assert report.location_evidence == "cv_unspecified"
    assert evaluate(observation(state=None), 30).reasons == ("observation_stale",)


def test_zone_estimate_is_labelled_and_optionally_refused():
    default = evaluate(observation(), 0, location_basis="zone_estimate")
    assert default.eligible_for_new_assignment
    assert default.location_state == "estimated"
    assert default.location_evidence == "cv_zone_estimate"
    strict = evaluate(observation(), 0, UncertaintyPolicy(allow_zone_estimates=False),
                      location_basis="zone_estimate")
    assert strict.reasons == ("zone_estimate_not_allowed",)
    calibrated = evaluate(observation(), 0, UncertaintyPolicy(allow_zone_estimates=False),
                          location_basis="calibrated_world")
    assert calibrated.eligible_for_new_assignment


@pytest.mark.parametrize("position,mismatch", [
    (Position(frame_id="world", x_m=4.7, y_m=2.5), False),
    (Position(frame_id="world", x_m=4.75, y_m=2.5), False),
    (Position(frame_id="world", x_m=4.8, y_m=2.5), True),
    (Position(frame_id="camera", x_m=4.5, y_m=2.5), True),
])
def test_position_mismatch_against_authoritative_position(position, mismatch):
    report = evaluate(observation(position=position))
    assert (report.reasons == ("observation_position_mismatch",)) is mismatch
    assert report.last_reported_position == position


def test_missing_observation_uses_human_known_position_without_exclusion():
    report = evaluate_uncertainty(None, at(3600), authoritative_position=HOME)
    assert report.eligible_for_new_assignment and report.reasons == ()
    assert report.location_evidence == "human_known"
    assert report.location_state == "observed"
    assert report.last_reported_position == HOME
    assert report.raw_confidence is None and report.ranking_score is None
    assert report.next_transition_at is None


def test_missing_observation_and_position_is_unknown_and_excluded():
    report = evaluate_uncertainty(None, NOW)
    assert report.reasons == ("location_unknown",)
    assert report.location_state == "unknown" and report.location_evidence == "none"
    assert report.last_reported_position is None


def test_decay_gate_is_opt_in_and_reports_exact_cutoff():
    assert evaluate(observation(), 26).eligible_for_new_assignment
    policy = UncertaintyPolicy(decay_gates_eligibility=True)
    cutoff = at(30 * math.log2(0.9 / 0.5))
    early = evaluate(observation(), 0, policy)
    assert early.eligible_for_new_assignment
    assert abs((early.next_transition_at - cutoff).total_seconds()) <= 1e-6
    crossed = evaluate_uncertainty(observation(), early.next_transition_at, policy,
                                   authoritative_position=HOME)
    assert crossed.reasons == ("decayed_confidence_below_threshold",)
    assert crossed.ranking_score == pytest.approx(0.5, abs=1e-6)
    assert crossed.next_transition_at == at(30)
    just_before = evaluate_uncertainty(observation(), early.next_transition_at - timedelta(microseconds=1),
                                       policy, authoritative_position=HOME)
    assert just_before.eligible_for_new_assignment
    at_threshold = evaluate(observation(confidence=0.5), 0, policy)
    assert at_threshold.reasons == ("decayed_confidence_below_threshold",)
    zero_floor = evaluate(observation(confidence=0.0), 0,
                          UncertaintyPolicy(min_confidence=0, decay_gates_eligibility=True))
    assert zero_floor.eligible_for_new_assignment


def test_extreme_policies_do_not_overflow():
    huge = UncertaintyPolicy(freshness_seconds=1e300, confidence_half_life_seconds=1e300,
                             decay_gates_eligibility=True, min_confidence=1e-300)
    report = evaluate(observation(), 1e6, huge)
    assert report.eligible_for_new_assignment
    assert report.next_transition_at is None
    tiny = UncertaintyPolicy(confidence_half_life_seconds=1e-300)
    assert evaluate(observation(), 1, tiny).ranking_score == 0.0
    instant = evaluate(observation(), 0, UncertaintyPolicy(freshness_seconds=0))
    assert instant.reasons == STALE


def test_evaluation_is_pure_and_deterministic():
    obs = observation(observed=0, seen=-5, state="occluded")
    snapshot = obs.model_dump()
    first = evaluate(obs, 7, location_basis="zone_estimate")
    second = evaluate(obs, 7, location_basis="zone_estimate")
    assert first == second
    assert obs.model_dump() == snapshot


def _transition_grid():
    observed_offsets = (-40, -29, 0, 5, 30)
    seen_deltas = (0, -10, -29)
    confidences = (0.0, 0.49, 0.5, 0.9, 1.0)
    states = ("visible", "occluded", "lost", None)
    positions = (HOME, Position(frame_id="world", x_m=4.8, y_m=2.5))
    for observed, delta, confidence, state, position in itertools.product(
            observed_offsets, seen_deltas, confidences, states, positions):
        if state is None and delta:
            continue
        yield observation(observed=observed, seen=observed + delta, confidence=confidence,
                          state=state, position=position)


@pytest.mark.parametrize("policy", [UncertaintyPolicy(), UncertaintyPolicy(freshness_seconds=12.5)])
def test_default_semantics_never_widen_legacy_solver_eligibility(policy):
    offsets = (-1, 0, 4.999999, 5, 5.000001, 12.5, 29.999999, 30, 30.000001, 31, 75)
    compared = boundary_only = 0
    for obs, offset in itertools.product(_transition_grid(), offsets):
        report = evaluate(obs, offset, policy)
        legacy_reasons = legacy(obs, offset, policy)
        compared += 1
        if report.eligible_for_new_assignment:
            assert legacy_reasons == []
        if list(report.reasons) != legacy_reasons:
            boundary_only += 1
            stamps = [obs.observed_at] + ([obs.track.last_seen_at] if obs.track else [])
            assert at(offset) in [stamp + timedelta(seconds=policy.freshness_seconds)
                                  for stamp in stamps]
    assert compared > 1000 and boundary_only > 0


@pytest.mark.parametrize("policy", [
    UncertaintyPolicy(), UncertaintyPolicy(decay_gates_eligibility=True),
    UncertaintyPolicy(decay_gates_eligibility=True, confidence_half_life_seconds=7, min_confidence=0.3),
])
def test_next_transition_is_exact_for_time_driven_reason_changes(policy):
    for obs, offset in itertools.product(_transition_grid(), (-50, 0, 3, 29)):
        report = evaluate(obs, offset, policy)
        transition = report.next_transition_at
        if transition is None:
            far = evaluate_uncertainty(obs, at(offset) + timedelta(days=365), policy,
                                       authoritative_position=HOME)
            assert far.reasons == report.reasons
            continue
        assert transition > report.evaluated_at
        before = transition - timedelta(microseconds=1)
        if before > report.evaluated_at:
            middle = report.evaluated_at + (before - report.evaluated_at) / 2
            for instant in (middle, before):
                assert evaluate_uncertainty(obs, instant, policy,
                                            authoritative_position=HOME).reasons == report.reasons
        after = evaluate_uncertainty(obs, transition, policy, authoritative_position=HOME)
        assert after.reasons != report.reasons
        assert after.next_transition_at is None or after.next_transition_at > transition


def test_assignment_exclusions_and_earliest_transition():
    reports = {
        "casualty-b": evaluate(observation(state="lost"), 0),
        "casualty-a": evaluate(observation(confidence=0.1), 0),
        "casualty-c": evaluate(observation(observed=0, seen=-20), 0),
        "casualty-d": evaluate_uncertainty(None, NOW, authoritative_position=HOME),
    }
    assert assignment_exclusions(reports) == {
        "casualty-a": ["observation_low_confidence"],
        "casualty-b": ["track_lost"],
    }
    assert list(assignment_exclusions(reports)) == ["casualty-a", "casualty-b"]
    assert earliest_transition(reports.values()) == at(10)
    assert earliest_transition([reports["casualty-d"]]) is None
    assert assignment_exclusions({}) == {} and earliest_transition([]) is None


def test_report_serializes_to_json():
    payload = evaluate(observation(), 0, location_basis="zone_estimate").model_dump(mode="json")
    assert payload["reasons"] == []
    assert payload["next_transition_at"] == "2026-11-13T12:00:30Z"
    assert payload["location_evidence"] == "cv_zone_estimate"


def test_evaluation_harness_meets_expectations_and_matches_committed_reports(tmp_path):
    report = harness.build_report()
    summary = report["summary"]
    assert summary["passed"] == summary["steps"] > 30
    assert summary["wider_than_legacy"] == 0
    assert summary["eligible"] + summary["excluded"] == summary["steps"]
    assert {"track_occluded", "track_lost", "observation_stale", "observation_future_dated",
            "observation_low_confidence", "observation_position_mismatch", "location_unknown",
            "zone_estimate_not_allowed", "decayed_confidence_below_threshold"} <= set(
                summary["exclusions_by_reason"])
    root = Path(__file__).resolve().parents[2]
    committed = json.loads((root / "reports" / "uncertainty.json").read_text())
    assert committed == json.loads(json.dumps(report))
    assert harness.main(["--check", "--output-dir", str(root / "reports")]) == 0
    assert harness.main(["--output-dir", str(tmp_path)]) == 0
    assert (tmp_path / "uncertainty.md").read_text() == (root / "reports" / "uncertainty.md").read_text()
