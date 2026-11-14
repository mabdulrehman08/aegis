# SIMULATION Intelligence API

Advisory diagnostics and opt-in policy for SIMULATION scenarios. Nothing here
approves, dispatches, or contacts any device. Ranking scores are deterministic
ordering heuristics, not calibrated probabilities, clinical severities, or
measures of medical urgency. Objective tiers describe the solver's stated
lexicographic objective, not real-world outcome quality.

All four routes live under `/api/v1/simulations/{scenario_id}/intelligence`.
`{scenario_id}` must be a loaded scenario (`demo` loads lazily); unknown ids
return 404. Requests with `"mode": "real"` are rejected with 422, like every
other SIMULATION route.

## GET /intelligence

Read-only temporal and objective diagnostics after the normal freshness tick.
Because the tick runs, time-driven transitions (expiry, observation freshness)
still apply here exactly as they do for `/state`; the GET itself installs no
proposal and emits no event beyond what the tick already would.

Response (`IntelligenceSnapshot`):

- `policy`: current per-scenario `IntelligencePolicy` (version 0 is the
  untouched default; reset restores it).
- `thresholds`: legacy hard gates owned by the store (`freshness_seconds`,
  `min_confidence`, `position_tolerance_m`). No intelligence policy can relax
  them.
- `casualties[]`: per-casualty `uncertainty` (`TemporalUncertainty` from
  `backend/app/intelligence/uncertainty.py`, copied unchanged: `raw_confidence`
  is the detector value, `reasons` use the legacy solver's names),
  `temporal_gate_applied`, `exclusion_reasons` (temporal reasons prefixed with
  `temporal_gate:`, empty unless the gate excluded this casualty), zone
  provenance (`zone_id`, `zone_reference_position`: the registered zone's
  reference point, never an observed coordinate), and per-entry `warnings`.
- `next_transition_at`: earliest instant at which any temporal reason changes
  by time alone (diagnostic even with the gate off). It feeds the proposal
  expiry horizon and the freshness signature as eligibility/reasons only, never
  as a continuous score, so decay cannot cause replan loops.
- `source_valid_until`: freshness horizon capping proposal expiry.
- `previous_recommendations` / `previous_recommendation_proposal_id`: the
  prior UNAPPROVED recommendation (open pending proposal if any, otherwise the
  last pending proposal closed without a decision); cleared on reject, approve,
  dispatch, and reset.
- `proposal`: solver objective reasoning recorded for the latest proposal
  (copied, not recomputed): `objective_policy`, `objective_tiers`,
  `objective_values`, `previous_assignment_outcomes`,
  `applied_assignment_exclusions`, plus `stability_budget_enforced`, the
  solver's own guarantee flag, true only when the bounded travel stage
  actually ran and held the budget.

## PUT /intelligence/policy

Full replacement (PUT semantics: omitted settings return to defaults) of the
per-scenario policy. Body is `IntelligencePolicyRequest`, which requires an
explicit `actor`. Every default is legacy behavior.

Settings:

| Setting | Default | Effect when enabled |
|---|---|---|
| `temporal_gate` | false | Temporal ineligibility becomes an extra exclusion for NEW work only. Never edits CV confidence, positions, or human assessments, never touches active work, can only tighten legacy gates. Extra exclusions are reported as `temporal_gate:<reason>`. |
| `decay_gates_eligibility` | false | Ranking-score decay at or below `min_confidence` also gates eligibility (inclusive cutoff, rounded up to a microsecond). Without `temporal_gate` it changes diagnostics only. |
| `allow_zone_estimates` | true | Setting false excludes `zone_estimate` locations (opt-in tightening). |
| `confidence_half_life_seconds` | 30.0 | Half-life of the ranking-score heuristic (> 0). |
| `stability_preference` | false | Passes the previous UNAPPROVED recommendation to the solver as a soft preference. Count and priority always dominate; approved active work is always hard reserved. |
| `objective_policy` | `distance_first` | `stability_first` or `bounded_travel_stability` (requires `stability_travel_budget_m`) need `stability_preference: true`. |
| `stability_travel_budget_m` | null | Travel budget, required for and only valid with `bounded_travel_stability`. |

An actual change bumps the scene revision (superseding open proposals and
invalidating unconsumed approvals), emits
`simulation.intelligence_policy_updated` (visible on `/events` and the
websocket stream, which replays the same event log), then runs the normal
bounded auto-replan. An identical policy is a duplicate with no effect.

## POST /intelligence/comparisons

Pure counterfactual reruns of the real solver on explicitly changed copies of
one captured request. Read-only: installs no proposal, approves nothing,
authorizes no dispatch, emits no events, runs no tick.

Body (`ComparisonRequest`): 1..=MAX_SCENARIOS scenarios (each with
`responder_availability`, `responder_capabilities`, `obstacle_added`,
`obstacle_removed`, or `preference` changes), an optional per-request `policy`
override (never changes stored policy), and an optional `solver` override.

The base request is captured detached at the current instant under the lock
(never built by `_prepare`, which would emit `simulation.replan_requested`);
the solver runs unlocked. The immutable `source` records epoch, scene
revision, sequence, map revision, captured policy, active assignments, and
`source_valid_until`. The result is `stale` when the epoch or scene revision
changed, or `source_valid_until` passed, before results returned; otherwise
`current`. A stale comparison is still computed, but never act on it: rerun.
Each scenario outcome carries `assumption_differences`, `changed_assignments`,
`route_changes` for retained pairs whose route, map, or distance changed,
`objective_deltas`, and `authorizes_dispatch: false`.

## GET /intelligence/evaluations

Committed synthetic evaluation artifacts (`allocation`, `uncertainty`,
`counterfactuals`) with per-file SHA-256 digests over the exact report bytes.
Unavailable or invalid reports are listed with `available: false` and no
result is inferred. Synthetic only, never live telemetry.

## Worked example

```
POST /api/v1/simulations/demo/reset -> 201
PUT  /api/v1/simulations/demo/intelligence/policy
     {"actor": "operator-1", "temporal_gate": true} -> 200 (changed, version 1)
GET  /api/v1/simulations/demo/intelligence -> 200 (gate diagnostics)
POST /api/v1/simulations/demo/intelligence/comparisons
     {"scenarios": [{"id": "s1", "label": "busy 001",
                     "changes": [{"kind": "responder_availability",
                                  "responder_id": "responder-001",
                                  "status": "busy"}]}]} -> 200 (current)
GET  /api/v1/simulations/demo/intelligence/evaluations -> 200 (digests)
```
