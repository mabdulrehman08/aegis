"use client";

import { useEffect, useRef, useState } from "react";
import { Eye, Map, Network, ShieldCheck, Send, RefreshCw, Pause, Play } from "lucide-react";
import { Section, SectionHeading } from "./Section";

const STAGES = [
  {
    key: "observe",
    title: "Observe",
    icon: Eye,
    short: "Computer vision detects and tracks people through a webcam using YOLO and ByteTrack.",
    detail:
      "Each frame from the webcam passes through YOLO person detection. ByteTrack keeps a persistent track ID for each person across frames, so the system follows people rather than re-detecting strangers.",
    input: "Webcam frames",
    output: "Person tracks with pixel boxes",
    note: "Camera output is evidence for the operator, never a medical assessment.",
    tech: ["Logitech webcam", "YOLO", "ByteTrack"],
  },
  {
    key: "map",
    title: "Map",
    icon: Map,
    short: "OpenCV homography maps detected positions into a shared 2.5D scene representation.",
    detail:
      "Calibration points define a homography from the camera image to the floor plane. Each track's foot point is projected into meters on a shared scene map with an occupancy grid for walls and debris.",
    input: "Pixel tracks + calibration",
    output: "Positions in scene meters",
    note: "The operator links tracks to casualties — association is a human-confirmed step.",
    tech: ["OpenCV", "Homography", "Occupancy grid"],
  },
  {
    key: "optimize",
    title: "Optimize",
    icon: Network,
    short: "Google OR-Tools recommends feasible responder assignments. A* calculates obstacle-aware routes.",
    detail:
      "OR-Tools matches available responders to casualties using human-confirmed priorities. A* searches the occupancy grid for routes around obstacles. The result is a revisioned recommendation that expires if not acted on.",
    input: "Confirmed priorities, availability, map",
    output: "Recommendation rev N with routes",
    note: "Only human-entered priorities feed the optimizer.",
    tech: ["Google OR-Tools", "A* pathfinding", "FastAPI"],
  },
  {
    key: "approve",
    title: "Approve",
    icon: ShieldCheck,
    short: "Incident commanders review recommendations and explicitly approve assignments.",
    detail:
      "The commander approves one exact revision of the recommendation. If anything changes — a new casualty, a blocked cell, a responder going offline — that approval is invalidated and a new revision must be reviewed.",
    input: "Recommendation rev N",
    output: "Approval bound to rev N",
    note: "Nothing is dispatched automatically.",
    tech: ["Command dashboard", "Idempotent decisions"],
  },
  {
    key: "dispatch",
    title: "Dispatch",
    icon: Send,
    short: "Approved tasks are delivered to physical responder hardware through an Arduino-based interface.",
    detail:
      "Dispatch is a separate, explicit action after approval. Each assignment becomes a task command addressed to the responder's device, which lights an LED to signal an incoming task.",
    input: "Approved revision",
    output: "Task commands to responder devices",
    note: "Approval and dispatch are deliberately two steps.",
    tech: ["Arduino UNO R4 WiFi", "Serial", "Grove LED"],
  },
  {
    key: "adapt",
    title: "Adapt",
    icon: RefreshCw,
    short: "Physical acknowledgments update system state. Changes in the scene trigger dynamic replanning.",
    detail:
      "A button press on the responder device records delivery and acknowledgment. New casualties, unavailable responders, and blocked routes produce a fresh recommendation revision — back to command for review.",
    input: "Acknowledgments + scene changes",
    output: "Updated state, new revision",
    note: "The loop closes only when a responder physically confirms.",
    tech: ["Grove button", "WebSockets", "Replanning"],
  },
];

const DWELL_MS = 5000;

