"""
Mathematical tests for the composite scoring model.

Run with:  python test_scoring.py

These check properties, not golden numbers. A scoring model that returns the
value it returned yesterday is not thereby correct — what matters is that the
z-scores are causal, the composite really has unit variance, the weights
renormalise, and the backtest's p-value holds its nominal size against data
with no signal in it. Each of those is a place the original formula was wrong.
"""

import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scoring  # noqa: E402

failures = []


def expect(label, ok, detail=""):
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)


def synthetic_ohlcv(n=600, seed=3, drift=0.0004, vol=0.015):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    op = close * (1 + rng.normal(0, 0.003, n))
    hi = np.maximum(op, close) * (1 + abs(rng.normal(0, 0.005, n)))
    lo = np.minimum(op, close) * (1 - abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close,
                         "Volume": rng.integers(1e6, 9e6, n)}, index=idx)


print("\nCausality — no score may use data from after its own timestamp.")
df = synthetic_ohlcv()
score_full, _ = scoring.technical_score(df)
cut = 400
score_partial, _ = scoring.technical_score(df.iloc[:cut])
overlap = score_full.iloc[:cut].dropna().index.intersection(score_partial.dropna().index)
max_drift = float((score_full.loc[overlap] - score_partial.loc[overlap]).abs().max())
expect("truncating future data leaves past scores unchanged",
       max_drift < 1e-9, f"max difference {max_drift:.2e}")

print("\nStandardisation — a threshold must mean the same thing every time.")
sd = float(score_full.dropna().std())
expect("standardised technical score has SD close to 1",
       0.75 < sd < 1.35, f"SD = {sd:.3f}")
# The un-standardised weighted sum is what the original formula thresholded on.
_, ind = scoring.technical_score(df)
raw_sd = float(ind["technical_raw"].dropna().std())
expect("the raw weighted sum does NOT have unit SD (this is the bug)",
       abs(raw_sd - 1.0) > 0.1, f"raw SD = {raw_sd:.3f} vs standardised {sd:.3f}")

print("\nZ-score stability — a near-constant input must not explode.")
flat = pd.Series([100.0] * 400, index=pd.date_range("2022-01-03", periods=400, freq="B"))
z_flat = scoring.rolling_zscore(flat)
expect("constant input yields finite z-scores",
       bool(np.isfinite(z_flat.dropna()).all()) and float(z_flat.abs().max() or 0) < 1e3,
       f"max |z| = {float(z_flat.abs().max() or 0):.3e}")

print("\nWeight renormalisation — a missing component must not shrink the score.")
both = scoring.combine(technical=1.2, sentiment=0.0, risk_penalty=0.0)
tech_only = scoring.combine(technical=1.2, sentiment=None, risk_penalty=0.0)
expect("technical-only reading is not scaled down by the sentiment weight",
       abs(tech_only["final"] - 1.2) < 1e-9, f"got {tech_only['final']:.4f}")
expect("a real neutral sentiment DOES pull the blend toward zero",
       abs(both["final"] - 0.66) < 1e-9, f"got {both['final']:.4f}")
expect("weights used are reported and sum to 1",
       abs(sum(tech_only["weights_used"].values()) - 1.0) < 1e-9)

print("\nMacro haircut — beta must be the maximum haircut, as documented.")
idx = pd.date_range("2022-01-03", periods=400, freq="B")
calm = pd.Series(np.r_[np.full(300, 15.0), np.full(100, 15.0)], index=idx)
spike = pd.Series(np.r_[np.full(300, 15.0), np.linspace(15, 80, 100)], index=idx)
p_calm = scoring.macro_risk_penalty(idx, calm)
p_spike = scoring.macro_risk_penalty(idx, spike)
expect("a calm market incurs no haircut", float(p_calm.max()) < 1e-9)
expect("an extreme spike reaches the configured maximum",
       abs(float(p_spike.max()) - scoring.CFG.geo_beta) < 1e-6,
       f"max haircut {float(p_spike.max()):.3f} vs geo_beta {scoring.CFG.geo_beta}")
