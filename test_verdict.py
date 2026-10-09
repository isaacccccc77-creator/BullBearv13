"""
Checks on the buy test.

Run with:  python test_verdict.py

The formulas here decide how much money a reader puts at risk, so each one
is checked against something external to itself: an analytic result, a
numerical optimum found by brute force, or a Monte Carlo simulation of the
process the formula claims to describe. Checking kelly_fraction against a
rearrangement of kelly_fraction would prove nothing.
"""

import math
import sys

import numpy as np
import pandas as pd

import verdict as V

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


print("Kelly against the classic coin-flip result")
# For a bet that wins or loses the whole stake, Kelly collapses to 2p - 1.
for p in (0.55, 0.60, 0.75):
    f = V.kelly_fraction(p, 1.0, 1.0)
    check(f"p={p}: f* = 2p-1 = {2 * p - 1:.4f}", abs(f - (2 * p - 1)) < 1e-12,
          f"got {f:.6f}")

# 2:1 payoff, p=0.5 -> textbook f = p - q/b = 0.5 - 0.5/2 = 0.25.
check("2:1 payoff at p=0.5 gives 0.25",
      abs(V.kelly_fraction(0.5, 2.0, 1.0) - 0.25) < 1e-12,
      f"got {V.kelly_fraction(0.5, 2.0, 1.0):.6f}")

print("\nKelly actually maximises growth (brute-force search)")
for p, w, l in [(0.52, 0.08, 0.06), (0.60, 0.12, 0.10), (0.45, 0.20, 0.05),
                (0.70, 0.05, 0.05), (0.55, 0.30, 0.25)]:
    f_star = V.kelly_fraction(p, w, l)
    grid = np.linspace(1e-6, 0.999 / l, 400_001)
    growths = [V.growth_rate(f, p, w, l) for f in grid]
    f_best = grid[int(np.argmax(growths))]
    check(f"p={p} W={w} L={l}: closed form {f_star:.4f} vs grid {f_best:.4f}",
          abs(f_star - f_best) < 2e-3, f"gap {abs(f_star - f_best):.5f}")

print("\nThe hill: double the optimal stake and growth returns to zero")
for p, w, l in [(0.52, 0.02, 0.02), (0.55, 0.01, 0.01), (0.60, 0.015, 0.015)]:
    f_star = V.kelly_fraction(p, w, l)
    g_double = V.growth_rate(2 * f_star, p, w, l)
    g_star = V.growth_rate(f_star, p, w, l)
    # G(2f*) = 0 exactly only in the infinitesimal-payoff limit, so the
    # claim under test is the sign: at twice the optimal stake every bit of
    # the growth is gone, and past it the compounding runs backwards.
    check(f"p={p} W=L={w}: G(f*) = {g_star:+.2e} but G(2f*) = {g_double:+.2e}",
          g_star > 0 and g_double <= 0, f"ratio {g_double / g_star:.4f}")
    check(f"p={p}: and G(3f*) is worse still",
          V.growth_rate(3 * f_star, p, w, l) < g_double)

print("\nKelly beats any fixed alternative over a simulated run")
# The claim that justifies the whole module: compounding at f* ends higher
# than compounding at other stakes. Simulated, not asserted.
rng = np.random.default_rng(20261009)
p, w, l = 0.56, 0.09, 0.07
f_star = V.kelly_fraction(p, w, l)
draws = rng.random((400, 500)) < p
terminal = {}
for f in (0.25 * f_star, f_star, 2 * f_star, 3 * f_star):
    step = np.where(draws, 1 + f * w, 1 - f * l)
    terminal[f] = float(np.median(np.prod(step, axis=1)))
check(f"f* ({f_star:.3f}) median {terminal[f_star]:.1f} beats 2f* {terminal[2 * f_star]:.1f}",
      terminal[f_star] > terminal[2 * f_star])
check(f"f* beats 3f* ({terminal[3 * f_star]:.3f})",
      terminal[f_star] > terminal[3 * f_star])
check(f"f* beats quarter-Kelly {terminal[0.25 * f_star]:.1f} on the median",
      terminal[f_star] > terminal[0.25 * f_star])
