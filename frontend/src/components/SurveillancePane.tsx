"use client";

import { useEffect, useRef, useState } from "react";
import type { FieldNote, Tri } from "@/lib/fieldNotes";
import { SEVERITIES, SEVERITY_META, ageSeconds, humanize, responderName, shortId } from "@/lib/format";
import type { Casualty, Severity, SimulationProposal, SimulationState, TrackRecord } from "@/lib/types";
import { Badge, Button } from "./ui";
import { VideoFeed } from "./VideoFeed";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAssess: (casualtyId: string, severity: Severity) => void;
  onAssociate: (trackId: string, casualtyId: string, create: boolean) => void;
  note: (id: string) => FieldNote;
  onNote: (id: string, patch: Partial<FieldNote>) => void;
  busy: boolean;
  now: number;
}

const STALE_S = 30;

export function SurveillancePane(props: Props) {
  const { state, note, now } = props;
  const cas = state.scenario.casualties;
  const count = (s: Severity) => cas.filter((c) => c.medical_assessment?.severity === s).length;
  const unassessed = cas.filter((c) => !c.medical_assessment).length;
  const bleeding = cas.filter((c) => note(c.id).bleeding === "yes").length;
  const pulsed = cas.filter((c) => note(c.id).pulse).length;
  const unlinked = state.tracks.filter((t) => !t.casualty_id);
  const visibleTracks = state.tracks.filter(
    (t) => (t.latest_observation.track?.state ?? "visible") === "visible" && ageSeconds(t.latest_observation.observed_at, now) <= STALE_S,
  ).length;

  return (
    <section className="panel flex min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="label !text-text">Building surveillance</h2>
        <span className="mono text-[10.5px] text-muted">
          {state.cameras.length ? state.cameras.map((c) => c.camera_id).join(" · ") : "no CV camera registered"}
        </span>
      </header>

      <div className="p-2 pb-0">
        <VideoFeed
          feedId="surveillance"
          title="CAM · BUILDING"
          className="aspect-video w-full"
          overlay={
            <div className="mono flex gap-3 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-4 text-[10px] text-white/80">
              <span>CV: {visibleTracks} person(s) tracked</span>
              <span className="text-white/50">detection only · no injury inference</span>
            </div>
          }
        />
      </div>

      {/* victim summary */}
      <div className="grid grid-cols-4 gap-px border-y border-line bg-line mt-2">
        <Summary label="Victims" value={cas.length} sub={unlinked.length ? `+${unlinked.length} unconfirmed` : "confirmed"} />
        <Summary
          label="Priority"
          value={
            <span className="flex items-center gap-1.5">
              {(["immediate", "delayed", "minor"] as Severity[]).map((s) => (
                <span key={s} style={{ color: SEVERITY_META[s].color }}>
                  {count(s)}
                </span>
              ))}
            </span>
          }
          sub={`${unassessed} unassessed`}
          warn={unassessed > 0}
        />
        <Summary label="Bleeding" value={bleeding} sub="reported" danger={bleeding > 0} />
        <Summary label="Pulse" value={`${pulsed}/${cas.length}`} sub="logged" />
      </div>

      <div className="scrollbar-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {cas.map((c) => (
          <VictimCard key={c.id} c={c} {...props} />
        ))}
        {unlinked.length > 0 && (
          <div>
            <p className="label mb-1 px-1 !text-warn">Unconfirmed detections · {unlinked.length}</p>
            {unlinked.map((t) => (
              <DetectionCard key={t.track_id} track={t} {...props} />
            ))}
          </div>
        )}
        <p className="px-1 pb-1 text-[10px] leading-snug text-faint">
          Pulse, bleeding and wound entries are operator observations (from the feed or the responder&apos;s report), not
          automated findings or a diagnosis. Stored in this browser only until the backend adds a vitals schema.
        </p>
      </div>
    </section>
  );
}

function Summary({
  label,
  value,
  sub,
  warn,
  danger,
}: {
  label: string;
  value: React.ReactNode;
  sub: string;
  warn?: boolean;
  danger?: boolean;
}) {
  return (
    <div className="bg-panel px-2.5 py-1.5">
      <div className="label !text-[9px]">{label}</div>
      <div className={`mono text-lg font-semibold leading-tight ${danger ? "text-danger" : ""}`}>{value}</div>
      <div className={`text-[10px] ${warn ? "text-warn" : "text-faint"}`}>{sub}</div>
    </div>
  );
}

