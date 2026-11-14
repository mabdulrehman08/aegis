# Proposal: observed signs on camera observations (`signs`, v0.1)

Status: **proposal**, needs Prince (backend/contract) and Hillary (dashboard)
review. The CV side is built behind `signs.enabled` (compute and show) and
`publisher.send_signs` (put them on the wire). Both are off by default because
the current backend rejects unknown fields (`extra="forbid"`).

## Why

Our inspiration, the DARPA Triage Challenge, scores systems that report
per-casualty signs (severe hemorrhage, respiratory signs, alertness, trauma
region) to medics. AEGIS keeps the human in the loop:

```
camera ──signs (observations + confidence)──▶ backend ──suggested priority + evidence──▶ dashboard
                                                                     medic confirms / overrides ──▶ human assessment
```

1. **CV** reports what it sees, each sign with a confidence and an explicit
   `not_testable`. It never outputs a diagnosis, injury type, severity or
   priority.
2. **The backend** computes a *suggested* priority from the signs. It is shown
   as machine-suggested with the evidence it used, and never stored as a
   `MedicalAssessment`.
3. **The medic** confirms or overrides it in the dashboard. Only that action
   creates the `source: "human"` assessment the solver already uses.

This changes the team rule "CV never supplies medical labels" only in the
direction above: CV supplies evidence, the backend suggests, a human decides.
It needs the team's agreement before merge.

## Wire format

One optional field on `CameraObservation` (and on `CVObservation` for the
legacy route), present only on `visible` reports:

```json
"signs": {
  "schema": "aegis-cv-signs/0.1",
  "window_s": 10.0,
  "posture":   {"value": "lying",        "confidence": 0.82},
  "movement":  {"value": "still",        "confidence": 0.90, "still_for_s": 42.5},
  "response":  {"value": "no_response",  "confidence": 0.70, "command_at": "2026-11-14T06:10:02.120Z"},
  "eyes":      {"value": "closed",       "confidence": 0.75},
  "breathing": {"value": "motion_detected", "confidence": 0.55, "rate_bpm": 14.0},
  "visible_blood": {"value": "detected", "confidence": 0.65,
                    "regions": ["torso", "left_leg"], "method": "color"}
}
```

| Sign | `value` | Extra fields | How it is measured |
| --- | --- | --- | --- |
| `posture` | `standing` / `sitting` / `lying` / `unknown` | – | Pose keypoints on the floor plane, 15-frame vote (already used internally) |
| `movement` | `moving` / `still` / `not_testable` | `still_for_s` | Keypoint motion normalized by body size. `still_for_s` restarts after the camera moved |
| `response` | `responded` / `no_response` / `not_tested` | `command_at` | Limb motion in the window after the operator marks a spoken command |
| `eyes` | `open` / `closed` / `not_testable` | – | MediaPipe Face Landmarker blink scores on the face crop; `not_testable` when the face is turned away or too small |
| `breathing` | `motion_detected` / `no_motion_detected` / `not_testable` | `rate_bpm` (only when detected) | Optical flow on the chest while the person is still, clear spectral peak in 6–40 /min in both halves of a 15 s window. `no_motion_detected` = chest motion under 0.3 px (low confidence); irregular motion = `not_testable`. Tuned to miss faint breathing rather than report breathing that is not there |
| `visible_blood` | `detected` / `not_detected` / `not_testable` | `regions`, `method` | Deep-red pixels (green and blue ≤ 0.35 × red, which excludes skin and lips) inside body regions from keypoints (`color`), later a fine-tuned model (`model`). Red clothing triggers it |

**Moving cameras (bodycam, handheld).** These add `camera_motion`, measured from
the background:

```json
"camera_motion": {"value": "steady", "speed_px_s": 34.2},
"breathing": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
```

| `camera_motion.value` | Effect on the other signs |
| --- | --- |
| `still` | As on a fixed camera |
| `steady` (sway) / `unknown` (too little background) | Movement and response measured relative to the body (`still` confidence ≤ 0.6); breathing `not_testable` |
| `moving` (walking, turning) | Movement and response `not_testable` / `not_tested` too |

Posture, eyes and blood are judged per frame and are not affected. A sign that
is `not_testable` or `not_tested` because of the camera carries
`"reason": "camera_moving"`. Fixed cameras send no `camera_motion`.

`regions` ⊆ `head`, `torso`, `left_arm`, `right_arm`, `left_leg`, `right_leg`,
following DARPA's trauma regions. `confidence` is in [0, 1] and is a detector
confidence, not a clinical probability. Values are smoothed over `window_s`.

Rules for consumers:

- **Missing sign or `signs` absent:** not observed. That is never evidence of
  absence. `not_testable` / `not_tested` mean the camera could not tell.
- **Non-visible reports:** `occluded` / `lost` reports carry no `signs`. The
  last visible report's signs age with `observed_at`.
- **Validation:** unknown sign names or values should be rejected (keep
  `extra="forbid"` inside `signs`).

## Backend changes requested (Prince)

1. Add `signs: CameraSigns | None = None` to `CameraObservation` and
   `CVObservation`, with the models above (frozen enums, confidence 0..1,
   `extra="forbid"`).
2. **Store without disturbing approvals:** store the latest signs per track
   without bumping the scene revision on every change. Only a change of a
   sign's `value` should count as impactful, and only if it changes the
   suggestion.
3. **Suggested priority:** compute it per associated casualty from the signs,
   for example DARPA-style ordering where `visible_blood: detected` on the
   torso or legs, `breathing: no_motion_detected`, or `response: no_response`
   with `eyes: closed` push the suggestion up. Return it with the sign values
   and confidences it used (`evidence`). The rules are Prince's to define.
4. **Expose the suggestion** in state, events and the explanation API, labeled
   machine-suggested. Medic confirmation keeps using
   `POST /casualties/{id}/assessment` with `source: "human"`.

## Dashboard (Hillary)

Per casualty:

- Each sign with its confidence and age.
- The suggested priority with its evidence.
- One-click "confirm" (posts the assessment) or "override".

Show `not_testable` as grey, not as normal.

## Not proposed

- **Heart rate:** the brief excludes camera-based heart rate (rPPG), and a
  webcam at 2–3 m cannot measure it reliably.
- **CV output:** no injury type, diagnosis, severity or priority comes from
  CV.
