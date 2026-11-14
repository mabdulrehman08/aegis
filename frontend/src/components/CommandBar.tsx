"use client";

import { describeEvent, humanize, meters, responderName, shortId } from "@/lib/format";
import type { EventEnvelope, SimulationProposal, SimulationState } from "@/lib/types";
import { Badge, Button } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  events: EventEnvelope[];
  pending: string | null;
  now: number;
  onDecide: (p: SimulationProposal, d: "approve" | "reject") => void;
  onDispatch: (p: SimulationProposal, approvalId: string) => void;
  onReplan: () => void;
  onOpenDetails: () => void;
}

const TASK_TONE = { dispatched: "warn", delivered: "info", acknowledged: "ok", completed: "neutral" } as const;

/** Always-visible strip for the human approval gate and task acknowledgements. */
export function CommandBar({ state, proposal, events, pending, now, onDecide, onDispatch, onReplan, onOpenDetails }: Props) {
  const busy = pending !== null;
  const approval = proposal?.approval_id ? state.approvals.find((a) => a.id === proposal.approval_id) : undefined;
  const isPending = proposal?.status === "pending_approval";
  const isApproved = proposal?.status === "approved" && approval?.status === "active";
  const deadline = isApproved ? approval!.expires_at : proposal?.expires_at;
  const secs = deadline ? Math.round((new Date(deadline).getTime() - now) / 1000) : 0;
  const tasks = state.assignments.filter((a) => a.status !== "completed");
  const last = [...events].reverse().find((e) => !e.type.endsWith("replan_requested") && !e.type.endsWith("recommendation_computed"));

  return (
    <footer className="grid grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)_auto] gap-2">
      {/* recommendation / approval gate */}
      <div className={`panel flex items-center gap-3 px-3 py-2 ${isPending ? "ring-1 ring-warn/50" : ""}`}>
        <div className="flex shrink-0 flex-col gap-0.5">
          <span className="label !text-text">Recommendation</span>
          {proposal ? (
            <Badge tone={isPending ? "warn" : isApproved ? "ok" : "neutral"}>
              {isPending ? `advisory · rev ${proposal.revision}` : humanize(proposal.status)}
            </Badge>
          ) : (
            <Badge>none pending</Badge>
          )}
        </div>
        <div className="min-w-0 flex-1">
          {proposal ? (
            <>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12px]">
                {proposal.assignments.map((a) => (
                  <span key={a.casualty_id} className="whitespace-nowrap">
                    <b>{responderName(state, a.responder_id)}</b> <span className="text-accent">→</span>{" "}
                    <span className="mono">{shortId(a.casualty_id)}</span>{" "}
                    <span className="mono text-[10.5px] text-muted">{a.route ? meters(a.route.distance_m) : ""}</span>
                  </span>
                ))}
              </div>
              <div className="mono mt-0.5 truncate text-[10px] text-faint">
                {proposal.engine} · {proposal.unassigned_casualty_ids.length} unassigned ·{" "}
                <span className={secs < 30 ? "text-warn" : ""}>
                  {secs > 0 ? `${isApproved ? "approval" : "expires"} ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}` : "expired"}
                </span>
              </div>
            </>
          ) : (
            <p className="truncate text-[11px] text-muted">
              {state.last_recommendation ? "No new assignable work." : "Waiting for optimizer."}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {isPending && (
            <>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => onDecide(proposal!, "reject")}>
                Reject
              </Button>
              <Button
                variant="warn"
                disabled={busy || secs <= 0 || proposal!.assignments.length === 0}
                onClick={() => onDecide(proposal!, "approve")}
              >
                {pending === "Approve" ? "Recording…" : `Approve rev ${proposal!.revision}`}
              </Button>
            </>
          )}
          {isApproved && (
            <Button variant="ok" disabled={busy} onClick={() => onDispatch(proposal!, approval!.id)}>
              {pending === "Dispatch" ? "Dispatching…" : "Dispatch"}
            </Button>
          )}
          {proposal?.status === "approved" && !isApproved && <span className="text-[11px] text-danger">approval {approval?.status ?? "missing"}</span>}
          <Button size="sm" disabled={busy} onClick={onReplan} title="Recompute recommendations">
            ↻
          </Button>
        </div>
      </div>

      {/* task acknowledgements */}
      <div className="panel flex min-w-0 items-center gap-2 px-3 py-2">
        <span className="label shrink-0 !text-text">Tasks</span>
        <div className="scrollbar-thin flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
          {tasks.length === 0 && <span className="text-[11px] text-muted">none active</span>}
          {tasks.map((a) => (
            <Badge key={a.id} tone={TASK_TONE[a.status]} className={a.status === "dispatched" || a.status === "delivered" ? "blink" : ""}>
              {responderName(state, a.responder_id)}→{shortId(a.casualty_id)} {a.status === "acknowledged" ? "ack'd" : a.status === "delivered" ? "awaiting ack" : a.status}
            </Badge>
          ))}
        </div>
      </div>

      <button onClick={onOpenDetails} className="panel flex w-56 flex-col justify-center px-3 py-2 text-left hover:border-line-strong">
        <span className="label !text-text">Timeline & details ›</span>
        <span className="truncate text-[10.5px] text-muted">{last ? describeEvent(last).title : "no events"}</span>
      </button>
    </footer>
  );
}
