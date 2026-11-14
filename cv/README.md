# CV and mapping (AskAr)

Webcam → YOLO pose → ByteTrack IDs → floor homography → `CVObservation` JSON
for the backend, as defined in `../shared/CONTRACT.md` and
`../docs/INTEGRATION.md`.

The CV module reports **observations with confidence** only: where a person is on
the floor, how sure the detector is, and whether the track is currently seen.
It never outputs injury type, diagnosis, severity or triage. Humans associate
tracks with casualties and confirm priorities in the dashboard. All scenes and
casualties are simulated.

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Detection test harness | Done |
| 1 | Camera → YOLO → ByteTrack → homography → publisher | Done (needs a real-scene calibration) |
| 3 | Evaluation scripts, record / replay | Not started (file replay already works) |
| 2 | Posture, stillness, breathing, command response | Not started (posture used internally) |

## Setup

The CV module has its own environment so the backend's `pyproject.toml` / `uv.lock`
stay untouched. Run everything from the **repository root**.

```sh
uv venv --python 3.11 cv/.venv
uv pip install --python cv/.venv/bin/python -r cv/requirements-dev.txt
source cv/.venv/bin/activate
```

`requirements.txt` is enough to run the pipeline; `requirements-dev.txt` adds
the backend's packages so the tests can validate payloads against the
backend's own models. Without `uv`: `python3.11 -m venv cv/.venv`,
activate it, then `pip install -r cv/requirements-dev.txt`.
Model weights download on first use into `cv/models/` (git-ignored).

### Camera

The app that launches Python needs camera access: **System Settings → Privacy &
Security → Camera**, enable Terminal / iTerm / Cursor, then restart that app.
The first run triggers the prompt and fails; run again after allowing.

Camera indices follow AVFoundation's order and can change when devices are
plugged in. Check with:

```sh
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep "\]"
```

On the dev MacBook the Logitech C922 is index 0 (1080p at 30 FPS); the
FaceTime camera and the iPhone Continuity cameras come after it.

## 1. Calibrate the floor

Mount the camera, then mark 4–6 points on the floor with tape and measure their
positions in meters. Coordinates are in the shared scene frame
(`demo-floor-1`, +x right/east, +y up/north, origin at the lower-left).

```sh
python -m cv.tools.calibrate --camera 0
```

1. SPACE freezes the live frame.
2. Click each tape mark (right-click or Backspace undoes, `r` resets), then Enter.
3. Type each point's `x y` in meters in the terminal.
4. Read the reprojection error table and check the blue 0.5 m grid lies on
   the floor, then answer `y` to save `cv/config/homography.json`.

With exactly 4 points the fit is exact, so the error is 0 by construction. Use
5–6 points for a real accuracy figure.

`--offset X Y` adds a constant to every entered point. For example, if the staged
area's corner stands for (10, 4) on the 24 × 20 m demo map, measure locally and
pass `--offset 10 4`. Re-calibrate after moving the camera or changing its
resolution. The same aspect ratio at another resolution is rescaled
automatically.

Non-interactive form (scripts, a still image):
`--image frame.png --points "u,v;..." --world "x,y;..." --yes`.

## 2. Run the pipeline

```sh
python -m cv.main --mock-backend           # print the JSON, no backend needed
python -m cv.main                          # publish to http://127.0.0.1:8000
python -m cv.main --source clip.mp4        # replay a recorded clip (labelled REPLAY)
```

| Flag | Meaning |
| --- | --- |
| `--camera N` | Camera index (config: `camera.index`) |
| `--source FILE` | Replay a video, paced to its FPS (`--no-pace` = as fast as possible) |
| `--mock-backend` | Log requests to stdout and `cv/artifacts/mock_observations.jsonl` instead of sending them (`--quiet-mock`: file only) |
| `--backend-url URL` | Backend base URL |
| `--homography FILE`, `--model NAME`, `--config FILE` | Overrides |
| `--no-display`, `--duration S`, `-v` | Headless, stop after S seconds, debug logs |

