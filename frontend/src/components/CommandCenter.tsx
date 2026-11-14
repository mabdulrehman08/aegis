"use client";

import { useCallback, useEffect, useState } from "react";
import { api, SCENARIO_ID } from "@/lib/api";
import { useSimulation, type LinkStatus } from "@/lib/useSimulation";
import type { SceneMap as SceneMapT, SimulationApproval } from "@/lib/types";
import { CasualtyPanel } from "./CasualtyPanel";
import { LoopStrip } from "./LoopStrip";
import { ProposalPanel } from "./ProposalPanel";
import { ResponderPanel } from "./ResponderPanel";
import { SceneMap } from "./SceneMap";
import { TasksPanel } from "./TasksPanel";
import { Timeline } from "./Timeline";
import { Button } from "./ui";

const LINK: Record<LinkStatus, { label: string; color: string }> = {
  connecting: { label: "connecting", color: "var(--muted)" },
  live: { label: "live · ws", color: "var(--ok)" },
  polling: { label: "polling", color: "var(--warn)" },
  offline: { label: "backend offline", color: "var(--danger)" },
};

export function CommandCenter() {
  const { state, events, link, pending, toasts, run, toast } = useSimulation();
  const [selected, setSelected] = useState<string | null>(null);
  const [editMap, setEditMap] = useState(false);
  const [operator, setOperator] = useState("demo-command");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    try {
      const saved = localStorage.getItem("aegis.operator");
      if (saved) setOperator(saved);
    } catch {
      /* storage unavailable */
    }
    return () => clearInterval(t);
  }, []);

  const saveOperator = (v: string) => {
    setOperator(v);
    try {
      localStorage.setItem("aegis.operator", v);
    } catch {
      /* ignore */
    }
  };

  const actor = operator.trim() || "demo-command";
  const busy = pending !== null;
  const proposal = state?.current_proposal_id ? state.proposals.find((p) => p.id === state.current_proposal_id) ?? null : null;

  const toggleCell = useCallback(
    (row: number, col: number) => {
      const map = state?.scenario.scene_map;
      if (!map?.occupancy_grid) return;
      const grid = map.occupancy_grid;
      const cells = grid.cells.slice();
      const i = row * grid.width + col;
      cells[i] = cells[i] === 100 ? 0 : 100;
      const base = map.revision.replace(/-ui-[a-z0-9]+$/, "");
      const next: SceneMapT = {
        ...map,
        revision: `${base}-ui-${Date.now().toString(36)}`,
        occupancy_grid: { ...grid, cells },
      };
      void run("Map update", () => api.updateMap(next)).then((r) => {
        if (r) toast("info", `Cell (${col}, ${row}) ${cells[i] === 100 ? "blocked" : "cleared"} — replanning`);
      });
    },
    [state, run, toast],
  );

  if (!state) {
    return (
      <main className="flex h-screen flex-col items-center justify-center gap-3 text-center">
        <Logo />
        <p className="text-sm text-muted">{link === "offline" ? "Cannot reach the AEGIS backend." : "Connecting to backend…"}</p>
        {link === "offline" && (
          <pre className="mono rounded border border-line bg-panel px-3 py-2 text-left text-[11px] text-muted">
            uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
          </pre>
        )}
      </main>
    );
  }

  return (
    <main className="flex h-screen min-h-[720px] flex-col gap-2 overflow-hidden p-2">
      {/* top bar */}
      <header className="flex items-center gap-4 rounded-md border border-line bg-panel px-3 py-2">
        <Logo />
        <div className="hidden min-w-0 flex-col lg:flex">
          <span className="truncate text-[13px] font-semibold">{state.scenario.name}</span>
          <span className="mono text-[10px] text-muted">
            scenario {SCENARIO_ID} · epoch {state.epoch} · scene rev {state.scene_revision} · seq {state.sequence}
          </span>
        </div>
        <div className="sim-stripe mono ml-auto rounded border border-warn/60 px-2.5 py-1 text-[11px] font-bold tracking-[0.18em] text-warn">
          SIMULATION ONLY
        </div>
        <div className="mono flex items-center gap-1.5 text-[11px]" style={{ color: LINK[link].color }}>
          <span className={`h-2 w-2 rounded-full ${link === "live" ? "" : "blink"}`} style={{ background: LINK[link].color }} />
          {LINK[link].label}
        </div>
        <label className="flex items-center gap-1.5">
          <span className="label">operator</span>
          <input
            value={operator}
            onChange={(e) => saveOperator(e.target.value)}
            className="mono w-32 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
          />
        </label>
        <Button
          size="sm"
          disabled={busy}
          onClick={() => {
            if (window.confirm("Reset the simulation to the seeded demo scenario?")) void run("Reset", () => api.reset());
          }}
        >
          Reset
        </Button>
      </header>

      <LoopStrip state={state} proposal={proposal} events={events} />

      <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)_360px] gap-2">
        {/* left: casualties + responders */}
        <div className="flex min-h-0 flex-col gap-2">
          <CasualtyPanel
            state={state}
            proposal={proposal}
            selectedId={selected}
            onSelect={setSelected}
            busy={busy}
            now={now}
            onAssess={(cid, sev) => void run("Assessment", () => api.assess(cid, sev, actor))}
            onAssociate={(tid, cid, create) =>
              void run(create ? "Confirm casualty" : "Link track", () => api.associate(tid, cid, create, actor))
            }
          />
          <ResponderPanel
            state={state}
            selectedId={selected}
            onSelect={setSelected}
            busy={busy}
            onStatus={(rid, status) => void run("Responder update", () => api.updateResponder(rid, status))}
          />
        </div>

        {/* center: map + timeline */}
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_210px] gap-2">
          <section className="panel relative flex min-h-0 flex-col">
            <div className="flex items-center gap-3 border-b border-line px-3 py-2">
              <h2 className="label !text-text">Scene map</h2>
              <Legend />
              <div className="ml-auto flex items-center gap-2">
                {pending && <span className="mono blink text-[10.5px] text-accent">{pending}…</span>}
                <Button
                  size="sm"
                  variant={editMap ? "warn" : "ghost"}
                  onClick={() => setEditMap((v) => !v)}
                  disabled={!state.scenario.scene_map?.occupancy_grid}
                  title="Click grid cells to block or clear them; the backend replans automatically"
                >
                  {editMap ? "Done editing" : "Block route"}
                </Button>
              </div>
            </div>
            {editMap && (
              <div className="sim-stripe border-b border-warn/30 px-3 py-1 text-[11px] text-warn">
                Click cells to toggle blocked / free. Each change publishes a new map revision and triggers replanning.
              </div>
            )}
            <div className="min-h-0 flex-1 p-2">
              <SceneMap
                state={state}
                proposal={proposal}
                selectedId={selected}
                onSelect={setSelected}
                editMode={editMap}
                onToggleCell={toggleCell}
                now={now}
              />
            </div>
          </section>
          <Timeline events={events} />
        </div>

        {/* right: recommendation + tasks */}
        <div className="flex min-h-0 flex-col gap-2">
          <div className="scrollbar-thin max-h-[62%] shrink-0 overflow-y-auto">
            <ProposalPanel
              state={state}
              proposal={proposal}
              pending={pending}
              now={now}
              onReplan={() => void run("Replan", () => api.replan(actor))}
              onDecide={(p, decision) =>
                void run(decision === "approve" ? "Approve" : "Reject", () =>
                  api.decide(p.id, p.revision, decision, actor, state.epoch),
                ).then((r) => {
                  const a = r?.result as SimulationApproval | null | undefined;
                  if (a && decision === "approve") toast("info", `Approval ${a.id} recorded for rev ${a.proposal_revision}`);
                })
              }
              onDispatch={(p, approvalId) =>
                void run("Dispatch", () => api.dispatch(p.id, p.revision, approvalId, actor, state.epoch))
              }
            />
          </div>
          <TasksPanel
            state={state}
            busy={busy}
            onSimulate={(a, kind) => void run(`Simulated ${kind}`, () => api.simulateDevice(a.id, kind))}
          />
        </div>
      </div>

      {/* toasts */}
      <div className="pointer-events-none fixed bottom-3 right-3 z-50 flex w-96 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto rounded border px-3 py-2 text-xs shadow-lg ${
              t.kind === "error" ? "border-danger/60 bg-[#1d0b0b] text-[#fecaca]" : "border-line-strong bg-panel-2 text-text"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </main>
  );
}

