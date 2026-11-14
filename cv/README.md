# CV and mapping — AskAr

Reserved module; no CV pipeline is implemented yet.
Publish `CVObservation` payloads defined in `../shared/CONTRACT.md`.
Report person detections, detector confidence, and mapped coordinates only.
Confidence is detection confidence, not medical certainty. Do not infer severity,
triage, clinical state, or dispatch priority from images. Coordinate calibration
must be agreed before mixing camera and hardware positions.
