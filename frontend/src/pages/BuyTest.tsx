import { useEffect, useState } from "react";
import { post } from "../api";
import { Card, Formula, Loading, Problem, Stat, useLoad } from "../components/Bits";
import LineChart from "../components/Chart";
import { compact, frac, money, num } from "../format";

const BROKERS: Record<string, { commission_pct: number; commission_min: number; spread_pct: number; fx_pct: number }> = {
  "Low-cost broker": { commission_pct: 0.08, commission_min: 1, spread_pct: 0.03, fx_pct: 0 },
  "Typical retail broker": { commission_pct: 0.25, commission_min: 10, spread_pct: 0.05, fx_pct: 0.3 },
  "Local broker, higher minimum": { commission_pct: 0.28, commission_min: 25, spread_pct: 0.05, fx_pct: 0.5 },
  "Zero-commission app": { commission_pct: 0, commission_min: 0, spread_pct: 0.1, fx_pct: 0.5 },
};

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

function NumberField({ label, value, onChange, min, max, step }: {
  label: string; value: number; onChange: (v: number) => void; min: number; max: number; step: number;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" inputMode="decimal" value={value} min={min} max={max} step={step}
        onChange={(e) => { const v = Number(e.target.value); if (Number.isFinite(v)) onChange(v); }} />
    </label>
  );
}

