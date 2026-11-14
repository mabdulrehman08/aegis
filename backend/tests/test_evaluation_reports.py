import hashlib
import json

from backend.app.services.evaluation_reports import evaluation_index


def test_evaluation_index_labels_measured_artifacts_and_missing_results(tmp_path):
    encoded = json.dumps({"synthetic_only": True, "runs": [{"violations": 0}]}).encode()
    (tmp_path / "benchmark.json").write_bytes(encoded)
    result = evaluation_index(tmp_path)
    assert result.mode == "simulation"
    artifacts = {item.name: item for item in result.artifacts}
    assert artifacts["allocation"].available is True
    assert artifacts["allocation"].sha256 == hashlib.sha256(encoded).hexdigest()
    assert artifacts["uncertainty"].available is False
    assert artifacts["uncertainty"].report is None
    assert artifacts["counterfactuals"].available is False


def test_corrupt_and_oversized_evaluations_do_not_break_dashboard(tmp_path):
    (tmp_path / "benchmark.json").write_text("not-json")
    (tmp_path / "uncertainty.json").write_text("[]")
    (tmp_path / "scenario_comparison.json").write_text(" " * 2_000_001)
    assert all(not item.available for item in evaluation_index(tmp_path).artifacts)
