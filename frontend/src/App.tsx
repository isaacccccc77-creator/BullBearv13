import { useEffect, useState } from "react";
import { get } from "./api";
import { useLoad } from "./components/Bits";
import { direction, num, pct } from "./format";
import Deals from "./pages/Deals";
import Home from "./pages/Home";
import Stock from "./pages/Stock";

function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => { setRoute(read()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const go = (path: string) => { window.location.hash = path; };

function Tape() {
  const { data } = useLoad("tape", (s) => get("/tape", s));
  const items: { symbol: string; label: string; price: number; change_pct: number }[] = data?.items ?? [];
  if (!items.length) return null;
  const run = (hidden: boolean) => (
    <div className="tape-run" aria-hidden={hidden}>
      {items.map((i) => (
        <span key={i.symbol} className="tape-item">
          <b>{i.label}</b> {num(i.price, i.price < 10 ? 4 : 2)}
          <em className={direction(i.change_pct)}>{pct(i.change_pct, 2, true)}</em>
        </span>
      ))}
    </div>
  );
  return (
    <div className="tape" aria-label={data.demo ? "Market tape, demo data" : "Market tape"}>
      {data.demo && <span className="tape-flag">DEMO</span>}
      <div className="tape-track">{run(false)}{run(true)}</div>
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const config = useLoad("config", (s) => get("/config", s)).data;
  const page = route[0] ?? "";

  return (
    <>
      <div className="backdrop" aria-hidden><i /><i /><i /></div>
      <Tape />
      <header className="nav">
        <a className="brand" href="#/">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden>
            <rect width="32" height="32" rx="9" fill="var(--lime)" />
            <path d="M6 21l6-7 5 4 9-10" fill="none" stroke="#0B0A12" strokeWidth="3.2"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Tickveil
        </a>
        <nav>
          <a href="#/" className={page === "" ? "on" : ""}>Home</a>
          <a href="#/deals" className={page === "deals" ? "on" : ""}>Deal Room</a>
        </nav>
      </header>
      <main>
        {page === "s" && route[1]
          ? <Stock key={route[1]} ticker={decodeURIComponent(route[1]).toUpperCase()} tab={route[2] ?? "overview"} config={config} />
          : page === "deals" ? <Deals /> : <Home config={config} />}
      </main>
      <footer>
        <p>
          <b>Tickveil is an education and research tool, not investment advice.</b> Scores and
          odds describe the past and do not predict the future. The Boardroom's analysts are AI
          personas, not people; they can be wrong and can misread the news. Market data is
          delayed. Decide with your own judgement or a licensed adviser.
        </p>
      </footer>
    </>
  );
}
