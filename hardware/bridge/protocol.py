"""Line protocol shared by the firmware and the laptop bridge.

Must stay in sync with hardware/firmware/aegis_responder/aegis_responder.ino.
"""

from dataclasses import dataclass
import re

BAUD_RATE = 115200
CMD_MAX = 40
LABEL_MAX = 4
URGENCIES = ("high", "normal")

_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceMessage:
    verb: str
    args: tuple[str, ...]

    @property
    def command_id(self) -> str | None:
        if self.verb in {"DELIVERED", "CANCELLED", "ACK", "DONE"} and self.args:
            return self.args[0]
        return None


def _check_token(value: str, name: str, max_len: int) -> None:
    if not value or len(value) > max_len or not _TOKEN.match(value):
        raise ProtocolError(f"invalid {name}: {value!r}")


def format_assign(command_id: str, label: str, urgency: str) -> str:
    _check_token(command_id, "command_id", CMD_MAX)
    _check_token(label, "label", LABEL_MAX)
    if urgency not in URGENCIES:
        raise ProtocolError(f"invalid urgency: {urgency!r}")
    return f"ASSIGN {command_id} {label} {urgency}"


def format_cancel(command_id: str) -> str:
    _check_token(command_id, "command_id", CMD_MAX)
    return f"CANCEL {command_id}"


def format_ok(kind: str, command_id: str) -> str:
    if kind not in {"ACK", "DONE"}:
        raise ProtocolError(f"invalid report kind: {kind!r}")
    _check_token(command_id, "command_id", CMD_MAX)
    return f"OK {kind} {command_id}"


def parse_device_line(line: str) -> DeviceMessage | None:
    """Parse one line from the device; None for blank lines."""
    parts = line.strip().split()
    if not parts:
        return None
    return DeviceMessage(verb=parts[0], args=tuple(parts[1:]))
