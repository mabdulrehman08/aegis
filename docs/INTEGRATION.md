# Synthetic end-to-end integration

Run from the repository root with the locked development dependencies:

```sh
uv sync --locked
uv run python -m scripts.demo
```

The CLI uses FastAPI's `TestClient` and `backend.app.main.create_app`, not direct
coordinator calls. It must fail if a required transition or safety gate fails.
All mutable requests target the simulation-only namespace
`/api/v1/simulations/{scenario_id}`. There is no production dispatch here.

For the judging rehearsal, run `uv run python -m scripts.demo`. For a dashboard
session, start the server separately with
`uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000` (one worker).
The CLI uses its own isolated app instance; it does **not** populate the running
server or drive the dashboard. Use the HTTP sequence below against that server
for the live UI. Never present simulated acknowledgments as physical delivery.

## Teammate integration payloads — available now

Base HTTP path: `http://127.0.0.1:8000/api/v1/simulations/demo`.
Reset with `POST /reset` and `{"mode":"simulation"}` before a session.
Use the returned `state`; do not guess proposal, assignment, or approval IDs.
Full request/response schemas are at `/docs` and in
`backend/app/simulation/models.py`; the route and lifecycle reference is
`backend/app/simulation/README.md`. These additive local interfaces are proposed
separately in `docs/SIMULATION_CONTRACT_PROPOSAL.md`; the shared production
contract is unchanged.

### AskAr — observations and explicit associations

`POST /observations` accepts the shared `CVObservation` schema. This is a template:
replace timestamps with current UTC observation times and IDs with persistent
camera track IDs. Positions are calibrated meters in the scenario frame.
Observation IDs identify individual reports, not casualties or persistent tracks.

```json
{
  "id": "camera-1-report-001",
  "source": "cv",
  "observed_at": "2026-11-13T20:00:00Z",
  "position": {"frame_id": "demo-floor-1", "x_m": 4.0, "y_m": 8.0},
  "kind": "person_detected",
  "confidence": 0.9,
  "track": {
    "track_id": "camera-1-track-7",
    "state": "visible",
    "first_seen_at": "2026-11-13T20:00:00Z",
    "last_seen_at": "2026-11-13T20:00:00Z"
  }
}
```

An operator explicitly associates the track via `POST /associations`:

```json
{"track_id":"camera-1-track-7","casualty_id":"casualty-001","create_casualty":false,"actor":"demo-command"}
```

For lost/occluded reports, advance `observed_at` but retain the last actual
detection's position, confidence, and `last_seen_at`. Do not send medical labels.
New unassociated tracks are observations only; explicit association with
`create_casualty: true` creates an unassessed casualty. Freshness is 30 seconds;
stale/lost/occluded/low-confidence associated tracks block **new** assignments.

### Two cameras / two actors — explicit calibration or zones

Prefer the camera-specific endpoints below for the live demo. The old
`/observations` endpoint remains compatible for already-normalized observations,
but it cannot assert camera calibration or infer a coordinate transform.
Never send raw pixels, phone GPS, or unrelated camera-local meters as scene
coordinates. Each camera requires explicit registration. The backend stores an
external calibration assertion; it does **not** compute or verify a homography.

Register Logitech with `PUT /cameras/logitech`:

```json
{"mode":"simulation","actor":"demo-command","mapping_mode":"calibrated_world","calibration_ref":"askAr-demo-floor-calibration-v1"}
```

Send `POST /cameras/logitech/observations` with a current timestamp:

```json
{
  "mode":"simulation", "id":"report-001",
  "observed_at":"2026-11-13T20:00:00Z", "confidence":0.9,
  "track":{"track_id":"track-1","state":"visible","first_seen_at":"2026-11-13T20:00:00Z","last_seen_at":"2026-11-13T20:00:00Z"},
  "position":{"frame_id":"demo-floor-1","x_m":14.0,"y_m":8.0}
}
```

Without phone-to-floor calibration, command can explicitly map a predefined
zone to a known scene reference point via `PUT /cameras/phone`:

```json
{"mode":"simulation","actor":"demo-command","mapping_mode":"predefined_zones","zones":{"actor-b-zone":{"frame_id":"demo-floor-1","x_m":3.0,"y_m":2.0}}}
```

Then `POST /cameras/phone/observations` reports the named zone, **not** an
unmapped phone position:

