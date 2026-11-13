import { useEffect, useState, type ReactNode } from "react";
import { ApiError, type Agent, type Call, type Json } from "../api";

/** Fetches whenever `key` changes; aborts the stale request. */
export function useLoad(key: string | null, load: (signal: AbortSignal) => Promise<Json>) {
  const [state, setState] = useState<{ data: Json | null; error: string | null; loading: boolean }>(
    { data: null, error: null, loading: !!key });
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    load(controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((e) => {
        if (controller.signal.aborted) return;
        setState({ data: null, loading: false,
          error: e instanceof ApiError ? e.message : "Could not reach the server." });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

export function Loading({ label = "Crunching the numbers" }: { label?: string }) {
  return <div className="loading"><span className="pulse" />{label}…</div>;
}

export function Problem({ message }: { message: string }) {
  return <div className="problem" role="alert"><b>That did not work.</b> {message}</div>;
}

export function Card({ title, kicker, children, className = "" }: {
  title?: string; kicker?: string; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {kicker && <div className="kicker">{kicker}</div>}
      {title && <h3>{title}</h3>}
      {children}
    </section>
  );
}

export function Stat({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "up" | "down" | "flat";
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

export function Delta({ value, text }: { value: number | null; text: string }) {
  const dir = value === null || value === 0 ? "flat" : value > 0 ? "up" : "down";
  return <span className={`delta ${dir}`}>{dir === "up" ? "▲" : dir === "down" ? "▼" : "■"} {text}</span>;
}

export function DemoBanner() {
  return (
    <div className="demo-banner" role="status">
      <b>Demo data.</b> The live market feed is switched off on this server, so every price and
      figure here is a seeded simulation. Nothing on this page describes a real stock.
    </div>
  );
}

export function Formula({ children }: { children: ReactNode }) {
  return <div className="formula">{children}</div>;
}

const GLYPHS: Record<string, ReactNode> = {
  pm: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" /><path d="M12 2v4M12 18v4M2 12h4M18 12h4" /></>,
  analyst: <path d="M5 20V11M12 20V4M19 20v-6" />,
  quant: <path d="M17 5H7l6 7-6 7h10" />,
  macro: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></>,
  bear: <path d="M4 7l6 6 4-4 6 8M20 12v5h-5" />,
  chair: <><path d="M12 3l8 9-8 9-8-9z" /><path d="M8 12h8" /></>,
  desk: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
};

export function Avatar({ agent, size = 44, state = "" }: {
  agent: Pick<Agent, "id" | "color" | "name">; size?: number; state?: string;
}) {
  return (
    <span className={`avatar ${state}`} style={{ width: size, height: size, color: agent.color }} aria-hidden>
      <svg viewBox="0 0 24 24" width={size * 0.5} height={size * 0.5} fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{GLYPHS[agent.id]}</svg>
    </span>
  );
}

const CALL_WORD: Record<Call, string> = { BUY: "Buy", HOLD: "Hold", AVOID: "Avoid" };
const CALL_MARK: Record<Call, string> = { BUY: "▲", HOLD: "■", AVOID: "▼" };

export function CallChip({ call, small }: { call: Call; small?: boolean }) {
  return <span className={`call ${call.toLowerCase()} ${small ? "small" : ""}`}>{CALL_MARK[call]} {CALL_WORD[call]}</span>;
}

export function Conviction({ level }: { level: number }) {
  return (
    <span className="conviction" aria-label={`Conviction ${level} out of 5`}>
      {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= level ? "on" : ""} />)}
    </span>
  );
}