function Logo() {
  return (
    <div className="flex items-center gap-2">
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
        <path d="M12 2 L21 6 V12 C21 17 17 21 12 22 C7 21 3 17 3 12 V6 Z" fill="none" stroke="var(--accent)" strokeWidth="1.8" />
        <path d="M12 7 V17 M7 12 H17" stroke="var(--accent)" strokeWidth="1.8" />
      </svg>
      <div className="leading-none">
        <div className="mono text-[15px] font-bold tracking-[0.22em]">AEGIS</div>
        <div className="label !text-[8.5px] !tracking-[0.2em]">command center</div>
      </div>
    </div>
  );
}

function Legend() {
  const item = (el: React.ReactNode, text: string) => (
    <span className="flex items-center gap-1">
      {el}
      {text}
    </span>
  );
  return (
    <div className="mono hidden items-center gap-3 text-[10px] text-muted xl:flex">
      {item(<span className="h-2.5 w-2.5 rounded-full border border-dashed border-[var(--sev-unassessed)]" />, "unassessed")}
      {item(<span className="h-2.5 w-2.5 rounded-sm border-2 border-accent" />, "responder")}
      {item(<span className="w-4 border-t-2 border-dashed border-accent" />, "proposed")}
      {item(<span className="w-4 border-t-2 border-warn" />, "dispatched")}
      {item(<span className="w-4 border-t-2 border-ok" />, "ack'd")}
      {item(<span className="h-2.5 w-2.5 border border-dashed border-warn" />, "CV track")}
    </div>
  );
}

export type { LinkStatus };