print("\nWhy a quarter of it: full Kelly on an overestimated p loses money")
# With p known, full Kelly wins on every measure, so the case for a
# fraction has to be made where it actually applies — when the hit rate
# was estimated too high. Sized as though p were 0.62 while the truth is
# 0.50, full Kelly compounds downwards and a quarter of it barely moves.
believed, truth = 0.62, 0.50
f_believed = V.kelly_fraction(believed, 0.07, 0.07)
real = rng.random((400, 500)) < truth
outcome = {}
for label, f in (("full", f_believed), ("quarter", V.DEFAULT_KAPPA * f_believed)):
    step = np.where(real, 1 + f * 0.07, 1 - f * 0.07)
    outcome[label] = float(np.median(np.prod(step, axis=1)))
check(f"full Kelly on a 12-point overestimate ends at {outcome['full']:.2e} of capital",
      outcome["full"] < 0.5)
check(f"quarter Kelly survives the same mistake ({outcome['quarter']:.2f})",
      outcome["quarter"] > 20 * outcome["full"],
      f"full {outcome['full']:.3e} vs quarter {outcome['quarter']:.3e}")

print("\nBreak-even hit rate")
# At exactly p*, expected value must be zero.
for w, l in [(0.10, 0.08), (0.05, 0.05), (0.25, 0.10)]:
    be = V.breakeven_hit_rate(w, l)
    check(f"W={w} L={l}: edge at p* is zero (p*={be:.4f})",
          abs(V.edge_per_trade(be, w, l)) < 1e-12,
          f"edge {V.edge_per_trade(be, w, l):.2e}")
    check(f"W={w} L={l}: Kelly at p* is zero",
          abs(V.kelly_fraction(be, w, l)) < 1e-10)
check("symmetric payoffs need 50%", abs(V.breakeven_hit_rate(0.1, 0.1) - 0.5) < 1e-12)
check("a win that loses money can never break even",
      V.breakeven_hit_rate(-0.01, 0.05) == 1.0)

print("\nWilson lower bound")
check("bound never exceeds the point estimate",
      all(V.wilson_lower_bound(p, n) <= p + 1e-12
          for p in np.linspace(0.01, 0.99, 50) for n in (5, 20, 100, 1000)))
check("bound stays in [0,1]",
      all(0.0 <= V.wilson_lower_bound(p, n) <= 1.0
          for p in np.linspace(0.0, 1.0, 21) for n in (1, 7, 50, 5000)))
check("bound converges on the estimate as evidence grows",
      abs(V.wilson_lower_bound(0.6, 1_000_000) - 0.6) < 1e-3,
      f"got {V.wilson_lower_bound(0.6, 1_000_000):.6f}")
check("more evidence is never punished",
      all(V.wilson_lower_bound(0.6, n) <= V.wilson_lower_bound(0.6, n * 2) + 1e-12
          for n in (5, 10, 25, 60, 200)))
check("a 60% hit rate on 20 windows is honestly as low as ~49%",
      0.46 < V.wilson_lower_bound(0.6, 20) < 0.52,
      f"got {V.wilson_lower_bound(0.6, 20):.4f}")
check("certainty on a tiny sample is still discounted hard",
      V.wilson_lower_bound(1.0, 5) < 0.86, f"got {V.wilson_lower_bound(1.0, 5):.4f}")

print("\nCosts")
c = V.Costs(commission_pct=0.0, commission_min=5.0, spread_pct=0.0, fx_pct=0.0)
check("a $5 flat fee is 2% round trip on a $500 trade",
      abs(c.round_trip(500) - 0.02) < 1e-12, f"got {c.round_trip(500):.6f}")
check("the same fee is 0.2% on a $5,000 trade",
      abs(c.round_trip(5000) - 0.002) < 1e-12)
check("flat fees fall with size, percentage fees do not",
      c.round_trip(500) > c.round_trip(5000) > c.round_trip(50000))
pct_only = V.Costs(commission_pct=0.1, commission_min=0.0, spread_pct=0.0, fx_pct=0.0)
check("a pure percentage cost is size-invariant",
      abs(pct_only.round_trip(500) - pct_only.round_trip(500_000)) < 1e-15)
check("itemised breakdown sums to the total",
      abs(sum(V.Costs().breakdown(10_000).values()) - V.Costs().round_trip(10_000)) < 1e-12)

print("\nCosts make the bar higher, never lower")
odds = V.Odds(hit_rate=0.58, avg_win=0.09, avg_loss=0.07, n_windows=40, n_rows=840,
              horizon_days=21, baseline_hit_rate=0.52, baseline_avg_win=0.08,
              baseline_avg_loss=0.07)
bars = [V.assess(odds, n, 100_000, V.Costs(commission_min=5.0)).breakeven
        for n in (500, 2_000, 10_000, 50_000)]
