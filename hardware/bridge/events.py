"""Device milestone payloads for the backend's simulation device-event endpoint."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

REPORT_TO_KIND = {"DELIVERED": "delivered", "ACK": "acknowledged", "DONE": "completed"}
MILESTONE_RANK = {"dispatched": 0, "delivered": 1, "acknowledged": 2, "completed": 3}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_device_event(
    *,
    kind: str,
    assignment: dict[str, Any],
    scenario_id: str,
    epoch: int,
    physical_device_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    """A SimulatedDeviceEvent correlated to one dispatched backend assignment.

    Every correlation field is copied from the assignment, as the backend
    requires; ``device_id`` is the backend's logical device for that responder.
    The event ID is deterministic, so a retry resends the identical payload.
    """
    event = {
        "id": f"{physical_device_id}-e{epoch}-{assignment['id']}-{kind}",
        "scenario_id": scenario_id,
        "device_id": assignment["device_id"],
        "dispatch_id": assignment["dispatch_id"],
        "command_id": assignment["command_id"],
        "casualty_id": assignment["casualty_id"],
        "responder_id": assignment["responder_id"],
        "kind": kind,
        "occurred_at": occurred_at,
        "mode": "simulation",
    }
    validate_event(event)
    return event


def validate_event(event: dict[str, Any]) -> None:
    """Validate against the backend's model when the backend package is importable."""
    try:
        from backend.app.simulation.models import SimulatedDeviceEvent
    except ImportError:
        return
    SimulatedDeviceEvent.model_validate(event)


class JsonlLog:
    """Local audit log of every event the bridge produced and what happened to it."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