Without a calibration the pipeline still detects and tracks, but publishes
nothing and shows `UNCALIBRATED`. Tunables live in `config/pipeline.yaml` and
`config/bytetrack.yaml`; unknown keys are rejected.

**Debug window:** boxes and skeletons, track ID, published state, whether the
person was seen this frame, confidence, posture, floor coordinates (m), the
ground point (cyan dot), calibration marks, a top-down mini-map, FPS and
publisher counters. Green means seen this frame, orange occluded, red stale.
Press `q` or Esc to quit. The run summary prints FPS, inference time, track
counts, accepted / rejected reports and capture-to-sent latency.

### Live demo with the backend

```sh
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000      # Prince's backend
curl -X POST http://127.0.0.1:8000/api/v1/simulations/demo/reset \
     -H 'Content-Type: application/json' -d '{"mode":"simulation"}'
python -m cv.main
```

The pipeline registers camera `logitech-1` (`calibrated_world`; the
`calibration_ref` comes from `homography.json`) and starts posting. The operator
then links each track to a casualty with `POST /associations`, using the
canonical `track_id` from `GET /state`
(`sim-camera:10:logitech-1:<session>-t<n>`). The CV module never associates
tracks or creates casualties.

## What is published

`POST /api/v1/simulations/demo/cameras/logitech-1/observations` (the default;
`publisher.endpoint: legacy` posts a plain `CVObservation` to `/observations`):

```json
{"mode": "simulation", "id": "s20261114T051005-o000001",
 "observed_at": "2026-11-14T05:10:08.377Z", "confidence": 0.89,
 "track": {"track_id": "s20261114T051005-t1", "state": "visible",
           "first_seen_at": "2026-11-14T05:10:06.362Z", "last_seen_at": "2026-11-14T05:10:06.362Z"},
 "position": {"frame_id": "demo-floor-1", "x_m": 10.23, "y_m": 5.44}}
```

- **IDs:** track IDs are `<session>-t<n>`. The session is the process start
  time (UTC), so IDs are never reused within or across runs. Observation IDs are
  unique per report and reused only for a retry of the same report.
- **States:**
  - `visible`: detected within the last 0.5 s (`occlusion_grace_s`, which
    absorbs one-frame misses).
  - `occluded`: not detected since, but the track is alive.
  - `lost`: not detected for 30 s (`lost_after_s`). Sent once, then the ID is
    retired.
- **Repeated values:** occluded and lost reports repeat the position,
  confidence and `last_seen_at` of the last report the backend accepted, as
  the contract requires. `last_seen_at` never advances while unseen.
- **Position:** floor position of the ground point, smoothed (EMA) with a
  15 cm deadband. It only changes when the person really moves, so detector
  jitter does not invalidate pending approvals.
- **Confidence:** detector confidence smoothed over frames (EMA). It is not
  medical certainty.
- **Rate:** 5 Hz tick. Changed tracks go out on the next tick and unchanged
  tracks every 1 s (`heartbeat_s`), well within the backend's 30 s freshness
  window.
- **Failures:**
  - Backend down: the video loop never blocks. Only the newest report per
    track is kept, retries back off up to 5 s, and a retry reuses the same ID
    and body.
  - Camera registration gone (scenario reset): it registers again.
  - Other 4xx: logged and dropped.
- **Not published:** tracks without a valid floor position (uncalibrated, or a
  point at or above the horizon) are omitted rather than guessed.

**Ground point:** standing or sitting people use the bottom-center of the box.
Lying people use the hip midpoint when the hip keypoints are confident, else
the box center. Posture comes from:

1. Torso length on the floor plane, which catches people lying along the
   camera's line of sight.
2. Torso angle and thigh angle.
3. Box aspect ratio when keypoints are missing.

The result is majority-voted over 15 frames.

**Re-attach:** if ByteTrack issues a new ID within 0.6 m of a track that is
currently unseen and not yet lost, the old track ID continues, so casualties
don't need re-linking after an occlusion. Set `tracking.reattach: false` to
disable it.

