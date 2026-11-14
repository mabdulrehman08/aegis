/**
 * Illustrative scene for the hero. Every value here is hand-placed demo data,
 * labelled SIMULATED in the UI — it is not perception output or backend state.
 * Scale: 20 px = 1 m, origin bottom-left of the scene frame (40, 480).
 */
const ORIGIN_X = 40;
const ORIGIN_Y = 480;
const toM = (px: number, py: number) => [(px - ORIGIN_X) / 20, (ORIGIN_Y - py) / 20] as const;

const CASUALTIES = [
  { id: "C-001", x: 230, y: 150 },
  { id: "C-002", x: 360, y: 405 },
  { id: "C-003", x: 405, y: 225 },
];
const RESPONDERS = [
  { id: "R-01", x: 110, y: 420 },
  { id: "R-02", x: 580, y: 215 },
];
const ROUTE = "M110 420 V250 H230 V170";

function Casualty({ id, x, y, i }: { id: string; x: number; y: number; i: number }) {
  const [mx, my] = toM(x, y);
  const b = 15;
  return (
    <g>
      <circle cx={x} cy={y} r="7" fill="none" stroke="var(--sev-unassessed)" strokeOpacity="0.6" className="pulse-ring" style={{ animationDelay: `${i * 0.5}s` }} />
      {/* detection brackets */}
      <path
        d={`M${x - b} ${y - b + 5}v-5h5 M${x + b - 5} ${y - b}h5v5 M${x + b} ${y + b - 5}v5h-5 M${x - b + 5} ${y + b}h-5v-5`}
        stroke="var(--accent)"
        strokeWidth="1.2"
        fill="none"
        opacity="0.85"
      />
      <circle cx={x} cy={y} r="5" fill="var(--bg)" stroke="var(--sev-unassessed)" strokeWidth="1.5" strokeDasharray="2 1.6" />
      <circle cx={x} cy={y} r="1.6" fill="var(--sev-unassessed)" />
      <text x={x + b + 5} y={y - 3} className="mono" fontSize="10.5" fill="var(--text)">
        {id}
      </text>
      <text x={x + b + 5} y={y + 9} className="mono" fontSize="8.5" fill="var(--muted)">
        {mx.toFixed(1)}, {my.toFixed(1)} m
      </text>
    </g>
  );
}

function Responder({ id, x, y }: { id: string; x: number; y: number }) {
  const [mx, my] = toM(x, y);
  return (
    <g>
      <circle cx={x} cy={y} r="11" fill="var(--accent)" opacity="0.1" />
      <path d={`M${x} ${y - 7} L${x + 6.5} ${y + 5} L${x - 6.5} ${y + 5} Z`} fill="var(--accent)" />
      <text x={x + 13} y={y - 2} className="mono" fontSize="10.5" fill="var(--accent)">
        {id}
      </text>
      <text x={x + 13} y={y + 10} className="mono" fontSize="8.5" fill="var(--muted)">
        {mx.toFixed(1)}, {my.toFixed(1)} m
      </text>
    </g>
  );
}

