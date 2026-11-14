"""AEGIS CV pipeline.

Webcam (or video file) -> YOLO pose -> ByteTrack IDs -> floor homography ->
CVObservation JSON to the backend at ``publisher.rate_hz``.

    python -m cv.main                      # live camera, real backend
    python -m cv.main --mock-backend       # print JSON instead of sending
    python -m cv.main --source clip.mp4    # replay a recorded clip

Reports detections, tracking state and floor positions only. It never infers
injuries, severity or triage.
"""

from __future__ import annotations

import argparse
import logging
import signal
import statistics
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from cv.camera import Camera
from cv.clock import Timebase, session_id
from cv.homography import Calibration, CalibrationError
from cv.publisher import HttpTransport, MockTransport, Publisher
from cv.settings import DEFAULT_CONFIG, CV_DIR, load_settings, resolve
from cv.tracker import TrackManager

log = logging.getLogger("cv")
WINDOW = "AEGIS CV (q / Esc to quit)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AEGIS CV pipeline")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="pipeline YAML")
    parser.add_argument("--camera", type=int, help="camera index (overrides config)")
    parser.add_argument("--source", help="video file to replay instead of the camera")
    parser.add_argument("--no-pace", action="store_true", help="replay files as fast as possible")
    parser.add_argument("--model", help="model weights (overrides config)")
    parser.add_argument("--homography", help="calibration JSON (overrides config)")
    parser.add_argument("--backend-url", help="backend base URL (overrides config)")
    parser.add_argument("--mock-backend", action="store_true", help="log JSON instead of sending it")
    parser.add_argument("--quiet-mock", action="store_true", help="mock mode: write the log file only")
    parser.add_argument("--no-display", action="store_true", help="no debug window")
    parser.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = run)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def load_calibration(path: Path) -> Calibration | None:
    if not path.exists():
        log.warning("No calibration at %s: tracking runs, but nothing is published. "
                    "Run: python -m cv.tools.calibrate", path)
        return None
    try:
        calibration = Calibration.load(path)
    except (CalibrationError, KeyError, ValueError) as error:
        log.error("Invalid calibration %s: %s; nothing will be published", path, error)
        return None
    log.info("Calibration %s: frame %s, %d points, RMS error %.3f m",
             calibration.calibration_ref, calibration.frame_id,
             len(calibration.image_points), calibration.rms_error_m)
    return calibration


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.camera is not None:
        settings = replace(settings, camera=replace(settings.camera, index=args.camera))
    if args.model:
        settings = replace(settings, detector=replace(settings.detector, model=args.model))
    if args.backend_url:
        settings = replace(settings, publisher=replace(settings.publisher, base_url=args.backend_url))

    timebase = Timebase()
    session = session_id(timebase.to_wall(timebase.now()))
    base_calibration = load_calibration(Path(args.homography) if args.homography
                                        else resolve(settings.mapping.homography))
    calibration = None

    from cv.detector import Detector  # heavy import (torch) after config errors

    detector = Detector(settings.detector)
    detector.warmup(settings.camera.width, settings.camera.height)
    camera = Camera(settings.camera, args.source, pace=not args.no_pace, clock=timebase.now)
    tracker = TrackManager(settings.tracking, session)

    publisher = None
    if base_calibration is not None:
        if args.mock_backend:
            mock_path = resolve(settings.publisher.mock_log, CV_DIR)
            transport = MockTransport(mock_path, echo=not args.quiet_mock)
            log.info("MOCK backend: requests are logged to %s", mock_path)
        else:
            transport = HttpTransport(settings.publisher.base_url, settings.publisher.timeout_s)
            log.info("Publishing to %s%s", settings.publisher.base_url,
                     f"/api/v1/simulations/{settings.publisher.scenario_id}")
        publisher = Publisher(settings.publisher, frame_id=base_calibration.frame_id,
                              calibration_ref=base_calibration.calibration_ref, session_id=session,
                              timebase=timebase, transport=transport)
        publisher.start()

    log.info("Session %s | model %s on %s | source %s", session, settings.detector.model,
             detector.device, camera.label)
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    started = timebase.now()
    publish_period = 1.0 / max(0.1, settings.publisher.rate_hz)
    next_publish = started
    frames = 0
    inference_ms: list[float] = []
    fps = 0.0
    last_frame_t = None
    resolution_error = None
    checked_size = None

    try:
        while not stopping:
            now = timebase.now()
            if args.duration and now - started >= args.duration:
                break
            frame = camera.read()
            if frame is not None:
                height, width = frame.image.shape[:2]
                if base_calibration is not None and checked_size != (width, height):
                    checked_size = (width, height)
                    try:
                        calibration = base_calibration.for_image_size(width, height)
                        resolution_error = None
                        if (width, height) != base_calibration.image_size:
                            log.warning("Calibration was made at %dx%d; rescaled to %dx%d",
                                        *base_calibration.image_size, width, height)
                    except CalibrationError as error:
                        calibration, resolution_error = None, str(error)
                        log.error("%s", error)
                    tracker.set_calibration(calibration)
                detections, inference_s = detector(frame.image)
                tracks = tracker.update(detections, frame.t)
                frames += 1
                inference_ms.append(inference_s * 1000.0)
                if last_frame_t is not None:
                    instant = 1.0 / max(1e-6, frame.t - last_frame_t)
                    fps = instant if fps == 0 else 0.9 * fps + 0.1 * instant
                last_frame_t = frame.t
            else:
                if camera.finished:
                    break
                tracks = tracker.update([], now)  # time passes: occluded / stale / lost

            now = timebase.now()
            if publisher is not None and calibration is not None and now >= next_publish:
                publisher.submit(tracker.reports(now), now)
                next_publish = max(next_publish + publish_period, now - publish_period)

            if not args.no_display and frame is not None:
                if not show(frame.image, tracks, tracker, calibration, camera, publisher, fps,
                            detector, settings, args, resolution_error):
                    break
    finally:
        camera.release()
        if publisher is not None:
            publisher.stop()
        if not args.no_display:
            import cv2

            cv2.destroyAllWindows()

    elapsed = timebase.now() - started
    print_summary(frames, elapsed, inference_ms, tracker, publisher, camera)
    return 0


