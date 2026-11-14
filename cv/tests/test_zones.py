from datetime import datetime, timezone

import numpy as np
import pytest

from cv.camera import Camera
from cv.clock import Timebase
from cv.publisher import Publisher
from cv.settings import CameraSettings, PublisherSettings, TrackingSettings
from cv.tests.helpers import det, standing_box
from cv.tests.test_camera import Clock, FakeCapture
from cv.tests.test_publisher import FakeClock, FakeTransport
from cv.tracker import TrackManager, TrackReport
from cv.zones import ZoneError, parse_zones

SIZE = (1000, 1000)
HALVES = [
    {"id": "left", "x_m": 3.0, "y_m": 2.0, "image_polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]},
    {"id": "right", "x_m": 6.0, "y_m": 2.0, "image_polygon": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]},
]


def test_zone_lookup_uses_normalized_polygons_and_includes_the_edges():
    zones = parse_zones(HALVES, "demo-floor-1")
    assert zones.zone_at((100, 900), SIZE).zone_id == "left"
    assert zones.zone_at((900, 900), SIZE).zone_id == "right"
    assert zones.zone_at((500, 1000), SIZE).zone_id == "left"    # shared edge: first match
    assert zones.zone_at((1000, 1000), SIZE).zone_id == "right"  # bottom-right corner
    assert zones.zone_at((1200, 500), SIZE).zone_id == "right"   # clamped into the frame
    assert zones.zone_at(None, SIZE) is None


def test_full_frame_is_the_default_and_registration_matches_the_backend_shape():
    zones = parse_zones([{"id": "zone-b", "x_m": 3, "y_m": 2}], "demo-floor-1")
    assert zones.zone_at((0, 0), SIZE).zone_id == "zone-b"
    assert zones.registration() == {"zone-b": {"frame_id": "demo-floor-1", "x_m": 3.0, "y_m": 2.0}}


@pytest.mark.parametrize("raw", [
    [],
    [{"id": "a", "x_m": 1}],
    [{"id": "a", "x_m": 1, "y_m": 1}, {"id": "a", "x_m": 2, "y_m": 2}],
    [{"id": "a", "x_m": 1, "y_m": 1, "image_polygon": [[0, 0], [2, 0], [0, 1]]}],
    [{"id": "a", "x_m": 1, "y_m": 1, "image_polygon": [[0, 0], [1, 0]]}],
    [{"id": "a", "x_m": 1, "y_m": 1, "colour": "red"}],
    [{"id": "", "x_m": 1, "y_m": 1}],
])
def test_invalid_zone_config_is_rejected(raw):
    with pytest.raises(ZoneError):
        parse_zones(raw, "demo-floor-1")


def zone_tracker(**overrides):
    settings = TrackingSettings(zone_switch_frames=3, **overrides)
    return TrackManager(settings, "s1", zones=parse_zones(HALVES, "demo-floor-1"), image_size=SIZE)


def test_zone_track_publishes_the_zone_reference_point_and_switches_with_hysteresis():
    tracker = zone_tracker()
    t = 0.0
    tracker.update([det(1, standing_box(200, 900))], t)
    track = tracker.tracks["s1-t1"]
    assert (track.zone_id, track.published_xy) == ("left", (3.0, 2.0))
    for _ in range(2):                       # two frames on the other side: not yet
        t += 0.1
        tracker.update([det(1, standing_box(800, 900))], t)
    assert track.zone_id == "left" and track.zone_candidate == "right"
    t += 0.1
    tracker.update([det(1, standing_box(200, 900))], t)   # flicker back resets the count
    assert track.zone_candidate is None
    for _ in range(3):
        t += 0.1
        tracker.update([det(1, standing_box(800, 900))], t)
    assert (track.zone_id, track.published_xy) == ("right", (6.0, 2.0))
    (report,) = tracker.reports(t)
    assert (report.zone_id, report.x_m, report.y_m) == ("right", 6.0, 2.0)


def test_zone_tracks_reattach_by_image_distance():
    tracker = zone_tracker()
    tracker.update([det(1, standing_box(200, 900))], 0.0)
    tracker.update([], 1.0)                                  # occluded
    tracker.update([det(7, standing_box(230, 905))], 1.5)    # new raw ID, 30 px away
    assert list(tracker.tracks) == ["s1-t1"] and tracker.tracks["s1-t1"].reattached == 1
    tracker.update([], 2.0)
    tracker.update([det(8, standing_box(700, 905))], 2.5)    # far away: a new track
    assert sorted(tracker.tracks) == ["s1-t1", "s1-t2"]


def zone_publisher():
    clock = FakeClock()
    timebase = Timebase(monotonic=clock, wall=lambda: datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
    transport = FakeTransport()
    publisher = Publisher(PublisherSettings(camera_id="phone-1", heartbeat_s=1.0),
                          frame_id="demo-floor-1", calibration_ref=None, session_id="s1",
                          timebase=timebase, transport=transport, clock=clock,
                          zones=parse_zones(HALVES, "demo-floor-1"))
    return publisher, transport, clock


def zone_report(t, zone="left", state="visible", last_seen=None):
    return TrackReport(track_id="s1-t1", state=state, observed_t=t, first_seen_t=90.0,
                       last_seen_t=t if last_seen is None else last_seen, x_m=0.0, y_m=0.0,
                       confidence=0.8, capture_t=t, zone_id=zone)


def test_zone_publisher_registers_predefined_zones_and_reports_zone_ids():
    publisher, transport, clock = zone_publisher()
    publisher.submit([zone_report(100.0)])
    publisher.step()
    (_, path, registration), (_, _, body) = transport.calls
    assert path == "/api/v1/simulations/demo/cameras/phone-1"
    assert registration["mapping_mode"] == "predefined_zones"
    assert set(registration["zones"]) == {"left", "right"} and "calibration_ref" not in registration
    assert body["zone_id"] == "left" and "position" not in body

    clock.t = 100.2
    assert publisher.submit([zone_report(100.2, zone="right")]) == 1   # zone change goes out now
    publisher.step()
    clock.t = 101.0
    publisher.submit([zone_report(101.0, zone="left", state="occluded", last_seen=100.2)])
    publisher.step()
    occluded = transport.posts()[-1]
    assert occluded["zone_id"] == "right"        # repeats the last accepted zone
    assert occluded["track"]["last_seen_at"] == "2026-11-14T05:00:00.200Z"


def test_zone_publisher_needs_the_camera_endpoint():
    with pytest.raises(ValueError):
        Publisher(PublisherSettings(endpoint="legacy"), frame_id="f", calibration_ref=None,
                  session_id="s1", timebase=Timebase(), transport=FakeTransport(),
                  zones=parse_zones(HALVES, "f"))


def test_looping_file_restarts_and_keeps_counting_frames():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    clock = Clock()
    captures = [FakeCapture([image, image]), FakeCapture([image, image]), FakeCapture([])]
    camera = Camera(CameraSettings(), source="clip.mp4", pace=False, clock=clock, sleep=clock.sleep,
                    opener=lambda: captures.pop(0), loop=True, fps_override=12.5)
    frames = [camera.read() for _ in range(8)]
    indices = [f.index for f in frames if f is not None]
    assert indices == [0, 1, 2, 3] and camera.loops == 2 and camera.finished
    assert camera.file_fps == 12.5
