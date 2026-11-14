"""Assert a complete synthetic HTTP lifecycle through the real FastAPI app."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math

from fastapi.testclient import TestClient

from backend.app.main import create_app


PREFIX = "/api/v1/simulations/demo"
ACTOR = "synthetic-demo-operator"


def grid_cell(grid, position):
    return (
        math.floor((position["x_m"] - grid["origin"]["x_m"]) / grid["resolution_m"]),
        math.floor((position["y_m"] - grid["origin"]["y_m"]) / grid["resolution_m"]),
    )


def endpoint_cells(grid, position):
    column, row = grid_cell(grid, position)
    offset_x = (position["x_m"] - grid["origin"]["x_m"]) / grid["resolution_m"]
    offset_y = (position["y_m"] - grid["origin"]["y_m"]) / grid["resolution_m"]
    columns = [column, column - 1] if offset_x.is_integer() else [column]
    rows = [row, row - 1] if offset_y.is_integer() else [row]
    return {(column, row) for column in columns for row in rows}


def traversed_cells(grid, route):
    cells = set()
    for first, second in zip(route["waypoints"], route["waypoints"][1:]):
        distance = math.hypot(second["x_m"] - first["x_m"], second["y_m"] - first["y_m"])
        steps = max(1, math.ceil(distance / (grid["resolution_m"] / 4)))
        for step in range(steps + 1):
            fraction = step / steps
            cells.add(grid_cell(grid, {
                "x_m": first["x_m"] + fraction * (second["x_m"] - first["x_m"]),
                "y_m": first["y_m"] + fraction * (second["y_m"] - first["y_m"]),
            }))
    return cells


class Session:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def request(self, method, path, payload=None, expected=(200, 201)):
        fingerprint = hashlib.sha256(json.dumps([method, path, payload], sort_keys=True).encode()).hexdigest()
        response = self.client.request(method, PREFIX + path, json=payload,
                                       headers={"Idempotency-Key": f"demo-{fingerprint}"})
        self.calls.append({"method": method, "path": PREFIX + path, "status": response.status_code})
        assert response.status_code in expected, (
            f"{method} {PREFIX + path}: expected {expected}, got {response.status_code}: {response.text}"
        )
        body = response.json()
        if response.is_success:
            assert body["mode"] == "simulation", "response escaped simulation boundary"
        return body

    def snapshot(self):
        return self.request("GET", "/state")

    def current_proposal(self, state):
        proposal_id = state["current_proposal_id"]
        assert proposal_id is not None, "expected a feasible new proposal"
        proposal = next(proposal for proposal in state["proposals"] if proposal["id"] == proposal_id)
        assert proposal["status"] == "pending_approval"
        assert proposal["assignments"], "demo must actually propose work"
        assert proposal["engine"] == "ortools", "demo must exercise actual OR-Tools, not a fallback"
        for assignment in proposal["assignments"]:
            assert assignment["route"] is not None, "proposed assignment lacks a verified route"
            assert assignment["route"]["algorithm"] == "astar"
            assert len(assignment["route"]["waypoints"]) >= 2
        return proposal

    def approve(self, proposal):
        response = self.request("POST", f"/proposals/{proposal['id']}/decision", {
            "proposal_revision": proposal["revision"], "decision": "approve",
            "actor": ACTOR, "mode": "simulation",
        })
        approval = response["result"]
        assert approval["status"] == "active"
        assert approval["authenticated"] is False
        return approval

    def dispatch(self, proposal, approval):
        payload = {"proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
                   "approval_id": approval["id"], "actor": ACTOR, "mode": "simulation"}
        response = self.request("POST", "/dispatches", payload)
        dispatch = response["result"]["dispatch"]
        assert dispatch["real_device_effect"] is False
        replay = self.request("POST", "/dispatches", payload)
        assert replay["duplicate"] is True
        assert replay["result"]["dispatch"]["id"] == dispatch["id"], "dispatch retry created new work"
        return dispatch, response["state"]


def run():
    with TestClient(create_app()) as client:
        session = Session(client)
        reset = session.request("POST", "/reset", {"mode": "simulation"})
        state = reset["state"]
        assert len(state["scenario"]["casualties"]) == 3
        assert len(state["scenario"]["responders"]) == 2
        assert all(casualty["medical_assessment"] is None for casualty in state["scenario"]["casualties"])
        assert not state["dispatches"] and not state["assignments"]

        for index, casualty in enumerate(state["scenario"]["casualties"]):
            response = session.request("POST", f"/casualties/{casualty['id']}/assessment", {
                "severity": ["immediate", "delayed", "minor"][index], "source": "human",
                "assessed_by": ACTOR, "assessed_at": datetime.now(timezone.utc).isoformat(),
            })
            assessed = next(record for record in response["state"]["scenario"]["casualties"] if record["id"] == casualty["id"])
            assert assessed["medical_assessment"]["source"] == "human"
            assert assessed["medical_assessment"]["assessed_by"] == ACTOR
        response = session.request("POST", "/replan", {"actor": ACTOR})
        proposal = session.current_proposal(response["state"])
        session.request("POST", "/dispatches", {
            "proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
            "approval_id": "missing-approval", "actor": ACTOR, "mode": "simulation",
        }, expected=(404, 409))
        approval = session.approve(proposal)
        dispatch, state = session.dispatch(proposal, approval)
        assignments = [assignment for assignment in state["assignments"] if assignment["dispatch_id"] == dispatch["id"]]
        assert len(assignments) == len(proposal["assignments"])
        for assignment in assignments:
            for kind in ("delivered", "acknowledged"):
                response = session.request("POST", f"/assignments/{assignment['id']}/simulate-device-event", {
                    "kind": kind, "event_id": f"demo-{assignment['id']}-{kind}",
                })
                actual = next(record for record in response["state"]["assignments"] if record["id"] == assignment["id"])
                assert actual["status"] == kind

        active_responder = assignments[0]["responder_id"]
        other_assignment = next(assignment for assignment in assignments if assignment["responder_id"] != active_responder)
        response = session.request("POST", f"/assignments/{other_assignment['id']}/simulate-device-event", {
            "kind": "completed", "event_id": f"demo-{other_assignment['id']}-completed",
        })
        stale_proposal = session.current_proposal(response["state"])
        stale_approval = session.approve(stale_proposal)
        state = session.snapshot()
        active_ids = {assignment["id"] for assignment in state["assignments"]}
        disrupted_map = state["scenario"]["scene_map"]
        disrupted_map["revision"] = "demo-disruption-map"
        grid = disrupted_map["occupancy_grid"]
        stale_assignment = stale_proposal["assignments"][0]
        before_route = stale_assignment["route"]
        excluded_cells = set()
        endpoints = [entity["position"] for entity in [*state["scenario"]["casualties"], *state["scenario"]["responders"]]]
        for assignment in state["assignments"]:
            if assignment["status"] != "completed":
                endpoints.extend([assignment["route"]["waypoints"][0], assignment["route"]["waypoints"][-1]])
        for endpoint in endpoints:
            excluded_cells.update(endpoint_cells(grid, endpoint))
        candidates = []
        for point in before_route["waypoints"][1:-1]:
            column, row = grid_cell(grid, point)
            if (0 < column < grid["width"] - 1 and 0 < row < grid["height"] - 1
                    and (column, row) not in excluded_cells
                    and grid["cells"][row * grid["width"] + column] == 0):
                candidates.append((column, row))
        assert candidates, "synthetic route must have a traversed interior cell away from every endpoint"
        blocked_cell = candidates[len(candidates) // 2]
        assert blocked_cell in traversed_cells(grid, before_route)
        grid["cells"][blocked_cell[1] * grid["width"] + blocked_cell[0]] = 100
        response = session.request("PUT", "/map", disrupted_map)
        assert response["state"]["scenario"]["scene_map"]["revision"] == disrupted_map["revision"]
        response = session.request("PATCH", f"/responders/{active_responder}", {"status": "offline"})
        assert {assignment["id"] for assignment in response["state"]["assignments"]} == active_ids
        assert next(record for record in response["state"]["approvals"]
                    if record["id"] == stale_approval["id"])["status"] == "invalidated"
        session.request("POST", "/dispatches", {
            "proposal_id": stale_proposal["id"], "proposal_revision": stale_proposal["revision"],
            "approval_id": stale_approval["id"], "actor": ACTOR, "mode": "simulation",
        }, expected=(409,))
        response = session.request("POST", "/replan", {"actor": ACTOR, "reason": "synthetic map and responder disruption"})
        replan = session.current_proposal(response["state"])
        assert replan["id"] not in {proposal["id"], stale_proposal["id"]}
        assert replan["revision"] > stale_proposal["revision"]
        assert all(assignment["responder_id"] != active_responder for assignment in replan["assignments"])
        assert all(assignment["route"]["map_revision"] == disrupted_map["revision"] for assignment in replan["assignments"])
        detoured = next(assignment for assignment in replan["assignments"]
                        if assignment["casualty_id"] == stale_assignment["casualty_id"]
                        and assignment["responder_id"] == stale_assignment["responder_id"])
        after_route = detoured["route"]
        assert before_route["waypoints"] != after_route["waypoints"], "disruption did not change the actual routed geometry"
        assert blocked_cell not in traversed_cells(grid, after_route), "replan traverses the newly blocked cell"
        assert after_route["distance_m"] >= before_route["distance_m"] - 1e-8
        explanation = session.request("GET", f"/proposals/{replan['id']}/explanation")
        assert explanation["proposal_id"] == replan["id"]
        assert explanation["proposal_revision"] == replan["revision"]
        facts = {fact["id"]: fact for fact in explanation["explanation"]["facts"]}
        assert facts["approval-required"]["category"] == "procedural"
        assignment_fact = facts[f"assignment:{detoured['casualty_id']}"]
        assert assignment_fact["category"] == "estimated"
        assert detoured["responder_id"] in assignment_fact["text"]
        assert detoured["casualty_id"] in assignment_fact["text"]
        assert str(after_route["distance_m"]) in assignment_fact["text"]
        unchanged = session.snapshot()
        assert unchanged["sequence"] == response["state"]["sequence"], "explanation endpoint mutated authoritative state"
        assert unchanged["scene_revision"] == response["state"]["scene_revision"]
        session.request("POST", "/dispatches", {
            "proposal_id": replan["id"], "proposal_revision": replan["revision"],
            "approval_id": stale_approval["id"], "actor": ACTOR, "mode": "simulation",
        }, expected=(409,))
        new_approval = session.approve(replan)
        assert new_approval["id"] not in {approval["id"], stale_approval["id"]}
        second_dispatch, state = session.dispatch(replan, new_approval)
        assert second_dispatch["id"] != dispatch["id"]
        new_assignments = [assignment for assignment in state["assignments"]
                           if assignment["dispatch_id"] == second_dispatch["id"]]
        for assignment in new_assignments:
            for kind in ("delivered", "acknowledged"):
                response = session.request("POST", f"/assignments/{assignment['id']}/simulate-device-event", {
                    "kind": kind, "event_id": f"demo-{assignment['id']}-{kind}",
                })
                state = response["state"]
                assert next(record for record in state["assignments"] if record["id"] == assignment["id"])["status"] == kind
        assert any(assignment["id"] == assignments[0]["id"] and assignment["status"] == "acknowledged"
                   for assignment in state["assignments"]), "active task history was overwritten"
        historical = next(assignment for assignment in state["assignments"] if assignment["id"] == assignments[0]["id"])
        assert historical["route"] == assignments[0]["route"], "approved historical route was silently rewritten"
        assert next(responder for responder in state["scenario"]["responders"]
                    if responder["id"] == active_responder)["status"] == "offline"
        active = [assignment for assignment in state["assignments"] if assignment["status"] != "completed"]
        assert len({assignment["casualty_id"] for assignment in active}) == len(active)
        assert len({assignment["responder_id"] for assignment in active}) == len(active)

        events = session.request("GET", "/events")["events"]
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(set(sequences)), "events must be strictly sequenced"
        assert len({event["event_id"] for event in events}) == len(events)
        assert all(event["mode"] == "simulation" for event in events)
        event_counts = Counter(event["type"] for event in events)
        assert event_counts["simulation.device_delivered"] == len(assignments) + len(new_assignments)
        assert event_counts["simulation.device_acknowledged"] == len(assignments) + len(new_assignments)
        assert event_counts["simulation.dispatch_created"] == len(state["dispatches"])
        assert event_counts["simulation.approval_invalidated"] > 0
        assert all(dispatch["real_device_effect"] is False for dispatch in state["dispatches"])
        for path in ("/api/v1/dispatches", "/api/v1/scenarios/demo/dispatches"):
            response = client.post(path, json={"mode": "real"})
            session.calls.append({"method": "POST", "path": path, "status": response.status_code})
            assert response.status_code in (404, 405), "reserved production dispatch became writable"
        return {
            "mode": state["mode"], "scenario_id": state["scenario_id"],
            "scene_revision": state["scene_revision"], "map_revision": state["scenario"]["scene_map"]["revision"],
            "casualties": len(state["scenario"]["casualties"]),
            "human_assessments": sum(casualty["medical_assessment"] is not None for casualty in state["scenario"]["casualties"]),
            "responders": len(state["scenario"]["responders"]), "proposals": len(state["proposals"]),
            "approvals": len(state["approvals"]), "dispatches": len(state["dispatches"]),
            "approval_statuses": dict(Counter(approval["status"] for approval in state["approvals"])),
            "assignment_statuses": dict(Counter(assignment["status"] for assignment in state["assignments"])),
            "events": len(events), "event_types": dict(event_counts),
            "initial_engine": proposal["engine"], "replan_engine": replan["engine"],
            "route_evidence": {
                "casualty_id": detoured["casualty_id"], "responder_id": detoured["responder_id"],
                "blocked_grid_cell": list(blocked_cell), "excluded_endpoint_cells": sorted(map(list, excluded_cells)),
                "before": before_route, "after": after_route,
                "geometry_changed": before_route["waypoints"] != after_route["waypoints"],
                "distance_changed": not math.isclose(before_route["distance_m"], after_route["distance_m"], abs_tol=1e-8),
                "blocked_cell_avoided": blocked_cell not in traversed_cells(grid, after_route),
            },
            "grounded_explanation": explanation,
            "historical_active_route_review_warnings": sum(
                assignment["route"] is None or assignment["route"]["map_revision"] != state["scenario"]["scene_map"]["revision"]
                for assignment in active),
            "http_requests": len(session.calls), "http_statuses": dict(Counter(call["status"] for call in session.calls)),
            "real_device_effects": sum(dispatch["real_device_effect"] for dispatch in state["dispatches"]),
        }


def main():
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
