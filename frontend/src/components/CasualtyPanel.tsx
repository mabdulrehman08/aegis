"use client";

import { useState } from "react";
import { SEVERITIES, SEVERITY_META, ageSeconds, humanize, responderName, shortId } from "@/lib/format";
import type { Severity, SimulationProposal, SimulationState, TrackRecord } from "@/lib/types";
import { Badge, Button, Empty, PanelHeader } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAssess: (casualtyId: string, severity: Severity) => void;
  onAssociate: (trackId: string, casualtyId: string, create: boolean) => void;
  busy: boolean;
  now: number;
}

export function CasualtyPanel({ state, proposal, selectedId, onSelect, onAssess, onAssociate, busy, now }: Props) {
  const casualties = state.scenario.casualties;
  const assessed = casualties.filter((c) => c.medical_assessment).length;
  const resolved = new Set(state.resolved_casualty_ids);
  const unlinked = state.tracks.filter((t) => !t.casualty_id);
  const trackFor = (cid: string) => state.tracks.find((t) => t.casualty_id === cid);
  const reasons = proposal?.reasons ?? state.last_recommendation?.reasons ?? {};

  return (
    <section className="panel flex min-h-0 flex-col">
      <PanelHeader
        title="Casualties"
        sub={`${assessed}/${casualties.length} assessed`}
      />
      <div className="scrollbar-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {casualties.length === 0 && <Empty>No confirmed casualties.</Empty>}
        {casualties.map((c) => {
          const sev = c.medical_assessment?.severity ?? "unassessed";
          const meta = SEVERITY_META[sev];
          const task = state.assignments.find((a) => a.casualty_id === c.id && a.status !== "completed");
          const proposed = proposal?.status === "pending_approval" || proposal?.status === "approved"
            ? proposal.assignments.find((a) => a.casualty_id === c.id)
            : undefined;
          const track = trackFor(c.id);
          const sel = selectedId === c.id;
          return (
            <article
              key={c.id}
              onClick={() => onSelect(c.id)}
              className={`cursor-pointer rounded border bg-panel-2 p-2 transition ${sel ? "border-text/60" : "border-line hover:border-line-strong"}`}
              style={{ boxShadow: `inset 3px 0 0 ${meta.color}` }}
            >
              <div className="flex items-center justify-between gap-2 pl-1">
                <div className="flex items-baseline gap-2">
                  <span className="mono text-sm font-semibold">{shortId(c.id)}</span>
                  <span className="mono text-[10.5px] text-muted">
                    ({c.position.x_m.toFixed(1)}, {c.position.y_m.toFixed(1)}) m
                  </span>
                </div>
                {sev === "unassessed" ? (
                  <Badge tone="neutral" className="border-dashed !text-text">unassessed</Badge>
                ) : (
                  <span
                    className="mono rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wider"
                    style={{ background: meta.color, color: sev === "deceased" ? "#cbd5e1" : "#05080b" }}
                  >
                    {meta.label}
                  </span>
                )}
              </div>

              <div className="mt-1 pl-1 text-[11px] text-muted">
                {resolved.has(c.id) ? (
                  <span className="text-ok">✓ task reported complete</span>
                ) : task ? (
                  <span>
                    <span className="text-text">{responderName(state, task.responder_id)}</span> · {task.status}
                  </span>
                ) : proposed ? (
                  <span className="text-accent">proposed → {responderName(state, proposed.responder_id)}</span>
                ) : reasons[c.id]?.length ? (
                  <span>unassigned · {humanize(reasons[c.id][0])}</span>
                ) : (
                  <span>no task</span>
                )}
                {c.medical_assessment && (
                  <span className="text-faint"> · by {c.medical_assessment.assessed_by}</span>
                )}
              </div>

              {track && <TrackLine track={track} now={now} />}

              {sel && (
                <div className="mt-2 border-t border-line pt-2" onClick={(e) => e.stopPropagation()}>
                  <p className="label mb-1.5 !text-[9.5px]">Command assessment</p>
                  <div className="grid grid-cols-5 gap-1">
                    {SEVERITIES.map((s) => {
                      const m = SEVERITY_META[s];
                      const active = c.medical_assessment?.severity === s;
                      return (
                        <button
                          key={s}
                          disabled={busy || active}
                          onClick={() => onAssess(c.id, s)}
                          title={m.label}
                          className="mono rounded border py-1 text-[10px] font-bold uppercase transition disabled:cursor-default"
                          style={{
                            borderColor: m.color,
                            background: active ? m.color : "transparent",
                            color: active ? (s === "deceased" ? "#cbd5e1" : "#05080b") : m.color,
                            opacity: busy && !active ? 0.5 : 1,
                          }}
                        >
                          {m.short}
                        </button>
                      );
                    })}
                  </div>
                  <p className="mt-1.5 text-[10px] leading-snug text-faint">
                    Recorded as <span className="mono">source: human</span>. Detections never set severity.
                  </p>
                </div>
              )}
            </article>
          );
        })}

        {unlinked.length > 0 && (
          <div className="pt-1">
            <p className="label mb-1 px-1 !text-warn">Unconfirmed detections · {unlinked.length}</p>
            {unlinked.map((t) => (
              <DetectionCard
                key={t.track_id}
                track={t}
                state={state}
                busy={busy}
                now={now}
                selected={selectedId === t.track_id}
                onSelect={() => onSelect(t.track_id)}
                onAssociate={onAssociate}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function TrackLine({ track, now }: { track: TrackRecord; now: number }) {
  const o = track.latest_observation;
  const age = ageSeconds(o.observed_at, now);
  const state = o.track?.state ?? "visible";
  const stale = age > 30 || state !== "visible";
  return (
    <div className="mono mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-1 text-[10px] text-muted">
      <span className={stale ? "text-warn" : "text-accent"}>◉ CV {state}</span>
      <span>conf {o.confidence.toFixed(2)}</span>
      <span className={stale ? "text-warn" : ""}>{age}s ago{stale ? " · stale" : ""}</span>
      {track.camera && (
        <span title={track.camera.calibration_ref ?? undefined}>
          {track.camera.camera_id}/{track.camera.local_track_id}
          {track.camera.location_basis === "zone_estimate" ? ` · zone ${track.camera.zone_id} (estimate)` : " · calibrated"}
        </span>
      )}
    </div>
  );
}

function DetectionCard({
  track,
  state,
  busy,
  now,
  selected,
  onSelect,
  onAssociate,
}: {
  track: TrackRecord;
  state: SimulationState;
  busy: boolean;
  now: number;
  selected: boolean;
  onSelect: () => void;
  onAssociate: Props["onAssociate"];
}) {
  const [target, setTarget] = useState("");
  const nextId = () => {
    const nums = state.scenario.casualties.map((c) => Number(c.id.match(/(\d+)$/)?.[1] ?? 0));
    return `casualty-${String(Math.max(0, ...nums) + 1).padStart(3, "0")}`;
  };
  return (
    <article
      onClick={onSelect}
      className={`mb-2 cursor-pointer rounded border border-dashed bg-panel-2 p-2 ${selected ? "border-warn" : "border-warn/40"}`}
    >
      <div className="flex items-center justify-between">
        <span className="mono text-xs font-semibold text-warn">{track.camera?.local_track_id ?? shortId(track.track_id)}</span>
        <Badge tone="warn">person detected</Badge>
      </div>
      <TrackLine track={track} now={now} />
      <div className="mt-2 flex gap-1" onClick={(e) => e.stopPropagation()}>
        <Button size="sm" variant="warn" disabled={busy} onClick={() => onAssociate(track.track_id, nextId(), true)}>
          Confirm new casualty
        </Button>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-1 text-[11px]"
        >
          <option value="">link to…</option>
          {state.scenario.casualties.map((c) => (
            <option key={c.id} value={c.id}>
              {shortId(c.id)}
            </option>
          ))}
        </select>
        <Button size="sm" disabled={busy || !target} onClick={() => onAssociate(track.track_id, target, false)}>
          Link
        </Button>
      </div>
    </article>
  );
}