expect("the haircut never exceeds geo_beta", float(p_spike.max()) <= scoring.CFG.geo_beta + 1e-9)

print("\nHaircut reduces conviction in both directions, by design.")
bull = scoring.combine(1.5, None, risk_penalty=0.4)
bear = scoring.combine(-1.5, None, risk_penalty=0.4)
expect("a positive reading is shrunk toward zero", bull["final"] < 1.5)
expect("a negative reading is also shrunk toward zero", bear["final"] > -1.5)
expect("shrink is symmetric", abs(bull["final"] + bear["final"]) < 1e-9)

print("\nSentiment — absent data must be distinguishable from balanced data.")
expect("no headlines returns None, not 0.0", scoring.decayed_sentiment([]) is None)
balanced = scoring.decayed_sentiment([{"sentiment": 1.0, "age_hours": 1},
                                      {"sentiment": -1.0, "age_hours": 1}])
expect("balanced headlines return a real score of ~0",
       balanced is not None and abs(balanced["score"]) < 1e-9)
fresh = scoring.decayed_sentiment([{"sentiment": 1.0, "age_hours": 0},
                                   {"sentiment": -1.0, "age_hours": 240}])
expect("a fresh headline outweighs a 10-day-old one",
       fresh["score"] > 0.9, f"score {fresh['score']:.3f}")
few = scoring.sentiment_to_sigma(1.0, 2)
many = scoring.sentiment_to_sigma(1.0, 40)
expect("many agreeing headlines count for more than a couple",
       many > few, f"n=2 -> {few:.3f}, n=40 -> {many:.3f}")

print("\nBacktest inference — the p-value must hold its size against pure noise.")
rng = np.random.default_rng(99)
trials, alpha, H = 120, 0.05, 10
naive_fp = boot_fp = 0
for _ in range(trials):
    n = 500
    # A persistent predictor, like a 126-day rolling z-score.
    s = np.zeros(n)
    e = rng.normal(size=n) * np.sqrt(1 - 0.98 ** 2)
    for i in range(1, n):
        s[i] = 0.98 * s[i - 1] + e[i]
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ind2 = pd.date_range("2021-01-04", periods=n, freq="B")
    bt = scoring.backtest(pd.Series(s, index=ind2), pd.Series(px, index=ind2), H)
    if bt.get("p_naive", 1) < alpha:
        naive_fp += 1
    if np.isfinite(bt.get("p_bootstrap", np.nan)) and bt["p_bootstrap"] < alpha:
        boot_fp += 1
naive_rate = 100 * naive_fp / trials
boot_rate = 100 * boot_fp / trials
print(f"        naive p<0.05 on pure noise     : {naive_rate:.1f}%")
print(f"        bootstrap p<0.05 on pure noise : {boot_rate:.1f}%")
expect("the naive p-value over-rejects badly (documenting the bug)", naive_rate > 25)
expect("the bootstrap p-value holds roughly its nominal 5% size",
       boot_rate <= 15, f"{boot_rate:.1f}% (want <=15% at n={trials})")

print("\nReading bands.")
expect("a strong positive score reads as strongly positive",
       scoring.describe_reading(1.5)[0].startswith("Strongly positive"))
expect("a flat score reads as mixed", scoring.describe_reading(0.1)[0].startswith("Mixed"))
expect("no band is labelled BUY or SELL",
       not any(w in " ".join(b[0] for b in scoring.READING_BANDS).upper()
               for w in ("BUY", "SELL", "AVOID")))

print("\nGeo-beta estimation replaces the hard-coded constant.")
vix = pd.Series(20 + 5 * np.sin(np.arange(len(df)) / 20), index=df.index)
est = scoring.estimate_geo_beta(df["Close"], vix)
expect("returns an estimate with its own diagnostics",
       est is not None and {"geo_beta", "slope", "r_squared", "p_value"} <= set(est))
expect("estimate stays inside its stated bounds", 0.1 <= est["geo_beta"] <= 0.8)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All scoring checks passed.")
