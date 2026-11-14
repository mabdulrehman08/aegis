"""Scripted fake casualties with observed signs, for building without a camera.

    python -m cv.tools.fake_casualties                       # live: positions to the backend
    python -m cv.tools.fake_casualties --send-signs          # once the backend accepts "signs"
    python -m cv.tools.fake_casualties --mock-backend --send-signs --fast   # whole scene to a JSONL now

Plays a fixed 90 s scene on the staged floor (map x 12-16 m, y 6-10 m) through
the same ``Publisher`` as ``cv.main``, so registration, IDs, heartbeats and the
``signs`` object (cv/CONTRACT_PROPOSAL.md) are exactly what the camera sends.
It registers its own camera (``fake-cam-1``) so it never conflicts with the
real cameras. Spoken commands are scripted at 10 s and 50 s; one person
deteriorates at 45 s and one walks out of view at 30-34 s.

The scene is synthetic: positions and signs are made up, no imagery is
involved. The legend printed at start says what each fake track is meant to
exercise; that meaning is never sent.
"""

from __future__ import annotations

import argparse
import logging
import math
import signal
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Sequence

from cv.clock import Timebase, session_id
from cv.publisher import HttpTransport, MockTransport, Publisher
from cv.settings import CV_DIR, DEFAULT_CONFIG, load_settings, resolve
from cv.signs import SCHEMA
from cv.tracker import TrackReport

log = logging.getLogger("cv.fake")
FRAME_ID = "demo-floor-1"
CALIBRATION_REF = "fake-calibration-001"
SCENE_S = 90.0
COMMANDS_S = (10.0, 50.0)
RESPONSE_DELAY_S = 2.0       # a responder moves this long after the command
COMMAND_WINDOW_S = 8.0       # matches signs.command_window_s
WINDOW_S = 10.0


@dataclass(frozen=True)
class Phase:
    start_s: float
    posture: str                  # standing / sitting / lying
    moving: bool
    eyes: str                     # open / closed / not_testable
    breathing: str                # motion_detected / no_motion_detected / not_testable
    rate_bpm: float | None = None
    blood: tuple[str, ...] = ()   # regions with visible blood; () = not_detected
    responds: bool = True         # to a command given during this phase


@dataclass(frozen=True)
class Persona:
    local_id: str
    legend: str                   # console only, never sent
    phases: tuple[Phase, ...]
    path: tuple[tuple[float, float], ...]   # one point = fixed; more = walks back and forth
    period_s: float = 20.0
    hidden: tuple[tuple[float, float], ...] = ()   # out of view (occluded) intervals
    confidence: float = 0.85


SCENE: tuple[Persona, ...] = (
    Persona("t1", "lying, visible blood on torso and leg, no response, eyes closed, fast breathing",
            (Phase(0, "lying", False, "closed", "motion_detected", 26, ("left_leg", "torso"), False),),
            ((13.0, 7.0),)),
    Persona("t2", "lying, no response, eyes closed, no chest motion",
            (Phase(0, "lying", False, "closed", "no_motion_detected", None, (), False),),
            ((15.0, 8.5),)),
    Persona("t3", "walking, blood on left arm, responds; out of view 30-34 s",
            (Phase(0, "standing", True, "open", "not_testable", None, ("left_arm",), True),),
            ((12.5, 9.5), (15.5, 9.5)), hidden=((30.0, 34.0),), confidence=0.7),
    Persona("t4", "sitting and responding, then at 45 s lies down, eyes closed, stops responding",
            (Phase(0, "sitting", False, "open", "motion_detected", 16, (), True),
             Phase(45, "lying", False, "closed", "motion_detected", 10, (), False)),
            ((14.0, 8.0),)),
    Persona("t5", "standing, moving, responds, nothing visible",
            (Phase(0, "standing", True, "open", "not_testable", None, (), True),),
            ((12.5, 6.5), (13.5, 6.5)), period_s=12.0),
)


def phase_at(persona: Persona, t: float) -> Phase:
    return [p for p in persona.phases if p.start_s <= t][-1]


def position_at(persona: Persona, t: float) -> tuple[float, float]:
    if len(persona.path) == 1:
        return persona.path[0]
    (x0, y0), (x1, y1) = persona.path[0], persona.path[-1]
    share = 0.5 - 0.5 * math.cos(2 * math.pi * t / persona.period_s)
    # 0.2 m steps, like the tracker's deadband, so walkers do not post every tick.
    return (round(round((x0 + (x1 - x0) * share) / 0.2) * 0.2, 2),
            round(round((y0 + (y1 - y0) * share) / 0.2) * 0.2, 2))


