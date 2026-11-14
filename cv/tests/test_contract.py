"""Perception payloads against the backend's own models and routes.

Skipped unless the backend dependencies are installed
(``pip install -r cv/requirements-dev.txt``).
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import create_app  # noqa: E402
from backend.app.models import CVObservation  # noqa: E402
from backend.app.simulation.models import CameraObservation, CameraRegistration  # noqa: E402
from cv.clock import Timebase  # noqa: E402
from cv.publisher import Publisher  # noqa: E402
from cv.settings import PublisherSettings  # noqa: E402
from cv.tracker import TrackReport  # noqa: E402


FIRST_SEEN: dict[str, float] = {}


@pytest.fixture(autouse=True)
def _fresh_tracks():
    FIRST_SEEN.clear()


def make_publisher(transport, endpoint="camera"):
    timebase = Timebase()
    publisher = Publisher(PublisherSettings(endpoint=endpoint, heartbeat_s=0.0),
                          frame_id="demo-floor-1", calibration_ref="cv-demo-floor-1-test",
                          session_id="s1", timebase=timebase, transport=transport)
    return publisher, timebase


def report(now, state="visible", x=4.0, y=8.0, conf=0.85, track="s1-t1", last_seen=None,
           first_seen=None):
    return TrackReport(track_id=track, state=state, observed_t=now,
                       first_seen_t=FIRST_SEEN.setdefault(track, now) if first_seen is None else first_seen,
                       last_seen_t=now if last_seen is None else last_seen,
                       x_m=x, y_m=y, confidence=conf, capture_t=now)


def collect():
    calls = []

    def transport(method, path, body):
        calls.append((method, path, body))
        return (200 if method == "PUT" else 201), {}

    return calls, transport


def test_camera_payloads_validate_against_backend_models():
    calls, transport = collect()
    publisher, timebase = make_publisher(transport)
    now = timebase.now()
    publisher.submit([report(now)], now)
    publisher.step()
    publisher.submit([report(now + 1, state="occluded", last_seen=now)], now + 1)
    publisher.step()
    (_, _, registration), *posts = calls
    CameraRegistration.model_validate(registration)
    for _, _, body in posts:
        CameraObservation.model_validate(body)


def test_legacy_payload_validates_as_contract_cv_observation():
    calls, transport = collect()
    publisher, timebase = make_publisher(transport, endpoint="legacy")
    publisher.submit([report(timebase.now())])
    publisher.step()
    observation = CVObservation.model_validate(calls[0][2])
    assert observation.track.track_id == "logitech-1-s1-t1"


def test_full_track_lifecycle_is_accepted_by_the_running_backend():
    client = TestClient(create_app())
    assert client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"}).status_code == 201

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    publisher, timebase = make_publisher(transport)
    t0 = timebase.now() - 3.0
    steps = [
        [report(t0, x=4.0, y=8.0), report(t0, track="s1-t2", x=12.0, y=5.0)],
        [report(t0 + 0.5, x=4.2, y=8.1, conf=0.7), report(t0 + 0.5, track="s1-t2", x=12.0, y=5.0)],
        [report(t0 + 1.0, state="occluded", x=9.9, y=9.9, last_seen=t0 + 0.6)],  # values replaced
        [report(t0 + 1.5, state="occluded", last_seen=t0 + 0.6)],
        [report(t0 + 2.0, state="lost", last_seen=t0 + 0.6)],
    ]
    for reports in steps:
        publisher.submit(reports, reports[0].observed_t)
        publisher.step()

    assert publisher.stats.rejected == 0, publisher.stats.last_error
    assert publisher.stats.accepted == 7
    state = client.get("/api/v1/simulations/demo/state").json()
    tracks = {t["camera"]["local_track_id"]: t for t in state["tracks"]}
    assert tracks["s1-t1"]["latest_observation"]["track"]["state"] == "lost"
    assert tracks["s1-t1"]["latest_observation"]["position"]["x_m"] == 4.2
    assert tracks["s1-t2"]["camera"]["calibration_ref"] == "cv-demo-floor-1-test"
    assert tracks["s1-t2"]["camera"]["location_basis"] == "calibrated_world"
    assert all(t["casualty_id"] is None for t in state["tracks"])  # association is human-only


def test_backend_reset_clears_the_camera_and_publisher_recovers():
    client = TestClient(create_app())
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    publisher, timebase = make_publisher(transport)
    now = timebase.now() - 1.0
    publisher.submit([report(now)], now)
    publisher.step()
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})
    publisher.submit([report(now + 0.5, x=5.0)], now + 0.5)
    publisher.step()   # 404 -> marks unregistered
    publisher.step()   # re-registers and resends
    assert publisher.stats.registrations == 2 and publisher.stats.accepted == 2