check(f"break-even falls monotonically with size: {[f'{b:.1%}' for b in bars]}",
      all(bars[i] > bars[i + 1] for i in range(len(bars) - 1)))
stakes = [V.assess(odds, n, 100_000, V.Costs(commission_min=5.0)).stake_fraction
          for n in (500, 2_000, 10_000, 50_000)]
check(f"and stake rises with size: {[f'{s:.2%}' for s in stakes]}",
      all(stakes[i] <= stakes[i + 1] for i in range(len(stakes) - 1)))
check("tax reduces the stake",
      V.assess(odds, 10_000, 100_000, V.Costs(tax_rate=0.3)).stake_fraction
      < V.assess(odds, 10_000, 100_000, V.Costs(tax_rate=0.0)).stake_fraction)

print("\nThe three ways it says no")
thin = V.Odds(hit_rate=0.80, avg_win=0.15, avg_loss=0.05, n_windows=4, n_rows=84,
              horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.08,
              baseline_avg_loss=0.08)
a_thin = V.assess(thin, 10_000, 100_000)
check("four windows of an 80% hit rate still sizes to zero",
      a_thin.verdict == "thin" and a_thin.stake_fraction == 0.0,
      f"got {a_thin.verdict} / {a_thin.stake_fraction}")

tiny = V.Odds(hit_rate=0.58, avg_win=0.004, avg_loss=0.02, n_windows=50, n_rows=1050,
              horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.004,
              baseline_avg_loss=0.02)
a_tiny = V.assess(tiny, 300, 100_000, V.Costs(commission_min=5.0))
check("a 0.4% average win cannot pay a 3.4% round trip",
      a_tiny.verdict == "no" and a_tiny.stake_fraction == 0.0,
      f"got {a_tiny.verdict}, W_net {a_tiny.w_net:.4f}")

marginal = V.Odds(hit_rate=0.47, avg_win=0.08, avg_loss=0.08, n_windows=22, n_rows=462,
                  horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.08,
                  baseline_avg_loss=0.08)
a_marg = V.assess(marginal, 10_000, 100_000)
check("a hit rate below break-even sizes to zero",
      a_marg.stake_fraction == 0.0 and a_marg.verdict == "no",
      f"got {a_marg.verdict} / {a_marg.stake_fraction:.4f}")

print("\nGuards on the stake itself")
huge = V.Odds(hit_rate=0.95, avg_win=0.40, avg_loss=0.02, n_windows=500, n_rows=10_500,
              horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.08,
              baseline_avg_loss=0.08)
a_huge = V.assess(huge, 10_000, 100_000)
check(f"an absurd edge is still capped at 10% (asked {a_huge.kelly_shrunk:.1%})",
      abs(a_huge.stake_fraction - 0.10) < 1e-12, f"got {a_huge.stake_fraction:.4f}")
check("the cap is explained in the reasons",
      any("Capped" in r for r in a_huge.reasons))
check("stake is never negative",
      all(V.assess(V.Odds(hit_rate=p, avg_win=0.08, avg_loss=0.08, n_windows=50,
                          n_rows=1050, horizon_days=21, baseline_hit_rate=0.5,
                          baseline_avg_win=0.08, baseline_avg_loss=0.08),
                   10_000, 100_000).stake_fraction >= 0.0
          for p in np.linspace(0.0, 1.0, 41)))
check("cash stake matches the fraction of the portfolio",
      abs(V.assess(odds, 10_000, 250_000).stake_cash
          - V.assess(odds, 10_000, 250_000).stake_fraction * 250_000) < 1e-9)
check("the shrunk stake never exceeds the raw one",
      all(V.assess(V.Odds(hit_rate=p, avg_win=0.09, avg_loss=0.07, n_windows=n,
                          n_rows=int(n * 21), horizon_days=21, baseline_hit_rate=0.5,
                          baseline_avg_win=0.08, baseline_avg_loss=0.08),
                   10_000, 100_000).kelly_shrunk <= V.kelly_fraction(p, 0.09, 0.07) + 1e-12
          for p in (0.55, 0.65, 0.8) for n in (25, 60, 300)))

