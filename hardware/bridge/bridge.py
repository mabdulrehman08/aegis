"""Device link: shows assignments on the responder device and reports what the
responder physically did (delivered / acknowledged / completed).

This layer knows nothing about the backend. ``BackendSync`` decides what to show
(approved dispatches only) and turns reports into contract events.
"""

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable

from .events import REPORT_TO_KIND, utc_now
from .protocol import format_assign, format_cancel, format_ok, parse_device_line
from .transport import NoDeviceYet, Transport, TransportError

log = logging.getLogger("aegis.hardware")

ASSIGN_RETRY_S = 1.0
ASSIGN_MAX_RETRIES = 5
HANDSHAKE_TIMEOUT_S = 3.0
PING_EVERY_S = 2.0
LINK_TIMEOUT_S = 7.0  # no line from the device for this long = out of range / unplugged


@dataclass
class ActiveAssignment:
    token: str  # command identifier as sent to the device
    label: str
    urgency: str
    sent_at: float
    context: dict[str, Any] = field(default_factory=dict)  # caller's correlation data
    retries: int = 0
    reported: set[str] = field(default_factory=set)

    @property
    def finished(self) -> bool:
        return "completed" in self.reported


@dataclass
class BridgeStats:
    delivered_latencies_s: list[float] = field(default_factory=list)
    duplicate_reports: int = 0
    reconnects: int = 0
    device_errors: list[str] = field(default_factory=list)


# on_report(kind, assignment, occurred_at_iso)
ReportCallback = Callable[[str, ActiveAssignment, str], None]


class Bridge:
    def __init__(
        self,
        transport_factory: Callable[[], Transport],
        *,
        on_report: ReportCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = transport_factory
        self._transport: Transport | None = None
        self.on_report = on_report
        self.clock = clock
        self.device_id: str | None = None
        self.firmware: str | None = None
        self.link: str | None = None
        self.active: ActiveAssignment | None = None
        self.stats = BridgeStats()
        self._last_rx = 0.0
        self._last_ping = 0.0
        self._waiting_logged = False
        self._clear_pending = False

    # ------------------------------------------------------------ connection

    @property
    def connected(self) -> bool:
        return self._transport is not None and self.device_id is not None

    def connect(self) -> bool:
        self._drop_transport()
        try:
            self._transport = self._factory()
            self._transport.write_line("HELLO")
            deadline = time.monotonic() + HANDSHAKE_TIMEOUT_S
            while time.monotonic() < deadline and not self.device_id:
                for line in self._transport.read_lines():
                    msg = parse_device_line(line)
                    if msg and msg.verb == "HELLO":
                        fields = dict(a.split("=", 1) for a in msg.args if "=" in a)
                        self.device_id = fields.get("device")
                        self.firmware = fields.get("fw")
                        self.link = fields.get("link")
                if not self.device_id:
                    time.sleep(0.02)
        except NoDeviceYet as exc:
            if not self._waiting_logged:
                log.info("%s", exc)
                self._waiting_logged = True
            self._drop_transport()
            return False
        except TransportError as exc:
            log.warning("connect failed: %s", exc)
            self._drop_transport()
            return False
        if not self.device_id:
            log.warning("device did not answer HELLO")
            self._drop_transport()
            return False
        log.info("connected to %s (fw %s, link %s)", self.device_id, self.firmware, self.link)
        self._waiting_logged = False  # log the next wait (e.g. out of range) once again
        self._last_rx = self._last_ping = self.clock()
        if self.active and not self.active.finished:
            # Re-show an unfinished assignment after a reconnect or device reboot.
            # Same token keeps the device's progress; a different one replaces it.
            self._send(format_assign(self.active.token, self.active.label, self.active.urgency))
        else:
            # The device may still show a task from before (e.g. this bridge was
            # restarted). Do not wipe it yet: the caller confirms with the backend
            # first, then either re-shows it (keeping the responder's progress)
            # or calls clear_stale().
            self._clear_pending = True
        return True

    def clear_stale(self) -> None:
        """Clear whatever the device shows, once it is confirmed nothing should be shown."""
        if self._clear_pending and self.connected and self.active is None:
            self._send("RESET")
        self._clear_pending = False

    def _drop_transport(self) -> None:
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self.device_id = None
        self.link = None

    def _send(self, line: str) -> None:
        if self._transport is None:
            raise TransportError("not connected")
        log.debug("-> %s", line)
        self._transport.write_line(line)

    # ------------------------------------------------------------ assignments

    def assign(self, token: str, label: str, urgency: str, context: dict[str, Any] | None = None) -> None:
        if self.active and not self.active.finished:
            raise RuntimeError(f"device busy with {self.active.token}; cancel it first")
        line = format_assign(token, label, urgency)  # validates before changing state
        self.active = ActiveAssignment(token, label, urgency, self.clock(), dict(context or {}))
        self._clear_pending = False  # ASSIGN replaces whatever the device showed
        if self.connected:
            self._send(line)

    def cancel(self) -> None:
        """Clear the device. The active assignment is dropped locally even if the
        device is offline, so a stale task can never be acknowledged later."""
        a = self.active
        if a is None:
            return
        self.active = None
        if self.connected and not a.finished:
            try:
                self._send(format_cancel(a.token))
            except TransportError:
                pass

    # ------------------------------------------------------------ polling

    def poll(self) -> None:
        if self._transport is None:
            return
        try:
            lines = self._transport.read_lines()
            now = self.clock()
            if lines:
                self._last_rx = now
            for line in lines:
                self._handle_line(line)
            if now - self._last_rx > LINK_TIMEOUT_S:
                raise TransportError(f"no reply from device for {now - self._last_rx:.0f} s")
            if now - self._last_ping >= PING_EVERY_S:
                self._last_ping = now
                self._send("PING")
            self._retry_assign()
        except TransportError as exc:
            log.warning("device link lost: %s", exc)
            self._drop_transport()
            self.stats.reconnects += 1

    def _retry_assign(self) -> None:
        a = self.active
        if not a or "delivered" in a.reported or a.retries >= ASSIGN_MAX_RETRIES:
            return
        if self.clock() - a.sent_at >= ASSIGN_RETRY_S * (a.retries + 1):
            a.retries += 1
            self._send(format_assign(a.token, a.label, a.urgency))

    def _handle_line(self, line: str) -> None:
        msg = parse_device_line(line)
        if msg is None:
            return
        log.debug("<- %s", line)
        if msg.verb in ("ACK", "DONE") and msg.command_id:
            # Always confirm so the device stops retrying, even for stale commands.
            self._send(format_ok(msg.verb, msg.command_id))
        if msg.verb == "ERR":
            self.stats.device_errors.append(" ".join(msg.args))
            log.warning("device error: %s", " ".join(msg.args))
            return
        kind = REPORT_TO_KIND.get(msg.verb)
        if kind is None:
            return
        a = self.active
        if a is None or msg.command_id != a.token:
            log.warning("ignoring %s for inactive command %s", msg.verb, msg.command_id)
            return
        if kind in a.reported:
            self.stats.duplicate_reports += 1
            return
        a.reported.add(kind)
        if kind == "delivered":
            self.stats.delivered_latencies_s.append(self.clock() - a.sent_at)
        if self.on_report:
            self.on_report(kind, a, utc_now())
