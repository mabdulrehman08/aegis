import { Camera, Cpu, LayoutDashboard, RadioTower, ArrowDown } from "lucide-react";
import { Section, SectionHeading } from "./Section";

const LAYERS = [
  {
    key: "perception",
    name: "Perception",
    icon: Camera,
    role: "Sees the scene and places people on the floor plan.",
    items: ["Logitech webcam", "YOLO object detection", "ByteTrack tracking", "OpenCV", "Homography calibration"],
    emits: "Tracks + scene positions",
  },
  {
    key: "intelligence",
    name: "Intelligence",
    icon: Cpu,
    role: "Holds scenario state, recommends assignments, plans routes.",
    items: ["FastAPI", "Python", "Google OR-Tools", "A* pathfinding", "Structured LLM advisory"],
    emits: "Revisioned state over REST + WebSocket",
  },
  {
    key: "command",
    name: "Command",
    icon: LayoutDashboard,
    role: "Where a human reviews, approves, and dispatches.",
    items: ["Next.js", "TypeScript", "Tailwind CSS", "SVG / Canvas", "REST APIs", "WebSockets"],
    emits: "Approved dispatch commands",
  },
  {
    key: "physical",
    name: "Physical response",
    icon: RadioTower,
    role: "Puts the task in the responder's hands and confirms receipt.",
    items: ["Arduino UNO R4 WiFi", "Grove button", "Grove LED", "Serial communication", "Physical acknowledgment"],
    emits: "Delivered / acknowledged events",
  },
];

export function Technology() {
  return (
    <Section id="technology" className="overflow-hidden">
      <div className="lp-grid lp-grid-fade pointer-events-none absolute inset-0 opacity-60" aria-hidden="true" />
      <div className="relative">
        <SectionHeading
          index="03"
          eyebrow="Technology"
          title="Intelligence across the entire response pipeline."
          lede="Four layers, each with a narrow job and a clear interface to the next. Data flows left to right; acknowledgments flow back."
        />

        <div className="relative mt-14">
          <ol className="grid gap-4 lg:grid-cols-4 lg:gap-10">
            {LAYERS.map((l, i) => {
              const Icon = l.icon;
              return (
                <li key={l.key} className="relative">
                  <article className="lp-card lp-corners flex h-full flex-col p-5" aria-labelledby={`layer-${l.key}`}>
                    <header className="flex items-center justify-between">
                      <span className="flex items-center gap-2.5">
                        <span className="flex h-8 w-8 items-center justify-center rounded-[3px] border border-accent/40 bg-accent/10 text-accent">
                          <Icon size={16} aria-hidden="true" />
                        </span>
                        <h3 id={`layer-${l.key}`} className="mono text-[12.5px] font-medium uppercase tracking-[0.16em] text-text">
                          {l.name}
                        </h3>
                      </span>
                      <span className="mono text-[11px] text-faint">L{i + 1}</span>
                    </header>
                    <p className="mt-4 text-[13.5px] leading-relaxed text-muted">{l.role}</p>
                    <ul className="mt-4 space-y-2 border-t border-line pt-4">
                      {l.items.map((it) => (
                        <li key={it} className="mono flex items-center gap-2.5 text-[12.5px] text-text/90">
                          <span className="h-1 w-1 rounded-full bg-accent/70" aria-hidden="true" />
                          {it}
                        </li>
                      ))}
                    </ul>
                    <p className="mono mt-auto pt-5 text-[11px] uppercase tracking-[0.1em] text-faint">
                      Emits <span className="normal-case tracking-normal text-accent">{l.emits}</span>
                    </p>
                  </article>

                  {i < LAYERS.length - 1 && (
                    <>
                      {/* desktop connector */}
                      <div className="absolute left-full top-1/2 hidden w-10 -translate-y-1/2 lg:block" aria-hidden="true">
                        <svg viewBox="0 0 40 20" className="w-10">
                          <path d="M0 10 H36" stroke="var(--line-strong)" />
                          <path d="M0 10 H36" pathLength={100} stroke="var(--accent)" strokeWidth="2" className="lp-packet" style={{ animationDelay: `${i * 0.6}s` }} />
                          <path d="M33 6 L38 10 L33 14" fill="none" stroke="var(--accent)" />
                        </svg>
                      </div>
                      {/* mobile connector */}
                      <div className="flex justify-center py-1 text-accent/70 lg:hidden" aria-hidden="true">
                        <ArrowDown size={16} />
                      </div>
                    </>
                  )}
                </li>
              );
            })}
          </ol>

          {/* return lane: acknowledgment back to intelligence */}
          <div className="mt-4 hidden lg:block" aria-hidden="true">
            <svg viewBox="0 0 1000 56" preserveAspectRatio="none" className="h-14 w-full">
              <path d="M875 0 V36 H375 V0" fill="none" stroke="var(--warn)" strokeOpacity="0.35" vectorEffect="non-scaling-stroke" strokeDasharray="4 4" />
              <path d="M875 0 V36 H375 V0" pathLength={100} fill="none" stroke="var(--warn)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" className="lp-packet-slow" />
            </svg>
          </div>
          <p className="mono mt-1 hidden text-center text-[11px] uppercase tracking-[0.14em] text-warn/90 lg:block">
            Physical acknowledgment → state update → replanning when the scene changes
          </p>
          <p className="mono mt-6 text-[11px] uppercase tracking-[0.14em] text-warn/90 lg:hidden">
            ↺ Acknowledgments flow back from L4 to L2 and trigger replanning when the scene changes
          </p>
        </div>
      </div>
    </Section>
  );
}
