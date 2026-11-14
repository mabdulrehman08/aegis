"""End-to-end verification of the responder device against a running backend.

    uv run python -m hardware.tools.verify_e2e                     # automatic checks
    uv run python -m hardware.tools.verify_e2e --presses           # + physical button checks

Requires: backend on --backend-url and the Arduino on the laptop hotspot (WiFi).
It RESETS the backend's demo scenario, so never run it during a live demo.
Operator approval/dispatch are performed by this script (SIMULATION ONLY) so the
device path can be measured; in the demo they come from the dashboard.

Checks, all against the backend's own records:
  1. delivery latency: dispatch -> device shows task -> backend records `delivered`
  2. dropped WiFi link mid-task: device reconnects, task is re-shown, no duplicate event
  3. bridge restart mid-task: fresh bridge state, still no duplicate event
  4. (--presses) repeated presses and holds: exactly one `acknowledged` and one `completed`
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import statistics
import sys
import time

from hardware.bridge.backend import BackendSync, http_requester
from hardware.bridge.bridge import Bridge
from hardware.bridge.transport import DEFAULT_TCP_PORT, HOTSPOT_HOST, TcpDeviceServer

ACTOR = "verify-e2e-operator"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Timeline:
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.perf_counter())

    def ms(self, start: str, end: str) -> float | None:
        if start in self.marks and end in self.marks:
            return (self.marks[end] - self.marks[start]) * 1000
        return None


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.request = http_requester(args.backend_url, timeout_s=3)
        self.base = f"/api/v1/simulations/{args.scenario}"
        host, _, port = args.wifi.rpartition(":")
        self.server = TcpDeviceServer(host, int(port))
        self.results: dict = {"started_at": now_iso(), "checks": {}}
        self.timeline = Timeline()
        self.bridge: Bridge | None = None
        self.sync: BackendSync | None = None
        self.failures: list[str] = []

    # ------------------------------------------------------------ plumbing

    def api(self, method: str, path: str, body=None, key: str | None = None):
        status, payload = self.request(method, self.base + path, body,
                                       {"Idempotency-Key": key} if key else {})
        if not 200 <= status < 300:
            raise RuntimeError(f"{method} {path} -> {status}: {payload}")
        return payload

    def state(self) -> dict:
        return self.api("GET", "/state")

    def start_bridge(self) -> None:
        """A fresh bridge + sync, exactly like starting the console again."""
        self.bridge = Bridge(self.server.accept)
        self.sync = BackendSync(self.bridge, self.request, scenario_id=self.args.scenario,
                                responder_id=self.args.responder,
                                on_event=lambda e, r: self.timeline.mark(f"posted:{e['kind']}"))
        forward = self.bridge.on_report

        def on_report(kind, active, occurred_at):
            self.timeline.mark(f"device:{kind}")
            forward(kind, active, occurred_at)

        self.bridge.on_report = on_report
        if not self.wait(lambda: self.bridge.connected or self.bridge.connect(), 40, "device to join over WiFi"):
            raise SystemExit("Device did not connect over WiFi. Is it powered and the hotspot on?")

    def pump(self) -> None:
        self.bridge.poll()
        self.sync.tick()

    def wait(self, condition, timeout: float, what: str) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.bridge and self.sync:
                self.pump()
            if condition():
                return True
            time.sleep(0.01)
        print(f"   timed out waiting for {what}")
        return False

    def check(self, name: str, ok: bool, detail: str) -> None:
        print(f"   [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        self.results["checks"][name] = {"ok": ok, "detail": detail}
        if not ok:
            self.failures.append(name)

    # ------------------------------------------------------------ scenario

    def dispatch_round(self, round_id: str) -> dict:
        """Reset, approve and dispatch; return this responder's assignment."""
        self.api("POST", "/reset", {"mode": "simulation"})
        snapshot = self.state()
        proposal = next(p for p in snapshot["proposals"] if p["id"] == snapshot["current_proposal_id"])
        approval = self.api("POST", f"/proposals/{proposal['id']}/decision",
                            {"proposal_revision": proposal["revision"], "decision": "approve",
                             "actor": ACTOR, "mode": "simulation"}, f"verify-approve-{round_id}")
        self.timeline = Timeline()
        self.timeline.mark("dispatch:sent")
        result = self.api("POST", "/dispatches",
                          {"proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
                           "approval_id": approval["result"]["id"], "actor": ACTOR, "mode": "simulation"},
                          f"verify-dispatch-{round_id}")
        self.timeline.mark("dispatch:returned")
        return next(a for a in result["result"]["assignments"] if a["responder_id"] == self.args.responder)

    def assignment(self, assignment_id: str) -> dict:
        return next(a for a in self.state()["assignments"] if a["id"] == assignment_id)

    def device_events(self, assignment_id: str) -> dict[str, int]:
        page = self.api("GET", "/events?limit=1000")
        counts = {"delivered": 0, "acknowledged": 0, "completed": 0}
        for event in page["events"]:
            if event["type"].startswith("simulation.device_"):
                if event["payload"]["assignment"]["id"] == assignment_id:
                    counts[event["type"].removeprefix("simulation.device_")] += 1
        return counts

    def backend_recorded(self, assignment_id: str, status: str) -> bool:
        rank = ["dispatched", "delivered", "acknowledged", "completed"]
        return rank.index(self.assignment(assignment_id)["status"]) >= rank.index(status)

    # ------------------------------------------------------------ checks

    def wifi_rtt(self) -> None:
        print("\n0. WiFi round trip (bridge PING -> device PONG)")
        samples = []
        for _ in range(20):
            self.bridge._transport.read_lines()  # drain
            t0 = time.perf_counter()
            self.bridge._send("PING")
            got = False
            while time.perf_counter() - t0 < 2 and not got:
                got = any(l == "PONG" for l in self.bridge._transport.read_lines())
                time.sleep(0.002)
            if got:
                samples.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.05)
        self.results["wifi_rtt_ms"] = samples
        self.check("wifi_rtt", len(samples) >= 18,
                   f"median {statistics.median(samples):.0f} ms, max {max(samples):.0f} ms, {len(samples)}/20 replies")

    def delivery_latency(self) -> None:
        rounds = self.args.rounds
        print(f"\n1. Delivery latency over {rounds} approved dispatches")
        to_device, to_backend = [], []
        for i in range(rounds):
            record = self.dispatch_round(f"lat-{int(time.time())}-{i}")
            ok = self.wait(lambda: self.backend_recorded(record["id"], "delivered"), 10, "delivered")
            if ok:
                to_device.append(self.timeline.ms("dispatch:returned", "device:delivered"))
                to_backend.append(self.timeline.ms("dispatch:returned", "posted:delivered"))
        self.results["delivery_ms"] = {"to_device": to_device, "to_backend": to_backend}
        self.check("delivery", len(to_device) == rounds,
                   f"dispatch->device median {statistics.median(to_device):.0f} ms (max {max(to_device):.0f}); "
                   f"dispatch->backend recorded median {statistics.median(to_backend):.0f} ms "
                   f"(max {max(to_backend):.0f}); bridge polls state every 400 ms")

    def link_drop(self) -> None:
        print("\n2. WiFi link dropped mid-task")
        record = self.dispatch_round(f"drop-{int(time.time())}")
        self.wait(lambda: self.backend_recorded(record["id"], "delivered"), 10, "delivered")
        t0 = time.perf_counter()
        self.bridge._drop_transport()  # like the laptop losing the device's connection
        back = self.wait(lambda: self.bridge.connected or self.bridge.connect(), 30, "device to reconnect")
        reconnect_ms = (time.perf_counter() - t0) * 1000
        redelivered = self.wait(lambda: self.bridge.stats.duplicate_reports >= 1, 10,
                                "device to confirm the re-shown task")
        time.sleep(1)
        self.pump()
        counts = self.device_events(record["id"])
        self.results["link_drop"] = {"reconnect_ms": reconnect_ms, "events": counts}
        self.check("reconnect_no_duplicate", back and redelivered and counts["delivered"] == 1,
                   f"reconnected in {reconnect_ms / 1000:.1f} s, device re-confirmed the same task, "
                   f"backend delivered events = {counts['delivered']}")
        self.last_assignment = record

    def bridge_restart(self) -> None:
        print("\n3. Bridge restarted mid-task (fresh process state)")
        record = self.last_assignment
        self.bridge._drop_transport()
        self.start_bridge()
        shown = self.wait(lambda: self.bridge.active is not None and "delivered" in self.bridge.active.reported,
                          15, "re-shown task to be confirmed")
        time.sleep(1)
        self.pump()
        counts = self.device_events(record["id"])
        self.check("restart_no_duplicate", shown and counts["delivered"] == 1 and self.sync.stats.posted == 0,
                   f"task re-shown, new bridge posted {self.sync.stats.posted} events, "
                   f"backend delivered events = {counts['delivered']}")

    def presses(self) -> None:
        print("\n4. Physical button: repeated presses and holds")
        record = self.dispatch_round(f"press-{int(time.time())}")
        self.wait(lambda: self.backend_recorded(record["id"], "delivered"), 10, "delivered")
        print("   >>> The device is blinking. PRESS the button 3-4 times quickly.")
        acked = self.wait(lambda: self.backend_recorded(record["id"], "acknowledged"), 60, "a button press")
        press_to_backend = self.timeline.ms("device:acknowledged", "posted:acknowledged")
        print("   >>> Keep pressing a few more times for 3 seconds...")
        self.wait(lambda: False, 3, "")
        print("   >>> Now HOLD the button for 2 seconds, release, then hold it again.")
        done = self.wait(lambda: self.backend_recorded(record["id"], "completed"), 60, "a hold")
        hold_to_backend = self.timeline.ms("device:completed", "posted:completed")
        self.wait(lambda: False, 4, "")
        counts = self.device_events(record["id"])
        final = self.assignment(record["id"])
        self.results["presses"] = {"events": counts, "press_to_backend_ms": press_to_backend,
                                   "hold_to_backend_ms": hold_to_backend, "final_status": final["status"]}
        self.check("repeated_presses_single_transitions",
                   acked and done and counts == {"delivered": 1, "acknowledged": 1, "completed": 1},
                   f"backend events {counts}; press->backend {press_to_backend:.0f} ms, "
                   f"hold->backend {hold_to_backend:.0f} ms (plus ~half the WiFi round trip)"
                   if acked and done else f"backend events {counts}")
        ordered = (final["delivered_at"] or "") <= (final["acknowledged_at"] or "") <= (final["completed_at"] or "")
        self.check("milestones_in_order", acked and done and ordered,
                   f"delivered {final['delivered_at']} <= acknowledged {final['acknowledged_at']} "
                   f"<= completed {final['completed_at']}")

    def run(self) -> int:
        print("=== AEGIS responder end-to-end verification (SIMULATION ONLY) ===")
        print(f"backend {self.args.backend_url}, device on WiFi {self.args.wifi}, responder {self.args.responder}")
        self.start_bridge()
        print(f"device {self.bridge.device_id} fw {self.bridge.firmware} link {self.bridge.link}")
        self.results["device"] = {"id": self.bridge.device_id, "fw": self.bridge.firmware, "link": self.bridge.link}
        self.wifi_rtt()
        self.delivery_latency()
        self.link_drop()
        self.bridge_restart()
        if self.args.presses:
            self.presses()
        self.api("POST", "/reset", {"mode": "simulation"})  # leave the device idle
        self.wait(lambda: self.bridge.active is None, 5, "device to clear")
        self.results["finished_at"] = now_iso()
        self.results["failures"] = self.failures
        out = Path("hardware/logs") / f"verify-{datetime.now():%Y%m%d-%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.results, indent=2))
        print(f"\n{'ALL CHECKS PASSED' if not self.failures else 'FAILED: ' + ', '.join(self.failures)}"
              f"  (details: {out})")
        return 0 if not self.failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", default="demo")
    parser.add_argument("--responder", default="responder-001")
    parser.add_argument("--wifi", default=f"{HOTSPOT_HOST}:{DEFAULT_TCP_PORT}")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--presses", action="store_true", help="Include the physical button checks")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    return Harness(args).run()


if __name__ == "__main__":
    sys.exit(main())
