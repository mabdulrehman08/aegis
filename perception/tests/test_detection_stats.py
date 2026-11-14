"""Unit tests for the Phase 0 harness statistics and CSV output (no camera, no model)."""

import csv

import pytest

from perception.tools.detection_test import CSV_FIELDS, RunStats, append_csv, build_parser, main


def test_summary_counts_frames_meeting_expected():
    stats = RunStats(expected=3)
    stats.add_frame([0.9, 0.8, 0.7], 0.010)       # meets
    stats.add_frame([0.9, 0.8], 0.020)            # one person missed
    stats.add_frame([0.6, 0.6, 0.6, 0.6], 0.030)  # more than expected still meets
    stats.add_frame([], 0.040)                    # nobody detected

    summary = stats.summary(elapsed_s=2.0)

    assert summary["frames"] == 4
    assert summary["frames_meeting_expected"] == 2
    assert summary["pct_frames_meeting_expected"] == 50.0
    assert summary["detections"] == 9
    assert summary["mean_people_per_frame"] == 2.25
    assert summary["mean_confidence"] == pytest.approx((2.4 + 1.7 + 2.4) / 9, abs=1e-3)
    assert summary["fps"] == 2.0
    assert summary["mean_inference_ms"] == 25.0
    assert summary["p95_inference_ms"] == 40.0


def test_summary_reports_none_instead_of_guessing():
    empty = RunStats(expected=1).summary(elapsed_s=1.0)
    assert empty["frames"] == 0
    for key in ("pct_frames_meeting_expected", "mean_confidence", "fps", "mean_inference_ms"):
        assert empty[key] is None

    no_people = RunStats(expected=1)
    no_people.add_frame([], 0.01)
    summary = no_people.summary(elapsed_s=1.0)
    assert summary["pct_frames_meeting_expected"] == 0.0
    assert summary["mean_confidence"] is None  # not 0.0: there was nothing to average


def test_expected_zero_is_always_met():
    stats = RunStats(expected=0)
    stats.add_frame([], 0.01)
    assert stats.summary(1.0)["pct_frames_meeting_expected"] == 100.0


def test_append_csv_writes_header_once_and_blank_for_unknown(tmp_path):
    path = tmp_path / "eval" / "runs.csv"
    row = {key: "x" for key in CSV_FIELDS}
    append_csv(path, {**row, "scenario": "standing"})
    append_csv(path, {**row, "scenario": "lying_sideways", "mean_confidence": None})

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert [r["scenario"] for r in rows] == ["standing", "lying_sideways"]
    assert rows[1]["mean_confidence"] == ""
    assert path.read_text(encoding="utf-8").count("run_at_utc") == 1


def test_cli_defaults_match_the_spec():
    args = build_parser().parse_args([])
    assert (args.model, args.camera, args.conf) == ("yolo11n.pt", 0, 0.4)


def test_cli_rejects_invalid_threshold():
    assert main(["--conf", "1.5", "--no-display"]) == 2
