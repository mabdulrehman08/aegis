"use client";

import { useState } from "react";
import { Check, X, Send, RotateCcw, Cpu, UserCheck, Lock } from "lucide-react";
import { ExampleTag, Section, SectionHeading } from "./Section";

type Phase = "review" | "approved" | "rejected" | "dispatched";

const REC = {
  revision: 3,
  casualty: "C-001",
  priority: "IMMEDIATE",
  responder: "R-01",
  availability: "Available",
  rationale: [
    "Highest human-confirmed priority among unassigned casualties.",
    "R-01 is available and has a feasible obstacle-aware route.",
    "R-02 remains free for the next confirmed casualty.",
  ],
};

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] gap-3 border-b border-line/70 py-2.5 last:border-0">
      <dt className="label self-center">{k}</dt>
      <dd className="mono text-[13px] text-text">{children}</dd>
    </div>
  );
}

export function HumanLoop() {
  const [phase, setPhase] = useState<Phase>("review");

  const status: Record<Phase, { text: string; cls: string }> = {
    review: { text: "Awaiting command decision — nothing has been dispatched.", cls: "border-warn/40 bg-warn/5 text-warn" },
    approved: { text: `Rev ${REC.revision} approved. Not yet dispatched — dispatch is a separate step.`, cls: "border-accent/40 bg-accent/5 text-accent" },
    rejected: { text: "Recommendation rejected. No task sent. The optimizer can propose a new revision.", cls: "border-line-strong bg-panel-2 text-muted" },
    dispatched: { text: `Task sent to ${REC.responder}'s device — awaiting physical acknowledgment.`, cls: "border-accent/40 bg-accent/10 text-accent" },
  };

  const step = (n: number, label: string, done: boolean, active: boolean) => (
    <li className="flex items-center gap-3">
      <span
        className={`mono flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] ${
          done ? "border-accent bg-accent text-bg" : active ? "border-warn text-warn" : "border-line-strong text-faint"
        }`}
      >
        {done ? <Check size={12} aria-hidden="true" /> : n}
      </span>
      <span className={`text-[14px] ${done ? "text-text" : active ? "text-text" : "text-faint"}`}>{label}</span>
    </li>
  );

  return (
    <Section id="human-in-the-loop">
      <SectionHeading
        index="04"
        eyebrow="Human in the loop"
        title={
          <>
            AI recommends. <span className="text-accent">Humans decide.</span> Responders act.
          </>
        }
        lede="Try it: the optimizer's recommendation on the left cannot move until a commander acts on the right."
      />

      <div className="mt-12 grid overflow-hidden rounded-[4px] border border-line lg:grid-cols-2">
        {/* AI recommendation */}
        <div className="bg-panel p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="mono flex items-center gap-2 text-[12px] uppercase tracking-[0.16em] text-muted">
              <Cpu size={14} className="text-accent" aria-hidden="true" /> Optimizer recommendation
            </p>
            <ExampleTag />
          </div>
          <dl className="mt-6">
            <Row k="Revision">rev {REC.revision}</Row>
            <Row k="Casualty">{REC.casualty}</Row>
            <Row k="Priority">
              <span className="text-danger">{REC.priority}</span> <span className="text-muted">· entered by command</span>
            </Row>
            <Row k="Responder">
              {REC.responder} <span className="text-ok">· {REC.availability}</span>
            </Row>
            <Row k="Recommended">
              <span className="text-accent">
                {REC.responder} → {REC.casualty}
              </span>
            </Row>
            <Row k="Route cost">
              <span className="text-muted">Shown only when supplied by the optimizer</span>
            </Row>
          </dl>
          <div className="mt-5">
            <p className="label">Rationale</p>
            <ul className="mt-2 space-y-1.5">
              {REC.rationale.map((r) => (
                <li key={r} className="flex gap-2 text-[13.5px] leading-relaxed text-muted">
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-accent/70" aria-hidden="true" />
                  {r}
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Human authorization */}
        <div className="relative border-t border-line bg-bg p-6 sm:p-8 lg:border-l lg:border-t-0">
          <p className="mono flex items-center gap-2 text-[12px] uppercase tracking-[0.16em] text-muted">
            <UserCheck size={14} className="text-warn" aria-hidden="true" /> Command authorization
          </p>

          <ol className="mt-6 space-y-3.5">
            {step(1, "Review recommendation", true, false)}
            {step(2, phase === "rejected" ? "Rejected" : `Approve rev ${REC.revision}`, phase === "approved" || phase === "dispatched", phase === "review")}
            {step(3, "Dispatch to responder device", phase === "dispatched", phase === "approved")}
          </ol>

          <div className="mt-7 flex flex-wrap gap-2.5">
            <button
              type="button"
              disabled={phase !== "review"}
              onClick={() => setPhase("rejected")}
              className="inline-flex items-center gap-2 rounded-[3px] border border-line-strong px-4 py-2 text-[13.5px] text-text transition enabled:hover:border-danger/60 enabled:hover:text-danger disabled:opacity-35"
            >
              <X size={14} aria-hidden="true" /> Reject
            </button>
            <button
              type="button"
              disabled={phase !== "review"}
              onClick={() => setPhase("approved")}
              className="inline-flex items-center gap-2 rounded-[3px] border border-accent/60 bg-accent/10 px-4 py-2 text-[13.5px] text-accent transition enabled:hover:bg-accent/20 disabled:opacity-35"
            >
              <Check size={14} aria-hidden="true" /> Approve rev {REC.revision}
            </button>
            <button
              type="button"
              disabled={phase !== "approved"}
              onClick={() => setPhase("dispatched")}
              aria-describedby="dispatch-hint"
              className="inline-flex items-center gap-2 rounded-[3px] bg-accent px-4 py-2 text-[13.5px] font-medium text-bg transition enabled:hover:bg-[#5fdde6] disabled:cursor-not-allowed disabled:bg-panel-2 disabled:text-faint"
            >
              {phase === "approved" ? <Send size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />} Dispatch
            </button>
          </div>
          <p id="dispatch-hint" className="mt-2 text-[12px] text-faint">
            Dispatch unlocks only after an explicit approval of this exact revision.
          </p>

          <div className={`mt-6 rounded-[3px] border px-4 py-3 text-[13.5px] ${status[phase].cls}`} role="status" aria-live="polite">
            {status[phase].text}
          </div>

          {phase !== "review" && (
            <button
              type="button"
              onClick={() => setPhase("review")}
              className="mono mt-4 inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-muted hover:text-text"
            >
              <RotateCcw size={12} aria-hidden="true" /> Reset example
            </button>
          )}
          <p className="mono absolute bottom-3 right-4 text-[10px] uppercase tracking-[0.12em] text-faint">Example only · nothing is sent</p>
        </div>
      </div>

      <blockquote className="mx-auto mt-12 max-w-3xl border-l-2 border-accent pl-5 text-[18px] leading-relaxed text-text sm:text-[20px]">
        AEGIS keeps critical decisions accountable by placing human authorization at the center of every dispatch.
      </blockquote>
    </Section>
  );
}
