"use client";

import { shortId } from "@/lib/format";
import type { ResponderStatus, SimulationState } from "@/lib/types";
import { Badge, PanelHeader } from "./ui";

const STATUSES: ResponderStatus[] = ["available", "busy", "offline"];
const COLOR: Record<ResponderStatus, string> = {
  available: "var(--accent)",
  busy: "var(--warn)",
  offline: "var(--faint)",
};

interface Props {
  state: SimulationState;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onStatus: (responderId: string, status: ResponderStatus) => void;
  busy: boolean;
}

export function ResponderPanel({ state, selectedId, onSelect, onStatus, busy }: Props) {
  const responders = state.scenario.responders;
  const available = responders.filter((r) => r.status === "available").length;
  return (
    <section className="panel flex flex-col">
      <PanelHeader title="Responders" sub={`${available}/${responders.length} available`} />
      <div className="space-y-2 p-2">
        {responders.map((r) => {
          const task = state.assignments.find((a) => a.responder_id === r.id && a.status !== "completed");
          return (
            <article
              key={r.id}
              onClick={() => onSelect(r.id)}
              className={`cursor-pointer rounded border bg-panel-2 p-2 ${selectedId === r.id ? "border-text/60" : "border-line hover:border-line-strong"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-semibold">{r.display_name.replace(/^Mock Responder /, "")}</div>
                  <div className="mono text-[10px] text-muted">
                    {shortId(r.id)} · ({r.position.x_m.toFixed(1)}, {r.position.y_m.toFixed(1)}) m
                  </div>
                </div>
                {task && <Badge tone={task.status === "acknowledged" ? "ok" : "warn"}>{task.status}</Badge>}
              </div>
              {(r.capabilities?.length ?? 0) > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {r.capabilities!.map((c) => (
                    <Badge key={c}>{c.replace(/_/g, " ")}</Badge>
                  ))}
                </div>
              )}
              <div className="mt-2 grid grid-cols-3 gap-1" onClick={(e) => e.stopPropagation()} role="radiogroup" aria-label={`${r.display_name} availability`}>
                {STATUSES.map((s) => {
                  const active = r.status === s;
                  return (
                    <button
                      key={s}
                      role="radio"
                      aria-checked={active}
                      disabled={busy || active}
                      onClick={() => onStatus(r.id, s)}
                      className="mono rounded border py-0.5 text-[10px] uppercase tracking-wider transition disabled:cursor-default"
                      style={{
                        borderColor: active ? COLOR[s] : "var(--line-strong)",
                        color: active ? COLOR[s] : "var(--muted)",
                        background: active ? "color-mix(in srgb, currentColor 12%, transparent)" : "transparent",
                      }}
                    >
                      {s}
                    </button>
                  );
                })}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
