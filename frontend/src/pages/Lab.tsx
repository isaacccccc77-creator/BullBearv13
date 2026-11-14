import { get, type Json } from "../api";
import { Card, Formula, Loading, Problem, Stat, useLoad } from "../components/Bits";
import LineChart from "../components/Chart";
import { frac, num, shortDate } from "../format";

const FAMILY_LABEL: Record<string, string> = {
  return: "Past return", volatility: "Volatility", volume: "Volume vs usual",
};
const TONE_CLASS: Record<string, string> = { positive: "positive", negative: "negative", neutral: "neutral" };

/** Dot-and-whisker on an AUC ruler: where each contender landed, and how sure. */
function AucRuler({ rows }: { rows: { label: string; auc: number; lo: number; hi: number; color: string }[] }) {
  const lo = 0.3, hi = 0.7;
  const at = (v: number) => `${((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="ruler" role="img"
      aria-label={rows.map((r) => `${r.label}: AUC ${num(r.auc, 2)}, range ${num(r.lo, 2)} to ${num(r.hi, 2)}`).join("; ")}>
      <div className="ruler-zone" style={{ left: at(0.5), right: 0 }}><span>skill</span></div>
      <div className="ruler-zone bad" style={{ left: 0, right: `calc(100% - ${at(0.5)})` }}><span>backwards</span></div>
      <i className="ruler-mid" style={{ left: at(0.5) }}><b>0.50 coin flip</b></i>
      {rows.map((r, i) => (
        <div key={r.label} className="ruler-row" style={{ top: 34 + i * 34 }}>
          <i className="whisker" style={{ left: at(r.lo), width: `calc(${at(r.hi)} - ${at(r.lo)})`, background: r.color }} />
          <i className="pip" style={{ left: at(r.auc), background: r.color }} />
          <span className="pip-label" style={{ left: at(r.auc) }}>{r.label} {num(r.auc, 2)}</span>
        </div>
      ))}
      <div className="ruler-scale">{[0.3, 0.4, 0.5, 0.6, 0.7].map((t) => <span key={t}>{num(t, 1)}</span>)}</div>
    </div>
  );
}

function HeatGrid({ grid, windows }: { grid: Record<string, (number | null)[]>; windows: number[] }) {
  const all = Object.values(grid).flat().filter((v): v is number => v !== null);
  const top = Math.max(1e-9, ...all.map(Math.abs));
  return (
    <div className="heat-wrap">
      <table className="heat" aria-label="Model weights by feature and look-back">
        <thead>
          <tr><th />{windows.map((w) => <th key={w} scope="col">{w}</th>)}</tr>
        </thead>
        <tbody>
          {Object.entries(grid).map(([family, row]) => (
            <tr key={family}>
              <th scope="row">{FAMILY_LABEL[family]}</th>
              {row.map((v, i) => {
                const off = v === null || Math.abs(v) < 1e-6;
                const share = off ? 0 : Math.round(25 + 75 * Math.abs(v!) / top);
                const tint = off ? undefined
                  : `color-mix(in srgb, ${v! > 0 ? "var(--buy)" : "var(--avoid)"} ${share}%, #26233a)`;
                return (
                  <td key={i} className={off ? "off" : ""} style={{ background: tint }}
                    title={`${FAMILY_LABEL[family]}, ${windows[i]} week${windows[i] > 1 ? "s" : ""}: ${v === null ? "no data" : off ? "switched off" : num(v, 3)}`} />
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="heat-axis">look-back in weeks →</div>
      <div className="legend static">
        <span><i style={{ background: "var(--buy)" }} />Pushes toward "up"</span>
        <span><i style={{ background: "var(--avoid)" }} />Pushes toward "down"</span>
        <span><i className="hollow" />Switched off by the penalty</span>
      </div>
    </div>
  );
}

export default function Lab({ ticker }: { ticker: string }) {
  const { data: d, error, loading } = useLoad(ticker, (s) => get(`/stock/${encodeURIComponent(ticker)}/forecast`, s));
  if (error) return <Problem message={error} />;
  if (loading || !d) return <Loading label="Training on decades of history and testing month by month" />;
  if (d.status !== "ok") return <Card kicker="Forecast Lab" title="Not enough history to run this fairly"><p className="prose">{d.message}</p></Card>;

  const g = d.grades, c = g.contenders;
  const s = d.strategy;
  const eq: Json[] = d.equity;
  const ps = d.persistence;
  const series: Json[] = ps.series;
  const says = c.always.says;
  const rows = [
    { label: "Learned model", auc: c.model.auc, lo: c.model.auc_ci[0], hi: c.model.auc_ci[1], color: "var(--mark)" },
    ...(c.factor ? [{ label: "Factor Score", auc: c.factor.auc, lo: c.factor.auc_ci[0], hi: c.factor.auc_ci[1], color: "var(--hold)" }] : []),
  ];

  return (
    <>
      <Card className={`verdict-card ${TONE_CLASS[d.tone]}`}>
        <div className="verdict-main">
          <div>
            <div className="kicker">Can a model call next month? · tested {shortDate(g.test_start)} to {shortDate(g.test_end)}</div>
            <h2>{d.headline}</h2>
            <p className="prose">{d.detail}</p>
          </div>
          <div className="verdict-num">{num(c.model.auc, 2)}<small>AUC, out of sample. A coin flip scores 0.50.</small></div>
        </div>
        <AucRuler rows={rows} />
      </Card>

      <Card kicker="The scoreboard" title="Three ways to guess next month, graded on the same days">
        <div className="table-scroll">
          <table className="ledger">
            <thead><tr><th>Contender</th><th>Accuracy</th><th>AUC</th><th>90% range</th><th className="wide">What it is</th></tr></thead>
            <tbody>
              <tr><td>Always say "{says}"</td><td className="n">{frac(c.always.accuracy, 0)}</td><td className="n">0.50</td><td className="n">—</td>
                <td className="wide">No model at all. Its accuracy is just how often months went {says}.</td></tr>
              {c.factor && <tr><td>Our Factor Score</td><td className="n">{frac(c.factor.accuracy, 0)}</td><td className="n">{num(c.factor.auc, 2)}</td>
                <td className="n">{num(c.factor.auc_ci[0], 2)}–{num(c.factor.auc_ci[1], 2)}</td>
                <td className="wide">Hand-set weights on trend, RSI, bands and volume. Never trained on outcomes.</td></tr>}
              <tr className="total"><td>Learned model</td><td className="n">{frac(c.model.accuracy, 0)}</td><td className="n">{num(c.model.auc, 2)}</td>
                <td className="n">{num(c.model.auc_ci[0], 2)}–{num(c.model.auc_ci[1], 2)}</td>
                <td className="wide">{g.n_features} features from the Stanford paper, weights learned from the past, {g.penalty} penalty, retrained every month.</td></tr>
            </tbody>
          </table>
        </div>
        <p className="note">
          Read AUC, not accuracy. AUC asks: take one month that went up and one that went down at random; how often did the
          contender rate the up-month higher? 0.50 means it cannot tell them apart. The range is wide because {g.independent_months} months
          is not much evidence, and when it straddles 0.50 there is no edge to speak of.
        </p>
      </Card>

      <div className="grid-2">
        <Card kicker="Today's call" title={`${frac(d.today.prob, 0)} chance the next month finishes up`}>
          <p className="prose">
            The model's usual answer, the share of up-months it trained on, is {frac(d.today.base_rate, 0)}. Today it is
            {d.today.prob > d.today.base_rate ? " more" : " less"} optimistic than usual, so the strategy below would
            be <b>{d.today.invested ? "holding the stock" : "in cash"}</b>.
          </p>
          <p className="note warn">Weigh this by the scoreboard. A call from a model that grades as a coin flip is a coin flip with decimals.</p>
        </Card>
        <Card kicker="What it learned" title="All 69 weights, in plain sight">
          <HeatGrid grid={d.coefficients} windows={d.windows} />
          <p className="note">{g.n_used} of {g.n_features} features carry weight after the {g.penalty} penalty (strength {g.strength}). Weights are per standard deviation of each feature, so they compare directly.</p>
        </Card>
      </div>

      <Card kicker="If you had traded it" title="Hold the stock only when the model is more optimistic than usual">
        <LineChart height={280} ariaLabel="Growth of one dollar: model strategy against buy and hold"
          series={[
            { label: "Model strategy", color: "var(--mark)", points: eq.map((r, i) => ({ x: i, y: r.strategy })) },
            { label: "Buy and hold", color: "var(--ink-3)", points: eq.map((r, i) => ({ x: i, y: r.hold })), thin: true },
          ]}
          xFormat={(i) => shortDate(eq[Math.round(i)].t)} yFormat={(y) => `${num(y, 2)}×`} hGuides={[{ at: 1, label: "start" }]} />
        <div className="table-scroll">
          <table className="ledger">
            <thead><tr><th /><th>Per year</th><th>Volatility</th><th>Sharpe</th><th>Worst fall</th></tr></thead>
            <tbody>
              {(["strategy", "hold"] as const).map((k) => (
                <tr key={k}><td>{k === "strategy" ? "Model strategy" : "Buy and hold"}</td>
                  <td className="n">{frac(s[k].cagr, 1, true)}</td><td className="n">{frac(s[k].vol, 0)}</td>
                  <td className="n">{num(s[k].sharpe, 2)}</td><td className="n">{frac(s[k].max_drawdown, 0)}</td></tr>
              ))}
              <tr><td>Random days, same time invested</td><td className="n">{frac(s.random_cagr, 1, true)}</td>
                <td className="n">—</td><td className="n">—</td><td className="n">—</td></tr>
            </tbody>
          </table>
        </div>
        <p className="note warn">
          Before crediting the model: over {num(s.years, 0)} years a Sharpe ratio is only known to about ±{num(s.sharpe_se, 2)},
          so a gap smaller than that is noise. And sitting out a falling stock {frac(1 - s.time_invested, 0)} of the time helps
          whatever the reason for sitting out; the random-days row shows what that alone earns.
        </p>
        <p className="note">
          Invested {frac(s.time_invested, 0)} of the time, {s.switches} trades, {frac(s.cost, 2)} paid on each. Cash earns nothing here.
          A strategy that sits out half the time should have less volatility and shallower falls; the question is whether it gives
          up more return than that is worth. Sharpe (return per unit of volatility) is the fair comparison.
        </p>
      </Card>

      <Card kicker="The chart that fools people" title={`"Predicted" price that just copies yesterday's: R² = ${frac(ps.r2_level, 1)}`}>
        <LineChart height={260} ariaLabel="Real price against yesterday's price used as a forecast"
          series={[
            { label: "Real price", color: "var(--mark)", points: series.map((r, i) => ({ x: i, y: r.actual })) },
            { label: "\"Forecast\" = yesterday's price", color: "var(--hold)", points: series.map((r, i) => ({ x: i, y: r.guess })), thin: true },
          ]}
          xFormat={(i) => shortDate(series[Math.round(i)].t)} yFormat={(y) => num(y, y < 10 ? 3 : 1)} />
        <div className="grid-3 tight">
          <Stat label="Explains the price level" value={frac(ps.r2_level, 1)} note="Looks like a superb model" />
          <Stat label="Explains the daily move" value={frac(ps.r2_move, 1)} note="The only part worth money" tone="down" />
          <Stat label="Average miss" value={`${num(ps.mean_abs_error_pct, 1)}%`} note="Exactly one day's typical move" />
        </div>
        <p className="note">
          One of the two Stanford projects shows an LSTM's predicted price hugging the real one. The second line here does the same
          with no model at all, because tomorrow's price is always close to today's. Before believing any predicted-versus-actual
          price chart, ask for the R² on the change, not the level.
        </p>
      </Card>

      <Card kicker="How it was built" title="The Stanford method, with three leaks closed">
        <Formula>P(up) = σ( b + w₁x₁ + … + w₆₉x₆₉ ),&nbsp;&nbsp; σ(z) = 1 / (1 + e<sup>−z</sup>)</Formula>
        <ul className="plain">
          <li><b>Features.</b> Past return, volatility and volume over 23 look-backs (1, 2, 3, then every 4th week to 80): 3 × 23 = 69.</li>
          <li><b>Label.</b> Did the next 21 trading days finish up?</li>
          <li><b>Walk-forward.</b> Train on everything before a date, call the next month, slide forward one month, retrain. Never k-fold, which would let 2015 help predict 2012.</li>
          <li><b>Purge.</b> A training month whose 21-day outcome was still running on the decision date is dropped. The paper does not say it did this.</li>
          <li><b>Scaling on the past only.</b> The paper standardises with the whole sample's mean and spread, which quietly tells 1995 about 2020.</li>
          <li><b>Volume relative to its own year</b>, because raw volume grows for decades and jumps at splits.</li>
          <li><b>Penalty.</b> Ridge, lasso or elastic net, chosen on development months before the test began, by AUC, as the paper did.</li>
          <li><b>Why not the LSTM?</b> In the paper's own table it matched this plain baseline (test AUC 0.46–0.55 against 0.48–0.55). The baseline is the part that carried the result, and every weight can be shown.</li>
        </ul>
      </Card>
    </>
  );
}
