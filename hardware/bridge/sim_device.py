"""Software stand-in for the Arduino, mirroring the firmware state machine.

Lets the bridge, backend and dashboard be exercised without the board.
Time is passed in explicitly so tests are deterministic.
"""

from collections import deque

from .protocol import CMD_MAX, LABEL_MAX

FW_VERSION = "0.1.0-sim"
RETRY_S = 1.0
MAX_RETRIES = 10


class SimulatedDevice:
    def __init__(self, device_id: str = "responder-device-sim") -> None:
        self.device_id = device_id
        self.state = "IDLE"
        self.command_id = ""
        self.label = ""
        self.urgent = False
        self.display = ""
        self._pending_kind = ""
        self._pending_sent_at = 0.0
        self._pending_retries = 0
        self._outbox: deque[str] = deque()
        self.drop_next_reports = 0  # test hook: simulate lost device lines

    # host -> device
    def receive_line(self, line: str, now: float = 0.0) -> None:
        t = line.split()
        if not t:
            return
        verb = t[0]
        if verb == "HELLO":
            self._emit(f"HELLO aegis-responder fw={FW_VERSION} device={self.device_id}")
        elif verb == "PING":
            self._emit("PONG")
        elif verb == "ASSIGN":
            if len(t) != 4 or len(t[1]) > CMD_MAX or len(t[2]) > LABEL_MAX:
                self._emit("ERR bad_assign")
                return
            if t[1] != self.command_id:
                self.command_id, self.label = t[1], t[2]
                self.urgent = t[3] == "high"
                self.state = "ALERTING"
                self._pending_kind = ""
                self.display = self.label
            self._emit(f"DELIVERED {self.command_id}")
        elif verb == "CANCEL":
            if len(t) == 2 and t[1] == self.command_id:
                self._emit(f"CANCELLED {t[1]}")
                self._pending_kind = ""
                self._clear()
            else:
                self._emit("ERR unknown_command")
        elif verb == "RESET":
            self._pending_kind = ""
            self._clear()
            self._emit("CLEARED")
        elif verb == "OK":
            if len(t) == 3 and t[1] == self._pending_kind and t[2] == self.command_id:
                was_done = self._pending_kind == "DONE"
                self._pending_kind = ""
                if was_done:
                    self._clear()
        else:
            self._emit("ERR unknown_verb")

    # physical inputs
    def short_press(self, now: float = 0.0) -> None:
        if self.state == "ALERTING":
            self.state = "ACKED"
            self._queue_report("ACK", now)

    def long_press(self, now: float = 0.0) -> None:
        if self.state == "ACKED" and not self._pending_kind:
            self._queue_report("DONE", now)
            self.display = "OK"

    def tick(self, now: float) -> None:
        if not self._pending_kind or now - self._pending_sent_at < RETRY_S:
            return
        if self._pending_retries >= MAX_RETRIES:
            self._emit(f"ERR no_host_ok {self._pending_kind}")
            self._pending_kind = ""
            return
        self._pending_retries += 1
        self._pending_sent_at = now
        self._send_report(self._pending_kind)

    # device -> host
    def read_lines(self) -> list[str]:
        lines = list(self._outbox)
        self._outbox.clear()
        return lines

    @property
    def led(self) -> str:
        return {"IDLE": "off", "ALERTING": "blinking", "ACKED": "on"}[self.state]

    def _queue_report(self, kind: str, now: float) -> None:
        self._pending_kind = kind
        self._pending_retries = 0
        self._pending_sent_at = now
        self._send_report(kind)

    def _send_report(self, kind: str) -> None:
        if self.drop_next_reports > 0:
            self.drop_next_reports -= 1
            return
        self._emit(f"{kind} {self.command_id}")

    def _emit(self, line: str) -> None:
        self._outbox.append(line)

    def _clear(self) -> None:
        self.state = "IDLE"
        self.command_id = self.label = self.display = ""
        self.urgent = False