def show(image, tracks, tracker, calibration, camera, publisher, fps, detector, settings, args,
         resolution_error) -> bool:
    import cv2

    from cv.overlay import draw_calibration, draw_hud, draw_minimap, draw_tracks

    canvas = image.copy()
    draw_calibration(canvas, calibration)
    draw_tracks(canvas, tracks, settings.tracking.keypoint_min_conf)
    draw_minimap(canvas, tracks, calibration)
    pub = publisher.status() if publisher else None
    hud = [
        f"{fps:5.1f} FPS | {settings.detector.model} ({detector.device}) | {camera.label}",
        f"tracks {len(tracks)} | untracked dets {tracker.untracked} | retired {tracker.retired_total}",
    ]
    warnings = []
    if pub is not None:
        mode = "MOCK" if args.mock_backend else "backend"
        hud.append(f"{mode}: accepted {pub['accepted']} rejected {pub['rejected']} "
                   f"pending {pub['pending']} net-err {pub['network_errors']}"
                   + (f" | {pub['latency_ms_median']} ms" if pub["latency_ms_median"] else ""))
        if pub["backing_off"]:
            warnings.append(f"BACKEND: {pub['last_error'][:90]}")
    if calibration is None:
        warnings.append(resolution_error or "UNCALIBRATED - not publishing")
    if camera.is_file:
        warnings.append("REPLAY (recorded clip)")
    cv2.imshow(WINDOW, canvas)
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


def print_summary(frames, elapsed, inference_ms, tracker, publisher, camera) -> None:
    print("\n=== Perception run (measured) ===")
    print(f"source            : {camera.label} | reconnects {camera.reconnects}")
    print(f"frames            : {frames} in {elapsed:.1f} s ({frames / elapsed:.1f} FPS)" if elapsed > 0 else "")
    if inference_ms:
        print(f"inference         : mean {statistics.mean(inference_ms):.1f} ms, "
              f"median {statistics.median(inference_ms):.1f} ms")
    print(f"tracks            : {len(tracker.tracks)} alive, {tracker.retired_total} retired, "
          f"{tracker.created_total} created")
    if publisher is not None:
        stats = publisher.stats
        print(f"published         : {stats.accepted} accepted, {stats.rejected} rejected, "
              f"{stats.skipped} skipped, {stats.network_errors} network errors")
        if stats.latencies_ms:
            print(f"capture -> sent   : median {statistics.median(stats.latencies_ms):.1f} ms, "
                  f"max {max(stats.latencies_ms):.1f} ms")
        if stats.last_error:
            print(f"last error        : {stats.last_error}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        return run(args)
    except (FileNotFoundError, ValueError) as error:
        log.error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
