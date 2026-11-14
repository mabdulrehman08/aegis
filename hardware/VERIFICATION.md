# Responder device verification

Measured on the physical Arduino UNO R4 WiFi (firmware 0.2.0) running on a
power bank, connected over WiFi to the bridge laptop's 2.4 GHz hotspot, with the
real backend running locally (`uvicorn`, one worker). Operator approval and
dispatch were issued by the verification script (SIMULATION ONLY).

Re-run any time (it resets the backend's `demo` scenario, so not during a demo):

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
uv run python -m hardware.tools.verify_e2e              # automatic checks
uv run python -m hardware.tools.verify_e2e --presses    # + physical button checks
```

Results are written to `hardware/logs/verify-*.json` (git-ignored).

## Run of 2026-11-14 00:44 (automatic checks)

| Check | Result |
| --- | --- |
| WiFi round trip, bridge `PING` → device `PONG` | median 154 ms, max 187 ms, 20/20 replies |
| Approved dispatch → task on device (`DELIVERED`) | median 341 ms, max 513 ms, 10/10 dispatches |
| Approved dispatch → backend records `delivered` | median 380 ms, max 547 ms |
| WiFi link dropped mid-task | reconnected in 0.4 s; same task re-shown; backend `delivered` events: 1 |
| Bridge restarted mid-task | task re-shown; new bridge posted 0 events; backend `delivered` events: 1 |

Dispatch-to-device time is dominated by the bridge's 400 ms state poll; the
device link itself is one WiFi round trip.

## Physical button checks (`--presses`)

Repeated presses and repeated holds must produce exactly one `acknowledged`
and one `completed` event, with `delivered <= acknowledged <= completed`.
Pending: needs a person at the device.

## Why duplicates cannot occur

- The device sends `ACK` only on the alerting → acknowledged transition and
  `DONE` only after the `ACK` was confirmed; further presses are ignored.
  Unconfirmed reports are re-sent until the bridge answers `OK`.
- The bridge records which milestones it has reported per task and ignores
  repeats (counted as duplicates). After a restart it skips milestones the
  backend already holds.
- Event IDs are deterministic per task and milestone and are sent as the
  `Idempotency-Key`; the backend returns `duplicate: true` for a replay and
  rejects a different payload under the same ID.
