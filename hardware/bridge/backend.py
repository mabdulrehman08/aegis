"""Connects the device link to Prince's simulation backend.

Source of truth is ``GET /api/v1/simulations/{scenario}/state``. The device only
ever shows an assignment that a human approved and dispatched for this
responder; device reports go back via ``POST .../device-events``. Everything is
``mode: "simulation"``; nothing here reaches a real emergency service.
"""

from collections import deque
from dataclasses import dataclass, field
import json
import logging
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from .bridge import ActiveAssignment, Bridge
from .events import MILESTONE_RANK, JsonlLog, build_device_event
from .protocol import LABEL_MAX

log = logging.getLogger("aegis.hardware")

ACTIVE_STATUSES = ("dispatched", "delivered", "acknowledged")
STATE_POLL_S = 0.4
RETRY_S = 1.0

# request(method, path, body or None, headers) -> (status, parsed JSON or None)
Requester = Callable[[str, str, Any, dict[str, str]], tuple[int, Any]]


class BackendUnavailable(OSError):
    pass


def http_requester(base_url: str, timeout_s: float = 1.0) -> Requester:
    root = base_url.rstrip("/")

    def request(method: str, path: str, body: Any, headers: dict[str, str]) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(root + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"null")
            except ValueError:
                payload = None
            return exc.code, payload
        except (urllib.error.URLError, OSError) as exc:
            raise BackendUnavailable(str(exc)) from exc

    return request


def device_label(casualty_id: str) -> str:
    """Short matrix label: casualty-003 -> C3, casualty-actor-a -> A."""
    last = casualty_id.rsplit("-", 1)[-1]
    if last.isdigit():
        label = f"C{int(last)}"
    else:
        label = re.sub(r"[^A-Za-z0-9]", "", last).upper()
    return label[:LABEL_MAX] if label and len(label) <= LABEL_MAX else "?"


@dataclass
class PendingEvent:
    event: dict[str, Any]
    attempts: int = 0
    next_try: float = 0.0


@dataclass
class SyncStats:
    posted: int = 0
    rejected: list[str] = field(default_factory=list)
    backend_errors: int = 0
    last_state_ok: bool = False


