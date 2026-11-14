"""Byte transports between the bridge and a responder device."""

import select
import socket
from typing import Protocol

from .protocol import BAUD_RATE
from .sim_device import SimulatedDevice

HOTSPOT_HOST = "192.168.137.1"  # Windows Mobile Hotspot's address for the hosting laptop
DEFAULT_TCP_PORT = 7777


class TransportError(OSError):
    pass


class Transport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_lines(self) -> list[str]: ...
    def close(self) -> None: ...


class LineBuffer:
    def __init__(self) -> None:
        self._buffer = b""

    def push(self, chunk: bytes) -> list[str]:
        self._buffer += chunk
        *complete, self._buffer = self._buffer.split(b"\n")
        if len(self._buffer) > 4096:  # garbage without newlines: drop it
            self._buffer = b""
        return [raw.decode("ascii", errors="replace").strip() for raw in complete]


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
        self._lines = LineBuffer()

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
        return self._lines.push(chunk)

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:
            pass


class TcpTransport:
    """One device connection accepted by ``TcpDeviceServer``."""

    def __init__(self, sock: socket.socket, peer: str) -> None:
        self._sock = sock
        self.peer = peer
        self._sock.settimeout(2.0)  # sends only; reads use select
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._lines = LineBuffer()

    def write_line(self, line: str) -> None:
        try:
            self._sock.sendall((line + "\n").encode("ascii"))
        except OSError as exc:
            raise TransportError(f"device link lost: {exc}") from exc

    def read_lines(self) -> list[str]:
        lines: list[str] = []
        try:
            while select.select([self._sock], [], [], 0)[0]:
                chunk = self._sock.recv(1024)
                if not chunk:
                    raise TransportError("device closed the connection")
                lines += self._lines.push(chunk)
        except TransportError:
            raise
        except OSError as exc:
            raise TransportError(f"device link lost: {exc}") from exc
        return lines

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class TcpDeviceServer:
    """Listens for the responder device over WiFi.

    Binds only to the hotspot address by default, so the unauthenticated device
    port is reachable from the team's password-protected hotspot and not from
    the rest of the campus network. Binding is retried lazily, so the bridge can
    start before the hotspot is up.
    """

    def __init__(self, host: str = HOTSPOT_HOST, port: int = DEFAULT_TCP_PORT,
                 accept_wait_s: float = 0.05) -> None:
        self.host, self.port, self.accept_wait_s = host, port, accept_wait_s
        self._listener: socket.socket | None = None
        self.bind_error: str | None = None

    def _ensure_listening(self) -> socket.socket:
        if self._listener is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((self.host, self.port))
                sock.listen(2)
            except OSError as exc:
                sock.close()
                self.bind_error = f"cannot listen on {self.host}:{self.port} ({exc}); is the hotspot on?"
                raise TransportError(self.bind_error) from exc
            self._listener = sock
            self.bind_error = None
        return self._listener

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self._listener.getsockname()[1] if self._listener else self.port)

    def accept(self) -> TcpTransport:
        """Return the next device connection, or raise TransportError if none is waiting.

        A reconnecting device opens a new connection while the old one may still
        look open (half-open after roaming); taking the newest one replaces it.
        """
        listener = self._ensure_listening()
        newest = None
        while select.select([listener], [], [], self.accept_wait_s if newest is None else 0)[0]:
            conn, (ip, port) = listener.accept()
            if newest is not None:
                newest.close()
            newest = TcpTransport(conn, f"{ip}:{port}")
        if newest is None:
            raise TransportError("waiting for the responder device to join over WiFi")
        return newest

    def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None


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