export function HeroScene() {
  return (
    <div className="lp-corners relative overflow-hidden rounded-[4px] border border-line bg-panel/80 shadow-[0_40px_120px_-40px_rgba(56,208,220,0.25)]">
      {/* header bar */}
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="mono flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-danger blink" aria-hidden="true" />
          Scene · Building A<span className="hidden sm:inline"> · Floor 1</span>
        </div>
        <span className="mono rounded-[2px] border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.14em] text-warn">
          Simulated scene
        </span>
      </div>

      <svg
        viewBox="0 0 720 520"
        className="block h-auto w-full"
        role="img"
        aria-label="Simulated emergency scene: three detected people awaiting assessment, two responders, debris zones, and a proposed route from responder R-01 to casualty C-001 awaiting command approval."
      >
        <defs>
          <pattern id="hs-minor" width="20" height="20" patternUnits="userSpaceOnUse" x="40" y="40">
            <path d="M20 0H0V20" fill="none" stroke="var(--line)" strokeWidth="0.5" />
          </pattern>
          <pattern id="hs-major" width="100" height="100" patternUnits="userSpaceOnUse" x="40" y="80">
            <path d="M100 0H0V100" fill="none" stroke="var(--line-strong)" strokeWidth="0.7" />
          </pattern>
          <pattern id="hs-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <path d="M0 0V6" stroke="var(--danger)" strokeWidth="1.4" strokeOpacity="0.45" />
          </pattern>
          <linearGradient id="hs-fov" x1="1" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--accent)" stopOpacity="0.35" />
            <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <rect x="40" y="40" width="640" height="440" fill="url(#hs-minor)" />
        <rect x="40" y="40" width="640" height="440" fill="url(#hs-major)" />

        {/* axis ticks (meters) */}
        {[0, 5, 10, 15, 20, 25, 30].map((m) => (
          <text key={`x${m}`} x={40 + m * 20} y="498" className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="middle">
            {m}
          </text>
        ))}
        {[0, 5, 10, 15, 20].map((m) => (
          <text key={`y${m}`} x="30" y={483 - m * 20} className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="end">
            {m}
          </text>
        ))}
        <text x="680" y="512" className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="end">
          x (m)
        </text>

        {/* camera field of view */}
        <polygon points="660,60 300,250 520,470" fill="url(#hs-fov)" className="lp-scan" />

        {/* walls */}
        <g stroke="var(--muted)" strokeWidth="3" fill="none" strokeLinecap="square" opacity="0.75">
          <path d="M60 60 H660 V460 H380 M320 460 H60 V60" />
          <path d="M300 60 V180 M300 240 V300" />
          <path d="M420 300 H500 M560 300 H660" />
        </g>

        {/* debris / obstacle zones */}
        <g>
          <rect x="170" y="320" width="100" height="80" fill="url(#hs-hatch)" stroke="var(--danger)" strokeOpacity="0.5" />
          <text x="175" y="314" className="mono" fontSize="8.5" fill="var(--danger)" opacity="0.85">
            DEBRIS
          </text>
          <rect x="480" y="100" width="55" height="80" fill="url(#hs-hatch)" stroke="var(--danger)" strokeOpacity="0.5" />
          <text x="482" y="94" className="mono" fontSize="8.5" fill="var(--danger)" opacity="0.85">
            BLOCKED
          </text>
        </g>

        {/* proposed route (awaiting approval) */}
        <path d={ROUTE} fill="none" stroke="var(--warn)" strokeOpacity="0.25" strokeWidth="6" strokeLinejoin="round" />
        <path d={ROUTE} pathLength={100} fill="none" stroke="var(--warn)" strokeWidth="1.8" strokeLinejoin="round" className="lp-route-draw" />
        <path d={ROUTE} pathLength={100} fill="none" stroke="#ffe2a8" strokeWidth="3" strokeLinecap="round" className="lp-packet" />
        <g transform="translate(118 300)">
          <rect width="92" height="16" rx="2" fill="var(--bg)" stroke="var(--warn)" strokeOpacity="0.6" />
          <text x="6" y="11" className="mono" fontSize="8.5" fill="var(--warn)">
            PROPOSED · REV 1
          </text>
        </g>

        {CASUALTIES.map((c, i) => (
          <Casualty key={c.id} {...c} i={i} />
        ))}
        {RESPONDERS.map((r) => (
          <Responder key={r.id} {...r} />
        ))}

        {/* perception camera */}
        <g transform="translate(660 60)">
          <rect x="-11" y="-8" width="22" height="16" rx="2" fill="var(--panel-2)" stroke="var(--accent)" />
          <circle r="4" fill="none" stroke="var(--accent)" />
          <circle r="1.5" fill="var(--accent)" className="blink" />
        </g>
        <text x="645" y="88" className="mono" fontSize="8.5" fill="var(--accent)" textAnchor="end">
          CAM-01 · PERCEPTION
        </text>
      </svg>

      {/* floating status panel */}
      <div className="lp-card m-3 mt-0 p-3 backdrop-blur-sm sm:absolute sm:bottom-3 sm:right-3 sm:m-0 sm:w-[232px]" aria-label="Simulated scene status">
        <div className="mb-2 flex items-center justify-between">
          <span className="label">Incident status</span>
          <span className="mono text-[9px] uppercase tracking-[0.12em] text-warn">Sim</span>
        </div>
        <dl className="mono space-y-1.5 text-[10.5px]">
          {[
            ["Scene status", "ACTIVE", "text-danger"],
            ["Tracked entities", "03", "text-text"],
            ["Available responders", "02", "text-text"],
            ["Optimization", "READY", "text-accent"],
            ["Command authorization", "REQUIRED", "text-warn"],
          ].map(([k, v, c]) => (
            <div key={k} className="flex items-center justify-between gap-3 border-b border-line/60 pb-1.5 last:border-0 last:pb-0">
              <dt className="uppercase tracking-[0.08em] text-muted">{k}</dt>
              <dd className={`font-medium ${c}`}>{v}</dd>
            </div>
          ))}
        </dl>
      </div>

      {/* perception indicator */}
      <div className="mono absolute left-3 top-12 flex items-center gap-2 rounded-[2px] border border-accent/30 bg-bg/80 px-2 py-1 text-[9.5px] uppercase tracking-[0.12em] text-accent">
        <span className="relative flex h-2 w-2" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-60 blink" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
        </span>
        Perception · demo playback
      </div>
    </div>
  );
}
