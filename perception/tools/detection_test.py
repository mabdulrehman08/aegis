"""Phase 0 detection test harness.

Runs person-only YOLO detection on the live webcam (or a video file), shows
annotated frames, and appends one row of measured numbers per run to
``perception/eval/detection_runs.csv`` so models can be compared per scenario.

    python -m perception.tools.detection_test --model yolo11n.pt --expected 3 \
        --scenario lying_side_partial_occlusion --duration 20

It reports detections only. Nothing here infers injury, severity or triage,
and no frames are written to disk.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

PERCEPTION_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PERCEPTION_DIR / "eval" / "detection_runs.csv"
MODELS_DIR = PERCEPTION_DIR / "models"
PERSON_CLASS = 0
WINDOW = "AEGIS detection test (q / Esc to stop)"
MAX_READ_FAILURES_S = 3.0

CSV_FIELDS = [
    "run_at_utc",
    "scenario",
    "model",
    "source",
    "resolution",
    "device",
    "imgsz",
    "conf_threshold",
    "expected_people",
    "duration_s",
    "frames",
    "frames_meeting_expected",
    "pct_frames_meeting_expected",
    "mean_people_per_frame",
    "detections",
    "mean_confidence",
    "fps",
    "mean_inference_ms",
    "p95_inference_ms",
]


@dataclass
class RunStats:
    """Per-run accumulator. Pure Python so it is testable without a camera."""

    expected: int
    frames: int = 0
    frames_meeting_expected: int = 0
    detections: int = 0
    confidence_sum: float = 0.0
    inference_ms: list[float] = field(default_factory=list)

    def add_frame(self, confidences: Sequence[float], inference_s: float) -> None:
        self.frames += 1
        self.detections += len(confidences)
        self.confidence_sum += float(sum(confidences))
        if len(confidences) >= self.expected:
            self.frames_meeting_expected += 1
        self.inference_ms.append(inference_s * 1000.0)

    def summary(self, elapsed_s: float) -> dict[str, Any]:
        """Measured values only; anything that cannot be computed is ``None``."""
        has_frames = self.frames > 0
        latencies = sorted(self.inference_ms)
        p95_index = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1)))) if latencies else 0
        return {
            "duration_s": round(elapsed_s, 2),
            "frames": self.frames,
            "frames_meeting_expected": self.frames_meeting_expected,
            "pct_frames_meeting_expected": (
                round(100.0 * self.frames_meeting_expected / self.frames, 1) if has_frames else None
            ),
            "mean_people_per_frame": round(self.detections / self.frames, 2) if has_frames else None,
            "detections": self.detections,
            "mean_confidence": (
                round(self.confidence_sum / self.detections, 3) if self.detections else None
            ),
            "fps": round(self.frames / elapsed_s, 1) if has_frames and elapsed_s > 0 else None,
            "mean_inference_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p95_inference_ms": round(latencies[p95_index], 1) if latencies else None,
        }


def append_csv(path: Path, row: dict[str, Any]) -> None:
    """Append one run; unknown values are written as empty cells, never zeros."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({key: "" if row.get(key) is None else row[key] for key in CSV_FIELDS})


def resolve_model(name: str) -> str:
    """Keep downloaded weights in perception/models/ instead of the working dir."""
    candidate = Path(name)
    if candidate.exists() or candidate.parent != Path("."):
        return name
    return str(MODELS_DIR / name)


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def open_capture(args: argparse.Namespace):
    import cv2

    if args.source:
        return cv2.VideoCapture(args.source)
    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
    capture = cv2.VideoCapture(args.camera, backend)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    return capture


def draw_hud(frame, lines: Sequence[str]) -> None:
    import cv2

    for index, text in enumerate(lines):
        origin = (12, 28 + 26 * index)
        cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)