const TRI_STYLE: Record<Tri, string> = {
  yes: "border-danger/60 text-danger bg-danger/10",
  no: "border-ok/50 text-ok",
  unknown: "border-line-strong text-faint",
};

function Chip({ label, v, yesText = "yes" }: { label: string; v: Tri; yesText?: string }) {
  return (
    <span className={`mono rounded border px-1.5 py-px text-[10px] uppercase ${TRI_STYLE[v]}`}>
      {label} {v === "yes" ? yesText : v === "no" ? "no" : "?"}
    </span>
  );
}

function VictimCard({ c, state, proposal, selectedId, onSelect, onAssess, note, onNote, busy, now }: Props & { c: Casualty }) {
  const sev = c.medical_assessment?.severity ?? "unassessed";
  const meta = SEVERITY_META[sev];
  const n = note(c.id);
  const sel = selectedId === c.id;
  const ref = useRef<HTMLElement>(null);
  const [bpm, setBpm] = useState("");

  useEffect(() => {
    if (sel) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [sel]);

  const task = state.assignments.find((a) => a.casualty_id === c.id && a.status !== "completed");
  const proposed =
    proposal && (proposal.status === "pending_approval" || proposal.status === "approved")
      ? proposal.assignments.find((a) => a.casualty_id === c.id)
      : undefined;
  const track = state.tracks.find((t) => t.casualty_id === c.id);
  const reasons = proposal?.reasons ?? state.last_recommendation?.reasons ?? {};
  const done = state.resolved_casualty_ids.includes(c.id);

  return (
    <article
      ref={ref}
      onClick={() => onSelect(c.id)}
      className={`cursor-pointer rounded border bg-panel-2 p-2 transition ${sel ? "border-text/60" : "border-line hover:border-line-strong"}`}
      style={{ boxShadow: `inset 3px 0 0 ${meta.color}` }}
    >
      <div className="flex items-center gap-2 pl-1">
        <span className="mono text-sm font-semibold">{shortId(c.id)}</span>
        {track && <TrackBadge track={track} now={now} />}
        <span className="ml-auto">
          {sev === "unassessed" ? (
            <Badge className="border-dashed !text-text">unassessed</Badge>
          ) : (
            <span
              className="mono rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wider"
              style={{ background: meta.color, color: sev === "deceased" ? "#cbd5e1" : "#05080b" }}
            >
              {meta.label}
            </span>
          )}
        </span>
      </div>

      {/* vitals / visible signs */}
      <div className="mt-1.5 flex flex-wrap items-center gap-1 pl-1">
        <span
          className={`mono rounded border px-1.5 py-px text-[10px] ${n.pulse ? "border-accent/50 text-accent" : "border-line-strong text-faint"}`}
          title={n.pulse ? `${humanize(n.pulse.source)} at ${new Date(n.pulse.at).toLocaleTimeString()}` : "No pulse logged"}
        >
          ♥ {n.pulse ? `${n.pulse.bpm} bpm · ${ageSeconds(n.pulse.at, now)}s` : "— bpm"}
        </span>
        <Chip label="bleed" v={n.bleeding} />
        <Chip label="wound" v={n.wound} />
        <Chip label="breath" v={n.breathing} yesText="✓" />
      </div>
      {n.woundNote && <p className="mt-1 pl-1 text-[10.5px] text-muted">“{n.woundNote}”</p>}

      <div className="mt-1 pl-1 text-[10.5px] text-muted">
        {done ? (
          <span className="text-ok">✓ reached · task complete</span>
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
      </div>

      {sel && (
        <div className="mt-2 space-y-2 border-t border-line pt-2" onClick={(e) => e.stopPropagation()}>
          <div>
            <p className="label mb-1 !text-[9.5px]">Command priority (human)</p>
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
          </div>

          <div className="grid grid-cols-3 gap-2">
            <TriPicker label="Bleeding" v={n.bleeding} onChange={(v) => onNote(c.id, { bleeding: v })} />
            <TriPicker label="Wound" v={n.wound} onChange={(v) => onNote(c.id, { wound: v })} />
            <TriPicker label="Breathing" v={n.breathing} onChange={(v) => onNote(c.id, { breathing: v })} />
          </div>

          <input
            defaultValue={n.woundNote}
            onBlur={(e) => e.target.value !== n.woundNote && onNote(c.id, { woundNote: e.target.value.slice(0, 120) })}
            placeholder="What's visible? e.g. bleeding left leg, trapped under debris"
            className="w-full rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
          />

          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const v = Number(bpm);
              if (!Number.isFinite(v) || v < 0 || v > 300) return;
              onNote(c.id, { pulse: { bpm: Math.round(v), at: new Date().toISOString(), source: "responder_report" } });
              setBpm("");
            }}
          >
            <span className="label !text-[9.5px]">Pulse reported</span>
            <input
              value={bpm}
              onChange={(e) => setBpm(e.target.value.replace(/[^0-9]/g, ""))}
              inputMode="numeric"
              placeholder="bpm"
              className="mono w-16 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
            <Button size="sm" disabled={!bpm}>
              Log
            </Button>
            {n.pulse && (
              <button type="button" onClick={() => onNote(c.id, { pulse: null })} className="ml-auto text-[10px] text-faint hover:text-text">
                clear
              </button>
            )}
          </form>
        </div>
      )}
    </article>
  );
}

function TriPicker({ label, v, onChange }: { label: string; v: Tri; onChange: (v: Tri) => void }) {
  const opts: Tri[] = ["yes", "no", "unknown"];
  return (
    <div>
      <p className="label mb-1 !text-[9px]">{label}</p>
      <div className="grid grid-cols-3 overflow-hidden rounded border border-line-strong">
        {opts.map((o) => (
          <button
            key={o}
            onClick={() => onChange(o)}
            className={`mono py-0.5 text-[9.5px] uppercase ${v === o ? TRI_STYLE[o] + " !border-0" : "text-faint hover:text-text"}`}
          >
            {o === "unknown" ? "?" : o}
          </button>
        ))}
      </div>
    </div>
  );
}

function TrackBadge({ track, now }: { track: TrackRecord; now: number }) {
  const o = track.latest_observation;
  const age = ageSeconds(o.observed_at, now);
  const st = o.track?.state ?? "visible";
  const stale = age > STALE_S || st !== "visible";
  return (
    <span
      className={`mono text-[10px] ${stale ? "text-warn" : "text-accent"}`}
      title={track.camera ? `${track.camera.camera_id}/${track.camera.local_track_id} · ${track.camera.location_basis}` : "normalized input"}
    >
      ◉ {stale ? `${st === "visible" ? "stale" : st} ${age}s` : `CV ${Math.round(o.confidence * 100)}%`}
      {track.camera?.location_basis === "zone_estimate" ? " · zone est." : ""}
    </span>
  );
}

function DetectionCard({ track, state, busy, now, selectedId, onSelect, onAssociate }: Props & { track: TrackRecord }) {
  const [target, setTarget] = useState("");
  const nextId = () => {
    const nums = state.scenario.casualties.map((c) => Number(c.id.match(/(\d+)$/)?.[1] ?? 0));
    return `casualty-${String(Math.max(0, ...nums) + 1).padStart(3, "0")}`;
  };
  return (
    <article
      onClick={() => onSelect(track.track_id)}
      className={`mb-2 cursor-pointer rounded border border-dashed bg-panel-2 p-2 ${selectedId === track.track_id ? "border-warn" : "border-warn/40"}`}
    >
      <div className="flex items-center gap-2">
        <span className="mono text-xs font-semibold text-warn">{track.camera?.local_track_id ?? shortId(track.track_id)}</span>
        <TrackBadge track={track} now={now} />
        <Badge tone="warn" className="ml-auto">
          person detected
        </Badge>
      </div>
      <div className="mt-2 flex gap-1" onClick={(e) => e.stopPropagation()}>
        <Button size="sm" variant="warn" disabled={busy} onClick={() => onAssociate(track.track_id, nextId(), true)}>
          Confirm victim
        </Button>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-1 text-[11px]"
        >
          <option value="">same as…</option>
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
