import { useState } from "react";
import { get, type Json } from "../api";
import { Card, Delta, DemoBanner, Loading, Problem, Stat, useLoad } from "../components/Bits";
import LineChart from "../components/Chart";
import Search from "../components/Search";
import { compact, money, num, pct, shortDate } from "../format";
import Boardroom from "./Boardroom";
import BuyTest from "./BuyTest";
import Factor from "./Factor";
import Lab from "./Lab";
import Risk from "./Risk";

const TABS = [
  ["overview", "Overview"], ["factor", "Factor Score"], ["buytest", "Buy Test"],
  ["lab", "Forecast Lab"], ["boardroom", "Boardroom"], ["risk", "Risk"],
];
const RANGES = ["1M", "3M", "6M", "1Y", "5Y"];

export default function Stock({ ticker, tab: wanted, config }: { ticker: string; tab: string; config: Json }) {
  const tab = TABS.some(([id]) => id === wanted) ? wanted : "overview";
  const [range, setRange] = useState("1Y");
  const path = encodeURIComponent(ticker);
  const { data, error, loading } = useLoad(`${ticker}:${range}`, (s) => get(`/stock/${path}?range=${range}`, s));

  return (
    <div className="stock">
      {data?.demo && <DemoBanner />}
      <div className="stock-head">
        <div>
          <div className="crumbs">{data?.sector ?? "Security"}{data?.industry ? ` · ${data.industry}` : ""}</div>
          <h1>{ticker}</h1>
          <div className="stock-name">{data?.name ?? " "}</div>
        </div>
        {data && (
          <div className="quote">
            <div className="quote-price">{money(data.price, data.currency)}</div>
            <Delta value={data.change} text={`${money(Math.abs(data.change), data.currency)} (${pct(Math.abs(data.change_pct), 2)}) today`} />
            <div className="quote-asof">Last close {shortDate(data.as_of)} · delayed</div>
          </div>
        )}
        <Search />
      </div>

      <div className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <a key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""}
            href={`#/s/${path}/${id}`}>{label}{id === "boardroom" && <span className="tab-spark" aria-hidden />}</a>
        ))}
      </div>

      {error && <Problem message={error} />}
      {tab === "overview" && (loading && !data ? <Loading label="Pulling prices" /> : data && (
        <Overview data={data} range={range} setRange={setRange} />
      ))}
      {tab === "factor" && <Factor ticker={ticker} />}
      {tab === "buytest" && <BuyTest ticker={ticker} currency={data?.currency ?? "USD"} />}
      {tab === "lab" && <Lab ticker={ticker} />}
      {tab === "boardroom" && <Boardroom ticker={ticker} config={config} />}
      {tab === "risk" && <Risk ticker={ticker} currency={data?.currency ?? "USD"} />}
    </div>
  );
}

function Overview({ data, range, setRange }: { data: Json; range: string; setRange: (r: string) => void }) {
  const s = data.stats;
  const rows: Json[] = data.series;
  const line = (key: string) => rows.map((r, i) => ({ x: i, y: r[key] as number | null }));
  const long = range === "1Y" || range === "5Y";
  const series = [
    { label: "Close", color: "var(--mark)", points: line("c") },
    ...(long ? [
      { label: "50-day average", color: "var(--ink-3)", points: line("sma50"), thin: true },
      { label: "200-day average", color: "var(--ink-3)", points: line("sma200"), thin: true, dashed: true },
    ] : []),
  ];
  const spot = (data.price - s.low_52w) / (s.high_52w - s.low_52w || 1);

  return (
    <>
      <Card className="chart-card">
        <div className="chart-head">
          <div>
            <div className="kicker">Price, {range}</div>
            <Delta value={data.range_change_pct} text={`${pct(Math.abs(data.range_change_pct))} over the period`} />
          </div>
          <div className="seg" role="group" aria-label="Chart range">
            {RANGES.map((r) => (
              <button key={r} className={r === range ? "on" : ""} aria-pressed={r === range} onClick={() => setRange(r)}>{r}</button>
            ))}
          </div>
        </div>
        <LineChart series={series} area height={340} ariaLabel={`${data.ticker} closing price, ${range}`}
          xFormat={(i) => shortDate(rows[Math.round(i)].t)} yFormat={(y) => num(y, y < 10 ? 3 : 2)} />
      </Card>

      <div className="grid-4">
        <Stat label="Market cap" value={compact(s.market_cap, data.currency)} />
        <Stat label="P/E (trailing)" value={num(s.trailing_pe, 1)} note={s.forward_pe ? `${num(s.forward_pe, 1)} forward` : undefined} />
        <Stat label="Volatility" value={pct(s.vol_annual_pct, 0)} note="Annualised, past year" />
        <Stat label="Worst fall" value={pct(s.max_drawdown_pct, 0)} note="Peak to trough, past year" tone="down" />
      </div>

      <div className="grid-2">
        <Card kicker="52-week range" title="Where the price sits">
          <div className="range-bar"><i style={{ left: `${Math.min(Math.max(spot, 0), 1) * 100}%` }} /></div>
          <div className="range-ends">
            <span>{money(s.low_52w, data.currency)}<small>low</small></span>
            <span>{money(s.high_52w, data.currency)}<small>high</small></span>
          </div>
        </Card>
        <Card kicker="The street" title="What analysts publish">
          {s.analyst_target ? (
            <p className="prose">
              {s.analyst_count ?? "Several"} analysts carry a mean target of <b>{money(s.analyst_target, data.currency)}</b>,{" "}
              {pct((s.analyst_target / data.price - 1) * 100, 0, true)} from here
              {s.analyst_view ? <>, with a consensus of <b>{String(s.analyst_view).replace("_", " ")}</b></> : null}.
              Targets are opinions and they trail the price more often than they lead it.
            </p>
          ) : <p className="prose muted">No analyst targets are published for this ticker.</p>}
          <a className="cta" href={`#/s/${encodeURIComponent(data.ticker)}/boardroom`}>Hear our room argue it <span aria-hidden>→</span></a>
        </Card>
      </div>
    </>
  );
}
