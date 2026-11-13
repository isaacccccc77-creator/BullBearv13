import { useEffect, useReducer, useRef } from "react";
import {
  ApiError, streamDebate, type Agent, type Call, type DebateEvent, type Horizon, type Json,
  type Round, type Ruling, type Vote,
} from "../api";
import { Avatar, CallChip, Card, Conviction, Problem } from "../components/Bits";
import { num } from "../format";

interface Turn { text: string; status: "speaking" | "done" | "error"; vote: Vote | null; error?: string }
type Spoken = "opening" | "rebuttal";

interface State {
  status: "idle" | "running" | "done" | "error";
  phase: Round | null;
  agents: Agent[];
  searches: number;
  wire: string;
  sources: { title: string; url: string }[];
  briefed: boolean;
  turns: Record<Spoken, Record<string, Turn>>;
  ruling: Ruling | null;
  replayAge: number | null;
  error: string | null;
  meta: { tokens: number; models: string[] } | null;
}

const fresh = (agents: Agent[]): State => ({
  status: "idle", phase: null, agents, searches: 0, wire: "", sources: [], briefed: false,
  turns: { opening: {}, rebuttal: {} }, ruling: null, replayAge: null, error: null, meta: null,
});

type Action = DebateEvent | { type: "begin" } | { type: "fail"; message: string };

function reduce(state: State, e: Action): State {
  const setTurn = (round: Round, agent: string, patch: (t: Turn) => Turn): State => {
    if (round !== "opening" && round !== "rebuttal") return state;
    const current = state.turns[round][agent] ?? { text: "", status: "speaking", vote: null };
    return { ...state, turns: { ...state.turns, [round]: { ...state.turns[round], [agent]: patch(current) } } };
  };
  switch (e.type) {
    case "begin": return { ...fresh(state.agents), status: "running", phase: "research" };
    case "fail": return { ...state, status: "error", error: e.message };
    case "replay": return { ...state, replayAge: e.age_minutes };
    case "start": return { ...state, agents: e.agents };
    case "phase": return { ...state, phase: e.phase };
    case "search": return { ...state, searches: state.searches + 1 };
    case "briefing": return { ...state, wire: e.text, sources: e.sources, briefed: true };
    case "turn_start": return setTurn(e.round, e.agent, (t) => t);
    case "delta":
      if (e.agent === "desk") return { ...state, wire: state.wire + e.text };
      return setTurn(e.round, e.agent, (t) => ({ ...t, text: t.text + e.text }));
    case "turn_end": return setTurn(e.round, e.agent, (t) => ({ ...t, status: "done", vote: e.vote }));
    case "turn_error": return setTurn(e.round, e.agent, (t) => ({ ...t, status: "error", error: e.message }));
    case "verdict": return { ...state, ruling: e.data };
    case "done": return { ...state, status: "done", meta: {
      tokens: (e.usage.input_tokens ?? 0) + (e.usage.output_tokens ?? 0) + (e.usage.cache_read_tokens ?? 0),
      models: e.models } };
    case "error": return { ...state, status: "error", error: e.message };
  }
}

// The vote line is machine-readable; hide it, including while it is still arriving.
const spoken = (text: string) =>
  text.replace(/VOTE\s*\|[\s\S]*$/i, "").replace(/\n\s*V(O(T(E)?)?)?\s*$/, "").trimEnd();

const PHASES: [Round, string][] = [
  ["research", "The wire"], ["opening", "Openings"], ["rebuttal", "Rebuttals"], ["verdict", "The ruling"],
];
const CALLS: Call[] = ["BUY", "HOLD", "AVOID"];

function latestVotes(state: State): Record<string, Vote> {
  const out: Record<string, Vote> = {};
  for (const round of ["opening", "rebuttal"] as Spoken[])
    for (const [agent, turn] of Object.entries(state.turns[round])) if (turn.vote) out[agent] = turn.vote;
  return out;
}

function TallyBar({ label, votes, pick }: { label: string; votes: Vote[]; pick: (v: Vote) => Call }) {
  const counts = Object.fromEntries(CALLS.map((c) => [c, votes.filter((v) => pick(v) === c).length])) as Record<Call, number>;
  return (
    <div className="tally">
      <div className="tally-label">{label}</div>
      <div className="tally-bar" role="img"
        aria-label={`${label}: ${counts.BUY} buy, ${counts.HOLD} hold, ${counts.AVOID} avoid`}>
        {votes.length === 0 ? <i className="empty" /> : CALLS.map((c) => counts[c] > 0 && (
          <i key={c} className={c.toLowerCase()} style={{ flex: counts[c] }}>{counts[c]} {c.toLowerCase()}</i>
        ))}
      </div>
    </div>
  );
}

