# Perception — CV & mapping (AskAr)

Webcam → person detection → tracking → floor mapping → `CVObservation` payloads
for the backend, as defined in `../shared/CONTRACT.md`.

Perception reports **observations with confidence** only. It never outputs
injury type, diagnosis, severity or triage; humans confirm priorities in the
dashboard. All scenes and casualties are simulated.

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Detection test harness | Done |
| 1 | Camera → YOLO → ByteTrack → homography → publisher | Not started |
| 3 | Evaluation scripts, record / replay | Not started |
| 2 | Posture, stillness, breathing, command response | Not started |

## Setup

Perception has its own environment so the backend's `pyproject.toml` / `uv.lock`
stay untouched. Run everything from the **repository root**.

```sh
uv venv --python 3.11 perception/.venv
uv pip install --python perception/.venv/bin/python -r perception/requirements.txt
source perception/.venv/bin/activate
```

Without `uv`: `python3.11 -m venv perception/.venv`, activate it, then
`pip install -r perception/requirements.txt`.

Model weights download on first use into `perception/models/` (git-ignored).

### macOS camera permission

The app that launches Python needs camera access: **System Settings → Privacy &
Security → Camera**, enable Terminal / iTerm / Cursor, then restart that app.
The first run triggers the prompt and fails; run it again after allowing.
If the wrong camera opens (built-in or iPhone Continuity Camera instead of the
Logitech), try `--camera 1` or `--camera 2`.

## Phase 0 — detection test harness

```sh
python -m perception.tools.detection_test \
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
| `--csv` | `perception/eval/detection_runs.csv` | Results file to append to |

Press `q` or `Esc` to stop early. Each run prints and appends one row:
model, % of frames with at least N people, mean confidence, FPS, and mean / p95
inference latency in ms. The model is warmed up on the first frame before
timing starts. FPS covers the whole loop (capture, inference, drawing);
inference latency covers the model call only. Values that cannot be computed
are left blank, never written as zero. No frames are saved.

Scenarios to compare (same scene and lighting for each model):

```sh
for model in yolo11n.pt yolo11s.pt yolo11n-pose.pt; do
  python -m perception.tools.detection_test --model $model --expected 1 --scenario lying_sideways
done
```

`standing`, `sitting`, `lying_facing_camera`, `lying_sideways`,
`partially_covered`, `three_people` (`--expected 3`), `dim_light`.

## Tests

```sh
python -m pytest perception/tests
```

## Integration notes for Phase 1 (to confirm with Prince)

Found while reading the contract and the backend on `main`; nothing here
changes a shared file.

- The live ingest path is `PUT` then `POST
  /api/v1/simulations/demo/cameras/logitech-1/observations`
  (`mapping_mode: calibrated_world`), frame `demo-floor-1`.
- The backend rejects unknown fields, so posture / stillness / breathing cannot
  be sent until a schema extension is approved (`CONTRACT_PROPOSAL.md`, Phase 2).
- For an associated track, **any** position change, state change, or confidence
  crossing 0.5 bumps the scene revision and invalidates pending approvals.
  Perception will publish a stabilized position (deadband) and debounce
  `visible` ↔ `occluded`, otherwise detector jitter blocks approval.
- An `occluded` / `lost` report must repeat the last accepted position,
  confidence and `last_seen_at` exactly, or it gets a 409.
- The demo map is 24 × 20 m with responders at (0, 0) and (20, 0); the staged
  floor area is a few meters. Needs a decision: scale the calibrated floor into
  the demo frame, or reset the scenario with a small map.
- The repository README lists this module as `cv/`; this work lives in
  `perception/` per the team brief.
