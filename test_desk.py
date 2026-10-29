"""
Tests for the Research Desk.

Run with:  python test_desk.py

Like test_scoring.py these check properties, not golden numbers: the gate
cannot see the future, it rejects pure noise, it lets a real planted signal
through, and the Chief rules from the formula rather than from whichever
advocate is louder.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desk  # noqa: E402
import scoring  # noqa: E402

failures = []


def expect(label, ok, detail=""):
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)


def synthetic_ohlcv(n=900, seed=3, drift=0.0003, vol=0.015, returns=None):
    rng = np.random.default_rng(seed)
    r = returns if returns is not None else rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.date_range("2022-01-03", periods=len(close), freq="B")
    op = close * (1 + rng.normal(0, 0.003, len(close)))
    hi = np.maximum(op, close) * (1 + abs(rng.normal(0, 0.005, len(close))))
    lo = np.minimum(op, close) * (1 - abs(rng.normal(0, 0.005, len(close))))
    vol_ = rng.integers(1_000_000, 5_000_000, len(close)).astype(float)
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol_}, index=idx)


# ----------------------------------------------------------------------
print("\nNo look-ahead")
df = synthetic_ohlcv()
score = pd.Series(np.random.default_rng(1).normal(0, 1, len(df)), index=df.index)
frame = desk.gate_strategy_returns(score, df["Close"], 0.5, 0.0)
expected_pos = (score >= 0.5).astype(float).shift(1).fillna(0.0).reindex(frame.index)
expect("position uses yesterday's score", (frame["pos"] == expected_pos).all())

tampered = df["Close"].copy()
tampered.iloc[-50:] *= 3.0
frame_t = desk.gate_strategy_returns(score, tampered, 0.5, 0.0)
cut = frame.index[-51]
expect("changing future prices leaves past positions untouched",
       (frame.loc[:cut, "pos"] == frame_t.loc[:cut, "pos"]).all())

# ----------------------------------------------------------------------
print("\nCosts")
free = desk.gate_strategy_returns(score, df["Close"], 0.5, 0.0)["strat"].sum()
costly = desk.gate_strategy_returns(score, df["Close"], 0.5, 25.0)["strat"].sum()
expect("trading costs reduce strategy returns", costly < free, f"{costly:.4f} vs {free:.4f}")

# ----------------------------------------------------------------------
print("\nGate rejects noise (realistic score on a random walk)")
passes = 0
trials = 30
for seed in range(trials):
    d = synthetic_ohlcv(seed=100 + seed)
    tech, _ = scoring.technical_score(d)
    g = desk.backtest_gate(tech, d["Close"])
    passes += bool(g.metrics.get("passed"))
rate = passes / trials
expect("false pass rate on pure noise stays low (<= 15%)", rate <= 0.15, f"{passes}/{trials} passed")

# ----------------------------------------------------------------------
print("\nGate lets a genuine planted signal through")
rng = np.random.default_rng(7)
n = 900
r = rng.normal(0.0002, 0.015, n)
d = synthetic_ohlcv(returns=r, seed=7)
future = pd.Series(r, index=d.index).shift(-1)            # tomorrow's return
planted = (future / 0.015 + rng.normal(0, 0.8, n)).fillna(0)  # knows tomorrow, noisily
g = desk.backtest_gate(planted, d["Close"])
expect("planted predictive score passes", g.metrics.get("passed"),
       str([(c[0], c[1], c[2]) for c in g.metrics.get("checks", [])]))

# ----------------------------------------------------------------------
print("\nShort history is blocked, not guessed")
short = synthetic_ohlcv(n=200)
tech_s, _ = scoring.technical_score(short)
g_short = desk.backtest_gate(tech_s, short["Close"])
expect("gate blocks when out-of-sample slice is too short", g_short.metrics.get("passed") is False)

# ----------------------------------------------------------------------
print("\nThe Chief rules from the formula, not the loudest advocate")
pass_gate = desk.AgentReport("Backtest Gate", "", "bull", "", [], {"passed": True})
fail_gate = desk.AgentReport("Backtest Gate", "", "bear", "", [], {"passed": False})
risk = desk.AgentReport("Risk Manager", "", "neutral", "", [],
                        {"position_pct": 8.0, "price": 100.0, "stop_price": 94.0})
loud_bear = desk.AgentReport("Bear Advocate", "", "bear", "", [], {"strength": 9.0})
quiet_bull = desk.AgentReport("Bull Advocate", "", "bull", "", [], {"strength": 0.1})

pos = {"final": 1.2, "reading": "Strongly positive conditions"}
neg = {"final": -0.9, "reading": "Mildly negative conditions"}
flat = {"final": 0.1, "reading": "Mixed / no clear tilt"}

r1 = desk.chief_ruling(pos, fail_gate, risk, quiet_bull, loud_bear)
expect("gate failure means no trade, even with a strong score", r1["decision"].startswith("No trade"))
expect("no ticket when the gate fails", r1["ticket"] is None)

r2 = desk.chief_ruling(pos, pass_gate, risk, quiet_bull, loud_bear)
expect("positive score + gate pass -> paper candidate despite a louder bear",
       r2["decision"] == "Paper-trade candidate")
expect("paper ticket carries the risk manager's size", r2["ticket"] and r2["ticket"]["position_pct"] == 8.0)
expect("disagreement between advocates and formula is reported",
       r2["advocates_agree_with_formula"] is False)

r3 = desk.chief_ruling(neg, pass_gate, risk, quiet_bull, loud_bear)
expect("negative score + gate pass -> avoid, never a short", r3["decision"] == "Avoid / reduce"
       and r3["ticket"] is None)

r4 = desk.chief_ruling(flat, pass_gate, risk, quiet_bull, loud_bear)
expect("score inside the band -> no clear tilt", r4["decision"] == "No clear tilt")

# ----------------------------------------------------------------------
print("\nFundamentals are context, never scored")
fa = desk.fundamentals_analyst({"return_on_equity": 0.3, "debt_to_equity": 300,
                                "trailing_pe": 60, "forward_pe": 30,
                                "target_mean_price": 150}, 100.0)
expect("fundamentals produce arguments", len(fa.arguments) >= 3)
expect("every fundamentals argument has zero formula weight",
       all(a.weight == 0.0 and not a.scored for a in fa.arguments))

# ----------------------------------------------------------------------
print("\nRisk sizing")
d = synthetic_ohlcv()
_, ind = scoring.technical_score(d)
calm = desk.risk_manager(d, ind, 0.0)
nervous = desk.risk_manager(d, ind, 0.3)
expect("size is capped at the maximum", calm.metrics["position_pct"] <= desk.DESK_CFG.max_position_pct + 1e-9)
expect("macro haircut shrinks the size",
       abs(nervous.metrics["position_pct"] - calm.metrics["position_pct"] * 0.7) < 1e-9)
expect("expected shortfall is at least VaR", calm.metrics["es_1d_pct"] >= calm.metrics["var_1d_pct"] - 1e-12)

# ----------------------------------------------------------------------
print("\nEnd to end")
d = synthetic_ohlcv(seed=11)
tech, ind = scoring.technical_score(d)
pen = pd.Series(0.1, index=d.index)
comp = scoring.combine(float(tech.iloc[-1]), 0.4, 0.1)
out = desk.run_desk(daily_df=d, indicator_frame=ind, tech_score_series=tech, penalty_series=pen,
                    composite=comp, sentiment_sigma=0.4,
                    sentiment_info={"n_items": 6, "effective_n": 4.2, "newest_age_hours": 3.0},
                    fundamentals={"return_on_equity": 0.2}, index_close=d["Close"] * 1.0)
names = [r.name for r in out["reports"]]
expect("seven agents report", len(names) == 7, str(names))
expect("ruling has a decision", bool(out["ruling"]["decision"]))
expect("ruling score is the formula's final score", out["ruling"]["final"] == comp["final"])

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("All desk tests passed.")