function TurnCard({ agent, turn, moved }: { agent: Agent; turn: Turn | undefined; moved?: boolean }) {
  return (
    <article className={`turn ${turn?.status ?? "waiting"}`} style={{ "--c": agent.color } as React.CSSProperties}>
      <header>
        <Avatar agent={agent} size={40} state={turn?.status === "speaking" ? "speaking" : ""} />
        <div><b>{agent.name}</b><small>{agent.title}</small></div>
      </header>
      {!turn && <p className="muted">Waiting for the floor…</p>}
      {turn && turn.status !== "error" && (
        <p className="speech">{spoken(turn.text)}{turn.status === "speaking" && <span className="caret" />}</p>
      )}
      {turn?.status === "error" && <p className="muted">Could not speak this round. {turn.error}</p>}
      {turn?.vote && (
        <footer>
          <span>Short <CallChip call={turn.vote.short} small /></span>
          <span>Long <CallChip call={turn.vote.long} small /></span>
          <Conviction level={turn.vote.conviction} />
          {moved && <em>changed their mind</em>}
        </footer>
      )}
    </article>
  );
}

function HorizonCard({ title, span, h }: { title: string; span: string; h: Horizon }) {
  return (
    <div className={`horizon ${h.call.toLowerCase()}`}>
      <div className="kicker">{title} · {span}</div>
      <div className="stamp">{h.call}</div>
      <div className="horizon-conv">Conviction <Conviction level={h.conviction} /></div>
      <p>{h.thesis}</p>
      <ul>{h.drivers.map((d, i) => <li key={i}>{d}</li>)}</ul>
    </div>
  );
}