export function Loop() {
  const [active, setActive] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [hovered, setHovered] = useState(false);
  const [inView, setInView] = useState(false);
  const [reduced, setReduced] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    if (mq.matches) setPlaying(false);
    const el = rootRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const running = playing && !hovered && inView && !reduced;

  useEffect(() => {
    if (!running) return;
    const t = setTimeout(() => setActive((a) => (a + 1) % STAGES.length), DWELL_MS);
    return () => clearTimeout(t);
  }, [running, active]);

  const select = (i: number) => {
    setActive(i);
    setPlaying(false);
  };

  const onKey = (e: React.KeyboardEvent, i: number) => {
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % STAGES.length;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + STAGES.length) % STAGES.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = STAGES.length - 1;
    if (next >= 0) {
      e.preventDefault();
      select(next);
      tabRefs.current[next]?.focus();
    }
  };

  const s = STAGES[active];

  return (
    <Section id="how-it-works">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <SectionHeading
          index="02"
          eyebrow="The AEGIS solution"
          title="One system. A complete response loop."
          lede="Six stages connect what the camera sees to a responder physically confirming a task — with a human decision in the middle."
        />
        {!reduced && (
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="mono inline-flex shrink-0 items-center gap-2 self-start rounded-[3px] border border-line px-3 py-1.5 text-[11px] uppercase tracking-[0.12em] text-muted hover:text-text sm:self-auto"
            aria-label={playing ? "Pause stage walkthrough" : "Play stage walkthrough"}
          >
            {playing ? <Pause size={12} /> : <Play size={12} />} {playing ? "Auto-advancing" : "Paused"}
          </button>
        )}
      </div>

      <div ref={rootRef} className="mt-14" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
        <div role="tablist" aria-label="Response loop stages" className="relative grid grid-cols-3 gap-2 sm:gap-3 lg:grid-cols-6 lg:gap-0">
          {/* connector line (desktop) */}
          <div className="pointer-events-none absolute left-[8.33%] right-[8.33%] top-[35px] hidden h-px bg-line-strong lg:block" aria-hidden="true">
            <div
              className="h-px bg-accent transition-[width] duration-500"
              style={{ width: `${(active / (STAGES.length - 1)) * 100}%` }}
            />
          </div>
          {STAGES.map((st, i) => {
            const on = i === active;
            const done = i < active;
            const Icon = st.icon;
            return (
              <button
                key={st.key}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                role="tab"
                id={`stage-tab-${st.key}`}
                aria-selected={on}
                aria-controls="stage-panel"
                tabIndex={on ? 0 : -1}
                onClick={() => select(i)}
                onKeyDown={(e) => onKey(e, i)}
                className={`group relative flex flex-col items-start gap-2.5 rounded-[3px] border p-2.5 sm:gap-3 sm:p-3 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent lg:items-center lg:border-0 lg:bg-transparent lg:p-2 lg:text-center ${
                  on ? "border-accent/50 bg-accent/5" : "border-line bg-panel/50 hover:border-line-strong"
                }`}
              >
                <span
                  className={`relative z-10 flex h-11 w-11 sm:h-[54px] sm:w-[54px] items-center justify-center rounded-full border bg-bg transition-all ${
                    on ? "lp-glow border-accent text-accent" : done ? "border-accent/50 text-accent/80" : "border-line-strong text-muted group-hover:text-text"
                  }`}
                >
                  <Icon size={20} aria-hidden="true" />
                </span>
                <span>
                  <span className={`mono block text-[11px] ${on ? "text-accent" : "text-faint"}`}>{String(i + 1).padStart(2, "0")}</span>
                  <span className={`mono block text-[11px] font-medium uppercase tracking-[0.1em] sm:text-[13px] sm:tracking-[0.16em] ${on ? "text-text" : "text-muted"}`}>{st.title}</span>
                </span>
                {on && running && (
                  <span className="absolute inset-x-3 bottom-0 h-px overflow-hidden bg-line lg:inset-x-6" aria-hidden="true">
                    <span key={active} className="lp-progress block h-px bg-accent" style={{ ["--lp-dur" as string]: `${DWELL_MS}ms` }} />
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div
          id="stage-panel"
          role="tabpanel"
          aria-labelledby={`stage-tab-${s.key}`}
          className="lp-card lp-corners mt-8 grid gap-8 p-6 sm:p-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]"
        >
          <div>
            <p className="mono text-[12px] uppercase tracking-[0.16em] text-accent">
              Stage {String(active + 1).padStart(2, "0")} · {s.title}
            </p>
            <p className="mt-3 text-[20px] font-medium leading-snug tracking-[-0.01em] text-text sm:text-[22px]">{s.short}</p>
            <p className="mt-4 text-[15px] leading-relaxed text-muted">{s.detail}</p>
            <p className="mt-5 flex items-start gap-2 border-l-2 border-warn/60 pl-3 text-[13.5px] text-text/90">{s.note}</p>
          </div>
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-[auto_1fr] items-stretch gap-x-4 gap-y-3 rounded-[3px] border border-line bg-bg/50 p-4">
              <span className="label self-center">In</span>
              <span className="mono text-[13px] text-text">{s.input}</span>
              <span className="label self-center">Out</span>
              <span className="mono text-[13px] text-accent">{s.output}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {s.tech.map((t) => (
                <span key={t} className="mono rounded-[2px] border border-line-strong px-2 py-1 text-[11.5px] text-muted">
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Section>
  );
}
