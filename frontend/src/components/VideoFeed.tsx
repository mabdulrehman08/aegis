"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/**
 * A live feed from either a camera attached to this computer (e.g. the Logitech
 * webcam, or an iPhone via macOS Continuity Camera standing in for a body cam)
 * or a stream URL (e.g. an MJPEG stream published by the perception service).
 * Nothing is recorded or uploaded; the backend has no video API.
 */
type Source = { kind: "off" } | { kind: "device"; deviceId: string } | { kind: "url"; url: string; as: "img" | "video" };

interface Props {
  feedId: string; // persistence key, e.g. "surveillance" or "bodycam:responder-001"
  title: string;
  overlay?: ReactNode;
  className?: string;
}

function load(feedId: string): Source {
  try {
    const raw = localStorage.getItem(`aegis.feed.${feedId}`);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return { kind: "off" };
}

export function VideoFeed({ feedId, title, overlay, className = "" }: Props) {
  const [source, setSource] = useState<Source>({ kind: "off" });
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [setup, setSetup] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const [time, setTime] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setSource(load(feedId));
  }, [feedId]);

  useEffect(() => {
    const t = setInterval(() => setTime(new Date().toLocaleTimeString([], { hour12: false })), 1000);
    return () => clearInterval(t);
  }, []);

  const choose = (s: Source) => {
    setSource(s);
    setError(null);
    try {
      localStorage.setItem(`aegis.feed.${feedId}`, JSON.stringify(s));
    } catch {
      /* ignore */
    }
  };

  const refreshDevices = useCallback(async () => {
    try {
      // Labels are only exposed after permission has been granted once.
      const probe = await navigator.mediaDevices.getUserMedia({ video: true });
      probe.getTracks().forEach((t) => t.stop());
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "videoinput"));
      setError(null);
    } catch (e) {
      setError(`Camera permission: ${(e as Error).message}`);
    }
  }, []);

  // Attach the selected local camera.
  useEffect(() => {
    if (source.kind !== "device") return;
    let stream: MediaStream | null = null;
    let cancelled = false;
    navigator.mediaDevices
      .getUserMedia({ video: { deviceId: { exact: source.deviceId }, width: { ideal: 1280 } }, audio: false })
      .then((s) => {
        if (cancelled) return s.getTracks().forEach((t) => t.stop());
        stream = s;
        if (videoRef.current) videoRef.current.srcObject = s;
      })
      .catch((e) => setError(`Cannot open camera: ${(e as Error).message}`));
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [source]);

  const live = source.kind !== "off" && !error;
  const deviceLabel =
    source.kind === "device" ? devices.find((d) => d.deviceId === source.deviceId)?.label ?? "local camera" : null;

  return (
    <div className={`relative overflow-hidden rounded border border-line bg-black ${className}`}>
      {source.kind === "device" && <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-cover" />}
      {source.kind === "url" &&
        (source.as === "img" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={source.url} alt={title} className="h-full w-full object-cover" onError={() => setError("Stream not reachable")} />
        ) : (
          <video src={source.url} autoPlay playsInline muted loop className="h-full w-full object-cover" onError={() => setError("Stream not playable")} />
        ))}

      {!live && !setup && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[repeating-linear-gradient(0deg,#0a0f14_0_2px,#070b0f_2px_4px)]">
          <span className="mono text-[11px] tracking-[0.2em] text-faint">{error ? "SIGNAL ERROR" : "NO SIGNAL"}</span>
          {error && <span className="max-w-[80%] text-center text-[11px] text-danger">{error}</span>}
          <button
            onClick={() => {
              setSetup(true);
              void refreshDevices();
            }}
            className="rounded border border-line-strong px-2.5 py-1 text-[11px] text-text hover:border-accent"
          >
            Connect feed
          </button>
        </div>
      )}

      {/* HUD */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent px-2 py-1.5">
        {live && <span className="blink h-2 w-2 rounded-full bg-danger" />}
        <span className="mono text-[10.5px] font-semibold tracking-wider text-white/90">{title}</span>
        {deviceLabel && <span className="mono truncate text-[10px] text-white/50">{deviceLabel}</span>}
        <span className="mono ml-auto text-[10px] text-white/60">{time}</span>
      </div>
      {live && overlay && <div className="pointer-events-none absolute inset-x-0 bottom-0">{overlay}</div>}
      {live && (
        <button
          onClick={() => {
            setSetup(true);
            void refreshDevices();
          }}
          className="mono absolute right-1.5 top-7 rounded bg-black/60 px-1.5 py-0.5 text-[9.5px] text-white/60 transition hover:text-white"
        >
          source
        </button>
      )}

      {setup && (
        <div className="absolute inset-0 z-10 flex flex-col gap-2 overflow-y-auto bg-[#05080bf2] p-3 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="label !text-text">{title} · source</span>
            <button onClick={() => setSetup(false)} className="text-muted hover:text-text">
              ✕
            </button>
          </div>
          <p className="label !text-[9.5px]">Camera on this computer</p>
          {devices.length === 0 && <p className="text-muted">{error ?? "Requesting camera list…"}</p>}
          {devices.map((d) => (
            <button
              key={d.deviceId}
              onClick={() => {
                choose({ kind: "device", deviceId: d.deviceId });
                setSetup(false);
              }}
              className={`truncate rounded border px-2 py-1 text-left hover:border-accent ${
                source.kind === "device" && source.deviceId === d.deviceId ? "border-accent text-accent" : "border-line-strong"
              }`}
            >
              {d.label || "Camera"}
            </button>
          ))}
          <p className="label mt-1 !text-[9.5px]">Stream URL (MJPEG / MP4)</p>
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (!urlDraft.trim()) return;
              const u = urlDraft.trim();
              choose({ kind: "url", url: u, as: /\.(mp4|webm|mov)(\?|$)/i.test(u) ? "video" : "img" });
              setSetup(false);
            }}
          >
            <input
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              placeholder="http://127.0.0.1:5000/stream.mjpg"
              className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-2 py-1 outline-none focus:border-accent"
            />
            <button className="rounded border border-line-strong px-2 hover:border-accent">Use</button>
          </form>
          <button
            onClick={() => {
              choose({ kind: "off" });
              setSetup(false);
            }}
            className="mt-auto self-start text-muted underline hover:text-text"
          >
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}