## Measured (dev MacBook, Apple silicon, MPS)

These are synthetic and replay checks, not staged-scene numbers. The
staged-scene evaluation is Phase 3.

| Check | Result |
| --- | --- |
| Live C922, 1280×720, yolo11n-pose | 22.6 FPS loop, 19.5 ms median inference |
| Replay, 640×480 (paced to 30 FPS) | 30 FPS, ~19 ms median inference |
| Capture → backend accepted (live HTTP, visible reports) | median ~27 ms |
| Real backend, 270-frame clip with a 3 s blackout | 64 / 64 reports accepted; tracks went visible → occluded → lost, new IDs afterwards |
| Jitter (±3 px shake + noise), 3 tracks linked to casualties | 65 later reports, **0** scene-revision bumps |
| Backend down for the whole run | Pipeline kept full speed, backed off, no crash |

## Tests

```sh
python -m pytest cv/tests
```

The suite covers:

- Homography math: exact recovery, reprojection error, horizon, degenerate
  points, rescaling.
- Ground-point and posture selection.
- Track state: occluded, stale and lost, ID retirement, re-attach, deadband,
  duplicate IDs.
- Publisher: heartbeat, last-accepted repeat, backoff, re-registration, legacy
  format.
- Camera reconnect.
- Contract tests that validate every payload against `backend/app/models.py` and
  `backend/app/simulation/models.py`, and drive the in-process backend through
  a full track lifecycle (skipped without `requirements-dev.txt`).

## Phase 0 — detection test harness

```sh
python -m cv.tools.detection_test \
  --model yolo11n-pose.pt --camera 0 --expected 3 \
  --scenario lying_sideways --duration 20
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model` | `yolo11n.pt` | `yolo11n.pt`, `yolo11s.pt`, `yolo11n-pose.pt`, or a path |
| `--camera` | `0` | Camera index |
| `--expected` | `1` | Number of people who should be visible (N) |
| `--conf` | `0.4` | Detection confidence threshold |
| `--duration` | `20` | Seconds to run; `0` = until `q` / end of file |
| `--scenario` | `unlabeled` | Free-text label stored with the run |
| `--imgsz` | `640` | Inference size |
| `--width` / `--height` | `1280` / `720` | Requested capture resolution |
| `--device` | `auto` | `auto` (MPS when available), `cpu`, `mps` |
| `--source` | – | Video file instead of the camera |
| `--no-display` | off | Run without a window |
| `--csv` | `cv/eval/detection_runs.csv` | Results file to append to |

Each run prints and appends one row: the model, the % of frames with at least
N people, mean confidence, FPS, and mean / p95 inference latency. The model is
warmed up before timing starts. Values that cannot be computed are left blank,
never written as zero. No frames are saved. Scenarios to compare:
`standing`, `sitting`, `lying_facing_camera`, `lying_sideways`,
`partially_covered`, `three_people` (`--expected 3`), `dim_light`.

## Integration notes (to confirm with Prince and Hillary)

Nothing here changes a shared file.

- **Map scale:** the demo map is 24 × 20 m with responders at (0, 0) and
  (20, 0); the staged floor is a few meters. Placing it with `--offset` keeps
  real meters. Prince decides whether the demo instead uses a reset scenario
  with a small map.
- **Confidence near 0.5:** a person whose smoothed confidence hovers around 0.5
  (partly out of frame) still bumps the scene revision on each crossing, which
  the backend treats as impactful. Keep linked casualties fully in view.
- **Posture, stillness and breathing** are not sent: the backend rejects
  unknown fields. Phase 2 will add a schema proposal in `CONTRACT_PROPOSAL.md`,
  behind a config flag.
- **For Hillary:** per track, the dashboard can already show
  `camera.local_track_id`, `track.state` (visible / occluded / lost),
  `last_seen_at` (age = staleness), `confidence` and `position`, plus
  `camera.calibration_ref` / `location_basis: calibrated_world`.
- **Module name:** the code lives in `cv/`, matching the repository layout.
