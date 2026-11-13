import { useState } from "react";
import { go } from "../App";

const VALID = /^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$/;

export default function Search({ big, tab = "overview" }: { big?: boolean; tab?: string }) {
  const [value, setValue] = useState("");
  const [bad, setBad] = useState(false);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const ticker = value.trim().toUpperCase();
    if (!VALID.test(ticker)) { setBad(true); return; }
    go(`/s/${encodeURIComponent(ticker)}/${tab}`);
    setValue("");
  };
  return (
    <form className={`search ${big ? "big" : ""} ${bad ? "bad" : ""}`} onSubmit={submit} role="search">
      <input value={value} onChange={(e) => { setValue(e.target.value); setBad(false); }}
        placeholder={big ? "Type a ticker — NVDA, AAPL, D05.SI" : "Ticker"} aria-label="Ticker symbol"
        autoCapitalize="characters" autoComplete="off" spellCheck={false} maxLength={12} />
      <button type="submit">{big ? "Lift the veil" : "Go"} <span aria-hidden>→</span></button>
      {bad && <p className="search-hint" role="alert">Enter a ticker symbol like NVDA or D05.SI.</p>}
    </form>
  );
}