export default function BuyTest({ ticker, currency }: { ticker: string; currency: string }) {
  const [notional, setNotional] = useState(2000);
  const [portfolio, setPortfolio] = useState(20000);
  const [broker, setBroker] = useState("Low-cost broker");
  const [horizon, setHorizon] = useState(21);

  const valid = notional > 0 && portfolio > 0;
  const body = useDebounced({ notional, portfolio, horizon_days: horizon, tax_rate: 0, ...BROKERS[broker] }, 350);
  const key = valid ? `${ticker}:${JSON.stringify(body)}` : null;
  const { data: d, error, loading } = useLoad(key, (s) => post(`/stock/${encodeURIComponent(ticker)}/buytest`, body, s));

  const o = d?.odds;
  const cash = (v: number) => money(v, currency);

  return (
    <>
      <Card kicker="Your trade" title="Tell it what you would actually do">
        <div className="fields">
          <NumberField label={`Trade size (${currency})`} value={notional} onChange={setNotional} min={50} max={10_000_000} step={100} />
          <NumberField label={`Portfolio (${currency})`} value={portfolio} onChange={setPortfolio} min={100} max={1_000_000_000} step={1000} />
          <label className="field">
            <span>Broker</span>
            <select value={broker} onChange={(e) => setBroker(e.target.value)}>
              {Object.keys(BROKERS).map((b) => <option key={b}>{b}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Holding period</span>
            <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
              <option value={10}>2 weeks</option><option value={21}>1 month</option><option value={63}>3 months</option>
            </select>
          </label>
        </div>
      </Card>

      {error && <Problem message={error} />}
      {!d && loading && <Loading label="Measuring the odds" />}
      {d && (
        <div className={loading ? "stale" : ""}>
          <Card className={`verdict-card ${d.verdict === "buy" ? "positive" : d.verdict === "no" ? "negative" : "neutral"}`}>
            <div className="verdict-main">
              <div>
                <div className="kicker">The buy test</div>
                <h2>{d.headline}</h2>
                <ul className="reasons">{d.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
              </div>
              {o && (
                <div className="verdict-num">
                  {frac(d.stake_fraction, 1)}
                  <small>{d.stake_cash > 0 ? `${cash(d.stake_cash)} of your portfolio` : "suggested stake"}</small>
                </div>
              )}
            </div>
          </Card>

          {o && (
            <>
              <div className="grid-4">
                <Stat label="Won" value={frac(o.hit_rate, 0)} note={`of past days like today, vs ${frac(o.baseline_hit_rate, 0)} on any day`} />
                <Stat label="Average win" value={frac(o.avg_win, 1, true)} tone="up" />
                <Stat label="Average loss" value={`−${frac(o.avg_loss, 1)}`} tone="down" />
                <Stat label="Independent tests" value={num(o.n_windows, 0)} note={`from ${num(o.n_rows, 0)} overlapping days; ${d.min_windows} needed`} />
              </div>

              <Card kicker="The working" title="Every step, with your numbers in it">
                <Formula>p* = L<sub>net</sub> / ( W<sub>net</sub> + L<sub>net</sub> ) &nbsp;&nbsp;·&nbsp;&nbsp; f* = ( p·W<sub>net</sub> − (1−p)·L<sub>net</sub> ) / ( W<sub>net</sub>·L<sub>net</sub> )</Formula>
                <table className="ledger">
                  <thead><tr><th>Step</th><th>Value</th><th className="wide">What it means</th></tr></thead>
                  <tbody>
                    <tr><td>Round-trip cost</td><td className="n">{frac(d.cost_fraction, 2)}</td><td className="wide">Fees, spread and FX to get in and out: {cash(d.cost_fraction * notional)} on this trade.</td></tr>
                    <tr><td>Win after costs</td><td className="n">{frac(d.w_net, 2, true)}</td><td className="wide">Costs eat {frac(d.cost_share_of_win, 0)} of the average win.</td></tr>
                    <tr><td>Loss after costs</td><td className="n">−{frac(d.l_net, 2)}</td><td className="wide">Costs are paid on losers too.</td></tr>
                    <tr><td>Break-even hit rate</td><td className="n">{frac(d.breakeven, 1)}</td><td className="wide">You must be right this often just to end flat.</td></tr>
                    <tr><td>Measured hit rate</td><td className="n">{frac(o.hit_rate, 1)}</td><td className="wide">What history showed, on a small sample.</td></tr>
                    <tr><td>Cautious hit rate</td><td className="n">{frac(d.hit_lower, 1)}</td><td className="wide">Shrunk toward doubt for the sample size (Wilson lower bound).</td></tr>
                    <tr><td>Edge per trade</td><td className="n">{frac(d.edge_lower, 2, true)}</td><td className="wide">Expected gain using the cautious hit rate.</td></tr>
                    <tr><td>Kelly stake</td><td className="n">{frac(d.kelly_shrunk, 1)}</td><td className="wide">The growth-maximising bet at the cautious hit rate. Zero or below means do not bet.</td></tr>
                    <tr className="total"><td>Suggested stake</td><td className="n">{frac(d.stake_fraction, 1)}</td><td className="wide">A quarter of Kelly, capped at {frac(d.max_weight, 0)} of the portfolio.</td></tr>
                  </tbody>
                </table>
              </Card>

              <div className="grid-2">
                <Card kicker="Size is the decision" title="Small trades need to be right more often">
                  <LineChart height={250} logX ariaLabel="Break-even hit rate against trade size"
                    series={[{ label: "Break-even hit rate", color: "var(--mark)", points: d.breakeven_curve }]}
                    xFormat={(x) => compact(x, currency)} yFormat={(y) => frac(y, 0)}
                    xTicks={[100, 1000, 10000, 50000]}
                    hGuides={[{ at: o.hit_rate, label: `measured ${frac(o.hit_rate, 0)}`, tone: "accent" }]}
                    vGuides={notional >= 100 && notional <= 50000 ? [{ at: notional, label: "your trade" }] : []} />
                  <p className="note">A flat minimum fee is a bigger slice of a small trade. Where the curve sits above the dashed line, history says the trade loses money on average.</p>
                </Card>
                <Card kicker="Why not bet more" title="How fast money compounds at each stake size">
                  {d.growth_curve.length ? (
                    <>
                      <LineChart height={250} ariaLabel="Long-run growth rate against stake size"
                        series={[{ label: "Growth per trade", color: "var(--mark)", points: d.growth_curve }]}
                        xFormat={(x) => frac(x, 0)} yFormat={(y) => frac(y, 2)}
                        hGuides={[{ at: 0, label: "no growth" }]}
                        vGuides={d.stake_fraction > 0 ? [{ at: d.stake_fraction, label: "suggested" }] : []} />
                      <p className="note">
                        {d.kelly_raw > d.growth_curve[d.growth_curve.length - 1].x
                          ? `Taken at face value, the measured odds put the peak off this chart at ${frac(d.kelly_raw, 0)} of the portfolio. That is the formula recommending leverage on ${num(o.n_windows, 0)} data points, which is exactly why the suggested stake uses the cautious hit rate instead.`
                          : "Past the peak, a bigger bet makes you poorer over time even with the odds in your favour. The suggested stake sits well to the left on purpose."}
                      </p>
                    </>
                  ) : <p className="prose muted">No stake grows your money at these odds, so there is no curve to draw. Every bet size loses on average.</p>}
                </Card>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
