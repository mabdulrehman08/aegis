import type { ReactNode } from "react";

export function SectionHeading({
  index,
  eyebrow,
  title,
  lede,
  align = "left",
}: {
  index?: string;
  eyebrow: string;
  title: ReactNode;
  lede?: ReactNode;
  align?: "left" | "center";
}) {
  const center = align === "center";
  return (
    <div className={`max-w-3xl ${center ? "mx-auto text-center" : ""}`}>
      <p className={`lp-eyebrow flex items-center gap-3 ${center ? "justify-center" : ""}`}>
        {index && <span className="text-faint">{index}</span>}
        <span className="h-px w-8 bg-accent/50" aria-hidden="true" />
        {eyebrow}
      </p>
      <h2 className="mt-4 text-balance text-[clamp(28px,4vw,44px)] font-semibold leading-[1.08] tracking-[-0.02em] text-text">{title}</h2>
      {lede && <p className="mt-5 text-pretty text-[16px] leading-relaxed text-muted sm:text-[17px]">{lede}</p>}
    </div>
  );
}

export function Section({ id, children, className = "" }: { id?: string; children: ReactNode; className?: string }) {
  return (
    <section id={id} className={`relative border-t border-line/70 py-24 sm:py-28 ${className}`}>
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">{children}</div>
    </section>
  );
}

/** Small tag that marks illustrative (non-live) content. */
export function ExampleTag({ children = "Illustrative example" }: { children?: ReactNode }) {
  return (
    <span className="mono inline-flex items-center gap-1.5 rounded-[2px] border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.14em] text-warn">
      <span className="h-1 w-1 rounded-full bg-warn" aria-hidden="true" />
      {children}
    </span>
  );
}
