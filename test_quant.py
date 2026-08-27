"""
Tests for the quant models.

Run with:  python test_quant.py

Properties, not golden numbers. The important ones are calibration checks —
does the cointegration test reject a random walk at roughly its nominal rate,
does VaR actually get breached about as often as it claims — because those are
the places where a model can look plausible on screen while being wrong.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant  # noqa: E402

failures = []


def expect(label, ok, detail=""):
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        failures.append(label)


rng = np.random.default_rng(2026)
IDX = pd.date_range("2022-01-03", periods=500, freq="B")


# ----------------------------------------------------------------------
print("\nADF — the test must reject a random walk only as often as it claims.")
# ----------------------------------------------------------------------
false_positives = 0
TRIALS = 400
for _ in range(TRIALS):
    walk = np.cumsum(rng.normal(size=300))
    if quant.adf_pvalue(quant.adf_statistic(walk)) < 0.05:
        false_positives += 1
rate = 100 * false_positives / TRIALS
print(f"        random walks called stationary at p<0.05: {rate:.1f}% (nominal 5%)")
expect("null rejection rate is close to nominal", 2.0 <= rate <= 9.0, f"got {rate:.1f}%")

detected = 0
for _ in range(200):
    # Ornstein-Uhlenbeck: genuinely mean reverting.
    x = np.zeros(300)
    for i in range(1, 300):
        x[i] = 0.9 * x[i - 1] + rng.normal()
    if quant.adf_pvalue(quant.adf_statistic(x)) < 0.05:
        detected += 1
power = 100 * detected / 200
print(f"        genuinely mean-reverting series detected: {power:.1f}%")
expect("the test has real power against stationarity", power > 80, f"got {power:.1f}%")


# ----------------------------------------------------------------------
print("\nHalf-life recovers the speed of a known process.")
# ----------------------------------------------------------------------
for phi, label in ((0.9, "fast"), (0.99, "slow")):
    x = np.zeros(4000)
    for i in range(1, 4000):
        x[i] = phi * x[i - 1] + rng.normal()
    expected = -np.log(2) / np.log(phi)
    got = quant.half_life(pd.Series(x))
    expect(f"{label} reversion (phi={phi}) recovered within 25%",
           abs(got - expected) / expected < 0.25, f"expected ~{expected:.1f}, got {got:.1f}")

rising = pd.Series(np.cumsum(np.abs(rng.normal(size=300)) + 0.5))
expect("a diverging series reports an infinite half-life",
       not np.isfinite(quant.half_life(rising)))


# ----------------------------------------------------------------------
print("\nPairs — a cointegrated pair must be found, an unrelated one must not.")
# ----------------------------------------------------------------------
base = np.cumsum(rng.normal(0.0004, 0.012, len(IDX))) + 4.0
noise = np.zeros(len(IDX))
for i in range(1, len(IDX)):
    noise[i] = 0.92 * noise[i - 1] + rng.normal(0, 0.02)
a = pd.Series(np.exp(base + noise), index=IDX)
b = pd.Series(np.exp(base), index=IDX)

pair = quant.analyse_pair(a, b, "AAA", "BBB")
expect("a cointegrated pair is analysed", pair is not None)
expect("and is detected as cointegrated", pair.adf_pvalue < 0.05,
       f"p = {pair.adf_pvalue:.4f}")
expect("hedge ratio is near 1 for a shared trend",
       abs(pair.hedge_ratio - 1.0) < 0.35, f"got {pair.hedge_ratio:.3f}")
expect("half-life is finite and tradeable", 1 < pair.half_life_days < 120,
       f"got {pair.half_life_days:.1f}")

# A single pair proves nothing — any test fails 5% of the time by design.
# What matters is the RATE, because using plain ADF critical values on a
# regression residual (the natural mistake) doubles it and fills a pairs
# book with spurious relationships.
spurious = 0
PAIR_TRIALS = 250
for _ in range(PAIR_TRIALS):
    x = pd.Series(np.exp(np.cumsum(rng.normal(0.0003, 0.015, 400)) + 4))
    y = pd.Series(np.exp(np.cumsum(rng.normal(0.0003, 0.015, 400)) + 4))
    res = quant.analyse_pair(x, y)
    if res is not None and res.adf_pvalue < 0.05:
        spurious += 1
spurious_rate = 100 * spurious / PAIR_TRIALS
print(f"        independent walks called cointegrated: {spurious_rate:.1f}% (nominal 5%)")
expect("the cointegration test is correctly calibrated",
       spurious_rate <= 9.0, f"got {spurious_rate:.1f}% — plain ADF values give ~11%")

expect("the Engle-Granger null is stricter than the plain ADF null",
       quant.EG_NULL_QUANTILES[4] < quant.ADF_NULL_QUANTILES[4],
       f"EG 5% {quant.EG_NULL_QUANTILES[4]:.3f} vs ADF 5% {quant.ADF_NULL_QUANTILES[4]:.3f}")

expect("too little history returns None",
       quant.analyse_pair(a.iloc[:50], b.iloc[:50]) is None)

# Regression: two series whose timestamps differ — different exchange opens,
# tz-aware stamps from yfinance — must still align. An exact-index join
# produced an EMPTY overlap here and reported "no data" on years of history.
shifted_a = pd.Series(a.to_numpy(), index=a.index + pd.Timedelta(hours=9, minutes=30))
shifted_b = pd.Series(b.to_numpy(), index=b.index + pd.Timedelta(hours=16))
misaligned = quant.analyse_pair(shifted_a, shifted_b, "AAA", "BBB")
expect("series with different intraday timestamps still align",
       misaligned is not None and misaligned.n_obs > 300,
       "None" if misaligned is None else f"n_obs={misaligned.n_obs}")
expect("and produce the same finding as the aligned pair",
       misaligned is not None and abs(misaligned.hedge_ratio - pair.hedge_ratio) < 0.05)

print("\nPairs backtest must not read the future.")
bt = quant.backtest_pair(pair)
expect("the backtest runs", "error" not in bt)
expect("positions are lagged, so day one is flat", float(bt["position"].iloc[0]) == 0.0)
expect("exposure is a sane fraction of the sample", 0 <= bt["exposure_pct"] <= 100)


# ----------------------------------------------------------------------
print("\nVaR — ordering, scaling, and the fat-tail correction.")
# ----------------------------------------------------------------------
normal_returns = pd.Series(rng.normal(0.0004, 0.012, 1500))
var = quant.value_at_risk(normal_returns, confidence=0.95)
expect("VaR is computed", "error" not in var)
expect("Expected Shortfall exceeds VaR, always",
       var["expected_shortfall_pct"] > var["historical_var_pct"],
       f"ES {var['expected_shortfall_pct']:.3f} vs VaR {var['historical_var_pct']:.3f}")
expect("on normal data, Gaussian and historical VaR nearly agree",
       abs(var["gaussian_var_pct"] - var["historical_var_pct"]) < 0.25,
       f"{var['gaussian_var_pct']:.3f} vs {var['historical_var_pct']:.3f}")
expect("normality is not rejected for normal data", not var["normality_rejected"])

# Fat-tailed, left-skewed: the case Gaussian VaR gets wrong.
fat = pd.Series(rng.standard_t(3, 2000) * 0.008 - 0.0004)
var_fat = quant.value_at_risk(fat, confidence=0.99)
expect("fat tails are detected", var_fat["excess_kurtosis"] > 1,
       f"excess kurtosis {var_fat['excess_kurtosis']:.2f}")
expect("normality IS rejected for fat-tailed data", var_fat["normality_rejected"])
expect("Gaussian VaR understates the historical loss on fat tails",
       var_fat["gaussian_var_pct"] < var_fat["historical_var_pct"],
       f"gaussian {var_fat['gaussian_var_pct']:.2f} vs historical {var_fat['historical_var_pct']:.2f}")
expect("Cornish-Fisher never reports a SMALLER tail loss than the Gaussian "
       "on fat-tailed data — it falls back when the expansion breaks down",
       var_fat["cornish_fisher_var_pct"] >= var_fat["gaussian_var_pct"] - 1e-9,
       f"cf {var_fat['cornish_fisher_var_pct']:.2f} vs gaussian {var_fat['gaussian_var_pct']:.2f}, "
       f"valid={var_fat['cornish_fisher_valid']}")

print("\nCornish-Fisher validity — the expansion is not always a quantile.")
_z_ok, ok = quant.cornish_fisher_z(0.05, 0.0, 0.0)
expect("with normal moments it reduces to the Gaussian quantile and is valid",
       ok and abs(_z_ok - (-1.6449)) < 1e-3, f"z={_z_ok:.4f} valid={ok}")
_z_bad, ok_bad = quant.cornish_fisher_z(0.01, 2.0, 10.0)
expect("extreme moments are flagged invalid rather than trusted", not ok_bad)
_lo, ok_lo = quant.cornish_fisher_z(0.05, -0.6, 2.0)
expect("mild left skew and fat tails stay valid and push the quantile out",
       ok_lo and _lo < -1.6449, f"z={_lo:.4f} valid={ok_lo}")

expect("higher confidence means a larger loss estimate",
       quant.value_at_risk(normal_returns, 0.99)["historical_var_pct"]
       > quant.value_at_risk(normal_returns, 0.90)["historical_var_pct"])

one_day = quant.value_at_risk(normal_returns, 0.95, horizon_days=1)
ten_day = quant.value_at_risk(normal_returns, 0.95, horizon_days=10)
expect("horizon scales by sqrt(t)",
       abs(ten_day["historical_var_pct"] / one_day["historical_var_pct"] - np.sqrt(10)) < 0.01)

expect("money figures follow portfolio value",
       abs(quant.value_at_risk(normal_returns, 0.95, portfolio_value=200_000)["historical_var_money"]
           / var["historical_var_money"] - 2.0) < 0.01)
expect("too little history is refused", "error" in quant.value_at_risk(normal_returns.iloc[:10]))

print("\nKupiec coverage test.")
kup = quant.kupiec_test(normal_returns, var["historical_quantile"], 0.95)
expect("a correctly-specified VaR passes coverage", kup["p_value"] > 0.05,
       f"p = {kup['p_value']:.3f}, breaches {kup['observed_rate_pct']:.1f}%")
bad = quant.kupiec_test(normal_returns, float(np.percentile(normal_returns, 20)), 0.95)
expect("a badly-specified VaR fails coverage", bad["p_value"] < 0.01,
       f"p = {bad['p_value']:.4f}")
expect("and the verdict names the dangerous direction",
       "understating" in bad["verdict"])


# ----------------------------------------------------------------------
print("\nPortfolio optimisation.")
# ----------------------------------------------------------------------
n_assets = 4
cov_true = np.diag([0.0004, 0.0009, 0.0016, 0.0025])
prices = pd.DataFrame(
    {f"T{i}": 100 * np.exp(np.cumsum(rng.normal(0.0004, np.sqrt(cov_true[i, i]), 500)))
     for i in range(n_assets)}, index=IDX)

opt = quant.optimise_portfolio(prices)
expect("optimisation returns a result", opt is not None)
for name in ("Maximum Sharpe", "Minimum variance", "Risk parity", "Equal weight"):
    w = opt["weights"].loc[name].to_numpy()
    expect(f"{name}: weights sum to 1", abs(w.sum() - 1) < 1e-6, f"sum {w.sum():.6f}")
    expect(f"{name}: no short positions", (w >= -1e-9).all())

mv_vol = float(opt["summary"].set_index("Portfolio").loc["Minimum variance", "In-sample vol %"])
others = opt["summary"].set_index("Portfolio")["In-sample vol %"].drop("Minimum variance")
expect("minimum variance really is the lowest-volatility portfolio",
       mv_vol <= others.min() + 1e-6, f"{mv_vol:.3f} vs best other {others.min():.3f}")

mv_w = opt["weights"].loc["Minimum variance"].to_numpy()
expect("minimum variance overweights the calmest asset",
       int(np.argmax(mv_w)) == 0, f"weights {np.round(mv_w, 3)}")

rp_w = opt["weights"].loc["Risk parity"].to_numpy()
expect("risk parity weights fall as volatility rises",
       all(rp_w[i] > rp_w[i + 1] for i in range(n_assets - 1)),
       f"weights {np.round(rp_w, 3)}")

frontier = opt["frontier"]
expect("the frontier has points", len(frontier) > 3)
expect("the frontier is monotone: more return costs more risk",
       bool((frontier["return_pct"].diff().dropna() >= -1e-6).all()))
expect("a holdout was actually carved out", opt["out_sample_days"] > 0)
expect("and out-of-sample columns are reported",
       "Out-of-sample Sharpe" in opt["summary"].columns)
expect("a verdict is produced", len(quant.optimiser_verdict(opt)) > 40)
expect("one asset is refused", quant.optimise_portfolio(prices[["T0"]]) is None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All quant checks passed.")
