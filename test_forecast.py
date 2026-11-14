"""
Checks for forecast.py.

A forecasting test is only worth something if it can tell an edge from no
edge. So beyond checking the arithmetic, this builds two markets where the
right answer is known in advance: a pure random walk, where any honest test
must report a coin flip, and a market with a momentum effect planted in it,
where a working test must find it. A leak (the model seeing the future)
would make the random walk look predictable; a broken model would miss the
planted effect. The leak is also checked head-on, by changing the future
and confirming that no earlier prediction moves.
"""

import math
import sys
import time

import numpy as np
import pandas as pd
from scipy import optimize

import forecast as F

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def market(daily_log_returns: np.ndarray, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1000)
    close = 50 * np.exp(np.cumsum(daily_log_returns))
    index = pd.bdate_range("2000-01-03", periods=len(close))
    volume = rng.lognormal(15, 0.3, len(close))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": volume}, index=index)


def random_walk(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return market(rng.normal(0.0003, 0.012, n), seed)


def momentum_market(n: int, seed: int, beta: float = 0.03) -> pd.DataFrame:
    """
    Each day's drift is beta times the past four weeks' return: trends
    carry on. Stationary, because 20 * beta < 1. Volatility and volume carry
    no information here, so 46 of the 69 features are pure noise.
    """
    rng = np.random.default_rng(seed)
    r = np.zeros(n)
    noise = rng.normal(0, 0.012, n)
    for t in range(n):
        r[t] = 0.0003 + beta * r[max(0, t - 20):t].sum() + noise[t]
    return market(r, seed)


print("The 69 features")
df = random_walk(3000, seed=7)
feats = F.build_features(df)
check("23 look-backs: {1,2,3} and every 4th week up to 80",
      F.WINDOWS_WEEKS == (1, 2, 3, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52,
                          56, 60, 64, 68, 72, 76, 80))
check("3 families x 23 = 69 columns", feats.shape[1] == 69, str(feats.shape))
log_ret = np.log(df["Close"]).diff()
t = 2500
check("a 12-week return is the sum of 60 daily log returns",
      abs(feats["return_12w"].iloc[t] - log_ret.iloc[t - 59:t + 1].sum()) < 1e-12)
check("a 4-week volatility is SD of 20 daily returns times sqrt(20)",
      abs(feats["volatility_4w"].iloc[t] - log_ret.iloc[t - 19:t + 1].std() * math.sqrt(20)) < 1e-12)
no_volume = df.assign(Volume=0.0)
check("no volume data: the volume family is dropped, not invented",
      F.build_features(no_volume).shape[1] == 46)
changed = df.copy()
changed.iloc[t + 1:, changed.columns.get_loc("Close")] *= 3
check("features on day t use nothing after day t",
      np.allclose(F.build_features(changed).iloc[:t + 1].to_numpy(),
                  feats.iloc[:t + 1].to_numpy(), equal_nan=True))
labels = F.make_labels(df["Close"])
check("the last 21 days have no label yet", labels.iloc[-21:].isna().all()
      and labels.iloc[:-21].notna().all())

print("AUC")
rng = np.random.default_rng(1)
y = rng.random(400) < 0.3
s = rng.normal(size=400) + y * 0.8
s[::7] = 0.25                                   # force ties
pairs = sum((s[i] > s[j]) + 0.5 * (s[i] == s[j]) for i in np.where(y)[0] for j in np.where(~y)[0])
check("matches counting every up/down pair by hand",
      abs(F.auc(y, s) - pairs / (y.sum() * (~y).sum())) < 1e-12)
check("perfect ranking is 1, reversed is 0",
      F.auc(y, y.astype(float)) == 1.0 and F.auc(y, -y.astype(float)) == 0.0)
big = rng.random(20000) < 0.9
check("noise scores 0.5 even when 90% of months are up",
      abs(F.auc(big, rng.normal(size=20000)) - 0.5) < 0.02)

print("Logistic regression")
X = rng.normal(size=(30000, 3))
true_w, true_b = np.array([1.5, -1.0, 0.0]), 0.3
yy = (rng.random(30000) < 1 / (1 + np.exp(-(X @ true_w + true_b)))).astype(float)
params = F.fit_logistic(X, yy, lam=1e-6)
fitted = np.r_[F.weights(params), params[-1]]
check("recovers the weights that generated the data",
      np.allclose(fitted, np.r_[true_w, true_b], atol=0.06), str(np.round(fitted, 3)))
p0 = np.abs(rng.normal(size=7))
for mix in (0.0, 0.5, 1.0):
    err = optimize.check_grad(lambda q: F._loss_and_grad(q, X[:500], yy[:500], 0.1, mix)[0],
                              lambda q: F._loss_and_grad(q, X[:500], yy[:500], 0.1, mix)[1], p0)
    check(f"analytic gradient agrees with finite differences (mix {mix})", err < 1e-5, f"{err:.2e}")
ridge = F.weights(F.fit_logistic(X, yy, lam=0.5, mix=0.0))
lasso = F.weights(F.fit_logistic(X, yy, lam=0.05, mix=1.0))
check("ridge shrinks every weight but switches none off",
      np.all(np.abs(ridge) > 1e-4) and np.abs(ridge).sum() < np.abs(fitted[:-1]).sum())
check("lasso switches the useless third input off exactly",
      abs(lasso[2]) < 1e-6 and abs(lasso[0]) > 0.5, str(np.round(lasso, 4)))

print("Walk-forward on a random walk: there is nothing to find")
grades = []
started = time.perf_counter()
for seed in range(5):
    grades.append(F.grade(F.walk_forward(random_walk(5040, seed=seed))))
elapsed = (time.perf_counter() - started) / 5
aucs = [g["contenders"]["model"]["auc"] for g in grades]
ranges = [g["contenders"]["model"]["auc_ci"] for g in grades]
check("no random walk is reported as having an edge",
      all(F.describe(g)[1] != "positive" for g in grades), str([F.describe(g)[0] for g in grades]))
check("the 90% range includes 0.5 for at least four of five",
      sum(lo < 0.5 < hi for lo, hi in ranges) >= 4, str(np.round(ranges, 3)))
check("on average no better than a coin flip (a leak would push this up)",
      np.mean(aucs) <= 0.52, str(np.round(aucs, 3)))
check(f"twenty years tests in reasonable time ({elapsed:.1f}s)", elapsed < 8)

print("Walk-forward on a market with momentum planted in it")
mom = momentum_market(5040, seed=3)
wf = F.walk_forward(mom)
g = F.grade(wf)
m = g["contenders"]["model"]
check("finds the edge: AUC clearly above 0.5", m["auc"] > 0.6, f"{m['auc']:.3f}")
check("and its 90% range clears 0.5", m["auc_ci"][0] > 0.5, str(m["auc_ci"]))
grid = F.coefficient_grid(wf)
ret_w = np.array(grid["return"])
check("the biggest return weight sits at a short look-back, where it was planted",
      int(np.argmax(ret_w)) <= F.WINDOWS_WEEKS.index(8), str(np.round(ret_w, 2)))
check("verdict reports an edge", F.describe(g)[1] == "positive")
check("the penalty switched most of the 46 noise features off", g["n_used"] < 35, str(g["n_used"]))

print("No peeking at the future")
base = momentum_market(5040, seed=11)
wf_a = F.walk_forward(base)
cut = wf_a.first_test + 900
cut_date = wf_a.dates[cut]
poisoned = base.copy()
after = poisoned.index > cut_date
poisoned.loc[after, "Close"] = poisoned.loc[after, "Close"].to_numpy()[::-1] * 4
poisoned.loc[after, "Volume"] *= 10
wf_b = F.walk_forward(poisoned)
same = np.array_equal(wf_a.prob[:cut + 1], wf_b.prob[:cut + 1], equal_nan=True)
check("rewriting everything after a date leaves every call up to that date unchanged", same)
check("and does change calls after it", not np.allclose(wf_a.prob[cut + 25:], wf_b.prob[cut + 25:]))
leaky = F.walk_forward(base, horizon=21)
check("each refit trains only on months that had finished",
      leaky.n_refits == math.ceil((len(leaky.prob) - leaky.first_test) / F.REFIT_EVERY))

print("Too little history is refused")
check("five years is not enough to grade a monthly call", F.walk_forward(random_walk(1260, 1)) is None)

print("The strategy arithmetic")
wf = F.walk_forward(mom)
st = F.strategy(wf, mom["Close"])
eq = st["equity"]
check("equity curves compound the daily returns",
      abs(eq["hold"].iloc[-1] - 1 - st["hold"]["total_return"]) < 1e-9)
check("yearly returns chain to the total",
      abs(np.prod([1 + r["strategy"] for r in st["yearly"]]) - 1 - st["strategy"]["total_return"]) < 1e-9)
check("time invested is a share", 0 <= st["time_invested"] <= 1)
always_in = F.WalkForward(**{**wf.__dict__, "prob": np.where(np.isnan(wf.prob), np.nan, 1.0)})
st_in = F.strategy(always_in, mom["Close"])
check("always invested equals buy-and-hold",
      abs(st_in["strategy"]["total_return"] - st_in["hold"]["total_return"]) < 1e-9
      and st_in["switches"] == 1)
check("timing a planted trend beats holding", st["strategy"]["sharpe"] > st["hold"]["sharpe"])
check("random timing at 100% invested earns what holding earns, before costs",
      abs(st_in["random_cagr"] - (np.prod(1 + mom["Close"].reindex(wf.dates[wf.first_test:]).pct_change().dropna()) ** (252 / (len(wf.dates) - wf.first_test - 1)) - 1)) < 1e-9)

print("The chart that fools people")
pc = F.persistence_check(random_walk(3000, 4)["Close"])
check("copying yesterday 'explains' over 90% of the price level", pc["r2_level"] > 0.9, f"{pc['r2_level']:.3f}")
check("and none of the move", pc["r2_move"] <= 0.01, f"{pc['r2_move']:.4f}")

print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All forecast checks passed.")