print("\nmeasure_odds on a series whose answer is known")
# A price that rises on exactly the days the signal is high.
n = 900
idx = pd.date_range("2022-01-03", periods=n, freq="B")
sig = pd.Series(np.tile([2.0, 2.0, 2.0, -2.0, -2.0, -2.0], n // 6)[:n], index=idx)
steps = np.where(sig.to_numpy() > 0, 0.004, -0.004)
px = pd.Series(100 * np.cumprod(1 + steps), index=idx)
o = V.measure_odds(sig, px, horizon_days=21)
check("odds are measured at all", o is not None)
if o:
    check(f"independent windows ({o.n_windows:.0f}) are far fewer than rows ({o.n_rows})",
          o.n_windows < o.n_rows / 20 + 1)
    check("horizon is carried through", o.horizon_days == 21)
    check("the bucket brackets today's reading, not one from a horizon ago",
          o.band[0] <= float(sig.iloc[-1]) <= o.band[1],
          f"band {o.band} vs latest {sig.iloc[-1]}")
    # The regression this guards: conditioning on the truncated frame
    # bucketed on the signal as it stood `horizon_days` earlier.
    _flip = sig.copy()
    _flip.iloc[-30:] = -5.0
    _o = V.measure_odds(_flip, px, horizon_days=21)
    check("a reading that only appears in the last 30 days still drives the bucket",
          _o is not None and _o.band[0] <= -5.0 + 1e-9,
          f"band {_o.band if _o else None}")
    check("avg win and avg loss are both positive magnitudes",
          o.avg_win > 0 and o.avg_loss > 0)

# A signal with no information: the conditional hit rate should land near
# the unconditional one, and the page is supposed to say so.
rng2 = np.random.default_rng(7)
noise = pd.Series(rng2.normal(size=n), index=idx)
walk = pd.Series(100 * np.cumprod(1 + rng2.normal(0.0004, 0.012, n)), index=idx)
o2 = V.measure_odds(noise, walk, horizon_days=21)
if o2 is not None:
    a2 = V.assess(o2, 10_000, 100_000)
    check("a noise signal does not claim a big edge over the baseline",
          abs(o2.hit_rate - o2.baseline_hit_rate) < 0.30,
          f"conditional {o2.hit_rate:.2f} vs baseline {o2.baseline_hit_rate:.2f}")
    if a2.verdict == "buy" and a2.signal_adds < 0.02:
        check("a buy on a baseline-equal hit rate carries the drift caveat",
              any("drift" in r for r in a2.reasons))
    else:
        print("  ok    noise case did not reach a buy (nothing to caveat)")

check("too little history returns nothing rather than guessing",
      V.measure_odds(sig.iloc[:20], px.iloc[:20], horizon_days=21) is None)
check("a flat price series does not produce odds",
      V.measure_odds(sig, pd.Series(100.0, index=idx), horizon_days=21) is None)

print("\nCurves used by the page")
curve = V.breakeven_curve(odds, V.Costs(commission_min=5.0))
check("break-even curve is downward sloping in trade size",
      curve["Break-even hit rate"].is_monotonic_decreasing)
check("break-even curve has no gaps", curve.notna().all().all())
g = V.growth_curve(V.assess(odds, 10_000, 100_000), clamp=10.0)
check("growth curve starts at zero stake with zero growth",
      abs(float(g["Growth"].iloc[0])) < 1e-12)
_a = V.assess(odds, 10_000, 100_000)
_peak = float(g.loc[g["Growth"].idxmax(), "Stake"])
check(f"growth curve peaks at the raw Kelly stake ({_a.kelly_raw:.3f})",
      abs(_peak - _a.kelly_raw) < 0.03 * max(_a.kelly_raw, 1.0),
      f"peak at {_peak:.4f}")
check("the stake actually taken sits left of the peak, not on it",
      _a.stake_fraction < _peak)
check("the curve reaches past the zero crossing so the hill is visible",
      float(g["Stake"].iloc[-1]) > 2 * _a.kelly_raw * 0.95
      or float(g["Growth"].iloc[-1]) < 0)
check("growth curve is finite throughout", np.isfinite(g["Growth"]).all())

print("\nEvery verdict reports its reasoning")
for name, a in [("buy", V.assess(odds, 10_000, 100_000)),
                ("thin", a_thin), ("no", a_marg), ("capped", a_huge)]:
    check(f"{name}: headline and at least one reason",
          bool(a.headline) and len(a.reasons) >= 1)
    check(f"{name}: headline agrees with the stake",
          (a.stake_fraction > 0) == a.headline.startswith("Buy"))

print("\nCounting independent windows")


def check2(name, cond, detail=""):
    check(name, cond, detail)


check2("contiguous days collapse to one window per horizon",
       V.independent_windows(np.arange(105), 21) == 5,
       f"got {V.independent_windows(np.arange(105), 21)}")
check2("days spaced a full horizon apart all count",
       V.independent_windows(np.arange(0, 210, 21), 21) == 10,
       f"got {V.independent_windows(np.arange(0, 210, 21), 21)}")
check2("one day is one window", V.independent_windows(np.array([7]), 21) == 1)
check2("no days is no windows", V.independent_windows(np.array([], dtype=int), 21) == 0)
check2("the count never exceeds the number of observations",
       all(V.independent_windows(np.arange(n), 21) <= n for n in (1, 5, 40, 500)))
check2("a signal visiting briefly but often beats one long stretch",
       V.independent_windows(np.arange(0, 2000, 100), 21)
       > V.independent_windows(np.arange(20), 21))
# The shortcut this replaced credited a single 200-day stretch with ~10
# independent windows; it is one episode and worth far fewer.
check2("one unbroken 200-day stretch is not 10 independent reads",
       V.independent_windows(np.arange(200), 21) == 10
       and V.independent_windows(np.arange(200), 200) == 1)

print("\nBucket width survives an extreme reading")
_n = 1300
_idx = pd.date_range("2020-01-01", periods=_n, freq="B")
_rng3 = np.random.default_rng(11)
_px3 = pd.Series(100 * np.cumprod(1 + _rng3.normal(0.0003, 0.013, _n)), index=_idx)
_mid = pd.Series(_rng3.normal(0, 1, _n), index=_idx)
_counts = {}
for _label, _last in (("mid", 0.0), ("high", 4.0), ("low", -4.0)):
    _s = _mid.copy()
    _s.iloc[-1] = _last
    _o3 = V.measure_odds(_s, _px3, horizon_days=21)
    _counts[_label] = _o3.n_rows if _o3 else 0
check(f"an extreme reading keeps a comparable sample: {_counts}",
      min(_counts.values()) > 0.6 * max(_counts.values()),
      f"{_counts}")
check("the bucket never exceeds its nominal width",
      all(V.measure_odds(_mid.assign() if False else _s2, _px3, 21).n_rows <= 0.26 * _n
          for _s2 in [_mid, _mid * -1]))

print("\nNo hill is drawn when there is no hill")
_neg = V.Odds(hit_rate=0.40, avg_win=0.05, avg_loss=0.07, n_windows=40, n_rows=840,
              horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.05,
              baseline_avg_loss=0.07)
_a_neg = V.assess(_neg, 10_000, 100_000)
check(f"negative Kelly ({_a_neg.kelly_raw:.1%}) yields an empty growth curve",
      V.growth_curve(_a_neg).empty)
check("a positive-edge case still yields one",
      not V.growth_curve(V.assess(odds, 20_000, 100_000)).empty)

print("\nThe growth curve stays readable when Kelly asks for leverage")
_lev = V.Odds(hit_rate=0.71, avg_win=0.11, avg_loss=0.05, n_windows=40, n_rows=840,
              horizon_days=21, baseline_hit_rate=0.5, baseline_avg_win=0.08,
              baseline_avg_loss=0.07)
_a_lev = V.assess(_lev, 25_000, 25_000)
_g_lev = V.growth_curve(_a_lev)
check(f"an uncapped Kelly of {_a_lev.kelly_raw:.0%} does not run the axis past 100%",
      float(_g_lev["Stake"].max()) <= 1.0 + 1e-9,
      f"top {float(_g_lev['Stake'].max()):.2f}")
check("the stake actually taken is still on the chart",
      _a_lev.stake_fraction <= float(_g_lev["Stake"].max()))
# A wide-payoff setup (a volatile name held long enough to fall 35%) is
# the case where the optimum is a stake a reader could actually take.
_a_mod = V.assess(V.Odds(hit_rate=0.52, avg_win=0.38, avg_loss=0.35, n_windows=60,
                         n_rows=1260, horizon_days=126, baseline_hit_rate=0.5,
                         baseline_avg_win=0.38, baseline_avg_loss=0.35),
                  20_000, 100_000)
_g_mod = V.growth_curve(_a_mod)
check(f"a modest edge still shows its peak ({_a_mod.kelly_raw:.1%}) and the crossing",
      _a_mod.kelly_raw <= float(_g_mod["Stake"].max())
      and 2 * _a_mod.kelly_raw <= float(_g_mod["Stake"].max()) + 1e-9,
      f"top {float(_g_mod['Stake'].max()):.3f} vs 2f* {2 * _a_mod.kelly_raw:.3f}")
check("the curve is still finite under the clamp", np.isfinite(_g_lev["Growth"]).all())

print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All buy-test checks passed (including window counting).")