def run(args: argparse.Namespace) -> int:
    import cv2
    from ultralytics import YOLO

    device = pick_device(args.device)
    model = YOLO(resolve_model(args.model))
    source_label = args.source or f"camera:{args.camera}"

    capture = open_capture(args)
    if not capture.isOpened():
        print(
            f"ERROR: could not open {source_label}. On macOS grant camera access to your "
            "terminal app (System Settings > Privacy & Security > Camera), then retry. "
            "Try another --camera index if the wrong device opens.",
            file=sys.stderr,
        )
        return 2

    def predict(image):
        return model.predict(
            image, classes=[PERSON_CLASS], conf=args.conf, imgsz=args.imgsz,
            device=device, verbose=False,
        )[0]

    # Warm up on a real frame (same shape as the run) so model load and
    # first-call compilation do not skew latency. That frame is then measured too.
    pending = None
    deadline = time.perf_counter() + MAX_READ_FAILURES_S
    while pending is None and time.perf_counter() < deadline:
        ok, frame = capture.read()
        if ok and frame is not None:
            pending = frame
        elif args.source:
            break
        else:
            time.sleep(0.05)
    if pending is None:
        capture.release()
        print(f"ERROR: {source_label} opened but delivered no frames.", file=sys.stderr)
        return 2
    predict(pending)

    stats = RunStats(expected=args.expected)
    resolution = ""
    failing_since: float | None = None
    started = time.perf_counter()
    try:
        while True:
            now = time.perf_counter()
            if args.duration > 0 and now - started >= args.duration:
                break
            if pending is not None:
                ok, frame, pending = True, pending, None
            else:
                ok, frame = capture.read()
            if not ok or frame is None:
                if args.source:
                    break  # end of file
                failing_since = failing_since or now
                if now - failing_since > MAX_READ_FAILURES_S:
                    print("ERROR: camera stopped delivering frames.", file=sys.stderr)
                    break
                time.sleep(0.05)
                continue
            failing_since = None
            resolution = f"{frame.shape[1]}x{frame.shape[0]}"

            tick = time.perf_counter()
            result = predict(frame)
            confidences = result.boxes.conf.tolist() if result.boxes is not None else []
            stats.add_frame(confidences, time.perf_counter() - tick)

            if not args.no_display:
                annotated = result.plot()
                elapsed = time.perf_counter() - started
                draw_hud(annotated, [
                    f"{args.model} | {args.scenario}",
                    f"people {len(confidences)} / expected {args.expected}",
                    f"{stats.frames / elapsed:.1f} FPS | {elapsed:.0f}s"
                    + (f" / {args.duration:.0f}s" if args.duration > 0 else ""),
                ])
                cv2.imshow(WINDOW, annotated)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        elapsed_s = time.perf_counter() - started
        capture.release()
        if not args.no_display:
            cv2.destroyAllWindows()

    if stats.frames == 0:
        print("ERROR: no frames were processed; nothing recorded.", file=sys.stderr)
        return 2

    row = {
        "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": args.scenario,
        "model": args.model,
        "source": source_label,
        "resolution": resolution,
        "device": device,
        "imgsz": args.imgsz,
        "conf_threshold": args.conf,
        "expected_people": args.expected,
        **stats.summary(elapsed_s),
    }
    append_csv(Path(args.csv), row)
    print_report(row, Path(args.csv))
    return 0


def print_report(row: dict[str, Any], csv_path: Path) -> None:
    def show(value: Any, unit: str = "") -> str:
        return "n/a" if value is None else f"{value}{unit}"

    print("\n=== Detection run (measured) ===")
    print(f"scenario             : {row['scenario']}")
    print(f"model                : {row['model']}")
    print(f"source               : {row['source']} ({row['resolution']})")
    print(f"device / imgsz / conf: {row['device']} / {row['imgsz']} / {row['conf_threshold']}")
    print(f"frames               : {row['frames']} in {row['duration_s']} s")
    print(
        f"frames with >= {row['expected_people']} people: "
        f"{show(row['pct_frames_meeting_expected'], ' %')} "
        f"({row['frames_meeting_expected']}/{row['frames']})"
    )
    print(f"mean people / frame  : {show(row['mean_people_per_frame'])}")
    print(f"mean confidence      : {show(row['mean_confidence'])} over {row['detections']} detections")
    print(f"FPS                  : {show(row['fps'])}")
    print(
        f"mean inference       : {show(row['mean_inference_ms'], ' ms')} "
        f"(p95 {show(row['p95_inference_ms'], ' ms')})"
    )
    print(f"appended to          : {csv_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="yolo11n.pt", help="yolo11n.pt, yolo11s.pt, yolo11n-pose.pt or a path")
    parser.add_argument("--camera", type=int, default=0, help="camera index")
    parser.add_argument("--expected", type=int, default=1, help="people that should be visible (N)")
    parser.add_argument("--conf", type=float, default=0.4, help="detection confidence threshold")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds to run (0 = until q or end of file)")
    parser.add_argument("--scenario", default="unlabeled", help='free-text label, e.g. "lying_side_partial_occlusion"')
    parser.add_argument("--imgsz", type=int, default=640, help="inference image size")
    parser.add_argument("--width", type=int, default=1280, help="requested capture width")
    parser.add_argument("--height", type=int, default=720, help="requested capture height")
    parser.add_argument("--device", default="auto", help="auto, cpu or mps")
    parser.add_argument("--source", default=None, help="video file to use instead of the camera")
    parser.add_argument("--no-display", action="store_true", help="do not open a window")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="results file to append to")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.expected < 0 or not 0.0 <= args.conf <= 1.0 or args.duration < 0:
        print("ERROR: --expected and --duration must be >= 0 and --conf within [0, 1].", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
