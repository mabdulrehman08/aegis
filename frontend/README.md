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

| Area | Backend source | Operator actions |
| --- | --- | --- |
| Coordination loop strip | derived from `state` + events (current epoch) | — |
| Casualties | `scenario.casualties`, `tracks[].casualty_id`, assignments | Enter human priority → `POST /casualties/{id}/assessment` |
| Unconfirmed detections | `tracks` with `casualty_id: null`, `camera` provenance | Confirm new casualty / link → `POST /associations` |
| Responders | `scenario.responders` | available / busy / offline → `PATCH /responders/{id}` |
| Scene map | occupancy grid, obstacles, A* waypoints, CV tracks | **Block route** mode toggles cells → `PUT /map` (new revision, auto-replan) |
| Recommendation | `proposals[current_proposal_id]`, `GET /proposals/{id}/explanation` | Approve / Reject exact revision → `POST /decision`; then **separate** Dispatch → `POST /dispatches`; Recompute → `POST /replan` |
| Active tasks | `assignments` (dispatched → delivered → acknowledged → completed) | Rehearsal toggle exposes labelled SIMULATE device events |
| Incident timeline | WebSocket events (+ `GET /events` backfill) | toggle system events |

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
src/components/     CommandCenter, SceneMap, CasualtyPanel, ResponderPanel,
                    ProposalPanel, TasksPanel, Timeline, LoopStrip, ui
src/lib/            types.ts (contract mirror), api.ts, useSimulation.ts, format.ts
scripts/smoke.mjs   end-to-end check via the proxy
```
