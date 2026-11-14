# Temporal perception uncertainty

`backend/app/intelligence/uncertainty.py` is a pure, deterministic model that
turns one shared `CVObservation` (or its absence) into an explainable
per-casualty `UncertaintyReport`. It reads no clock, performs no I/O, mutates
nothing, and trains nothing. It changes no API, auth, dispatch, simulation or
solver behaviour on its own; integration is opt-in and owned by the caller.

## Interface

```python
from backend.app.intelligence.uncertainty import (
    UncertaintyPolicy, UncertaintyReport,
    evaluate_uncertainty, assignment_exclusions, earliest_transition,
)

report = evaluate_uncertainty(
    observation,                 # CVObservation | None
    now,                         # timezone-aware datetime (naive raises ValueError)
    policy,                      # UncertaintyPolicy | None (None -> defaults)
    location_basis="calibrated_world",   # "calibrated_world" | "zone_estimate" | None
    authoritative_position=casualty.position,  # Position | None
)
exclusions = assignment_exclusions({casualty_id: report, ...})  # {id: [reason, ...]} ineligible only
horizon = earliest_transition(reports.values())                 # datetime | None
```

`UncertaintyPolicy` (frozen, `extra="forbid"`, finite values only):

| Field | Default | Effect |
|---|---|---|
| `freshness_seconds` | 30 | Report and detection freshness window (>= 0) |
| `min_confidence` | 0.5 | Raw confidence floor, inclusive |
| `position_tolerance_m` | 0.25 | Max distance from `authoritative_position` |
| `confidence_half_life_seconds` | 30 | Half-life of `ranking_score` (> 0) |
| `decay_gates_eligibility` | false | Opt-in: exclude once `ranking_score` would fall below `min_confidence` |
| `allow_zone_estimates` | true | Opt-in tightening when false: exclude `zone_estimate` locations |

`UncertaintyReport` (frozen) fields: `evaluated_at`, `observation_id`,
`track_id`, `track_state`, `location_state`, `location_evidence`,
`last_reported_position`, `raw_confidence`, `ranking_score`,
`report_age_seconds`, `detection_age_seconds`, `reasons` (tuple),
`eligible_for_new_assignment` (true exactly when `reasons` is empty) and
`next_transition_at`.

## Rules

* **Two ages.** `report_age_seconds = now - observed_at`;
  `detection_age_seconds = now - track.last_seen_at` (or `observed_at` with no
  track). Both are checked; a fresh report that re-sends an old detection is
  excluded as `track_stale`. Negative ages are future-dated.
* **Boundaries.** A stamp is future-dated while `now < stamp` and stale once
  `now >= stamp + freshness_seconds`. The legacy solver treats the exact
  boundary instant as fresh (`age > freshness`); this model is stricter at
  that single instant so the reported transition has already happened when
  it is re-evaluated, and no caller re-expires the same instant forever.
* **Confidence.** `raw_confidence` is preserved unchanged and compared to
  `min_confidence` inclusively, exactly as today. `ranking_score =
  raw_confidence * 0.5 ** (max(detection_age, 0) / half_life)` is a
  deterministic ranking heuristic. It is not a calibrated probability, and it
  has no clinical or triage meaning. With `decay_gates_eligibility` the
  cutoff is `last_detection + half_life * log2(raw / min_confidence)`; a raw
  confidence equal to the threshold is then excluded immediately. A
  `min_confidence` of 0 never decay-excludes.
* **Tracks.** `occluded` and `lost` are never eligible for new assignments
  under any policy.
* **Positions are never extrapolated.** `last_reported_position` is the exact
  reported coordinate (a zone's reference point for zone estimates), or the
  authoritative position when there is no observation.
* **Mismatch.** With `authoritative_position`, a different frame or distance
  greater than `position_tolerance_m` gives `observation_position_mismatch`.
  Callers should always pass the scenario position so this check runs.
* **Missing observation.** With `authoritative_position` it is
  `location_evidence="human_known"` and remains eligible (the fixture position
  is the human-known location, as in the MVP). Without one it is
  `location_unknown` and excluded.

### Location state and evidence

| Situation | `location_state` | `location_evidence` |
|---|---|---|
| Fresh visible detection, calibrated or unspecified basis | `observed` | `cv_calibrated_world` / `cv_unspecified` |
| Fresh visible detection from a camera zone | `estimated` | `cv_zone_estimate` |
| Occluded track within freshness (last reported position) | `estimated` | by basis |
| Lost track, stale or future-dated report/detection | `unknown` | by basis |
| No observation, authoritative position supplied | `observed` | `human_known` |
| No observation, no position | `unknown` | `none` |

### Reasons

Legacy-compatible (same names and order as `optimization.solver`):
`observation_future_dated`, `observation_stale`, `observation_low_confidence`,
`observation_position_mismatch`, `track_occluded`, `track_lost`,
`track_future_dated`, `track_stale`. Additional:
`decayed_confidence_below_threshold` (opt-in), `zone_estimate_not_allowed`
(opt-in), `location_unknown` (no observation and no position).

### `next_transition_at`

The earliest instant strictly after `now` at which `reasons` change by time
alone: a future stamp arriving, a stamp reaching `stamp + freshness_seconds`,
or the decay cutoff when gated. Every value is finite; instants beyond the
representable datetime range are omitted, and `None` means time alone never
changes the result. Evaluating at exactly `next_transition_at` already yields
the new reasons, and its own `next_transition_at` is strictly later. Use
`earliest_transition` to bound approval/recommendation lifetimes.

## Relationship to the existing solver

With the default policy and `authoritative_position=casualty.position`, the
set of eligible casualties is a subset of the legacy
`optimization.solver` eligible set; reasons are identical except at the
exact freshness boundary instant. No policy value can make an observation
eligible that the legacy rule with the same thresholds excludes. This is
property-tested across a grid of over a thousand observation/time pairs in
`backend/tests/test_temporal_uncertainty.py`.

Intended integration (owned by the simulation layer): expose reports as
diagnostics first; only when a per-scenario opt-in temporal gate is enabled,
feed `assignment_exclusions` into the solver and fold `earliest_transition`
into the freshness horizon. With the gate off, live behaviour is unchanged.

## Evaluation

```bash
uv run python -m scripts.evaluate_uncertainty            # writes reports/uncertainty.{json,md}
uv run python -m scripts.evaluate_uncertainty --check    # fails if reports are out of date
```

The harness evaluates fixed synthetic cases at fixed offsets from a fixed
epoch: fresh ageing, confidence threshold, boundary and future timestamps,
detection-vs-report staleness, occlusion, loss, recovery, zone estimates,
position mismatch, missing information and the opt-in decay gate. Each step
records expected versus actual exclusion reasons and the legacy solver's
reasons for the same thresholds. It reports rule conformance and constraint
exclusions only; it does not measure perception, triage or medical accuracy.
