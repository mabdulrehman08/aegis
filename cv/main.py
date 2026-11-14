"""AEGIS CV pipeline.

Webcam (or video file) -> YOLO pose -> ByteTrack IDs -> floor mapping ->
camera observations to the backend at ``publisher.rate_hz``.

    python -m cv.main                                  # Logitech, calibrated floor
    python -m cv.main --config cv/config/phone.yaml    # phone, predefined zones
    python -m cv.main --mock-backend                   # print JSON instead of sending
    python -m cv.main --source clip.mp4 --loop         # replay a recording

Reports detections, tracking state and floor positions only. It never infers
injuries, severity or triage.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import statistics
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from cv.camera import Camera
from cv.clock import Timebase, iso_utc, session_id
from cv.homography import Calibration, CalibrationError
from cv.publisher import HttpTransport, MockTransport, Publisher
from cv.recording import load_sidecar
from cv.settings import CV_DIR, DEFAULT_CONFIG, Settings, load_settings, resolve
from cv.tracker import TrackManager
from cv.zones import ZoneMap, parse_zones

log = logging.getLogger("cv")
WINDOW = "AEGIS CV (q / Esc to quit)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AEGIS CV pipeline")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="pipeline YAML")
    parser.add_argument("--camera", type=int, help="camera index (overrides config)")
    parser.add_argument("--camera-id", help="backend camera ID, e.g. phone-1 (overrides config)")
    parser.add_argument("--source", help="video file to replay instead of the camera")
    parser.add_argument("--loop", action="store_true", help="replay: restart the file at its end")
    parser.add_argument("--no-pace", action="store_true", help="replay files as fast as possible")
    parser.add_argument("--model", help="model weights (overrides config)")
    parser.add_argument("--homography", help="calibration JSON (overrides config and recording)")
    parser.add_argument("--backend-url", help="backend base URL (overrides config)")
    parser.add_argument("--mock-backend", action="store_true", help="log JSON instead of sending it")
    parser.add_argument("--quiet-mock", action="store_true", help="mock mode: write the log file only")
    parser.add_argument("--no-display", action="store_true", help="no debug window")
    parser.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = run)")
    parser.add_argument("--summary-json", help="also write the measured run summary to this file")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.camera is not None:
        settings = replace(settings, camera=replace(settings.camera, index=args.camera))
    if args.model:
        settings = replace(settings, detector=replace(settings.detector, model=args.model))
    publisher = settings.publisher
    if args.backend_url:
        publisher = replace(publisher, base_url=args.backend_url)
    if args.camera_id:
        publisher = replace(publisher, camera_id=args.camera_id)
    return replace(settings, publisher=publisher)


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
    describe_calibration(calibration, str(path))
    return calibration


def describe_calibration(calibration: Calibration, origin: str) -> None:
    log.info("Calibration %s from %s: frame %s, %d points, RMS error %.3f m",
             calibration.calibration_ref, origin, calibration.frame_id,
             len(calibration.image_points), calibration.rms_error_m)


def resolve_mapping(settings: Settings, args: argparse.Namespace,
                    sidecar: dict | None) -> tuple[Calibration | None, ZoneMap | None]:
    """The calibration (calibrated mode) or zone map (zones mode) to publish with."""
    mode = settings.mapping.mode
    if mode == "zones":
        zones = parse_zones(settings.mapping.zones, settings.mapping.frame_id)
        log.info("Zones mapping: %s", ", ".join(f"{z.zone_id}=({z.x_m:g}, {z.y_m:g})"
                                               for z in zones.zones))
        return None, zones
    if mode != "calibrated":
        raise ValueError("mapping.mode must be 'calibrated' or 'zones'")
    if args.homography:
        return load_calibration(Path(args.homography)), None
    recorded = ((sidecar or {}).get("mapping") or {}).get("calibration")
    if recorded:
        try:
            calibration = Calibration.from_dict(recorded)
        except (CalibrationError, KeyError, ValueError) as error:
            raise ValueError(f"Recording calibration is invalid: {error}") from None
        describe_calibration(calibration, "the recording sidecar")
        return calibration, None
    return load_calibration(resolve(settings.mapping.homography)), None


def run(args: argparse.Namespace) -> int:
    settings = apply_overrides(load_settings(args.config), args)
    sidecar = load_sidecar(args.source) if args.source else None
    if sidecar and sidecar.get("camera", {}).get("camera_id") not in (None, settings.publisher.camera_id):
        log.warning("Recording was made as %s but this run publishes as %s",
                    sidecar["camera"]["camera_id"], settings.publisher.camera_id)
    base_calibration, zone_map = resolve_mapping(settings, args, sidecar)

    timebase = Timebase()
    session = session_id(timebase.to_wall(timebase.now()))
    calibration = None

    from cv.detector import Detector  # heavy import (torch) after config errors

    detector = Detector(settings.detector)
    detector.warmup(settings.camera.width, settings.camera.height)
    camera = Camera(settings.camera, args.source, pace=not args.no_pace, clock=timebase.now,
                    fps_override=(sidecar or {}).get("fps"), loop=args.loop)
    tracker = TrackManager(settings.tracking, session)

    publisher = None
    if base_calibration is not None or zone_map is not None:
        camera_id = settings.publisher.camera_id
        if args.mock_backend:
            mock_path = resolve(settings.publisher.mock_log.format(camera_id=camera_id), CV_DIR)
            transport = MockTransport(mock_path, echo=not args.quiet_mock)
            log.info("MOCK backend: requests are logged to %s", mock_path)
        else:
            transport = HttpTransport(settings.publisher.base_url, settings.publisher.timeout_s)
            log.info("Publishing as %s to %s%s", camera_id, settings.publisher.base_url,
                     f"/api/v1/simulations/{settings.publisher.scenario_id}")
        publisher = Publisher(
            settings.publisher,
            frame_id=base_calibration.frame_id if base_calibration else zone_map.frame_id,
            calibration_ref=base_calibration.calibration_ref if base_calibration else None,
            session_id=session, timebase=timebase, transport=transport, zones=zone_map)
        publisher.start()

    log.info("Session %s | camera %s | model %s on %s | source %s", session,
             settings.publisher.camera_id, settings.detector.model, detector.device, camera.label)
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    started = timebase.now()
    run_at = iso_utc(timebase.to_wall(started))
    publish_period = 1.0 / max(0.1, settings.publisher.rate_hz)
    next_publish = started
    frames = 0
    inference_ms: list[float] = []
    fps = 0.0
    last_frame_t = None
    mapping_error = None
    mapping_ready = False
    checked_size = None

    try:
        while not stopping:
            now = timebase.now()
            if args.duration and now - started >= args.duration:
                break
            frame = camera.read()
            if frame is not None:
                height, width = frame.image.shape[:2]
                if checked_size != (width, height):
                    checked_size = (width, height)
                    if zone_map is not None:
                        tracker.set_zones(zone_map, checked_size)
                        mapping_ready = True
                    elif base_calibration is not None:
                        try:
                            calibration = base_calibration.for_image_size(width, height)
                            mapping_error = None
                            if checked_size != base_calibration.image_size:
                                log.warning("Calibration was made at %dx%d; rescaled to %dx%d",
                                            *base_calibration.image_size, width, height)
                        except CalibrationError as error:
                            calibration, mapping_error = None, str(error)
                            log.error("%s", error)
                        tracker.set_calibration(calibration)
                        mapping_ready = calibration is not None
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
            if publisher is not None and mapping_ready and now >= next_publish:
                publisher.submit(tracker.reports(now), now)
                next_publish = max(next_publish + publish_period, now - publish_period)

            if not args.no_display and frame is not None:
                if not show(frame.image, tracks, tracker, calibration, zone_map, camera, publisher,
                            fps, detector, settings, args, mapping_error):
                    break
    finally:
        camera.release()
        if publisher is not None:
            publisher.stop()
        if not args.no_display:
            import cv2

            cv2.destroyAllWindows()

    summary = build_summary(run_at, frames, timebase.now() - started, inference_ms, tracker,
                            publisher, camera, settings, detector.device, calibration, zone_map)
    print_summary(summary)
    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"summary written : {path}")
    return 0


def show(image, tracks, tracker, calibration, zone_map, camera, publisher, fps, detector,
         settings, args, mapping_error) -> bool:
    import cv2

    from cv.overlay import draw_calibration, draw_hud, draw_minimap, draw_tracks, draw_zones

    canvas = image.copy()
    draw_calibration(canvas, calibration)
    draw_zones(canvas, zone_map)
    draw_tracks(canvas, tracks, settings.tracking.keypoint_min_conf)
    draw_minimap(canvas, tracks, calibration)
    pub = publisher.status() if publisher else None
    mapping = "zones" if zone_map is not None else "calibrated"
    hud = [
        f"{fps:5.1f} FPS | {settings.detector.model} ({detector.device}) | {camera.label}",
        f"{settings.publisher.camera_id} ({mapping}) | tracks {len(tracks)} | "
        f"untracked dets {tracker.untracked} | retired {tracker.retired_total}",
    ]
    warnings = []
    if pub is not None:
        mode = "MOCK" if args.mock_backend else "backend"
        hud.append(f"{mode}: accepted {pub['accepted']} rejected {pub['rejected']} "
                   f"pending {pub['pending']} net-err {pub['network_errors']}"
                   + (f" | {pub['latency_ms_median']} ms" if pub["latency_ms_median"] else ""))
        if pub["backing_off"]:
            warnings.append(f"BACKEND: {pub['last_error'][:90]}")
    if zone_map is None and calibration is None:
        warnings.append(mapping_error or "UNCALIBRATED - not publishing")
    if camera.is_file:
        warnings.append("REPLAY (recorded clip)" + (f" loop {camera.loops}" if args.loop else ""))
    draw_hud(canvas, hud, warnings)
    cv2.imshow(WINDOW, canvas)
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def build_summary(run_at, frames, elapsed, inference_ms, tracker, publisher, camera, settings,
                  device, calibration, zone_map) -> dict:
    summary = {
        "run_at_utc": run_at,
        "source": camera.label,
        "replay": camera.is_file,
        "camera_id": settings.publisher.camera_id,
        "mapping": ("predefined_zones" if zone_map is not None
                    else f"calibrated_world {calibration.calibration_ref}" if calibration else "none"),
        "model": settings.detector.model,
        "device": device,
        "frames": frames,
        "elapsed_s": round(elapsed, 1),
        "fps": round(frames / elapsed, 1) if elapsed > 0 else None,
        "inference_ms_median": round(statistics.median(inference_ms), 1) if inference_ms else None,
        "inference_ms_p95": _percentile(inference_ms, 0.95),
        "tracks_alive": len(tracker.tracks),
        "tracks_retired": tracker.retired_total,
        "tracks_created": tracker.created_total,
        "camera_reconnects": camera.reconnects,
        "replay_loops": camera.loops,
    }
    if publisher is not None:
        stats = publisher.stats
        summary.update({
            "reports_accepted": stats.accepted,
            "reports_rejected": stats.rejected,
            "reports_skipped": stats.skipped,
            "network_errors": stats.network_errors,
            "latency_ms_median": (round(statistics.median(stats.latencies_ms), 1)
                                  if stats.latencies_ms else None),
            "latency_ms_p95": _percentile(stats.latencies_ms, 0.95),
            "latency_ms_max": round(max(stats.latencies_ms), 1) if stats.latencies_ms else None,
            "last_error": stats.last_error,
        })
    return summary


def print_summary(summary: dict) -> None:
    print("\n=== CV run (measured) ===")
    print(f"source            : {summary['source']} as {summary['camera_id']} ({summary['mapping']}) "
          f"| reconnects {summary['camera_reconnects']}")
    if summary["fps"] is not None:
        print(f"frames            : {summary['frames']} in {summary['elapsed_s']} s ({summary['fps']} FPS)")
    if summary["inference_ms_median"] is not None:
        print(f"inference         : median {summary['inference_ms_median']} ms, "
              f"p95 {summary['inference_ms_p95']} ms")
    print(f"tracks            : {summary['tracks_alive']} alive, {summary['tracks_retired']} retired, "
          f"{summary['tracks_created']} created")
    if "reports_accepted" in summary:
        print(f"published         : {summary['reports_accepted']} accepted, "
              f"{summary['reports_rejected']} rejected, {summary['reports_skipped']} skipped, "
              f"{summary['network_errors']} network errors")
        if summary["latency_ms_median"] is not None:
            print(f"capture -> sent   : median {summary['latency_ms_median']} ms, "
                  f"p95 {summary['latency_ms_p95']} ms, max {summary['latency_ms_max']} ms")
        if summary["last_error"]:
            print(f"last error        : {summary['last_error']}")


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