```json
{
  "mode":"simulation", "id":"report-001",
  "observed_at":"2026-11-13T20:00:00Z", "confidence":0.9,
  "track":{"track_id":"track-1","state":"visible","first_seen_at":"2026-11-13T20:00:00Z","last_seen_at":"2026-11-13T20:00:00Z"},
  "zone_id":"actor-b-zone"
}
```

Both cameras may report local `track-1`; they become distinct canonical track
IDs. Use **`result.track_id`** in a separate `/associations` request identifying
the operator-confirmed casualty. There is no cross-camera re-identification,
automatic fusion, or automatic casualty creation. Mapping changes require an
epoch reset; identical registrations replay, conflicting mappings return 409.
The legacy endpoint reserves the `sim-camera:` namespace to prevent spoofing.

For Hillary: show `state.cameras` and `state.tracks[].camera` provenance. Its
`location_basis: zone_estimate` means the configured zone reference point is an
**estimate**, not a precise observed location. Display `camera_id`,
`local_track_id`, `zone_id`, and `calibration_ref`; `camera: null` identifies
legacy normalized inputs. `simulation.camera_registered` and
`simulation.observation_received` events carry this provenance.

With two available qualified responders and two reachable casualties, both can
be recommended; a different priority need not change cardinality. To demonstrate
priority versus proximity, explicitly mark one responder unavailable: the human
`immediate` farther actor must take precedence over the `minor` nearer actor.
Never fake that outcome with camera confidence or infer the labels from actors.

### Hillary — human controls and state synchronization

`POST /casualties/casualty-001/assessment` (replace the timestamp with current UTC):

```json
{"severity":"immediate","source":"human","assessed_by":"demo-command","assessed_at":"2026-11-13T20:00:00Z"}
```

Repeat with `delayed` and `minor` for the other two casualties, then
`POST /replan` with `{"actor":"demo-command","solver":"ortools"}`.
Automatic replanning also happens after impactful scene mutations.
Select the pending proposal identified by `state.current_proposal_id`.
`proposal.assignments` contains **new work only** with A* `route.waypoints`,
`distance_m`, and `map_revision`; `preserved_assignments` is historical active
work, not permission to redispatch. Show `reasons`, `rationale`, and
`GET /proposals/{proposal_id}/explanation` beside the approval control.

Approval: `POST /proposals/{proposal_id}/decision`, with an
`Idempotency-Key: judge-approve-001` header:

```json
{"proposal_revision":1,"decision":"approve","actor":"demo-command","mode":"simulation"}
```

Replace `1` with the **exact returned proposal revision**. Its response is an
`ActionResponse`: `{"mode":"simulation","action":...,"duplicate":false,
"result":{...approval record...},"state":{...authoritative snapshot...}}`.
The approval ID is `result.id`; its local actor is `authenticated: false`.
Dispatch is a separate explicit action: `POST /dispatches`, with a different
`Idempotency-Key: judge-dispatch-001` header:

```json
{"proposal_id":"COPY_PROPOSAL_ID","proposal_revision":1,"approval_id":"COPY_APPROVAL_ID","actor":"demo-command","mode":"simulation"}
```

Copy IDs and revision from the actual responses. Dispatch returns
`result.dispatch` and `result.assignments`, with `real_device_effect: false` on
the dispatch. A 409 means stale, expired, conflicting, or consumed state: refresh
and obtain new human approval, never silently approve the replacement.

Connect to `ws://127.0.0.1:8000/api/v1/simulations/demo/ws`. The first
`simulation.snapshot` envelope contains authoritative state directly at `payload`
and a sequence checkpoint. Subsequent envelopes contain `schema_version`,
`mode`, `event_id`, `type`, `scenario_id`, `occurred_at`, `sequence`, and `payload`.
Apply only events above the snapshot checkpoint; deduplicate event IDs and
sequence numbers. On reconnect or a gap use a fresh snapshot (or `GET /state`),
not a guessed local state. Events notify; WebSocket messages cannot dispatch.
Keep a visible **SIMULATION ONLY** label. Use a same-origin development proxy
or configure an explicitly reviewed local integration; permissive CORS is not
enabled by this backend.

### Muhammad — correlated simulated device milestones

For the hardware-independent rehearsal, call
`POST /assignments/{assignment_id}/simulate-device-event` with
`{"kind":"delivered"}`, then `{"kind":"acknowledged"}`. Completion uses
`{"kind":"completed"}`. All IDs come from the dispatch response.

