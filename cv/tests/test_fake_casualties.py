import pytest

from cv.tools.fake_casualties import SCENE, Scene, build_parser, run, signs_at, visible_at

BY_ID = {persona.local_id: persona for persona in SCENE}


def iso(t):
    return f"T+{t:.1f}"


def values(local_id, t):
    return {name: sign["value"] for name, sign in signs_at(BY_ID[local_id], t, iso).items()
            if isinstance(sign, dict)}


def test_responses_follow_the_scripted_commands():
    assert values("t2", 9.0)["response"] == "not_tested"
    assert values("t2", 12.0)["response"] == "not_tested"         # still inside the window
    assert values("t2", 18.0)["response"] == "no_response"
    assert values("t4", 12.0)["response"] == "responded"
    assert signs_at(BY_ID["t4"], 12.0, iso)["response"]["command_at"] == "T+10.0"


def test_deterioration_changes_signs_and_restarts_stillness():
    before, after = signs_at(BY_ID["t4"], 44.0, iso), signs_at(BY_ID["t4"], 47.0, iso)
    assert before["posture"]["value"] == "sitting" and after["posture"]["value"] == "lying"
    assert after["movement"]["still_for_s"] == 2.0
    assert after["breathing"]["value"] == "not_testable"            # not still long enough yet
    assert values("t4", 52.0)["response"] == "not_tested"
    assert values("t4", 58.0)["response"] == "no_response"
    assert values("t4", 60.0)["breathing"] == "motion_detected"


def test_occluded_reports_carry_no_signs():
    assert not visible_at(BY_ID["t3"], 31.0)
    reports = {r.track_id[-2:]: r for r in Scene(SCENE, "s1", iso, 0.0).reports(31.0)}
    assert reports["t3"].state == "occluded" and reports["t3"].signs is None
    assert reports["t1"].signs["visible_blood"]["regions"] == ["left_leg", "torso"]


def test_positions_stay_on_the_staged_floor():
    for persona in SCENE:
        for step in range(0, 900):
            report = Scene([persona], "s1", iso, 0.0).reports(step / 10)[0]
            assert 12.0 <= report.x_m <= 16.0 and 6.0 <= report.y_m <= 10.0


def backend_transport():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    client = TestClient(create_app())
    assert client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"}).status_code == 201
    calls = []

    def transport(method, path, body):
        calls.append(body)
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    return client, transport, calls


def test_whole_scene_is_accepted_by_the_backend_without_signs():
    client, transport, _ = backend_transport()
    args = build_parser().parse_args(["--fast", "--print-every", "1000"])
    assert run(args, transport=transport) == 0
    tracks = client.get("/api/v1/simulations/demo/state").json()["tracks"]
    mine = [t for t in tracks if t["camera"]["camera_id"] == "fake-cam-1"]
    assert len(mine) == 5
    assert {t["latest_observation"]["track"]["state"] for t in mine} == {"lost"}


def test_signed_payloads_match_the_backend_model_apart_from_signs():
    pytest.importorskip("fastapi")
    from backend.app.simulation.models import CameraObservation

    calls = []

    def transport(method, path, body):
        calls.append((method, body))
        return (200 if method == "PUT" else 201), {}

    args = build_parser().parse_args(["--fast", "--send-signs", "--print-every", "1000"])
    assert run(args, transport=transport) == 0
    posts = [body for method, body in calls if method == "POST"]
    signed = [body for body in posts if "signs" in body]
    assert signed and all(body["track"]["state"] == "visible" for body in signed)
    for body in posts:
        CameraObservation.model_validate({k: v for k, v in body.items() if k != "signs"})
