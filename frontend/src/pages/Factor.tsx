import { get, type Json } from "../api";
import { Card, Formula, Loading, Problem, Stat, useLoad } from "../components/Bits";
import LineChart from "../components/Chart";
import { ago, frac, num, pct, shortDate, sigma } from "../format";

const clamp = (v: number) => Math.min(Math.max(v, -3), 3);
const TONE: Record<string, string> = { positive: "up", negative: "down", neutral: "flat" };

function Dial({ value, mild, strong }: { value: number; mild: number; strong: number }) {
  const at = (v: number) => `${((clamp(v) + 3) / 6) * 100}%`;
  return (
    <div className="dial" role="img" aria-label={`Score ${sigma(value)} on a scale from minus three to plus three`}>
      <div className="dial-track">
        {[-strong, -mild, mild, strong].map((t) => <i key={t} className="dial-tick" style={{ left: at(t) }} />)}
        <b className="dial-pin" style={{ left: at(value) }} />
      </div>
      <div className="dial-scale"><span>−3σ weak</span><span>0 normal</span><span>strong +3σ</span></div>
    </div>
  );
}

function ZBar({ z }: { z: number | null }) {
  if (z === null) return <span className="muted">warming up</span>;
  const width = (Math.abs(clamp(z)) / 3) * 50;
  return (
    <span className="zbar"><i className={z >= 0 ? "pos" : "neg"}
      style={z >= 0 ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }} /></span>
  );
}

export default function Factor({ ticker }: { ticker: string }) {
  const { data: d, error, loading } = useLoad(ticker, (s) => get(`/stock/${encodeURIComponent(ticker)}/factor`, s));
  if (error) return <Problem message={error} />;
  if (loading || !d) return <Loading label="Scoring against six months of history" />;

  const hist: Json[] = d.history;
  const bt = d.backtest;
  const wT = d.weights_used.technical ?? 0, wS = d.weights_used.sentiment ?? 0;

  return (
    <>
      <Card className={`verdict-card ${d.tone === "bull" ? "positive" : d.tone === "bear" ? "negative" : "neutral"}`}>
        <div className="verdict-main">
          <div>
            <div className="kicker">Composite score · {shortDate(d.as_of)}</div>
            <h2>{d.reading}</h2>
            <p className="prose">
              Measured in standard deviations (σ) of this stock's own recent history. Zero is its
              normal. It describes conditions today; it is not a forecast.
            </p>
          </div>
          <div className="verdict-num">{sigma(d.final)}</div>
        </div>
        <Dial value={d.final} mild={d.thresholds.mild} strong={d.thresholds.strong} />
      </Card>

      <div className="grid-3">
        <Stat label="Technical" value={sigma(d.technical)} note="Price and volume behaviour" />
        <Stat label="Sentiment" value={d.sentiment_sigma === null ? "No headlines" : sigma(d.sentiment_sigma)}
          note={d.sentiment ? `${d.sentiment.n_items} headlines, newest ${ago(d.sentiment.newest_age_hours)}` : "Dropped from the blend, not counted as zero"} />
        <Stat label="Macro haircut" value={`−${pct(d.risk_penalty * 100, 0)}`}
          note={d.vix === null ? "VIX unavailable" : `VIX at ${num(d.vix, 1)}`} />
      </div>

      <Card kicker="Step 1" title="Four signals, each compared with its own past">
        <Formula>z = ( x − μ<sub>{d.config.zscore_window}</sub> ) / σ<sub>{d.config.zscore_window}</sub></Formula>
        <p className="prose">
          Each raw signal is turned into a z-score: how unusual today's value is against the
          last {d.config.zscore_window} trading days. That puts four different things on one ruler.
        </p>
        <table className="ledger">
          <thead><tr><th>Signal</th><th>z today</th><th className="wide" /><th>Weight</th><th>Adds</th></tr></thead>
          <tbody>
            {d.components.map((c: Json) => (
              <tr key={c.key}>
                <td>{c.label}</td><td className="n">{sigma(c.z)}</td><td className="wide"><ZBar z={c.z} /></td>
                <td className="n">{frac(c.weight, 0)}</td><td className="n">{num(c.contribution, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {d.momentum_band_correlation !== null && (
          <p className="note">
            Honest footnote: momentum and band position move together here (correlation {num(d.momentum_band_correlation, 2)}),
            so they partly share one vote. That is why the weighted sum is re-scaled to unit variance before it is called a score.
          </p>
        )}
      </Card>

      <Card kicker="Step 2" title="Blend with the news, then discount for a nervous market">
        <Formula>
          final = ( {num(wT, 2)}·{num(d.technical, 2)}{wS ? <> + {num(wS, 2)}·{num(d.sentiment_sigma, 2)}</> : null} ) × ( 1 − {num(d.risk_penalty, 2)} ) = <b>{num(d.final, 2)}</b>
        </Formula>
        <p className="prose">
          Headline tone counts for {frac(d.config.w_sentiment, 0)} when headlines exist, and a
          day-old headline counts half as much as a fresh one. When there are none the technical
          score carries the full weight. The haircut grows with the VIX and shrinks the score
          toward zero in both directions: fear makes every signal less reliable, not more bearish.
        </p>
      </Card>

      <Card kicker="One year" title="The score through time">
        <LineChart height={260} ariaLabel="Technical score over the past year"
          series={[{ label: "Technical score", color: "var(--mark)", points: hist.map((h, i) => ({ x: i, y: h.v })) }]}
          xFormat={(i) => shortDate(hist[Math.round(i)].t)} yFormat={(y) => `${num(y, 1)}σ`}
          hGuides={[{ at: d.thresholds.strong, label: "strong" }, { at: 0, label: "normal" }, { at: -d.thresholds.strong, label: "weak" }]} />
      </Card>

      <Card kicker="Does it work?" title="We tested it. Here is the honest answer.">
        <p className="prose">{d.backtest_reading}</p>
        {!bt.error && (
          <div className="grid-4 tight">
            <Stat label="Days tested" value={num(bt.n_obs, 0)} note={`${bt.n_independent} independent windows`} />
            <Stat label="Naive p-value" value={num(bt.p_naive, 3)} note="Pretends every day is new evidence" />
            <Stat label="Bootstrap p-value" value={num(bt.p_bootstrap, 3)} note="Respects overlapping windows" />
            <Stat label="Direction right" value={pct(bt.hit_rate_pct, 0)} note={`Over ${bt.horizon_days} days ahead`} />
          </div>
        )}
      </Card>

      <Card kicker="The wire" title="Headlines feeding the sentiment reading">
        {d.headlines.length === 0 ? <p className="prose muted">No recent headlines came back for this ticker.</p> : (
          <ul className="news">
            {d.headlines.map((h: Json, i: number) => (
              <li key={i}>
                <span className={`tone ${TONE[h.tone]}`}>{h.tone}</span>
                {h.link ? <a href={h.link} target="_blank" rel="noopener noreferrer">{h.title}</a> : <span>{h.title}</span>}
                <small>{[h.publisher, ago(h.age_hours)].filter(Boolean).join(" · ")}</small>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