def visible_at(persona: Persona, t: float) -> bool:
    return not any(start <= t < end for start, end in persona.hidden)


def _still_since(persona: Persona, t: float) -> float | None:
    """Scene time the current still run started, or None while moving.

    A posture change (e.g. sitting -> lying) is movement, so it restarts the run.
    """
    if phase_at(persona, t).moving:
        return None
    since, posture = None, None
    for phase in persona.phases:
        if phase.start_s > t:
            break
        if phase.moving:
            since = None
        elif since is None or phase.posture != posture:
            since = phase.start_s
        posture = phase.posture
    return since


def _response(persona: Persona, t: float, iso) -> dict:
    given = [c for c in COMMANDS_S if c <= t]
    if not given:
        return {"value": "not_tested", "confidence": 0.0}
    command = given[-1]
    at = {"command_at": iso(command)}
    responds = phase_at(persona, command).responds
    if responds and t - command >= RESPONSE_DELAY_S:
        return {"value": "responded", "confidence": 0.8, **at}
    if not responds and t - command >= COMMAND_WINDOW_S:
        return {"value": "no_response", "confidence": 0.72, **at}
    return {"value": "not_tested", "confidence": 0.0, **at}


def signs_at(persona: Persona, t: float, iso) -> dict:
    """The ``signs`` object cv.signs would report for this persona at scene time t."""
    phase = phase_at(persona, t)
    since = _still_since(persona, t)
    movement = ({"value": "moving", "confidence": 0.9, "still_for_s": 0.0} if since is None
                else {"value": "still", "confidence": 0.9, "still_for_s": round(t - since, 1)})
    if phase.breathing == "motion_detected" and (since is None or t - since < 11.0):
        breathing = {"value": "not_testable", "confidence": 0.0}       # needs ~11 s still
    elif phase.breathing == "motion_detected":
        breathing = {"value": "motion_detected", "confidence": 0.6, "rate_bpm": phase.rate_bpm}
    elif phase.breathing == "no_motion_detected":
        breathing = {"value": "no_motion_detected", "confidence": 0.45}
    else:
        breathing = {"value": "not_testable", "confidence": 0.0}
    blood = ({"value": "detected", "confidence": 0.8, "regions": sorted(phase.blood), "method": "color"}
             if phase.blood else {"value": "not_detected", "confidence": 0.7, "method": "color"})
    eyes = {"value": phase.eyes, "confidence": 0.0 if phase.eyes == "not_testable" else 0.75}
    return {
        "schema": SCHEMA,
        "window_s": WINDOW_S,
        "posture": {"value": phase.posture, "confidence": 0.8},
        "movement": movement,
        "response": _response(persona, t, iso),
        "eyes": eyes,
        "breathing": breathing,
        "visible_blood": blood,
    }


class Scene:
    """Turns scene time into TrackReports (with signs) for the publisher."""

    def __init__(self, personas: Sequence[Persona], session: str, iso, start_t: float):
        self.personas = personas
        self.session = session
        self.iso = iso
        self.start_t = start_t
        self.last_seen: dict[str, float] = {}

    def track_id(self, persona: Persona) -> str:
        return f"{self.session}-{persona.local_id}"

    def reports(self, now: float, scene_s: float | None = None) -> list[TrackReport]:
        t = (now - self.start_t) % SCENE_S if scene_s is None else scene_s
        out = []
        for persona in self.personas:
            track_id = self.track_id(persona)
            visible = visible_at(persona, t)
            if visible:
                self.last_seen[track_id] = now
            x, y = position_at(persona, t)
            # cv.signs times are pipeline seconds; map scene seconds onto them for command_at.
            iso = lambda s, base=now - t: self.iso(base + s)  # noqa: E731
            out.append(TrackReport(
                track_id=track_id, state="visible" if visible else "occluded", observed_t=now,
                first_seen_t=self.start_t, last_seen_t=self.last_seen.get(track_id, now),
                x_m=x, y_m=y, confidence=persona.confidence, capture_t=now,
                signs=signs_at(persona, t, iso) if visible else None))
        return out

    def lost(self, now: float) -> list[TrackReport]:
        return [replace(report, state="lost", signs=None) for report in self.reports(now)]


