"""AEGIS responder bridge console (SIMULATION ONLY).

Follow the backend (default): the device shows only assignments a human approved
and dispatched for --responder, and button presses become device events.

    uv run python -m hardware.bridge --sim                         # simulated device
    uv run --with pyserial python -m hardware.bridge --port COM13  # Arduino on USB
    uv run python -m hardware.bridge --wifi                        # Arduino on this laptop's hotspot

Offline (no backend; manual test assignments, nothing is posted):

    uv run python -m hardware.bridge --sim --offline

Commands: status | quit | press | long (--sim only, stand in for the button)
          assign <casualty_id> [high|normal] | cancel   (--offline only)
"""

import argparse
import logging
from pathlib import Path
import queue
import statistics
import sys
import threading
import time
import uuid

from .backend import BackendSync, device_label, http_requester
from .bridge import Bridge
from .events import JsonlLog
from .sim_device import SimulatedDevice
from .transport import DEFAULT_TCP_PORT, HOTSPOT_HOST, LoopbackTransport, SerialTransport, TcpDeviceServer

BANNER = "=== AEGIS RESPONDER BRIDGE - SIMULATION ONLY: not a real dispatch system ==="


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="Arduino serial port, e.g. COM5 or /dev/ttyACM0")
    source.add_argument("--sim", action="store_true", help="Use a simulated device instead of hardware")
    source.add_argument("--wifi", nargs="?", const=f"{HOTSPOT_HOST}:{DEFAULT_TCP_PORT}", metavar="HOST:PORT",
                        help=f"Device connects over WiFi to this laptop's hotspot "
                             f"(default {HOTSPOT_HOST}:{DEFAULT_TCP_PORT})")
    parser.add_argument("--offline", action="store_true", help="No backend; manual test assignments")
    parser.add_argument("--scenario", default="demo")
    parser.add_argument("--responder", default="responder-001", help="Responder this device belongs to")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000",
                        help="Backend base URL; over Tailscale e.g. http://prince-laptop:8000")
    parser.add_argument("--http-timeout", type=float, default=1.5,
                        help="Seconds per backend request (raise on slow/crowded WiFi)")
    parser.add_argument("--log", type=Path, default=Path("hardware/logs/device-events.jsonl"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print(BANNER)

    sim_device = SimulatedDevice() if args.sim else None
    retry_every = 2.0
    if sim_device:
        factory = lambda: LoopbackTransport(sim_device, clock=time.monotonic)  # noqa: E731
    elif args.wifi:
        host, _, port = args.wifi.rpartition(":")
        server = TcpDeviceServer(host or HOTSPOT_HOST, int(port))
        factory = server.accept
        retry_every = 0.3  # accepting is non-blocking, so check often
        print(f"waiting for the responder device on WiFi at {server.host}:{server.port}")
    else:
        factory = lambda: SerialTransport(args.port)  # noqa: E731

    bridge = Bridge(factory)
    sync = None
    if args.offline:
        bridge.on_report = lambda kind, a, at: print(f"REPORT {kind:<12} {a.token} at {at} (offline, not posted)")
        print("offline mode: nothing is sent to the backend")
    else:
        sync = BackendSync(
            bridge, http_requester(args.backend_url, args.http_timeout), scenario_id=args.scenario,
            responder_id=args.responder, event_log=JsonlLog(args.log),
            on_event=lambda e, result: print(f"EVENT {e['kind']:<12} {result:<9} {e['id']}"),
        )
        print(f"following {args.backend_url} scenario={args.scenario} responder={args.responder}")

    commands: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [commands.put(l.strip()) for l in sys.stdin], daemon=True).start()

    last_attempt = 0.0
    while True:
        try:
            if not bridge.connected and time.monotonic() - last_attempt > retry_every:
                last_attempt = time.monotonic()
                if bridge.connect():
                    print(f"device connected: {bridge.device_id} fw={bridge.firmware} link={bridge.link}")
            bridge.poll()
            if sync:
                sync.tick()
        except Exception:  # never let one bad reply kill the bridge mid-demo
            logging.exception("bridge loop error; continuing")
            time.sleep(0.5)

        try:
            cmd = commands.get_nowait()
        except queue.Empty:
            time.sleep(0.02)
            continue
        parts = cmd.split()
        if not parts:
            continue
        try:
            if parts[0] == "quit":
                break
            elif parts[0] == "status":
                print_status(bridge, sync)
            elif parts[0] in ("press", "long") and sim_device:
                (sim_device.short_press if parts[0] == "press" else sim_device.long_press)(time.monotonic())
            elif parts[0] == "assign" and args.offline and len(parts) >= 2:
                urgency = parts[2] if len(parts) > 2 else "normal"
                bridge.assign(f"test-{uuid.uuid4().hex[:8]}", device_label(parts[1]), urgency)
            elif parts[0] == "cancel" and args.offline:
                bridge.cancel()
            else:
                print(__doc__.split("Commands:")[1])
        except Exception as exc:  # keep the console alive during a demo
            print(f"error: {exc}")
    return 0


def print_status(bridge: Bridge, sync: BackendSync | None) -> None:
    a = bridge.active
    print(f"device={bridge.device_id or 'DISCONNECTED'} link={bridge.link} "
          f"showing={a.label + ' ' + a.token if a else 'nothing'}"
          f" reported={sorted(a.reported) if a else []}")
    lat = bridge.stats.delivered_latencies_s
    if lat:
        print(f"delivery latency ms: median={statistics.median(lat) * 1000:.1f} "
              f"max={max(lat) * 1000:.1f} n={len(lat)}")
    print(f"duplicates={bridge.stats.duplicate_reports} reconnects={bridge.stats.reconnects}")
    if sync:
        target = sync.target["id"] if sync.target else None
        print(f"backend={'OK' if sync.stats.last_state_ok else 'UNREACHABLE'} epoch={sync.epoch} "
              f"assignment={target} posted={sync.stats.posted} queued={len(sync.outbox)}")
        for item in sync.stats.rejected[-3:]:
            print(f"  rejected: {item}")


if __name__ == "__main__":
    raise SystemExit(main())
