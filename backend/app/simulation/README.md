# Simulation coordination API (SIMULATION ONLY)

Local, in-memory, synthetic. No authentication, no real devices, no real
dispatch. Every request/response/event carries `mode: "simulation"`; sending
`mode: "real"` anywhere returns 422. All request bodies forbid unknown fields.

Wiring (owned by `backend/app/main.py`):

```python
from backend.app.simulation.routes import router as simulation_router
from backend.app.simulation.store import SimulationStore
application.state.simulation_store = SimulationStore()  # clock=, solver=, auto_recommend= injectable
application.include_router(simulation_router)
```

Demo scenario id: **`demo`** (loaded lazily on first use, or via reset). Other
IDs must be reset with a supplied synthetic `scenario` whose `id` matches.

## Routes — prefix `/api/v1/simulations/{scenario_id}`

| Method | Path | Body | Success |
| --- | --- | --- | --- |
| POST | `/reset` | optional `ResetRequest` `{scenario?, required_capabilities?, add_demo_map=true}` | 201 ActionResponse |
| GET | `/state` | — | 200 SimulationState |
| GET | `/events?after_sequence=0&limit=200` | — | 200 EventPage |
| WS | `/ws` | — | snapshot then events |
| POST | `/observations` | contract `CVObservation` | 201 |
| POST | `/associations` | `{track_id, casualty_id, create_casualty=false, actor}` | 201 |
| POST | `/casualties/{casualty_id}/assessment` | contract `MedicalAssessment` (`source: "human"`) | 201 |
| PUT | `/casualties/{casualty_id}/required-capabilities` | `{capabilities: [str], actor}` | 200 |
| PATCH | `/responders/{responder_id}` | `{position?, status?, capabilities?, reported_at?}` (≥1 of first 3) | 200 |
| PUT | `/map` | contract `SceneMap` (new `revision` required for changes) | 200 |
| POST | `/replan` | `{actor, reason?, solver?: ortools/fallback/nearest}` | 201 |
| GET | `/proposals/{proposal_id}/explanation` | — | 200 grounded facts (no state change) |
| POST | `/proposals/{proposal_id}/decision` | `{proposal_revision, decision: approve/reject, actor}` + **`Idempotency-Key` header required** | 201 |
| POST | `/dispatches` | `{proposal_id, proposal_revision, approval_id, actor}` + **`Idempotency-Key` header required** | 201 |
| POST | `/device-events` | `SimulatedDeviceEvent` (contract DeviceAssignmentEvent, `mode` must be `simulation`) | 201 |
| POST | `/assignments/{assignment_id}/simulate-device-event` | `{kind: delivered/acknowledged/completed, event_id?, occurred_at?}` | 201 |

`Idempotency-Key` is optional on other mutations. Same key + same body replays
the stored response with `duplicate: true`; same key + different body → 409.
A key is reserved atomically before any unlocked work (e.g. an explicit replan's
solve): a retry while the original is still in flight gets 409 "still in
progress" (no waiting on locks); retry after it completes to get the replay.

Errors: `{"detail": "..."}`. 404 unknown scenario/entity/proposal/approval/
dispatch; 409 stale revision, out-of-order timestamp, already consumed/decided,
expired, superseded, conflicting duplicate ID/key, unavailable responder;
422 invalid payload, future-dated timestamp, wrong frame, `mode: real`.

## Responses

Every mutation returns `ActionResponse`:
`{mode, action, duplicate, result: {...action-specific...}, state: SimulationState}`
where `state` is the snapshot **after** any automatic replan.

`SimulationState` (see `models.py`): `mode, schema_version, scenario_id, epoch,
scene_revision, sequence, generated_at, scenario (contract Scenario),
required_capabilities {casualty_id: [tags]}, tracks [{track_id, casualty_id|null,
latest_observation, observation_count}], resolved_casualty_ids,
current_proposal_id, proposals, approvals, dispatches, assignments,
last_recommendation`.

Key `result` shapes:
- decision → `SimulationApproval` `{id, proposal_id, proposal_revision, decision, approved_by, operator_id, authenticated: false, decided_at, expires_at, status: active/consumed/invalidated/expired/recorded}`
- dispatch → `{dispatch: SimulationDispatch, assignments: [SimulatedAssignment]}`; each assignment has `id, dispatch_id, command_id, device_id, casualty_id, responder_id, route, status, delivered_at, acknowledged_at, completed_at`
- replan → the new `SimulationProposal`, or a `RecommendationState` when nothing new was proposable
- device event → `{event_id, assignment_id, state_changed}`

## Lifecycle semantics

