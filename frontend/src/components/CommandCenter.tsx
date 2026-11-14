"use client";

import { useCallback, useEffect, useState } from "react";
import { api, SCENARIO_ID } from "@/lib/api";
import { useFieldNotes } from "@/lib/fieldNotes";
import { responderName } from "@/lib/format";
import { useSimulation, type LinkStatus } from "@/lib/useSimulation";
import type { SceneMap as SceneMapT, SimulationApproval, SimulationProposal } from "@/lib/types";
import { CommandBar } from "./CommandBar";
import { FighterPane } from "./FighterPane";
import { LoopStrip } from "./LoopStrip";
import { ProposalPanel } from "./ProposalPanel";
import { SceneMap } from "./SceneMap";
import { SurveillancePane } from "./SurveillancePane";
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
  const { get: note, update: setNote } = useFieldNotes(state?.epoch);
  const [selected, setSelected] = useState<string | null>(null);
  const [fighterId, setFighterId] = useState<string | null>(null);
  const [editMap, setEditMap] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [drawer, setDrawer] = useState(false);
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPlacing(false);
        setEditMap(false);
        setDrawer(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
  const fighters = state?.scenario.responders ?? [];
  const activeFighter = fighters.find((r) => r.id === fighterId)?.id ?? fighters[0]?.id ?? null;

  const select = (id: string | null) => {
    setSelected(id);
    if (id && fighters.some((r) => r.id === id)) setFighterId(id);
  };

  const toggleCell = useCallback(
    (row: number, col: number) => {
      const map = state?.scenario.scene_map;
      if (!map?.occupancy_grid) return;
      const grid = map.occupancy_grid;
      const cells = grid.cells.slice();
      const i = row * grid.width + col;
      cells[i] = cells[i] === 100 ? 0 : 100;
      const base = map.revision.replace(/-ui-[a-z0-9]+$/, "");
      const next: SceneMapT = { ...map, revision: `${base}-ui-${Date.now().toString(36)}`, occupancy_grid: { ...grid, cells } };
      void run("Map update", () => api.updateMap(next)).then((r) => {
        if (r) toast("info", `Cell (${col}, ${row}) ${cells[i] === 100 ? "blocked" : "cleared"} — replanning`);
      });
    },
    [state, run, toast],
  );

  const place = useCallback(
    (x_m: number, y_m: number) => {
      if (!state || !activeFighter) return;
      setPlacing(false);
      void run("Position update", () => api.moveResponder(activeFighter, { frame_id: state.scenario.frame_id, x_m, y_m })).then(
        (r) => r && toast("info", `${responderName(state, activeFighter)} marked at (${x_m}, ${y_m}) m`),
      );
    },
    [state, activeFighter, run, toast],
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

  const decide = (p: SimulationProposal, decision: "approve" | "reject") =>
    void run(decision === "approve" ? "Approve" : "Reject", () => api.decide(p.id, p.revision, decision, actor, state.epoch)).then((r) => {
      const a = r?.result as SimulationApproval | null | undefined;
      if (a && decision === "approve") toast("info", `Approval ${a.id} recorded for rev ${a.proposal_revision} — dispatch is a separate step`);
    });
  const dispatch = (p: SimulationProposal, approvalId: string) =>
    void run("Dispatch", () => api.dispatch(p.id, p.revision, approvalId, actor, state.epoch));
  const replan = () => void run("Replan", () => api.replan(actor));

  return (
    <main className="flex h-screen min-h-[760px] min-w-[1280px] flex-col gap-2 overflow-hidden p-2">
      <header className="flex items-center gap-4 rounded-md border border-line bg-panel px-3 py-1.5">
        <Logo />
        <div className="hidden min-w-0 flex-col xl:flex">
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

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
        {/* 1 — map of victims and firefighters */}
        <section className="panel flex min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
            <h2 className="label !text-text">Scene map</h2>
            <span className="mono text-[10.5px] text-muted">
              {state.scenario.casualties.length} victims · {fighters.length} firefighters
            </span>
            <div className="ml-auto flex items-center gap-2">
              {pending && <span className="mono blink text-[10.5px] text-accent">{pending}…</span>}
              <Button
                size="sm"
                variant={editMap ? "warn" : "ghost"}
                onClick={() => {
                  setEditMap((v) => !v);
                  setPlacing(false);
                }}
                disabled={!state.scenario.scene_map?.occupancy_grid}
                title="Click grid cells to block or clear them; the backend replans automatically"
              >
                {editMap ? "Done" : "Block route"}
              </Button>
            </div>
          </div>
          {(editMap || placing) && (
            <div className="sim-stripe border-b border-warn/30 px-3 py-1 text-[11px] text-warn">
              {placing
                ? `Click where ${responderName(state, activeFighter ?? "")} is (as seen on the bodycam). Esc to cancel.`
                : "Click cells to toggle blocked / free. Each change publishes a new map revision and triggers replanning."}
            </div>
          )}
          <div className="min-h-0 flex-1 p-2">
            <SceneMap
              state={state}
              proposal={proposal}
              selectedId={selected}
              onSelect={select}
              editMode={editMap}
              onToggleCell={toggleCell}
              now={now}
              onPlace={placing ? place : null}
              focusResponderId={activeFighter}
            />
          </div>
          <MapLegend />
        </section>

        {/* 2 — building surveillance + victim panel */}
        <SurveillancePane
          state={state}
          proposal={proposal}
          selectedId={selected}
          onSelect={select}
          busy={busy}
          now={now}
          note={note}
          onNote={setNote}
          onAssess={(cid, sev) => void run("Assessment", () => api.assess(cid, sev, actor))}
          onAssociate={(tid, cid, create) => void run(create ? "Confirm victim" : "Link track", () => api.associate(tid, cid, create, actor))}
        />

        {/* 3 — firefighter bodycam + voice guidance */}
        <FighterPane
          state={state}
          proposal={proposal}
          fighterId={activeFighter}
          onFighter={(id) => {
            setFighterId(id);
            setSelected(id);
          }}
          onStatus={(rid, status) => void run("Responder update", () => api.updateResponder(rid, status))}
          placing={placing}
          onPlace={(on) => {
            setPlacing(on);
            if (on) setEditMap(false);
          }}
          busy={busy}
        />
      </div>

      <CommandBar
        state={state}
        proposal={proposal}
        events={events}
        pending={pending}
        now={now}
        onDecide={decide}
        onDispatch={dispatch}
        onReplan={replan}
        onOpenDetails={() => setDrawer(true)}
      />

      {/* details drawer */}
      {drawer && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={() => setDrawer(false)}>
          <aside
            className="scrollbar-thin flex h-full w-[440px] flex-col gap-2 overflow-y-auto border-l border-line bg-bg p-2"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-1">
              <span className="label !text-text">Details</span>
              <button onClick={() => setDrawer(false)} className="text-muted hover:text-text" aria-label="Close">
                ✕
              </button>
            </div>
            <ProposalPanel
              state={state}
              proposal={proposal}
              pending={pending}
              now={now}
              onDecide={decide}
              onDispatch={dispatch}
              onReplan={replan}
            />
            <div className="h-[360px] shrink-0">
              <TasksPanel state={state} busy={busy} onSimulate={(a, kind) => void run(`Simulated ${kind}`, () => api.simulateDevice(a.id, kind))} />
            </div>
            <div className="h-[420px] shrink-0">
              <Timeline events={events} />
            </div>
          </aside>
        </div>
      )}

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

function MapLegend() {
  const item = (el: React.ReactNode, text: string) => (
    <span className="flex items-center gap-1">
      {el}
      {text}
    </span>
  );
  return (
    <div className="mono flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 text-[10px] text-muted">
      {item(<span className="h-2.5 w-2.5 rounded-full bg-[var(--sev-immediate)]" />, "victim (priority)")}
      {item(<span className="h-2.5 w-2.5 rounded-full border border-dashed border-[var(--sev-unassessed)]" />, "unassessed")}
      {item(<span className="h-2.5 w-2.5 rounded-sm border-2 border-accent" />, "firefighter")}
      {item(<span className="w-4 border-t-2 border-dashed border-accent" />, "proposed")}
      {item(<span className="w-4 border-t-2 border-warn" />, "dispatched")}
      {item(<span className="w-4 border-t-2 border-ok" />, "ack'd")}
      {item(<span className="h-2.5 w-2.5 border border-dashed border-warn" />, "CV detection")}
    </div>
  );
}