export default function Boardroom({ ticker, config }: { ticker: string; config: Json }) {
  const roster: Agent[] = config?.agents ?? [];
  const [state, dispatch] = useReducer(reduce, roster, fresh);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const agents = state.agents.length ? state.agents : roster;
  const debaters = agents.filter((a) => a.id !== "chair");
  const chair = agents.find((a) => a.id === "chair");

  const convene = (forceFresh: boolean) => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    dispatch({ type: "begin" });
    streamDebate(ticker, forceFresh, dispatch, controller.signal).catch((err) => {
      if (controller.signal.aborted) return;
      dispatch({ type: "fail", message: err instanceof ApiError ? err.message : "The connection dropped mid-debate." });
    });
  };

  if (state.status === "idle") {
    return (
      <Card className="convene">
        <div className="kicker">The Boardroom</div>
        <h2>Should anyone buy {ticker}? Put it to the room.</h2>
        <p className="prose">
          A research desk reads today's news. Five AI analysts argue from it, blind at first and
          then against each other. The Chair rules twice: once for the next one to three months,
          once for the next three to five years. It takes a couple of minutes and you watch it live.
        </p>
        <div className="convene-seats">
          {debaters.map((a) => <span key={a.id}><Avatar agent={a} size={48} /><small>{a.name}</small></span>)}
        </div>
        {config && !config.debate_enabled ? (
          <p className="locked">
            The Boardroom is not switched on for this server yet. The site owner needs to add an
            Anthropic API key (<code>ANTHROPIC_API_KEY</code>) to the server's settings.
          </p>
        ) : (
          <button className="primary" onClick={() => convene(false)} disabled={!config}>Convene the Boardroom <span aria-hidden>→</span></button>
        )}
        {config?.demo && <p className="note">This server is in demo mode, so the room is given no price or risk figures and argues from the news alone.</p>}
        <p className="note">AI personas, not people. Analysis for learning, not advice for you.</p>
      </Card>
    );
  }

  const votes = latestVotes(state);
  const cast = Object.values(votes);
  const phaseIndex = state.status === "done" ? PHASES.length : PHASES.findIndex(([p]) => p === state.phase);
  const showRound = (round: Spoken) => Object.keys(state.turns[round]).length > 0;

  return (
    <div className="boardroom">
      <ol className="stepper" aria-label="Debate progress">
        {PHASES.map(([id, label], i) => (
          <li key={id} className={i < phaseIndex ? "past" : i === phaseIndex ? "now" : ""}
            aria-current={i === phaseIndex ? "step" : undefined}><span>{i + 1}</span>{label}</li>
        ))}
      </ol>

      {state.replayAge !== null && (
        <div className="replay">
          Replaying the debate held {state.replayAge < 1 ? "moments" : `${state.replayAge} min`} ago.
          {state.status === "done" && <button className="ghost" onClick={() => convene(true)}>Hold a fresh one</button>}
        </div>
      )}

      <div className="table-top">
        <div className="seats-live">
          {debaters.map((a) => {
            const speaking = (["opening", "rebuttal"] as Spoken[]).some((r) => state.turns[r][a.id]?.status === "speaking");
            return (
              <div key={a.id} className="seat-live">
                <Avatar agent={a} size={52} state={speaking ? "speaking" : votes[a.id] ? "voted" : ""} />
                <small>{a.name}</small>
                {votes[a.id] ? <CallChip call={votes[a.id].long} small /> : <span className="call small ghosted">···</span>}
              </div>
            );
          })}
        </div>
        <div className="tallies">
          <TallyBar label="Short run" votes={cast} pick={(v) => v.short} />
          <TallyBar label="Long run" votes={cast} pick={(v) => v.long} />
          <div className="tally-note">Chips under each seat show the long-run vote. Bars count the latest vote from everyone who has spoken.</div>
        </div>
      </div>

      <Card kicker="Round zero" title="The wire: what is happening in the world" className="wire">
        {!state.briefed && (
          <div className="searching"><span className="pulse" />
            {state.searches ? `Searching the news… ${state.searches} ${state.searches === 1 ? "search" : "searches"} so far` : "The research desk is opening the wires…"}
          </div>
        )}
        {state.wire && <p className="speech">{state.wire}{!state.briefed && <span className="caret" />}</p>}
        {state.sources.length > 0 && (
          <details>
            <summary>{state.sources.length} sources the desk found</summary>
            <ul className="sources">
              {state.sources.map((s) => <li key={s.url}><a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a></li>)}
            </ul>
          </details>
        )}
      </Card>

      {showRound("opening") && (
        <section>
          <h3 className="round-title"><span>Round one</span> Blind opening statements</h3>
          <div className="turns">
            {debaters.map((a) => <TurnCard key={a.id} agent={a} turn={state.turns.opening[a.id]} />)}
          </div>
        </section>
      )}

      {showRound("rebuttal") && (
        <section>
          <h3 className="round-title"><span>Round two</span> Rebuttals</h3>
          <div className="turns">
            {debaters.filter((a) => state.turns.opening[a.id]?.status === "done").map((a) => {
              const before = state.turns.opening[a.id]?.vote, after = state.turns.rebuttal[a.id]?.vote;
              const moved = !!before && !!after && (before.short !== after.short || before.long !== after.long);
              return <TurnCard key={a.id} agent={a} turn={state.turns.rebuttal[a.id]} moved={moved} />;
            })}
          </div>
        </section>
      )}

      {state.phase === "verdict" && !state.ruling && state.status === "running" && chair && (
        <div className="deliberating"><Avatar agent={chair} size={48} state="speaking" />The Chair is weighing the arguments…</div>
      )}

      {state.ruling && chair && (
        <section className="ruling">
          <h3 className="round-title"><span>The ruling</span> {chair.name}, {chair.title.toLowerCase()}</h3>
          <blockquote>{state.ruling.one_liner}</blockquote>
          <div className="horizons">
            <HorizonCard title="Short run" span="next 1–3 months" h={state.ruling.short_run} />
            <HorizonCard title="Long run" span="next 3–5 years" h={state.ruling.long_run} />
          </div>
          <div className="grid-2">
            <Card kicker="Strongest case for" className="case bull"><p className="prose">{state.ruling.bull_case}</p></Card>
            <Card kicker="Strongest case against" className="case bear"><p className="prose">{state.ruling.bear_case}</p></Card>
          </div>
          <div className="grid-2">
            <Card kicker="Watch out" title="Key risks"><ul className="plain">{state.ruling.key_risks.map((r, i) => <li key={i}>{r}</li>)}</ul></Card>
            <Card kicker="Stay honest" title="What would change our mind"><ul className="plain">{state.ruling.change_our_mind.map((r, i) => <li key={i}>{r}</li>)}</ul></Card>
          </div>
          <div className="grid-2">
            <Card kicker="Not unanimous" title="The dissent"><p className="prose">{state.ruling.dissent}</p></Card>
            <Card kicker="How much" title="Sizing"><p className="prose">{state.ruling.sizing_note}</p></Card>
          </div>
        </section>
      )}

      {state.error && <Problem message={state.error} />}
      {(state.status === "done" || state.status === "error") && (
        <div className="after">
          {state.replayAge === null && <button className="ghost" onClick={() => convene(state.status === "done")}>{state.status === "done" ? "Hold a fresh debate" : "Try again"}</button>}
          {state.meta && <small>Run on {state.meta.models.join(", ")} · about {num(state.meta.tokens / 1000, 0)}K tokens. AI-generated analysis; it can be wrong. Not investment advice.</small>}
        </div>
      )}
    </div>
  );
}
