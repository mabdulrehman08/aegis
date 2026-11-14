# Hardware — Muhammad

Responder device: an Arduino UNO R4 WiFi carried by a responder. It shows the
casualty a human approved and dispatched to that responder, alerts them, and
reports their explicit button presses back to the backend.

**Simulation only.** It follows Prince's simulation backend
(`/api/v1/simulations/{scenario}`), every event is `mode: "simulation"`, and
nothing reaches a real emergency service. The device never approves or
dispatches; pending, unapproved proposals are never shown.

## What the device does

| Moment | LED (D4) | LED matrix | Responder does | Bridge reports |
| --- | --- | --- | --- | --- |
| Idle | off | one corner dot | — | — |
| Approved dispatch arrives | blinks (fast = `immediate` casualty) | casualty label, e.g. `A`, `C3` | — | `delivered` |
| Responder accepts | solid | label | short press | `acknowledged` |
| Responder finishes | off | `OK`, then idle | hold 1.5 s | `completed` |

If the backend resets, completes, or replaces the task, the device clears. The
reports describe what the responder did, never the casualty's condition.

## Wiring

Grove Base Shield on the UNO R4 WiFi: **button → D2**, **LED → D4**, USB to the laptop.

## Flash the firmware (once)

```sh
arduino-cli core install arduino:renesas_uno
arduino-cli lib install ArduinoGraphics
arduino-cli board list                      # find the port, e.g. COM5
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi hardware/firmware/aegis_responder
arduino-cli upload  --fqbn arduino:renesas_uno:unor4wifi -p COM5 hardware/firmware/aegis_responder
```

## Run it with the backend

From the repository root, in three terminals:

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000                 # 1. backend
uv run python -m scripts.live_demo --base-url http://127.0.0.1:8000 --prepare-only # 2. scene (once)
uv run --with pyserial python -m hardware.bridge --port COM5                      # 3. bridge
```

Without the board, replace step 3 with `uv run python -m hardware.bridge --sim`
and type `press` / `long` for the button. `--offline` runs with no backend and
manual `assign <casualty_id> [high|normal]` for testing. `--responder` picks
which responder the device belongs to (default `responder-001`, the one that
stays available in the two-actor demo). Type `status` for connection state,
delivery latency, posted/queued events and any rejections.

Do not run the full `scripts.live_demo --base-url` rehearsal while the bridge is
connected; it dispatches and acknowledges on its own.

## How it connects

The bridge polls `GET /state` about every 0.4 s and shows the newest active
assignment for its responder. Device reports are posted to
`POST /api/v1/simulations/{scenario}/device-events` with every correlation field
(`device_id`, `dispatch_id`, `command_id`, `casualty_id`, `responder_id`)
copied from that assignment, and a deterministic event ID used as the
`Idempotency-Key`, so retries resend the identical payload. If the backend is
down, events queue in order and retry every second. A 4xx rejection is logged
and never retried blindly. Events are also logged to
`hardware/logs/device-events.jsonl` (git-ignored).

## Serial protocol (115200 baud, one line per message)

| Direction | Line | Reply |
| --- | --- | --- |
| host → device | `HELLO` | `HELLO aegis-responder fw=<v> device=<id>` |
| host → device | `ASSIGN <cmd> <label> <high\|normal>` | `DELIVERED <cmd>` |
| host → device | `CANCEL <cmd>` | `CANCELLED <cmd>` |
| host → device | `RESET` | `CLEARED` |
| host → device | `PING` | `PONG` |
| device → host | `ACK <cmd>` / `DONE <cmd>` | `OK ACK <cmd>` / `OK DONE <cmd>` |
| device → host | `ERR <reason>` | — |

`<cmd>` is `e<epoch>-<backend command_id>`, so a scenario reset can never be
confused with an earlier task. `ASSIGN` is resent until `DELIVERED`; `ACK`/`DONE`
are resent every second until `OK`. On reconnect the bridge re-shows an
unfinished task, or sends `RESET` if nothing should be shown.

## Tests

```sh
uv run pytest hardware/tests
```

These run the bridge against the real backend app in-process, with a simulated device.