class BackendSync:
    def __init__(
        self,
        bridge: Bridge,
        requester: Requester,
        *,
        scenario_id: str,
        responder_id: str,
        event_log: JsonlLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[dict[str, Any], str], None] | None = None,
    ) -> None:
        self.bridge = bridge
        self.request = requester
        self.scenario_id = scenario_id
        self.responder_id = responder_id
        self.log = event_log or JsonlLog(None)
        self.clock = clock
        self.on_event = on_event
        self.base = f"/api/v1/simulations/{scenario_id}"
        self.epoch: int | None = None
        self.target: dict[str, Any] | None = None  # backend assignment being shown
        self.outbox: deque[PendingEvent] = deque()
        self.stats = SyncStats()
        self._next_poll = 0.0
        bridge.on_report = self._on_report

    # ------------------------------------------------------------ main loop

    def tick(self) -> None:
        now = self.clock()
        if now >= self._next_poll:
            self._next_poll = now + STATE_POLL_S
            self.refresh()
        self.flush()

    def refresh(self) -> None:
        try:
            status, state = self.request("GET", f"{self.base}/state", None, {})
        except BackendUnavailable as exc:
            self._backend_down(exc)
            return
        if status != 200 or not isinstance(state, dict):
            self._backend_down(f"GET /state returned {status}")
            return
        self.stats.last_state_ok = True
        self.reconcile(state)

    def reconcile(self, state: dict[str, Any]) -> None:
        epoch = state.get("epoch")
        if epoch != self.epoch:
            if self.epoch is not None:
                log.info("scenario reset (epoch %s -> %s): clearing device", self.epoch, epoch)
                self.outbox.clear()
            self.epoch = epoch
            self._clear_device()

        mine = [a for a in state.get("assignments", [])
                if a.get("responder_id") == self.responder_id and a.get("status") in ACTIVE_STATUSES]
        wanted = max(mine, key=lambda a: a.get("created_at", ""), default=None)
        if len(mine) > 1:
            log.warning("%d active assignments for %s; showing the newest", len(mine), self.responder_id)

        if wanted is None:
            if self.target is not None:
                log.info("assignment %s no longer active in backend: clearing device", self.target["id"])
                self._clear_device()
            return

        if self.target is not None and self.target["id"] == wanted["id"]:
            self.target = wanted  # keep latest status for milestone skipping
            return

        if self.target is not None:
            self._clear_device()
        self.target = wanted
        severity = self._severity(state, wanted["casualty_id"])
        urgency = "high" if severity == "immediate" else "normal"
        token = f"e{self.epoch}-{wanted['command_id']}"
        self.bridge.assign(token, device_label(wanted["casualty_id"]), urgency,
                           context={"assignment_id": wanted["id"], "epoch": self.epoch})
        log.info("showing %s (%s, %s) on device", wanted["id"], wanted["casualty_id"], urgency)

    # ------------------------------------------------------------ reports

    def _on_report(self, kind: str, active: ActiveAssignment, occurred_at: str) -> None:
        target = self.target
        if (target is None or active.context.get("assignment_id") != target["id"]
                or active.context.get("epoch") != self.epoch):
            log.warning("dropping %s for an assignment that is no longer active", kind)
            return
        if MILESTONE_RANK.get(target.get("status", "dispatched"), 0) >= MILESTONE_RANK[kind]:
            log.info("%s already recorded by backend for %s; not resending", kind, target["id"])
            return
        event = build_device_event(
            kind=kind, assignment=target, scenario_id=self.scenario_id, epoch=self.epoch or 0,
            physical_device_id=self.bridge.device_id or "unknown-device", occurred_at=occurred_at,
        )
        self.outbox.append(PendingEvent(event))
        self.log.write({"queued": event})
        self.flush()

    def flush(self) -> None:
        while self.outbox:
            item = self.outbox[0]
            if self.clock() < item.next_try:
                return
            item.attempts += 1
            event = item.event
            try:
                status, body = self.request("POST", f"{self.base}/device-events", event,
                                            {"Idempotency-Key": event["id"]})
            except BackendUnavailable as exc:
                item.next_try = self.clock() + RETRY_S
                self._backend_down(exc)
                return  # keep order: later milestones wait for earlier ones
            if 200 <= status < 300:
                self.outbox.popleft()
                self.stats.posted += 1
                self.log.write({"posted": event["id"], "status": status})
                if self.on_event:
                    self.on_event(event, "accepted" if not (body or {}).get("duplicate") else "duplicate")
                continue
            if status >= 500:
                item.next_try = self.clock() + RETRY_S
                self._backend_down(f"device-event returned {status}")
                return
            # 4xx: the backend refused it (stale, uncorrelated, out of order). Never retry blindly.
            self.outbox.popleft()
            detail = (body or {}).get("detail") if isinstance(body, dict) else body
            self.stats.rejected.append(f"{event['kind']}: {status} {detail}")
            self.log.write({"rejected": event["id"], "status": status, "detail": detail})
            log.error("backend rejected %s (%s): %s", event["id"], status, detail)
            if self.on_event:
                self.on_event(event, f"rejected {status}")

    # ------------------------------------------------------------ helpers

    def _clear_device(self) -> None:
        self.target = None
        self.bridge.cancel()

    def _backend_down(self, why: Any) -> None:
        if self.stats.last_state_ok:
            log.warning("backend unavailable: %s", why)
        self.stats.last_state_ok = False
        self.stats.backend_errors += 1

    @staticmethod
    def _severity(state: dict[str, Any], casualty_id: str) -> str | None:
        for casualty in state.get("scenario", {}).get("casualties", []):
            if casualty.get("id") == casualty_id:
                return (casualty.get("medical_assessment") or {}).get("severity")
        return None
