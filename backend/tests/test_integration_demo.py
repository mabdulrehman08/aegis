from scripts.demo import run


def test_real_http_synthetic_workflow(monkeypatch):
    monkeypatch.delenv("AEGIS_ADVISORY_URL", raising=False)
    summary = run()
    assert summary["mode"] == "simulation"
    assert summary["dispatches"] == 2
    assert summary["approval_statuses"]["consumed"] == 2
    assert summary["approval_statuses"]["invalidated"] >= 1
    assert summary["assignment_statuses"]["completed"] == 1
    assert summary["real_device_effects"] == 0
    assert summary["initial_engine"] == summary["replan_engine"] == "ortools"
    evidence = summary["route_evidence"]
    assert evidence["before"]["algorithm"] == evidence["after"]["algorithm"] == "astar"
    assert evidence["before"]["map_revision"] != evidence["after"]["map_revision"]
    assert evidence["geometry_changed"] is True
    assert evidence["blocked_cell_avoided"] is True
    assert evidence["blocked_grid_cell"] not in evidence["excluded_endpoint_cells"]
    assert evidence["before"]["waypoints"] != evidence["after"]["waypoints"]
    explanation = summary["grounded_explanation"]["explanation"]
    assert explanation["source"] == "deterministic"
    facts = {fact["id"]: fact for fact in explanation["facts"]}
    assert facts["approval-required"]["category"] == "procedural"
    assert facts[f"assignment:{evidence['casualty_id']}"]["category"] == "estimated"
    repeated = run()
    assert repeated["route_evidence"] == evidence
    assert repeated["grounded_explanation"]["explanation"] == explanation
