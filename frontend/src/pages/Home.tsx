import type { Agent, Json } from "../api";
import { Avatar, DemoBanner } from "../components/Bits";
import Search from "../components/Search";

const PICKS = ["NVDA", "AAPL", "TSLA", "MSFT", "D05.SI", "BTC-USD"];

const STEPS = [
  ["01", "The wire", "A research desk searches today's news: earnings, rates, policy, geopolitics. Every analyst argues from the same dated briefing."],
  ["02", "Blind openings", "All five speak at once without hearing each other, so nobody anchors on the loudest voice."],
  ["03", "Rebuttals", "Each must answer the argument that hurts their case most. You watch the votes move."],
  ["04", "The ruling", "The Chair rules twice: once for the next few months, once for the next few years."],
];

export default function Home({ config }: { config: Json }) {
  const agents: Agent[] = (config?.agents ?? []).filter((a: Agent) => a.id !== "chair");
  return (
    <div className="home">
      {config?.demo && <DemoBanner />}
      <section className="hero">
        <p className="eyebrow"><span className="live-dot" /> The maths, shown. The argument, live.</p>
        <h1>Five analysts.<br />One stock.<br /><span className="grad">Zero veil.</span></h1>
        <p className="lede">
          Most apps hand you a green arrow and call it insight. Tickveil shows the working: a
          score you can audit, the odds after your broker takes its cut, and a boardroom of AI
          analysts who fight over today's headlines until someone has to rule.
        </p>
        <Search big />
        <div className="picks">
          <span>Try</span>
          {PICKS.map((t) => <a key={t} href={`#/s/${encodeURIComponent(t)}/overview`}>{t}</a>)}
        </div>
      </section>

      <section className="lineup">
        <h2>Meet the room</h2>
        <p className="section-lede">Five AI personas with five different jobs. They are built to disagree.</p>
        <div className="seats">
          {agents.map((a, i) => (
            <article key={a.id} className="seat" style={{ "--c": a.color, "--i": i } as React.CSSProperties}>
              <Avatar agent={a} size={56} />
              <h3>{a.name}</h3>
              <div className="seat-title">{a.title}</div>
              <p>{a.blurb}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="bento">
        <a className="tile t-board" href="#/s/NVDA/boardroom">
          <span className="tag">The Boardroom</span>
          <h3>Watch them argue it out, then get two verdicts.</h3>
          <p>Short run and long run are different questions. The room answers both, and tells you what would change its mind.</p>
          <div className="tug" aria-hidden><i style={{ flex: 3 }} className="buy" /><i style={{ flex: 1 }} className="hold" /><i style={{ flex: 1 }} className="avoid" /></div>
        </a>
        <a className="tile t-factor" href="#/s/AAPL/factor">
          <span className="tag">Factor Score</span>
          <h3>One number, fully unpacked.</h3>
          <code>z = (x − μ₁₂₆) / σ₁₂₆</code>
          <p>Trend, momentum, stretch and volume, each measured against the stock's own last six months.</p>
        </a>
        <a className="tile t-buy" href="#/s/AAPL/buytest">
          <span className="tag">Buy Test</span>
          <h3>Does the edge survive your broker?</h3>
          <code>p* = L / (W + L)</code>
          <p>Break-even hit rate after fees, then a Kelly stake, deliberately cut to a quarter.</p>
        </a>
        <a className="tile t-risk" href="#/s/TSLA/risk">
          <span className="tag">Risk</span>
          <h3>How bad is a bad day?</h3>
          <p>Value at Risk three ways, and the number that matters more: the average loss beyond it.</p>
        </a>
        <a className="tile t-deal" href="#/deals">
          <span className="tag">Deal Room</span>
          <h3>Run a buyout like a PE associate.</h3>
          <p>An LBO model with the returns bridge: how much is growth, how much is just debt.</p>
        </a>
      </section>

      <section className="steps">
        <h2>How a debate runs</h2>
        <ol>
          {STEPS.map(([n, title, body]) => (
            <li key={n}><span>{n}</span><h3>{title}</h3><p>{body}</p></li>
          ))}
        </ol>
      </section>
    </div>
  );
}
