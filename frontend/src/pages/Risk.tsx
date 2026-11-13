import { get, type Json } from "../api";
import { Card, Loading, Problem, Stat, useLoad } from "../components/Bits";
import { Bars } from "../components/Chart";
import { money, num, pct } from "../format";

export default function Risk({ ticker, currency }: { ticker: string; currency: string }) {
  const { data: d, error, loading } = useLoad(ticker, (s) => get(`/stock/${encodeURIComponent(ticker)}/risk`, s));
  if (error) return <Problem message={error} />;
  if (loading || !d) return <Loading label="Measuring the tails" />;

  const cash = (v: number) => money(v, currency, 0);
  const cutoff = -d.historical_var_pct;
  return (
    <>
      <Card className="verdict-card negative">
        <div className="verdict-main">
          <div>
            <div className="kicker">One bad day in twenty · {cash(d.position)} position</div>
            <h2>Expect to lose {cash(d.historical_var_money)} or more</h2>
            <p className="prose">
              That is the 95% one-day Value at Risk from the last {d.days} trading days. When that
              line is crossed, the average loss is <b>{cash(d.expected_shortfall_money)}</b>. The second
              number matters more, because it is the one that describes the days that hurt.
            </p>
          </div>
          <div className="verdict-num">−{pct(d.historical_var_pct, 1)}<small>1-day VaR, 95%</small></div>
        </div>
      </Card>

      <div className="grid-4">
        <Stat label="Historical VaR" value={`−${pct(d.historical_var_pct)}`} note="Straight from past days" />
        <Stat label="Bell-curve VaR" value={`−${pct(d.gaussian_var_pct)}`} note="Assumes normal returns; usually too calm" />
        <Stat label="Fat-tail VaR" value={d.cornish_fisher_valid ? `−${pct(d.cornish_fisher_var_pct)}` : "n/a"} note="Cornish-Fisher correction" />
        <Stat label="Expected shortfall" value={`−${pct(d.expected_shortfall_pct)}`} note="Average loss past the line" tone="down" />
      </div>

      <Card kicker="The shape of risk" title="Daily returns, last two years">
        <Bars height={240} ariaLabel="Histogram of daily returns" format={(v) => `${num(v, 0)} days`}
          items={d.histogram.map((b: Json) => ({
            label: "", value: b.n, tip: `${pct(b.x0, 1, true)} to ${pct(b.x1, 1, true)}`,
            color: b.x1 <= cutoff ? "var(--avoid)" : "var(--mark)",
          }))} />
        <div className="legend static"><span><i style={{ background: "var(--mark)" }} />Ordinary days</span><span><i style={{ background: "var(--avoid)" }} />Worst 5% of days</span></div>
        <div className="grid-4 tight">
          <Stat label="Volatility" value={pct(d.vol_annual_pct, 0)} note="Annualised" />
          <Stat label="Worst day" value={pct(d.worst_day_pct, 1)} tone="down" />
          <Stat label="Skew" value={num(d.skew, 2)} note={d.skew < 0 ? "Big moves lean down" : "Big moves lean up"} />
          <Stat label="Excess kurtosis" value={num(d.excess_kurtosis, 1)} note={d.normality_rejected ? "Fatter tails than a bell curve" : "Close to a bell curve"} />
        </div>
        <p className="note">Multi-day risk is not simply this times the square root of time: volatility clusters, so bad days arrive together.</p>
      </Card>
    </>
  );
}
