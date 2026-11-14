"use client";

import { SEVERITY_META, ageSeconds, responderName, shortId } from "@/lib/format";
import type { SimulationProposal, SimulationState } from "@/lib/types";
import { VideoFeed } from "./VideoFeed";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  activeId: string | null;
  onActive: (id: string) => void;
  flashing: Set<string>;
  now: number;
}

const STATUS_COLOR = { available: "var(--accent)", busy: "var(--warn)", offline: "var(--faint)" } as const;

/** Bodycam tiles (one per firefighter) + the building surveillance camera. */
export function CamGrid({ state, proposal, activeId, onActive, flashing, now }: Props) {
  const fighters = state.scenario.responders;
  const tracked = state.tracks.filter(
    (t) => (t.latest_observation.track?.state ?? "visible") === "visible" && ageSeconds(t.latest_observation.observed_at, now) <= 30,
  ).length;
  const spare = Math.max(0, 4 - fighters.length - 1);

  return (
    <div className="grid grid-cols-2 gap-1.5">
      {fighters.map((r) => {
        const task = state.assignments.find((a) => a.responder_id === r.id && a.status !== "completed");
        const prop =
          proposal && (proposal.status === "pending_approval" || proposal.status === "approved")
            ? proposal.assignments.find((a) => a.responder_id === r.id)
            : undefined;
        const targetId = task?.casualty_id ?? prop?.casualty_id;
        const target = state.scenario.casualties.find((c) => c.id === targetId);
        const sev = target?.medical_assessment?.severity ?? "unassessed";
        const active = r.id === activeId;
        return (
          <div
            key={r.id}
            role="button"
            tabIndex={0}
            onClick={() => onActive(r.id)}
            onKeyDown={(e) => e.key === "Enter" && onActive(r.id)}
            className={`relative cursor-pointer rounded ${active ? "ring-2 ring-accent" : "ring-1 ring-transparent hover:ring-line-strong"} ${
              flashing.has(r.id) ? "flash" : ""
            }`}
          >
            <VideoFeed compact audio feedId={`bodycam:${r.id}`} title={`${responderName(state, r.id).toUpperCase()} · BODYCAM`} className="aspect-video w-full" />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-black/90 to-transparent px-1.5 pb-1 pt-3">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: STATUS_COLOR[r.status] }} />
              <span className="mono text-[9.5px] uppercase text-white/80">{r.status}</span>
              {target && (
                <span className="mono ml-auto text-[9.5px] text-white/90">
                  → {shortId(target.id)} <span style={{ color: SEVERITY_META[sev].color }}>{SEVERITY_META[sev].short}</span>
                  <span className="text-white/50"> {task ? task.status : "proposed"}</span>
                </span>
              )}
            </div>
          </div>
        );
      })}
      <VideoFeed
        compact
        feedId="surveillance"
        title="BUILDING CAM"
        className="aspect-video w-full"
        overlay={
          <div className="mono bg-gradient-to-t from-black/90 to-transparent px-1.5 pb-1 pt-3 text-[9.5px] text-white/80">
            CV {tracked} tracked · detection only
          </div>
        }
      />
      {Array.from({ length: spare }, (_, i) => (
        <VideoFeed key={i} compact audio feedId={`extra-${i}`} title={`EXTRA FEED ${i + 1}`} className="aspect-video w-full" />
      ))}
    </div>
  );
}