The future demo bridge can report via `POST /device-events` using this template:

```json
{
  "id": "arduino-demo-event-001",
  "scenario_id": "demo",
  "device_id": "COPY_ASSIGNMENT_DEVICE_ID",
  "dispatch_id": "COPY_ASSIGNMENT_DISPATCH_ID",
  "command_id": "COPY_ASSIGNMENT_COMMAND_ID",
  "casualty_id": "COPY_ASSIGNMENT_CASUALTY_ID",
  "responder_id": "COPY_ASSIGNMENT_RESPONDER_ID",
  "kind": "acknowledged",
  "occurred_at": "2026-11-13T20:00:00Z",
  "mode": "simulation"
}
```

Copy every correlation field from the assignment and use actual UTC occurrence
time, at/after dispatch. A retry must reuse its event ID **and** payload. Timestamp
order must satisfy delivered ≤ acknowledged ≤ completed for recorded milestones.
No device report can approve a task. This PR supplies **no serial/Wi-Fi transport
or physical LED/button effects**. Muhammad's reviewed demo bridge must connect
those components explicitly, retain simulation provenance, and never forward
these records to a real emergency service.

The exercised lifecycle is reset, explicit human assessments, recommendation,
operator approval, simulation dispatch, delivered and acknowledged simulated
device events, map/responder disruption, and replanning with new approval.
JSON output reports observed state and event counts, not a precomputed success
message. No real hardware, camera, frontend, model service, medical inference,
or reinforcement-learning training is involved. Human-entered severity is test
input; the system must not infer a medical assessment from a detection.

## HTTP transitions exercised

All paths below are relative to `/api/v1/simulations/demo`:

| Method | Path | Assertion |
|---|---|---|
| POST | `/reset` | Three unassessed synthetic casualties, two responders, no dispatched work |
| POST | `/casualties/{id}/assessment` | Explicit `source: human`, recorded operator and supplied label |
| POST | `/replan` | Actual solver produces a nonempty pending proposal |
| POST | `/proposals/{id}/decision` | Exact proposal revision, explicit local operator approval |
| POST | `/dispatches` | Missing approval is rejected; approval consumed; no real device effect |
| POST | `/assignments/{id}/simulate-device-event` | Delivered, acknowledged, and completed state transitions |
| GET | `/state` | Authoritative state, not a script-maintained shadow |
| PUT | `/map` | New map revision and a blocked cell trigger replanning |
| PATCH | `/responders/{id}` | Offline active responder stays offline; active work is not overwritten |
| GET | `/events` | Unique event IDs and strictly increasing sequence; actual milestones agree with state |

Mutation requests carry deterministic `Idempotency-Key` headers. Both dispatches
are replayed with the same key and must return the original dispatch with
`duplicate: true`, not create a second command. The device-event convenience
endpoint builds correlated **simulated** reports; it does not contact hardware.

The demo completes one initial task to free a responder, approves a new pending
proposal, then changes the map and another responder's availability. It checks
that this **unconsumed** approval is invalidated, dispatch of the stale approved
proposal fails, and the explicit replan needs a different approval. New routes
must reference the current map. The existing active assignment's historical
route remains unchanged and is separately flagged for review; preserving it
does not certify continued safety or authorize another dispatch.

Approvals record a local operator identity but explicitly report
`authenticated: false`; this is not authentication or production authorization.
The script also checks that the reserved `/api/v1/dispatches` and
`/api/v1/scenarios/demo/dispatches` production write paths remain unavailable.

## Recorded validation

The initial November 13, 2026 validation run completed 29 actual HTTP requests, including
negative gates and idempotent retries. Its summary reported 70 chronological
events, two dispatches, three approvals (two consumed, one invalidated), three
delivered and three acknowledged simulated device events, and one completion.
Final assignment state contained two acknowledged tasks and one completed task.
One historical active-route review warning was reported; real device effects
were zero. These are observed results, not hardcoded CLI output; a failed
assertion exits nonzero instead of printing that summary.

The strengthened rehearsal additionally blocks an interior cell on the actual
pending route, verifies different A* geometry avoiding that cell, and fetches a
grounded explanation without changing state. Its output includes the before/after
routes, actual solver engine, blocked cell, and explanation facts; these extra
reads increase the HTTP request count. Use the current CLI output for counts.

State is process-local and synthetic. This is an integration check, not clinical
validation, a deployment readiness claim, or authorization to dispatch people.
