# Frontend — AEGIS Command Center (Hillary)

Next.js 15 · TypeScript · Tailwind v4 · SVG map. Built against `../shared/CONTRACT.md`
and the simulation API documented in `../docs/INTEGRATION.md` ("Hillary — human
controls and state synchronization").

**SIMULATION ONLY.** Every action targets `/api/v1/simulations/demo`. Approvals are
local operator decisions (`authenticated: false`), not production authorization.

## Run

```sh
# terminal 1 — backend (repo root)
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# terminal 2 — dashboard
cd frontend
npm install
npm run dev            # http://localhost:3000

# with both running
npm run smoke          # end-to-end API check through the dev proxy
npm run typecheck && npm run lint && npm run build
```

Config (all optional):

| Variable | Default | Purpose |
| --- | --- | --- |
| `AEGIS_BACKEND_URL` | `http://127.0.0.1:8000` | Target of the same-origin `/aegis-api/*` rewrite (backend has no CORS) |
| `NEXT_PUBLIC_AEGIS_WS_URL` | `ws://<page host>:8000` | WebSocket base; connects directly to the backend |
| `NEXT_PUBLIC_AEGIS_SCENARIO` | `demo` | Simulation scenario ID |

## What's on screen

The operator at the command center watches three panes and relays guidance to firefighters.

| Pane | Shows | Operator actions |
| --- | --- | --- |
| **1 · Scene map** | Victims (priority colour, UNASSESSED = dashed "?"), firefighters, occupancy grid/walls, proposed vs dispatched A* routes, CV detections | **Block route** toggles cells → `PUT /map` (new revision, auto-replan); select victims/firefighters |
| **2 · Building surveillance** | Building camera feed; victim summary (count, priorities, bleeding reported, pulses logged); per-victim card with pulse, bleeding, visible wound, breathing, CV confidence/staleness, task status | Set human priority → `POST /casualties/{id}/assessment`; log what is seen/reported (pulse bpm, bleeding, wound, breathing); confirm or link unconfirmed detections → `POST /associations` |
| **3 · Firefighter cam** | Selected firefighter's bodycam feed with target overlay; turn-by-turn directions generated from the planned A* route; comms log | Hold-to-talk (button or Space) routes the operator mic to the firefighter's speaker; spoken quick commands and typed messages; availability → `PATCH /responders/{id}`; **mark position** then click the map → `PATCH /responders/{id}` with position |
| Command bar | Current recommendation, approval countdown, active task acknowledgements | Approve exact revision → `POST /decision`; then separate **Dispatch** → `POST /dispatches`; ↻ replan |
| Details drawer | Full recommendation + grounded explanation, task progress (rehearsal SIMULATE controls), incident timeline | — |

### Video and audio

- Each feed's **Connect feed** offers any camera attached to this computer (Logitech webcam;
  an iPhone via macOS Continuity Camera works as a stand-in bodycam) or a stream URL
  (MJPEG renders as an image, MP4/WebM as video). The choice is remembered per feed.
  Nothing is recorded or sent anywhere; the backend has no video API.
- **Voice link**: the operator's mic plays out of a chosen audio output (Chrome supports
  per-device output) — e.g. a Bluetooth speaker clipped to the firefighter and paired to
  this computer. The mic is muted unless the talk button/Space is held.
- Quick commands and route directions use the browser's speech synthesis, which plays on
  the **system default output** — set the firefighter's speaker as the system output for the demo.
- Directions are map-relative (north = up) because firefighter heading isn't tracked;
  "mark position" updates the backend position the router plans from.

### Vitals and visible injuries (provisional)

There is no vitals/injury schema in `shared/CONTRACT.md`, and the CV pipeline must not infer
injuries. Pulse, bleeding, wound and breathing entries are therefore **operator observations**
(seen on a feed or reported by the firefighter), stored in this browser per simulation epoch
(`src/lib/fieldNotes.ts`). They never change priority automatically. Replace the hook with API
calls once the backend adds a schema.

## Sync model

- WebSocket `/ws`: the first `simulation.snapshot` sets state; each later event is
  deduplicated by `event_id`/`sequence`, appended to the timeline, and triggers a
  `GET /state` refetch. The UI never builds state from events itself.
- Every mutation response carries the authoritative post-state, which is applied
  only if its `sequence` ≥ the one already shown.
- If the socket drops, the UI polls `/state` + `/events` every 2 s and reconnects
  with backoff. The header shows `live · ws` / `polling` / `backend offline`.

## Safety rules enforced in the UI

- `medical_assessment: null` is shown as **UNASSESSED** (dashed "?"), never as a
  severity. Severity comes only from the operator's click and is sent with `source: "human"`.
- CV tracks show confidence, track state, age, and **STALE** after 30 s. Zone-based
  positions are labelled *estimate*. Detections never create casualties without an
  explicit operator confirmation.
- Recommendations are labelled advisory. Approve sends the exact `proposal.revision`.
  Dispatch is a second explicit click using the stored `approval_id`. Nothing
  dispatches on load, selection, a recommendation, or an acknowledgement.
- Idempotency keys are deterministic per epoch/proposal/revision, so a double click
  replays (`duplicate: true`) instead of acting twice. 409s are shown verbatim and
  require a fresh human decision.
- Simulated device events stay behind the "rehearsal" toggle and are striped/labelled.
  Real Arduino acknowledgements arrive through the backend (`/device-events`) and
  show up the same way.

## Layout

```
src/app/            layout, page, globals.css (design tokens)
src/components/     CommandCenter (layout), SceneMap, SurveillancePane, FighterPane,
                    VideoFeed, CommandBar, ProposalPanel, TasksPanel, Timeline, LoopStrip, ui
src/lib/            types.ts (contract mirror), api.ts, useSimulation.ts, format.ts,
                    navigation.ts (route → directions), fieldNotes.ts (operator observations)
scripts/smoke.mjs   end-to-end check via the proxy
```