1. Reset → scene revision bumps → auto recommendation → `current_proposal_id`
   (status `pending_approval`). `proposal.revision` == scene revision it was
   computed on; quote it exactly. Proposals contain **new assignments only**;
   `preserved_assignments` lists active work, which is never re-proposed.
   No proposal is created when nothing new is assignable (see `last_recommendation`).
2. Any impactful change bumps `scene_revision`, supersedes open proposals,
   invalidates unconsumed approvals, and auto-replans once (bounded; no solver
   loops). Impactful = reset, association, assessment, required capabilities,
   **any** responder change (status, capabilities, or position — no distance
   tolerance), map revision, an associated track's position (any change),
   state, or confidence crossing `min_confidence` (0.5), dispatch, device
   milestone, and **observation freshness transitions** (below).
   An unchanged heartbeat (same position/state, still fresh) does not bump.
3. Decision: only `pending_approval`, exact revision, unexpired.
4. Dispatch: approval must be `active`, proposal `approved` at the current
   scene revision, responders still available. Approval is consumed atomically;
   a second dispatch with a different key → 409. Responders become `busy`.
5. Device events must correlate dispatch/command/casualty/responder/device
   (`device_id` = `sim-device-{responder_id}`). Out-of-order milestones never
   regress status; missing earlier milestones stay `null`. Event ID reused with
   the same body → `duplicate: true`; with a different body → 409.
6. `completed` frees the responder to `available`, unless a status update
   arrived during active work (stored preference `busy`/`offline` wins). Active
   assignments survive responder status changes. Completed casualties enter
   `resolved_casualty_ids` and are not re-proposed.
7. Observations keep `track_id` separate from casualty IDs. Only an explicit
   association links a track to an existing casualty or creates a new confirmed
   casualty (unassessed). Associated tracks move the casualty and feed solver
   freshness/confidence checks. Observations never create assessments.
   Per track: older `observed_at`, regressing `last_seen_at`, or a different
   `first_seen_at` → 409. An `occluded`/`lost` report must repeat the last
   actual detection's position, confidence and `last_seen_at` exactly → else 409.
8. Freshness (evaluated at request time on every mutation, `GET /state`,
   explicit replan, and roughly every second while a WebSocket is connected):
   each associated observation is classified like the solver does —
   `observed_at` and `track.last_seen_at` are *future* (age < 0), *fresh*
   (0 ≤ age ≤ 30 s) or *stale* (age > 30 s). When any classification changes
   (e.g. a track goes stale, or a stale track is restored with identical
   values) the scene revision bumps and the system replans. Proposal
   `expires_at` = min(created + 120 s, next freshness transition), so an
   approval can never be dispatched against observations that have since gone
   stale. Stale/uncertain observations block *new* work for that casualty only;
   active work is preserved.
9. Map revisions are a per-epoch registry: re-sending a known revision with
   identical content is allowed (e.g. restoring a prior map); reusing any
   revision seen this epoch with different geometry/occupancy → 409. Reset
   reseeds the registry from the loaded map.
10. Device milestone times must be chronological against every milestone
    already recorded for that assignment (`delivered ≤ acknowledged ≤
    completed`); inconsistent times → 409 before anything is stored.
    Out-of-order *arrival* is fine when the times are consistent.
    `simulate-device-event` without `event_id`/`occurred_at` uses the
    deterministic ID `sim-device-event-{assignment_id}-{kind}` and, on retry,
    the originally stored timestamp, so retries replay (`duplicate: true`).

## Events (`EventEnvelope`)

`{schema_version: "1.0", mode: "simulation", event_id, type, scenario_id,
occurred_at, sequence, payload}`. `sequence` is per scenario, strictly
increasing and **never resets** (not even across `/reset`). Types:
`simulation.reset, simulation.snapshot (WS only, not stored), observation_received,
track_associated, casualty_created, assessment_recorded, responder_updated,
map_updated, capabilities_updated, replan_requested, recommendation_computed,
proposal_created, proposal_superseded, proposal_expired, decision_recorded,
approval_invalidated, dispatch_created, device_delivered, device_acknowledged,
device_completed, responder_released` (all prefixed `simulation.`).

WebSocket: first message is `simulation.snapshot` whose `sequence` is the last
event already reflected; then every event with a greater sequence. If the
client falls behind the retained window (1000 events) a fresh snapshot is sent.
On reconnect, simply use the new initial snapshot. Client messages are ignored.
Unknown scenarios are refused (close 1008) before accept. Reset does not
restart `sequence`, `scene_revision`, or ID counters; `epoch` increments.
