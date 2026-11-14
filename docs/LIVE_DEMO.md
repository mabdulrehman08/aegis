# Two-actor, two-camera live demo (SIMULATION ONLY)

A short rehearsal of the full human-supervised loop: two actors seen by two
cameras, an explicit operator association, a human assessment, a recommendation,
an exact-revision approval, a simulated dispatch/acknowledgment, and a replan
after the map changes. Every dependency defaults to a **simulated input**. Do not
claim real actors, real camera detections, or Arduino effects unless the
corresponding surrogate below has actually been replaced and verified.

## The scene

Started from `shared/mock/scenario.demo.json` (frame `demo-floor-1`, the 24x20 m
synthetic map with one wall at x = 8..9, y = 2..13), narrowed to two casualties:

| Actor | Casualty ID | Position | Human label | Camera | Mapping |
| --- | --- | --- | --- | --- | --- |
| A (far) | `casualty-actor-a` | (14, 8) | `immediate` | `logitech-1` | `calibrated_world`, scene-frame position, `calibration_ref` required |
| B (near) | `casualty-actor-b` | (3, 2) | `minor` | `phone-1` | `predefined_zones`, `zone-b` = (3, 2) |

Both cameras deliberately reuse the local track ID `person-1`. The backend
namespaces track IDs per camera, so they stay two tracks. **Always read the
canonical track ID from the observation response** (`result.track_id`, e.g.
`sim-camera:10:logitech-1:person-1`) and use that in `/associations`; the local
ID is rejected with 404. There is no automatic cross-camera matching: the
operator associates each track to a casualty explicitly. The phone position is a
`zone_estimate` (the zone's reference point), and the explanation says so.

Responders are `responder-001` at (0, 0) and `responder-002` at (20, 0).

- **Both available:** OR-Tools serves both actors (A to `responder-002`, B to `responder-001`).
- **`responder-002` offline:** only one responder remains. The far `immediate`
  actor gets `responder-001` even though the near `minor` actor is only ~3.6 m
  away and A's route is ~23 m. Priority beats proximity; B stays unassigned with
  a reason.

Unassessed casualties are not a medical inference: the solver gives them
protected parity with `immediate`. Fixture positions are trusted until a track is
associated, so a first proposal exists right after reset; nothing is approved.

## One-command rehearsal

```sh
uv sync --locked
uv run python -m scripts.live_demo            # isolated in-process app, ~1 s
uv run python -m scripts.live_demo --json     # machine-readable summary
```

It uses `TestClient` against `backend.app.main.create_app`, so every step is a
real HTTP route, and exits non-zero on any failed assertion. Sequence: reset,
register cameras, observe, associate, assess, control replan (both available),
second responder offline + replan, approve exact revision (idempotent replay
checked), dispatch, synthetic delivered + acknowledged, synthetic completed,
block an interior waypoint of B's pending route under a new map revision, replan,
explanation and event evidence. It stops with B's new proposal **pending**: a new
approval is required and the script never grants it.

To populate a running dashboard server instead (this resets that server's `demo`
scenario; use one worker):

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000
```

The script finishes in about a second, so the dashboard shows the end state. For
a paced demo use the runbook.

### Preparing a live session (`--prepare-only`)

```sh
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000 --prepare-only
```

This resets `demo` to the two unassessed actors and registers only the two camera
mappings, then exits. It sends **no** observations, assessments, approvals,
dispatches or device events, so the real CV service, dashboard and Arduino bridge
can drive the same state. It requires `--base-url`.

**Do not run the full `--base-url` rehearsal while real camera or hardware
components are connected.** It resets the scenario, posts its own synthetic
observations, and approves and dispatches on its own, which would overwrite or
conflict with the real events. Use it only against a server with no live
components attached. The physical camera-to-Arduino loop is not implemented or
claimed by this script; `--prepare-only` only readies the scene.

## Surrogates and what replaces them

| Surrogate in the script | Real replacement (when built and verified) |
| --- | --- |
| Posted `PUT /cameras/logitech-1` + observations with scene-frame `position` | CV service posts the same payload; the calibration is performed **outside** this backend and `calibration_ref` only asserts it exists |
| Posted `PUT /cameras/phone-1` + `zone_id` observations | Phone app posts `zone_id` from its known zone map |
| Operator `/associations`, `/assessment`, approval | Dashboard controls; always human |
| `simulate-device-event` `delivered` (LED command reached the device) | Arduino bridge `POST /device-events`, `kind: delivered` |
| `simulate-device-event` `acknowledged` (physical button press) | Arduino bridge `POST /device-events`, `kind: acknowledged` |
| `simulate-device-event` `completed` | Bridge or operator `completed` event |
| Map edit via `PUT /map` | Dashboard/operator map change with a new `revision` |

Payload shapes are in `docs/INTEGRATION.md`. `real_device_effect` stays `false`
on every dispatch.

## Two-minute runbook

Before the room: server running, dashboard open on `ws://…/demo/ws`, and one dry
run of `uv run python -m scripts.live_demo` passing.

| Time | Do | Say / show |
| --- | --- | --- |
| 0:00 | Reset (command 1), register both cameras (2) | Two cameras, two mapping modes |
| 0:15 | Post one observation per camera (3) | Same local ID, two separate canonical tracks; nothing associated yet |
| 0:30 | Associate both tracks, assess A `immediate`, B `minor` (4) | Detection is not identification; labels are human |
| 0:45 | Replan with both available, then `responder-002` offline and replan (5) | Both served; then priority over proximity |
| 1:05 | Approve exact revision, dispatch, delivered + ack (6) | Approval is human; ack is a surrogate unless the Arduino bridge is live |
| 1:25 | Complete A, block a waypoint, replan (7) | Same responder, new route, **new approval required** |
| 1:50 | Show the explanation and events (8) | Grounded facts, zone-estimate caveat, SIMULATION ONLY |

**Freshness:** a camera report is fresh for 30 s, and a proposal expires no later
than that horizon. Real cameras must keep reporting; with the commands below run
`heartbeat` before each replan. If a proposal expired, run `heartbeat` and replan.

**If anything stalls**, use `uv run python -m scripts.live_demo` for an isolated
fallback and say it is the scripted simulation. To replay on the dashboard
server with `--base-url http://127.0.0.1:8000`, disconnect the real camera
producers and hardware bridge first: that rehearsal resets and drives the scene.

## Fallback commands

Needs `curl` and `jq`. Run each block in the same shell, from the repository root.

```sh
B=http://127.0.0.1:8000/api/v1/simulations/demo
H='Content-Type: application/json'
F=demo-floor-1
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
post() { curl -sf -X "$1" "$B$2" -H "$H" ${4:+-H "Idempotency-Key: $4"} -d "$3"; }
T0=$(now)
```

**1. Reset: mock fixture narrowed to two unassessed actors**

```sh
jq '.name="Synthetic two-actor rehearsal"
    | .casualties=[{id:"casualty-actor-a",position:{frame_id:"demo-floor-1",x_m:14,y_m:8},medical_assessment:null},
                   {id:"casualty-actor-b",position:{frame_id:"demo-floor-1",x_m:3,y_m:2},medical_assessment:null}]
    | {mode:"simulation",scenario:.,add_demo_map:true}' shared/mock/scenario.demo.json |
  curl -sf -X POST "$B/reset" -H "$H" -d @- | jq '.state.scene_revision'
```

**2. Cameras**

```sh
post PUT /cameras/logitech-1 '{"mode":"simulation","actor":"live-demo-operator","mapping_mode":"calibrated_world","calibration_ref":"external-calibration-demo-001"}' | jq .result
post PUT /cameras/phone-1 '{"mode":"simulation","actor":"live-demo-operator","mapping_mode":"predefined_zones","zones":{"zone-b":{"frame_id":"demo-floor-1","x_m":3,"y_m":2}}}' | jq .result
```

**3. Observations**

`heartbeat` posts one report per camera (`first_seen_at` stays `T0`) and prints the canonical track IDs. If they differ from the `TRACK_A`/`TRACK_B` values below, use the printed ones.

```sh
heartbeat() {
  local t; t=$(now); N=$((${N:-0}+1))
  post POST /cameras/logitech-1/observations '{"mode":"simulation","id":"logi-'$N'","observed_at":"'$t'","confidence":0.9,"position":{"frame_id":"demo-floor-1","x_m":14,"y_m":8},"track":{"track_id":"person-1","state":"visible","first_seen_at":"'$T0'","last_seen_at":"'$t'"}}' | jq -r .result.track_id
  post POST /cameras/phone-1/observations '{"mode":"simulation","id":"phone-'$N'","observed_at":"'$t'","confidence":0.9,"zone_id":"zone-b","track":{"track_id":"person-1","state":"visible","first_seen_at":"'$T0'","last_seen_at":"'$t'"}}' | jq -r .result.track_id
}
heartbeat
TRACK_A='sim-camera:10:logitech-1:person-1'; TRACK_B='sim-camera:7:phone-1:person-1'
```

**4. Explicit associations and human assessments**

```sh
post POST /associations '{"track_id":"'$TRACK_A'","casualty_id":"casualty-actor-a","create_casualty":false,"actor":"live-demo-operator"}' | jq .result
post POST /associations '{"track_id":"'$TRACK_B'","casualty_id":"casualty-actor-b","create_casualty":false,"actor":"live-demo-operator"}' | jq .result
post POST /casualties/casualty-actor-a/assessment '{"severity":"immediate","source":"human","assessed_by":"live-demo-operator","assessed_at":"'$(now)'"}' | jq .result
post POST /casualties/casualty-actor-b/assessment '{"severity":"minor","source":"human","assessed_by":"live-demo-operator","assessed_at":"'$(now)'"}' | jq .result
```

**5. Control, then priority over proximity**

The first `replan` assigns both actors; after `responder-002` goes offline only the far `immediate` actor is assigned.

```sh
replan() { heartbeat >/dev/null; post POST /replan '{"actor":"live-demo-operator","reason":"live demo","solver":"ortools"}' | jq '.state | .current_proposal_id as $p | .proposals[] | select(.id==$p) | {id,revision,assignments:[.assignments[]|{casualty_id,responder_id,distance_m:.route.distance_m}],unassigned_casualty_ids}'; }
replan
post PATCH /responders/responder-002 '{"status":"offline"}' >/dev/null
replan
```

**6. Approve the exact revision, dispatch, SYNTHETIC delivered + button-surrogate ack**

```sh
state() { curl -sf "$B/state"; }
PID=$(state | jq -r .current_proposal_id); REV=$(state | jq ".proposals[]|select(.id==\"$PID\").revision")
AID=$(post POST /proposals/$PID/decision '{"proposal_revision":'$REV',"decision":"approve","actor":"live-demo-operator","mode":"simulation"}' approve-$PID | jq -r .result.id)
post POST /dispatches '{"proposal_id":"'$PID'","proposal_revision":'$REV',"approval_id":"'$AID'","actor":"live-demo-operator","mode":"simulation"}' dispatch-$PID | jq .result.dispatch.real_device_effect
ASSIGN=$(state | jq -r '.assignments[0].id')
post POST /assignments/$ASSIGN/simulate-device-event '{"kind":"delivered"}' | jq .result.state_changed
post POST /assignments/$ASSIGN/simulate-device-event '{"kind":"acknowledged"}' | jq .result.state_changed
```

**7. Complete A, block an interior waypoint of B's route, replan (new approval required)**

The pending route's waypoints are printed first. `CELL` is the grid index `row*24+column` of a waypoint's cell (`column=floor(x_m)`, `row=floor(y_m)`); the example blocks cell (10,5).

```sh
heartbeat >/dev/null
post POST /assignments/$ASSIGN/simulate-device-event '{"kind":"completed"}' | jq .result.state_changed
post PATCH /responders/responder-001 '{"position":{"frame_id":"demo-floor-1","x_m":14,"y_m":8}}' >/dev/null
replan
state | jq '.proposals[]|select(.status=="pending_approval")|.assignments[0].route.waypoints|map([.x_m,.y_m])'

CELL=$((5*24+10))
state | jq --argjson i $CELL '.scenario.scene_map | .revision="live-demo-blocked-1" | .occupancy_grid.cells[$i]=100' |
  curl -sf -X PUT "$B/map" -H "$H" -d @- | jq .result
replan
```

**8. Grounded explanation and events**

```sh
PID=$(state | jq -r .current_proposal_id)
curl -sf "$B/proposals/$PID/explanation" | jq '.explanation.facts[]|[.category,.text]'
curl -sf "$B/events?limit=1000" | jq -r '.events[]|"\(.sequence) \(.type)"' | tail -25
```

The blocked cell must lie on B's pending route and away from either
endpoint; otherwise the replan keeps the same route and the demo does not show
the change. The scripted CLI picks a valid cell automatically.

## Safety reminders

- Keep the visible **SIMULATION ONLY** label on screen.
- Approvals are local operator decisions (`authenticated: false`) and never
  authorize real dispatch. Replacement proposals always need new approval.
- Do not forward any of these records to a real emergency service.
- Neither this runbook nor the script verifies camera calibration, CV accuracy,
  serial/Wi-Fi transport, or LED/button hardware.
