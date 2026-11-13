import { useEffect, useState } from "react";
import { post, type Json } from "../api";
import { Card, Loading, Problem, Stat, useLoad } from "../components/Bits";
import { Bars } from "../components/Chart";
import { compact, frac, num } from "../format";

const SLIDERS: [string, string, number, number, number, (v: number) => string][] = [
  ["entry_multiple", "Entry multiple", 4, 16, 0.5, (v) => `${num(v, 1)}× EBITDA`],
  ["debt_turns", "Debt", 0, 8, 0.25, (v) => `${num(v, 2)}× EBITDA`],
  ["interest_rate", "Interest rate", 0.03, 0.16, 0.005, (v) => frac(v, 1)],
  ["revenue_growth", "Revenue growth", -0.05, 0.2, 0.005, (v) => frac(v, 1)],
  ["margin_improvement", "Margin gain per year", -0.01, 0.03, 0.0025, (v) => `${num(v * 100, 2)} pts`],
  ["exit_multiple", "Exit multiple", 4, 16, 0.5, (v) => `${num(v, 1)}× EBITDA`],
  ["hold_years", "Hold period", 3, 8, 1, (v) => `${v} years`],
];

const DEFAULTS: Record<string, number> = {
  entry_multiple: 9, debt_turns: 4.5, interest_rate: 0.09, revenue_growth: 0.04,
  margin_improvement: 0.005, exit_multiple: 9, hold_years: 5,
};

export default function Deals() {
  const [inputs, setInputs] = useState(DEFAULTS);
  const [settled, setSettled] = useState(DEFAULTS);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(inputs), 200);
    return () => clearTimeout(timer);
  }, [inputs]);
  const { data: d, error, loading } = useLoad(JSON.stringify(settled), (s) => post("/deals/lbo", settled, s));

  const m = (v: number) => compact(v, "USD");
  return (
    <div className="stock">
      <div className="stock-head">
        <div>
          <div className="crumbs">Private equity</div>
          <h1>Deal Room</h1>
          <div className="stock-name">A leveraged buyout of a company earning $100M EBITDA on $500M revenue. Move the levers.</div>
        </div>
      </div>

      <div className="deal-layout">
        <Card kicker="Assumptions" title="The levers">
          {SLIDERS.map(([key, label, min, max, step, show]) => (
            <label key={key} className="slider">
              <span>{label}<b>{show(inputs[key])}</b></span>
              <input type="range" min={min} max={max} step={step} value={inputs[key]}
                onChange={(e) => setInputs({ ...inputs, [key]: Number(e.target.value) })} />
            </label>
          ))}
          <button className="ghost" onClick={() => setInputs(DEFAULTS)}>Reset</button>
        </Card>

        <div>
          {error && <Problem message={error} />}
          {!d && loading && <Loading label="Running the model" />}
          {d && (
            <div className={loading ? "stale" : ""}>
              <Card className={`verdict-card ${d.equity_wiped ? "negative" : d.irr >= 0.2 ? "positive" : "neutral"}`}>
                <div className="verdict-main">
                  <div>
                    <div className="kicker">Sponsor returns</div>
                    <h2>{d.equity_wiped ? "The equity is wiped out" : `${num(d.moic, 2)}× money in ${settled.hold_years} years`}</h2>
                    <p className="prose">{d.verdict}</p>
                  </div>
                  <div className="verdict-num">{d.irr === null ? "n/a" : frac(d.irr, 1)}<small>IRR per year</small></div>
                </div>
              </Card>
              <div className="grid-4">
                <Stat label="Purchase price" value={m(d.entry_ev)} />
                <Stat label="Equity cheque" value={m(d.entry_equity)} note={`${m(d.sources["New debt"])} borrowed`} />
                <Stat label="Exit value" value={m(d.exit_ev)} />
                <Stat label="Equity at exit" value={m(d.exit_equity)} tone={d.exit_equity >= d.entry_equity ? "up" : "down"} />
              </div>
              <Card kicker="Returns bridge" title="Where the profit came from">
                <Bars height={200} ariaLabel="Value creation by source" format={m}
                  items={Object.entries(d.bridge as Record<string, number>).map(([label, value]) => ({
                    label, value, color: value >= 0 ? "var(--buy)" : "var(--avoid)" }))} />
                <div className="bridge-labels">
                  {Object.entries(d.bridge as Record<string, number>).map(([label, value]) => (
                    <span key={label}>{label}<b>{m(value)}</b></span>
                  ))}
                </div>
                <p className="note">Growth is earned. Multiple change is a bet on the market's mood at exit. Debt paydown is the leverage doing its work.</p>
                {d.covenant_breach_year && <p className="note warn">Leverage breaches a typical covenant in year {d.covenant_breach_year}. Lenders would be at the table.</p>}
              </Card>
              <Card kicker="Year by year" title="The schedule">
                <div className="table-scroll">
                  <table className="ledger">
                    <thead><tr><th>Year</th><th>Revenue</th><th>EBITDA</th><th>Free cash</th><th>Debt left</th><th>Leverage</th></tr></thead>
                    <tbody>
                      {d.schedule.map((r: Json) => (
                        <tr key={r.Year}>
                          <td>{r.Year}</td><td className="n">{m(r.Revenue)}</td><td className="n">{m(r.EBITDA)}</td>
                          <td className="n">{m(r["Free cash flow"])}</td><td className="n">{m(r["Ending debt"])}</td>
                          <td className="n">{num(r["Leverage (ND/EBITDA)"], 1)}×</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
