import argparse

import pytest

from cv.evaluation import Scorer, count_truth, parse_truth
from cv.recording import load_sidecar, sidecar_frame_times, write_sidecar
from cv.settings import Settings, TrackingSettings
from cv.tests.helpers import det, scale_calibration, standing_box
from cv.tools.record import measured_fps
from cv.tracker import TrackManager

TRUTH = {"scenario": "two_standing", "frame_id": "test-frame", "match_radius_m": 1.0,
         "people": [{"id": "A", "x_m": 2.0, "y_m": 5.0, "posture": "standing"},
                    {"id": "B", "x_m": 7.0, "y_m": 5.0}]}


def run(frames, truth, reattach=False):
    tracker = TrackManager(TrackingSettings(reattach=reattach), "s1", scale_calibration())
    scorer = Scorer(truth)
    for i, detections in enumerate(frames):
        t = i / 10.0
        scorer.add_frame(t, tracker.update(detections, t))
    return scorer.summary()


def test_positions_switches_false_positives_and_bias():
    # A stands 10 cm right of its truth point (210 px); B 7.0 m exactly.
    a, b = standing_box(210, 500), standing_box(700, 500)
    frames = [[det(1, a), det(2, b)] for _ in range(10)]
    frames += [[det(1, a)] for _ in range(10)]                         # B unseen 1 s
    frames += [[det(1, a), det(9, b), det(3, standing_box(500, 900))] for _ in range(10)]  # B new ID + a stray
    result = run(frames, parse_truth(TRUTH))

    assert result["frames"] == 30 and result["ground_truth"] == "positions"
    people = {p["id"]: p for p in result["people"]}
    assert people["A"]["detected_pct"] == 100.0 and people["A"]["id_switches"] == 0
    assert people["B"]["detected_pct"] == pytest.approx(66.7)
    assert people["B"]["id_switches"] == 1 and people["B"]["track_ids"] == 2
    assert people["B"]["longest_gap_s"] == pytest.approx(1.1)
    assert result["frames_all_detected_pct"] == pytest.approx(66.7)
    assert result["false_positive_tracks"] == 1
    assert people["A"]["error_cm_median"] == pytest.approx(10.0)
    assert result["mapping_bias_cm"][0] == pytest.approx(6.0)          # 30 frames at +10, 20 at 0
    assert people["A"]["posture_agreement_pct"] == 100.0
    assert result["id_switches_per_min"] == pytest.approx(1 / (2.9 / 60), rel=1e-3)


def test_reattach_keeps_the_id_through_the_same_gap():
    a, b = standing_box(210, 500), standing_box(700, 500)
    frames = [[det(1, a), det(2, b)]] * 10 + [[det(1, a)]] * 10 + [[det(1, a), det(9, b)]] * 10
    result = run(frames, parse_truth(TRUTH), reattach=True)
    assert result["id_switches"] == 0 and result["tracks_seen"] == 2


def test_count_only_scoring():
    frames = [[det(1, standing_box(200, 500))]] * 5 + [[]] * 5
    result = run(frames, count_truth(1, "solo"))
    assert result["ground_truth"] == "count"
    assert result["frames_exact_count_pct"] == 50.0 and "mapping_error_cm_median" not in result


@pytest.mark.parametrize("bad", [
    {"people": []},
    {"people": [{"id": "A", "x_m": 1}]},
    {"people": [{"id": "A", "posture": "crouching"}]},
    {"people": [{"id": "A"}], "extra": 1},
])
def test_invalid_truth_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_truth(bad)


def test_sidecar_round_trip_and_calibration_reuse(tmp_path):
    from cv.main import resolve_mapping

    video = tmp_path / "clip.mp4"
    calibration = scale_calibration("demo-floor-1")
    write_sidecar(video, {"fps": 24.5, "frame_times_s": [0.0, 0.041, 0.082],
                          "mapping": {"mode": "calibrated", "calibration": calibration.to_dict()}})
    sidecar = load_sidecar(video)
    assert sidecar["fps"] == 24.5 and sidecar_frame_times(sidecar) == [0.0, 0.041, 0.082]
    args = argparse.Namespace(homography=None)
    loaded, zones = resolve_mapping(Settings(), args, sidecar)
    assert zones is None and loaded.calibration_ref == calibration.calibration_ref
    assert load_sidecar(tmp_path / "missing.mp4") is None


def test_measured_fps():
    assert measured_fps([0.0, 0.5, 1.0]) == 2.0
    assert measured_fps([0.0]) is None