def summary_line(report: TrackReport) -> str:
    where = f"({report.x_m:5.2f}, {report.y_m:5.2f})"
    if report.signs is None:
        return f"  {report.track_id[-2:]} {report.state:8s} {where}"
    s = report.signs
    breathing = s["breathing"].get("rate_bpm") or s["breathing"]["value"]
    blood = ",".join(s["visible_blood"].get("regions", [])) or s["visible_blood"]["value"]
    return (f"  {report.track_id[-2:]} {report.state:8s} {where} {s['posture']['value']:8s} "
            f"{s['movement']['value']:6s} resp={s['response']['value']:11s} eyes={s['eyes']['value']:6s} "
            f"breath={breathing!s:18s} blood={blood}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scripted fake casualties with observed signs")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend-url", help="override publisher.base_url")
    parser.add_argument("--scenario", help="override publisher.scenario_id")
    parser.add_argument("--camera-id", default="fake-cam-1")
    parser.add_argument("--send-signs", action="store_true",
                        help="include signs (the backend must accept them; see CONTRACT_PROPOSAL.md)")
    parser.add_argument("--mock-backend", action="store_true",
                        help="log requests to cv/artifacts/mock_<camera>.jsonl instead of HTTP")
    parser.add_argument("--fast", action="store_true",
                        help="play the whole scene instantly, timestamps ending now")
    parser.add_argument("--loop", action="store_true", help="repeat the scene until Ctrl-C")
    parser.add_argument("--rate", type=float, default=5.0, help="publish ticks per second")
    parser.add_argument("--print-every", type=float, default=5.0, help="console table interval (s)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


class _FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def run(args: argparse.Namespace, transport=None) -> int:
    settings = load_settings(args.config).publisher
    settings = replace(settings, camera_id=args.camera_id, send_signs=args.send_signs, rate_hz=args.rate,
                       base_url=args.backend_url or settings.base_url,
                       scenario_id=args.scenario or settings.scenario_id)
    if args.fast and args.loop:
        log.error("--fast plays the scene once; drop --loop")
        return 2
    clock = _FakeClock() if args.fast else time.monotonic
    wall_start = datetime.now(timezone.utc) - (timedelta(seconds=SCENE_S) if args.fast else timedelta(0))
    timebase = Timebase(monotonic=clock, wall=lambda: wall_start)
    session = session_id(wall_start)
    if transport is not None:
        destination = "injected transport"
    elif args.mock_backend:
        path = resolve(settings.mock_log.format(camera_id=settings.camera_id), CV_DIR)
        path.unlink(missing_ok=True)
        transport = MockTransport(path, echo=False)
        destination = str(path)
    else:
        transport = HttpTransport(settings.base_url, settings.timeout_s)
        destination = f"{settings.base_url}/api/v1/simulations/{settings.scenario_id}"
    publisher = Publisher(settings, frame_id=FRAME_ID, calibration_ref=CALIBRATION_REF, session_id=session,
                          timebase=timebase, transport=transport, clock=clock)
    scene = Scene(SCENE, session, timebase.iso, timebase.now())

    print(f"Fake casualties as {settings.camera_id} -> {destination}"
          f" | signs {'SENT' if settings.send_signs else 'not sent (positions only)'}")
    for persona in SCENE:
        print(f"  {persona.local_id}: {persona.legend}")
    print(f"  commands at {', '.join(f'{c:.0f}' for c in COMMANDS_S)} s; scene {SCENE_S:.0f} s"
          + (", looping" if args.loop else ""))

    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    period = 1.0 / max(0.1, args.rate)
    next_print = 0.0
    tick = 0
    try:
        while not stopping:
            now = timebase.now()
            elapsed = now - scene.start_t
            if not args.loop and elapsed > SCENE_S:
                break
            reports = scene.reports(now)
            publisher.submit(reports, now)
            publisher.step()
            if elapsed >= next_print:
                next_print += args.print_every
                status = publisher.status()
                print(f"[{elapsed % SCENE_S:5.1f} s] accepted {status['accepted']} rejected {status['rejected']}"
                      + (f" | last error: {status['last_error']}" if status["rejected"] else ""))
                for report in reports:
                    print(summary_line(report))
            tick += 1
            if args.fast:
                clock.t += period
            else:
                time.sleep(max(0.0, scene.start_t + tick * period - timebase.now()))
    finally:
        publisher.submit(scene.lost(timebase.now()), timebase.now())
        publisher.step()
    status = publisher.status()
    print(f"Done: {status['accepted']} accepted, {status['rejected']} rejected, "
          f"{status['network_errors']} network errors" +
          (f"; last error: {status['last_error']}" if status["last_error"] else ""))
    if status["rejected"] and settings.send_signs and "signs" in (status["last_error"] or ""):
        print("The backend does not accept 'signs' yet; run without --send-signs, "
              "or with --mock-backend to see the payloads.")
    return 0 if status["accepted"] and not status["rejected"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
