"""Byte transports between the bridge and a responder device."""

from typing import Protocol

from .protocol import BAUD_RATE
from .sim_device import SimulatedDevice


class TransportError(OSError):
    pass


class Transport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_lines(self) -> list[str]: ...
    def close(self) -> None: ...


class SerialTransport:
    """USB serial to the Arduino. pyserial is imported lazily so the rest of
    the bridge (and its tests) work without it installed."""

    def __init__(self, port: str, baud: int = BAUD_RATE) -> None:
        try:
            import serial
        except ImportError as exc:
            raise TransportError("pyserial is required: uv run --with pyserial ...") from exc
        self._serial_mod = serial
        try:
            self._port = serial.Serial(port, baud, timeout=0)
        except serial.SerialException as exc:
            raise TransportError(str(exc)) from exc
        self._buffer = b""

    def write_line(self, line: str) -> None:
        try:
            self._port.write((line + "\n").encode("ascii"))
        except self._serial_mod.SerialException as exc:
            raise TransportError(str(exc)) from exc

    def read_lines(self) -> list[str]:
        try:
            waiting = self._port.in_waiting
            chunk = self._port.read(waiting) if waiting else b""
        except self._serial_mod.SerialException as exc:
            raise TransportError(str(exc)) from exc
        self._buffer += chunk
        *complete, self._buffer = self._buffer.split(b"\n")
        return [raw.decode("ascii", errors="replace").strip() for raw in complete]

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:
            pass


class LoopbackTransport:
    """Connects the bridge directly to a SimulatedDevice."""

    def __init__(self, device: SimulatedDevice, clock=lambda: 0.0) -> None:
        self.device = device
        self._clock = clock
        self.connected = True

    def write_line(self, line: str) -> None:
        if not self.connected:
            raise TransportError("simulated disconnect")
        self.device.receive_line(line, self._clock())

    def read_lines(self) -> list[str]:
        if not self.connected:
            raise TransportError("simulated disconnect")
        self.device.tick(self._clock())
        return self.device.read_lines()

    def close(self) -> None:
        pass
