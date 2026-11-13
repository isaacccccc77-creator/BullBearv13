// Analytics payloads are shaped by the Python backend and read loosely here;
// the debate stream is typed because the page is a state machine over it.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export class ApiError extends Error {}

async function unwrap(res: Response): Promise<Json> {
  if (res.ok) return res.json();
  let detail = `Something went wrong (${res.status}).`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* keep the generic message */
  }
  throw new ApiError(detail);
}

export const get = (path: string, signal?: AbortSignal) =>
  fetch(`/api${path}`, { signal }).then(unwrap);

export const post = (path: string, body: unknown, signal?: AbortSignal) =>
  fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }).then(unwrap);

export type Call = "BUY" | "HOLD" | "AVOID";
export interface Vote { short: Call; long: Call; conviction: number }
export interface Agent { id: string; name: string; title: string; color: string; blurb: string }
export interface Horizon { call: Call; conviction: number; thesis: string; drivers: string[] }
export interface Ruling {
  one_liner: string; short_run: Horizon; long_run: Horizon;
  bull_case: string; bear_case: string; key_risks: string[];
  change_our_mind: string[]; dissent: string; sizing_note: string;
}
export type Round = "research" | "opening" | "rebuttal" | "verdict";
export type DebateEvent =
  | { type: "replay"; age_minutes: number }
  | { type: "start"; ticker: string; agents: Agent[]; demo: boolean; model: string }
  | { type: "phase"; phase: Round }
  | { type: "search" }
  | { type: "briefing"; text: string; sources: { title: string; url: string }[] }
  | { type: "turn_start"; agent: string; round: Round }
  | { type: "delta"; agent: string; round: Round; text: string }
  | { type: "turn_end"; agent: string; round: Round; vote: Vote | null }
  | { type: "turn_error"; agent: string; round: Round; message: string }
  | { type: "verdict"; data: Ruling; votes: Record<string, Vote | null>; tally: Record<"short" | "long", Record<Call, number>> }
  | { type: "done"; usage: Record<string, number>; models: string[] }
  | { type: "error"; message: string };

/** POSTs and reads the server-sent stream. fetch, not EventSource: EventSource
 *  reconnects by itself, and a silent reconnect here would start a second paid debate. */
export async function streamDebate(
  ticker: string, fresh: boolean, onEvent: (e: DebateEvent) => void, signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/debate/${encodeURIComponent(ticker)}${fresh ? "?fresh=true" : ""}`,
    { method: "POST", signal });
  if (!res.ok || !res.body) { await unwrap(res); return; }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      if (frame.startsWith("data: ")) onEvent(JSON.parse(frame.slice(6)));
    }
  }
}
