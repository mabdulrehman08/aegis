"""Score a recording against the staged scene's ground truth (Phase 3).

    python -m cv.tools.evaluate --source cv/recordings/lying_two-<time>.mp4 \
        --truth cv/eval/truth/lying_two.yaml
    python -m cv.tools.evaluate --source clip.mp4 --expected 2 --scenario standing_two

Runs the same detector, tracker and floor mapping as ``cv.main`` on every frame,
timed by the recording's own frame times (so occlusion and lost timing behave as
live), then prints and saves the metrics described in ``cv/evaluation.py``:
JSON to ``cv/eval/runs/`` and one CSV row to ``cv/eval/evaluation_runs.csv``.
Only numbers are written; no frames are saved.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from cv.clock import iso_utc
from cv.evaluation import Scorer, Truth, count_truth, parse_truth
from cv.homography import CalibrationError
from cv.recording import load_sidecar, sidecar_frame_times
from cv.settings import CV_DIR, DEFAULT_CONFIG, load_settings
from cv.tracker import TrackManager

EVAL_DIR = CV_DIR / "eval"
CSV_FIELDS = [
    "run_at_utc", "scenario", "source", "model", "device", "calibration_ref", "ground_truth",
    "expected_people", "frames", "duration_s", "frames_all_detected_pct", "frames_exact_count_pct",
    "mean_detected", "tracks_seen", "extra_track_ids", "id_switches", "id_switches_per_min",
    "false_positive_tracks", "false_positive_frames_pct", "mapping_error_cm_median",
    "mapping_error_cm_p90", "mapping_error_cm_max", "published_error_cm_median",
    "posture_agreement_pct", "processing_fps", "inference_ms_median", "inference_ms_p95",
]
log = logging.getLogger("cv.evaluate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score a recording against ground truth")
    parser.add_argument("--source", required=True, help="recorded video (with its .json sidecar)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--truth", help="ground-truth YAML (positions, optional postures)")
    group.add_argument("--expected", type=int, help="number of people in view (count-only scoring)")
    parser.add_argument("--scenario", help="label (default: truth file or sidecar)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--model", help="model weights (overrides config)")
    parser.add_argument("--homography", help="calibration JSON (overrides the recording's)")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = all)")
    parser.add_argument("--out-dir", default=str(EVAL_DIR), help="where runs/ and the CSV go")
    parser.add_argument("--no-save", action="store_true", help="print only")
    parser.set_defaults(camera=None, camera_id=None, backend_url=None)  # for cv.main.apply_overrides
    return parser


def load_truth(args, sidecar) -> Truth:
    if args.truth:
        import yaml

        truth = parse_truth(yaml.safe_load(Path(args.truth).read_text(encoding="utf-8")))
    else:
        truth = count_truth(args.expected, (sidecar or {}).get("scenario", "unlabeled"))
    if args.scenario:
        truth = Truth(args.scenario, truth.frame_id, truth.people, truth.match_radius_m)
    return truth


def evaluate(args: argparse.Namespace) -> dict:
    import cv2

    from cv.detector import Detector
    from cv.main import apply_overrides, resolve_mapping

    settings = apply_overrides(load_settings(args.config), args)
    sidecar = load_sidecar(args.source)
    truth = load_truth(args, sidecar)
    base_calibration, zone_map = resolve_mapping(settings, args, sidecar)
    if truth.positioned and base_calibration is None:
        log.warning("No floor calibration: scoring counts only, not positions")
        truth = count_truth(len(truth.people), truth.scenario)
    if (truth.positioned and truth.frame_id and base_calibration is not None
            and truth.frame_id != base_calibration.frame_id):
        raise ValueError(f"truth frame {truth.frame_id!r} != calibration frame {base_calibration.frame_id!r}")

    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise FileNotFoundError(f"Cannot open {args.source}")
    fps = float((sidecar or {}).get("fps") or capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_times = sidecar_frame_times(sidecar)
    detector = Detector(settings.detector)
    detector.warmup(int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or settings.camera.width,
                    int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or settings.camera.height)
    tracker = TrackManager(settings.tracking, "eval")
    scorer = Scorer(truth)
    inference_ms: list[float] = []
    checked_size = None
    calibration = None
    index = 0
    started = time.perf_counter()
    while True:
        ok, image = capture.read()
        if not ok:
            break
        size = (image.shape[1], image.shape[0])
        if size != checked_size:
            checked_size = size
            if zone_map is not None:
                tracker.set_zones(zone_map, size)
            elif base_calibration is not None:
                try:
                    calibration = base_calibration.for_image_size(*size)
                except CalibrationError as error:
                    raise ValueError(str(error)) from None
                tracker.set_calibration(calibration)
        t = frame_times[index] if frame_times and index < len(frame_times) else index / fps
        detections, inference_s = detector(image)
        inference_ms.append(inference_s * 1000.0)
        scorer.add_frame(t, tracker.update(detections, t))
        index += 1
        if args.max_frames and index >= args.max_frames:
            break
    capture.release()
    processing_s = time.perf_counter() - started
    if not index:
        raise ValueError(f"No frames read from {args.source}")

    summary = scorer.summary()
    ordered = sorted(inference_ms)
    return {
        "run_at_utc": iso_utc(datetime.now(timezone.utc)),
        "source": Path(args.source).name,
        "model": settings.detector.model,
        "device": detector.device,
        "calibration_ref": calibration.calibration_ref if calibration else None,
        "mapping": "predefined_zones" if zone_map is not None else "calibrated_world",
        **summary,
        "tracks_created": tracker.created_total,
        "processing_fps": round(index / processing_s, 1) if processing_s > 0 else None,
        "inference_ms_median": round(statistics.median(inference_ms), 1),
        "inference_ms_p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1),
    }


def print_result(result: dict) -> None:
    def show(label, key, unit=""):
        value = result.get(key)
        print(f"  {label:<28}: {'n/a' if value is None else f'{value}{unit}'}")

    print(f"\n=== Evaluation: {result['scenario']} ({result['source']}) ===")
    print(f"  {'ground truth':<28}: {result['ground_truth']}, {result['expected_people']} people, "
          f"{result['frames']} frames / {result['duration_s']} s")
    show("everyone detected", "frames_all_detected_pct", " % of frames")
    show("exact person count", "frames_exact_count_pct", " % of frames")
    show("track IDs seen (extra)", "tracks_seen")
    show("ID switches", "id_switches")
    show("ID switches per minute", "id_switches_per_min")
    show("false-positive tracks", "false_positive_tracks")
    show("mapping error median", "mapping_error_cm_median", " cm")
    show("mapping error p90", "mapping_error_cm_p90", " cm")
    show("mapping bias (dx, dy)", "mapping_bias_cm", " cm")
    show("published position error", "published_error_cm_median", " cm")
    show("posture agreement", "posture_agreement_pct", " %")
    show("processing", "processing_fps", " FPS")
    show("inference median / p95", "inference_ms_median", " ms")
    for person in result.get("people", []):
        print(f"    {person['id']}: detected {person['detected_pct']} %, switches {person['id_switches']}, "
              f"IDs {person['track_ids']}, longest gap {person['longest_gap_s']} s, "
              f"error median {person['error_cm_median']} cm, posture {person['posture_agreement_pct']} %")


def save(result: dict, out_dir: Path) -> tuple[Path, Path]:
    runs = out_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = result["run_at_utc"].replace(":", "").replace("-", "").split(".")[0]
    json_path = runs / f"{result['scenario']}-{stamp}.json"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    csv_path = out_dir / "evaluation_runs.csv"
    new = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({key: ("" if result.get(key) is None else result.get(key)) for key in CSV_FIELDS})
    return json_path, csv_path


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        result = evaluate(args)
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print_result(result)
    if not args.no_save:
        json_path, csv_path = save(result, Path(args.out_dir))
        print(f"\n  saved {json_path}\n  appended {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
