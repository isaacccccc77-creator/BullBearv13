"""
Institutional quant models: statistical arbitrage, value at risk, and
portfolio construction.

Imports no Streamlit, so every number below can be tested directly — see
test_quant.py.

WHAT THESE ARE FOR
------------------
Each model here answers a question a risk or portfolio desk asks daily, and
each is implemented with the correction that separates the textbook version
from the one that survives contact with real returns:

  * Pairs trading is only meaningful if the spread is actually mean
    reverting, so cointegration is TESTED rather than assumed, and the
    half-life tells you whether the reversion is fast enough to trade.

  * VaR computed under a normal assumption understates tail losses, because
    equity returns are skewed and fat-tailed. Three estimators are shown side
    by side, plus Expected Shortfall, plus a coverage test that checks whether
    the VaR you were quoted was breached as often as it promised.

  * Mean-variance optimisation is an error maximiser: it pours weight into
    whichever asset had the luckiest estimated mean. The optimiser here
    reports its own out-of-sample decay rather than presenting the in-sample
    frontier as an achievement.

None of this predicts returns. All of it describes risk and relationships
that already exist in the data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, stats

TRADING_DAYS = 252


# ======================================================================
# 1. STATISTICAL ARBITRAGE
# ======================================================================
# The null distribution of the Augmented Dickey-Fuller statistic, obtained by
# simulating 60,000 random walks of length 500 rather than by copying
# constants out of a table. Two reasons: a mistyped critical value is a silent
# error no test would catch, and simulating it means the calibration itself is
# testable (test_quant.py checks the rejection rate against random walks).
#
# The large-sample values land on -3.45 / -2.87 / -2.57 at the 1/5/10% levels,
# which matches the published MacKinnon asymptotics, so the implementation and
# the table agree with the literature.
#
# The null distribution is nearly invariant in sample size above ~100 points,
# and every caller here uses at least a year of daily data, so one asymptotic
# grid is sufficient.
ADF_NULL_PROBS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2,
                  0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
ADF_NULL_QUANTILES = (-4.1235, -3.6612, -3.4483, -3.1313, -2.8702, -2.7025,
                      -2.5698, -2.3730, -2.2184, -1.9745, -1.7662, -1.5673,
                      -1.3664, -1.1451, -0.8589, -0.4355, -0.0650, 0.6322)

# Engle-Granger null: the SAME statistic, but computed on the residual of a
# fitted regression rather than on a series handed to you.
#
# This distinction is the difference between a pairs book that works and one
# full of spurious pairs. OLS chooses the hedge ratio that MINIMISES residual
# variance, so the residual is already fitted toward stationarity before the
# test ever sees it. Judging it against plain ADF critical values therefore
# over-rejects badly: measured on independent random walks, the plain values
# called 11% of them cointegrated at a nominal 5%. More than double.
#
# These quantiles come from 40,000 simulations of the actual null — regress
# one random walk on another, test the residual. The 5% value lands at -3.333
# against a published Engle-Granger value of about -3.34, so the simulation
# and the literature agree.
EG_NULL_QUANTILES = (-4.5866, -4.1019, -3.8929, -3.5920, -3.3333, -3.1651,
                     -3.0356, -2.8465, -2.6952, -2.4487, -2.2401, -2.0434,
                     -1.8533, -1.6464, -1.3908, -1.0108, -0.6316, 0.1137)


def adf_statistic(series: np.ndarray | pd.Series, lags: int = 1) -> float:
    """
    Augmented Dickey-Fuller t-statistic with a constant and no trend.

    Regresses the change in the series on its own lagged level plus lagged
    changes. The coefficient on the lagged level is the one that matters: a
    strongly negative t-statistic says the series pulls back toward its mean
    rather than wandering, which is the entire premise of a pairs trade.
    """
    y = np.asarray(series, dtype=float)
    y = y[np.isfinite(y)]
    dy = np.diff(y)
    n = len(dy) - lags
    if n < 20:
        return float("nan")

    target = dy[lags:]
    columns = [np.ones(n), y[lags:-1][:n]]
    for lag in range(1, lags + 1):
        columns.append(dy[lags - lag: len(dy) - lag][:n])
    design = np.column_stack([c[:n] for c in columns])

    beta, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design @ beta
    dof = n - design.shape[1]
    if dof <= 0:
        return float("nan")
    sigma2 = float(residuals @ residuals) / dof
    try:
        cov = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:
        return float("nan")
    se = np.sqrt(sigma2 * cov[1, 1])
    if se <= 0:
        return float("nan")
    return float(beta[1] / se)


def adf_pvalue(stat: float, regression_residual: bool = False) -> float:
    """
    Approximate p-value by interpolating the appropriate simulated null.

    Pass regression_residual=True when the tested series is the residual of a
    fitted relationship, as in a cointegration test — that null is shifted
    substantially more negative, and using the wrong one doubles the false
    positive rate. See the note on EG_NULL_QUANTILES.

    A low p-value means the series is unlikely to be a random walk, i.e. it
    shows evidence of mean reversion. Clamped to the simulated range rather
    than extrapolated, so an extreme statistic reports the boundary instead of
    an invented precision the simulation cannot support.
    """
    if stat is None or not np.isfinite(stat):
        return float("nan")
    quantiles = EG_NULL_QUANTILES if regression_residual else ADF_NULL_QUANTILES
    return float(np.clip(np.interp(stat, quantiles, ADF_NULL_PROBS),
                         ADF_NULL_PROBS[0], ADF_NULL_PROBS[-1]))


def half_life(spread: pd.Series) -> float:
    """
    Days for the spread to close half the distance back to its mean.

    Fits the Ornstein-Uhlenbeck discretisation Δs_t = λ·s_{t-1} + c + ε.
    A negative λ means reversion, and the half-life is -ln(2)/λ.

    This is the number that decides whether a cointegrated pair is tradeable
    at all. A statistically perfect relationship that takes 400 days to
    revert is an academic curiosity, not a position — financing and drift
    will eat it long before it converges.
    """
    s = pd.Series(spread).dropna()
    if len(s) < 30:
        return float("nan")
    lagged = s.shift(1).dropna()
    delta = s.diff().dropna()
    lagged, delta = lagged.align(delta, join="inner")
    if len(lagged) < 20 or lagged.std() == 0:
        return float("nan")
    reg = stats.linregress(lagged.to_numpy(), delta.to_numpy())
    if reg.slope >= 0:
        return float("inf")   # diverging, not reverting
    return float(-np.log(2) / reg.slope)


@dataclass
class PairResult:
    ticker_a: str
    ticker_b: str
    hedge_ratio: float
    alpha: float
    spread: pd.Series
    zscore: pd.Series
    adf_stat: float
    adf_pvalue: float
    half_life_days: float
    correlation: float
    current_z: float
    n_obs: int


def analyse_pair(price_a: pd.Series, price_b: pd.Series,
                 ticker_a: str = "A", ticker_b: str = "B",
                 zscore_window: int = 60) -> PairResult | None:
    """
    Engle-Granger two-step cointegration on a pair of price series.

    Step one regresses log(A) on log(B) to find the hedge ratio — how many
    units of B offset one unit of A. Step two tests whether the residual of
    that regression is stationary. If it is, the two prices share a long-run
    relationship and deviations from it are expected to close.

    Logs rather than raw prices, deliberately: the hedge ratio then means a
    constant proportional relationship, which is what you actually hold, and
    it stops a high-priced stock dominating the fit purely by scale.

    The z-score is ROLLING, not computed against the full-sample mean. Using
    the whole sample would let the spread's future inform today's signal —
    the most common way a pairs backtest is accidentally made to look good.
    """
    # Align on calendar DATE, not on the raw index.
    #
    # Two price series rarely carry byte-identical timestamps: yfinance returns
    # tz-aware stamps that differ between exchanges, and a US name joined
    # against a Hong Kong or London listing will not share a single index
    # entry. An exact-index join then produces an empty overlap and the whole
    # pair silently reports "no data" despite years of history on both sides.
    a = pd.Series(price_a.to_numpy(), index=pd.DatetimeIndex(price_a.index).normalize(), name="a")
    b = pd.Series(price_b.to_numpy(), index=pd.DatetimeIndex(price_b.index).normalize(), name="b")
    a = a[~a.index.duplicated(keep="last")]
    b = b[~b.index.duplicated(keep="last")]

    frame = pd.concat([a, b], axis=1, join="inner").dropna()
    frame = frame[(frame["a"] > 0) & (frame["b"] > 0)]
    if len(frame) < 120:
        return None

    log_a = np.log(frame["a"])
    log_b = np.log(frame["b"])
    if log_b.std() == 0 or log_a.std() == 0:
        return None

    reg = stats.linregress(log_b.to_numpy(), log_a.to_numpy())
    spread = log_a - (reg.slope * log_b + reg.intercept)

    rolling_mean = spread.rolling(zscore_window, min_periods=zscore_window // 2).mean()
    rolling_sd = spread.rolling(zscore_window, min_periods=zscore_window // 2).std()
    zscore = ((spread - rolling_mean) / rolling_sd.clip(lower=1e-9)).dropna()
    if zscore.empty:
        return None

    # regression_residual=True is load-bearing: the spread is an OLS residual,
    # so it must be judged against the Engle-Granger null, not the plain one.
    stat = adf_statistic(spread)
    return PairResult(
        ticker_a=ticker_a, ticker_b=ticker_b,
        hedge_ratio=float(reg.slope), alpha=float(reg.intercept),
        spread=spread, zscore=zscore,
        adf_stat=stat, adf_pvalue=adf_pvalue(stat, regression_residual=True),
        half_life_days=half_life(spread),
        correlation=float(np.corrcoef(log_a, log_b)[0, 1]),
        current_z=float(zscore.iloc[-1]),
        n_obs=int(len(frame)),
    )


def pair_verdict(result: PairResult, entry_z: float = 2.0) -> tuple[str, str, str]:
    """
    Turns the statistics into a plain reading: (headline, tone, explanation).

    The order of checks matters. Cointegration is the gate — without it the
    z-score is measuring deviation from a relationship that does not exist,
    and acting on it is not arbitrage but two uncorrelated bets. Half-life is
    the second gate for the reason given above.
    """
    if not np.isfinite(result.adf_pvalue) or result.adf_pvalue > 0.10:
        return ("Not cointegrated", "neutral",
                "The spread between these two behaves like a random walk, so there is no "
                "long-run relationship for it to revert to. A wide z-score here is not a "
                "mispricing — it is just two things that happen to have drifted apart.")

    hl = result.half_life_days
    if not np.isfinite(hl) or hl <= 0 or hl > 120:
        return ("Cointegrated but too slow", "neutral",
                f"The spread is statistically mean reverting (p = {result.adf_pvalue:.3f}) but the "
                f"half-life is {'undefined' if not np.isfinite(hl) else f'{hl:.0f} days'} — too slow "
                "to trade. Financing costs and drift would outlast the convergence.")

    z = result.current_z
    if abs(z) < entry_z:
        return ("Cointegrated, spread near fair", "neutral",
                f"Mean reverting with a {hl:.0f}-day half-life, but the spread sits {abs(z):.1f} "
                f"standard deviations from its mean — inside the ±{entry_z:.0f} band where there is "
                "nothing to act on.")

    rich, cheap = (result.ticker_a, result.ticker_b) if z > 0 else (result.ticker_b, result.ticker_a)
    return (f"Spread stretched: {rich} rich vs {cheap}", "bull" if z < 0 else "bear",
            f"The spread is {abs(z):.1f} standard deviations from its mean, with a {hl:.0f}-day "
            f"half-life and cointegration at p = {result.adf_pvalue:.3f}. Historically this gap has "
            f"closed; {rich} is the expensive leg relative to {cheap}. Historically is not a promise.")


def backtest_pair(result: PairResult, entry_z: float = 2.0, exit_z: float = 0.5,
                  stop_z: float = 4.0) -> dict:
    """
    Walks the spread forward one day at a time, trading the z-score.

    Enters when the spread stretches past entry_z, exits when it returns
    inside exit_z, and stops out past stop_z. Returns are the spread's own
    change while positioned — this is the dollar-neutral pair return, not a
    directional bet on either leg.

    The signal is lagged by one day. Deciding at today's close and earning
    today's move is the single most common error in a pairs backtest and it
    manufactures returns that cannot be captured.
    """
    z = result.zscore
    spread = result.spread.reindex(z.index)
    if len(z) < 40:
        return {"error": "Not enough overlapping history to test this pair."}

    position = np.zeros(len(z))
    current = 0
    for i, value in enumerate(z.to_numpy()):
        if current == 0:
            if value >= entry_z:
                current = -1          # spread rich: short A, long B
            elif value <= -entry_z:
                current = 1
        else:
            if abs(value) <= exit_z or abs(value) >= stop_z:
                current = 0
        position[i] = current

    # Yesterday's decision earns today's move.
    lagged = pd.Series(position, index=z.index).shift(1).fillna(0)
    spread_change = spread.diff().fillna(0)
    pnl = lagged * spread_change

    trades = int((lagged.diff().abs() > 0).sum())
    active = lagged != 0
    equity = pnl.cumsum()
    peak = equity.cummax()

    wins = int((pnl[active] > 0).sum())
    total = int(active.sum())

    return {
        "n_days": int(len(z)),
        "days_in_market": total,
        "exposure_pct": float(100 * total / len(z)) if len(z) else 0.0,
        "trades": trades,
        "total_return_log": float(equity.iloc[-1]) if len(equity) else 0.0,
        "win_rate_pct": float(100 * wins / total) if total else float("nan"),
        "sharpe": (float(pnl[active].mean() / pnl[active].std() * np.sqrt(TRADING_DAYS))
                   if total > 5 and pnl[active].std() > 0 else float("nan")),
        "max_drawdown": float((equity - peak).min()) if len(equity) else 0.0,
        "equity_curve": equity,
        "position": lagged,
    }


# ======================================================================
# 2. VALUE AT RISK
# ======================================================================
def _cf_expand(z: float, skew: float, excess_kurtosis: float) -> float:
    """The raw Cornish-Fisher polynomial at a normal quantile z."""
    return float(
        z
        + (z ** 2 - 1) * skew / 6
        + (z ** 3 - 3 * z) * excess_kurtosis / 24
        - (2 * z ** 3 - 5 * z) * (skew ** 2) / 36
    )


def cornish_fisher_z(alpha: float, skew: float,
                     excess_kurtosis: float) -> tuple[float, bool]:
    """
    Normal quantile adjusted for the distribution's actual shape.

    Returns (quantile, is_valid).

    Equity returns are left-skewed and fat-tailed, so the Gaussian quantile
    sits too close to the mean and the VaR built on it understates exactly the
    loss it exists to measure. Cornish-Fisher pushes the quantile out using
    the sample's own third and fourth moments.

    THE VALIDITY CHECK IS NOT OPTIONAL. Cornish-Fisher is a polynomial
    approximation, not a quantile function, and outside a limited region of
    (skew, kurtosis) it stops being monotonic in z — at which point it is no
    longer a quantile at all and can return a SMALLER tail loss than the plain
    Gaussian, which is the opposite of the correction's purpose. Sample skew
    is wildly unstable for genuinely fat-tailed data, so this is not an edge
    case: it fires on real return series.

    Monotonicity is therefore checked numerically across the tail before the
    result is trusted, and callers fall back to the historical quantile when
    it fails rather than quoting a number the expansion cannot support.
    """
    s = float(np.clip(skew, -2.0, 2.0))
    k = float(np.clip(excess_kurtosis, -2.0, 10.0))

    grid = np.linspace(-3.5, -0.5, 40)
    expanded = np.array([_cf_expand(float(z), s, k) for z in grid])
    monotonic = bool(np.all(np.diff(expanded) > 0))

    z_alpha = float(stats.norm.ppf(alpha))
    value = _cf_expand(z_alpha, s, k)

    # A "correction" that lands closer to the mean than the Gaussian quantile,
    # on data with fat tails, has broken down whatever the monotonicity check
    # says — the whole point is to move further out.
    if excess_kurtosis > 0 and value > z_alpha:
        monotonic = False

    return value, monotonic


def value_at_risk(returns: pd.Series, confidence: float = 0.95,
                  horizon_days: int = 1, portfolio_value: float = 100_000.0) -> dict:
    """
    VaR and Expected Shortfall by three methods, plus the distribution's shape.

    Historical uses the empirical quantile and assumes nothing about the
    shape. Gaussian assumes normality and is included mainly so the reader can
    see how much it understates. Cornish-Fisher corrects the Gaussian quantile
    for skew and fat tails and usually lands between the two.

    Expected Shortfall matters more than VaR and is reported alongside it.
    VaR says "you will lose at least this much on the worst 5% of days" and is
    silent about how much worse the worst day gets; ES averages the losses
    beyond the threshold and answers that. A desk that watches only VaR is
    blind to precisely the events that end funds.

    Horizon scaling uses sqrt(t), which assumes returns are independent across
    days. They are not — volatility clusters — so multi-day figures understate
    risk during a crisis. Stated here rather than buried.
    """
    r = pd.Series(returns).replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < 60:
        return {"error": "Need at least 60 days of returns to estimate VaR."}

    alpha = 1.0 - confidence
    scale = np.sqrt(max(horizon_days, 1))
    mu, sigma = float(r.mean()), float(r.std(ddof=1))
    skew = float(stats.skew(r))
    excess_kurt = float(stats.kurtosis(r))    # already excess (normal -> 0)

    hist_q = float(np.percentile(r, 100 * alpha))
    gauss_q = mu + sigma * stats.norm.ppf(alpha)
    cf_z, cf_valid = cornish_fisher_z(alpha, skew, excess_kurt)
    # When the expansion breaks down, fall back to the empirical quantile,
    # which assumes nothing, and say so rather than quoting a broken figure.
    cf_q = (mu + sigma * cf_z) if cf_valid else hist_q

    tail = r[r <= hist_q]
    es_q = float(tail.mean()) if len(tail) else hist_q

    def as_money(quantile: float) -> float:
        # Reported as a positive number: "you could lose this much".
        return float(abs(min(quantile, 0.0)) * scale * portfolio_value)

    jb_stat, jb_p = stats.jarque_bera(r)

    return {
        "confidence": confidence,
        "horizon_days": horizon_days,
        "portfolio_value": portfolio_value,
        "n_obs": int(len(r)),
        "historical_var_pct": float(abs(hist_q) * scale * 100),
        "gaussian_var_pct": float(abs(gauss_q) * scale * 100),
        "cornish_fisher_var_pct": float(abs(cf_q) * scale * 100),
        "cornish_fisher_valid": cf_valid,
        "expected_shortfall_pct": float(abs(es_q) * scale * 100),
        "historical_var_money": as_money(hist_q),
        "gaussian_var_money": as_money(gauss_q),
        "cornish_fisher_var_money": as_money(cf_q),
        "expected_shortfall_money": as_money(es_q),
        "mean_daily_pct": mu * 100,
        "vol_daily_pct": sigma * 100,
        "vol_annual_pct": sigma * np.sqrt(TRADING_DAYS) * 100,
        "skew": skew,
        "excess_kurtosis": excess_kurt,
        "jarque_bera_p": float(jb_p),
        "normality_rejected": bool(jb_p < 0.05),
        "worst_day_pct": float(r.min() * 100),
        "returns": r,
        "historical_quantile": hist_q,
    }


def kupiec_test(returns: pd.Series, var_quantile: float, confidence: float = 0.95) -> dict:
    """
    Kupiec proportion-of-failures test: was the VaR breached as often as promised?

    A 95% VaR should be exceeded on about 5% of days. Materially fewer means
    the model is too conservative and capital is being wasted; materially more
    means it is understating risk, which is the failure that matters. The
    likelihood-ratio statistic is chi-square with one degree of freedom.

    This is the step most retail implementations skip, and it is the only part
    that tells you whether the VaR number was ever any good.
    """
    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 100:
        return {"error": "Need at least 100 days to test VaR coverage."}

    expected_rate = 1.0 - confidence
    breaches = int((r < var_quantile).sum())
    observed_rate = breaches / n

    if breaches == 0 or breaches == n:
        return {"n_obs": n, "breaches": breaches,
                "observed_rate_pct": observed_rate * 100,
                "expected_rate_pct": expected_rate * 100,
                "lr_stat": float("nan"), "p_value": float("nan"),
                "verdict": "Too few breaches to test coverage meaningfully."}

    lr = -2 * (
        (n - breaches) * np.log(1 - expected_rate) + breaches * np.log(expected_rate)
        - (n - breaches) * np.log(1 - observed_rate) - breaches * np.log(observed_rate)
    )
    p = float(1 - stats.chi2.cdf(lr, df=1))

    if p >= 0.05:
        verdict = ("Coverage looks right — breaches happened about as often as the model "
                   "said they would.")
    elif observed_rate > expected_rate:
        verdict = ("Breached MORE often than promised. The model is understating risk, which "
                   "is the direction that actually hurts.")
    else:
        verdict = ("Breached LESS often than promised. Conservative rather than dangerous, "
                   "but it ties up capital against losses that did not arrive.")

    return {"n_obs": n, "breaches": breaches,
            "observed_rate_pct": observed_rate * 100,
            "expected_rate_pct": expected_rate * 100,
            "lr_stat": float(lr), "p_value": p, "verdict": verdict}


# ======================================================================
# 3. PORTFOLIO OPTIMISATION
# ======================================================================
def align_prices(series_by_ticker: dict) -> pd.DataFrame:
    """
    Builds a price matrix from per-ticker series, aligned on calendar date.

    Same trap as the pairs test: yfinance returns tz-aware timestamps that
    differ between exchanges, so building a DataFrame straight from the raw
    indices yields a mostly-NaN matrix that dropna() then empties completely —
    the optimiser reports "not enough data" while holding years of history for
    every name. Normalising to dates first is the whole fix.

    Inner-joined on purpose: an optimiser needs every asset observed on the
    same days, or the covariance matrix is estimated from different periods
    per pair and stops being a valid covariance at all.
    """
    normalised = {}
    for ticker, series in series_by_ticker.items():
        if series is None or len(series) == 0:
            continue
        clean = pd.Series(np.asarray(series, dtype=float),
                          index=pd.DatetimeIndex(series.index).normalize())
        clean = clean[~clean.index.duplicated(keep="last")]
        normalised[ticker] = clean.dropna()
    if len(normalised) < 2:
        return pd.DataFrame()
    return pd.concat(normalised, axis=1, join="inner").dropna()


def _portfolio_stats(weights: np.ndarray, mean_returns: np.ndarray,
                     cov: np.ndarray) -> tuple[float, float]:
    """Annualised (return, volatility) for a weight vector."""
    ret = float(weights @ mean_returns) * TRADING_DAYS
    vol = float(np.sqrt(weights @ cov @ weights)) * np.sqrt(TRADING_DAYS)
    return ret, vol


def _solve(objective, n_assets: int, cov: np.ndarray, extra=None) -> np.ndarray:
    """Long-only weights summing to 1, from an equal-weight start."""
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    if extra:
        constraints.extend(extra)
    bounds = tuple((0.0, 1.0) for _ in range(n_assets))
    start = np.repeat(1.0 / n_assets, n_assets)
    result = optimize.minimize(objective, start, method="SLSQP",
                               bounds=bounds, constraints=constraints,
                               options={"maxiter": 500, "ftol": 1e-10})
    weights = result.x if result.success else start
    weights = np.clip(weights, 0, None)
    total = weights.sum()
    return weights / total if total > 0 else start


def min_variance_weights(cov: np.ndarray) -> np.ndarray:
    """
    The lowest-volatility long-only portfolio.

    Worth its own entry because it uses only the covariance matrix and never
    touches expected returns. Means are estimated far less reliably than
    covariances — which is why minimum variance so often beats max Sharpe out
    of sample despite optimising for something narrower.
    """
    n = len(cov)
    return _solve(lambda w: w @ cov @ w, n, cov)


def max_sharpe_weights(mean_returns: np.ndarray, cov: np.ndarray,
                       risk_free: float = 0.0) -> np.ndarray:
    """The tangency portfolio: highest return per unit of volatility."""
    n = len(mean_returns)

    def negative_sharpe(w):
        ret, vol = _portfolio_stats(w, mean_returns, cov)
        return 1e6 if vol <= 0 else -(ret - risk_free) / vol

    return _solve(negative_sharpe, n, cov)


def risk_parity_weights(cov: np.ndarray) -> np.ndarray:
    """
    Weights where every holding contributes the same share of total risk.

    Not the same as equal weight: a volatile asset gets a smaller allocation
    so its risk contribution matches everyone else's. Like minimum variance it
    ignores expected returns, which is why it tends to be far more stable
    across periods than anything driven by estimated means.
    """
    n = len(cov)

    def dispersion(w):
        total_vol = np.sqrt(w @ cov @ w)
        if total_vol <= 0:
            return 1e6
        contributions = w * (cov @ w) / total_vol
        return float(np.sum((contributions - contributions.mean()) ** 2))

    return _solve(dispersion, n, cov)


def efficient_frontier(mean_returns: np.ndarray, cov: np.ndarray,
                       n_points: int = 40) -> pd.DataFrame:
    """
    The frontier, by minimising variance at each of a range of target returns.

    Solved rather than sampled. Randomly drawing thousands of weight vectors —
    the usual shortcut — does not find the frontier; it finds a fuzzy cloud
    whose upper edge merely looks like one, and in higher dimensions random
    weights concentrate near equal weight and never approach the corners at
    all.
    """
    n = len(mean_returns)
    lo = float(mean_returns.min()) * TRADING_DAYS
    hi = float(mean_returns.max()) * TRADING_DAYS
    rows = []
    for target in np.linspace(lo, hi, n_points):
        constraint = [{"type": "eq",
                       "fun": (lambda w, t=target: float(w @ mean_returns) * TRADING_DAYS - t)}]
        w = _solve(lambda w: w @ cov @ w, n, cov, extra=constraint)
        ret, vol = _portfolio_stats(w, mean_returns, cov)
        rows.append({"return_pct": ret * 100, "vol_pct": vol * 100,
                     "sharpe": ret / vol if vol > 0 else np.nan})
    frontier = pd.DataFrame(rows).sort_values("vol_pct").reset_index(drop=True)
    # Keep only the efficient half: points where nothing else offers more
    # return for less risk.
    return frontier[frontier["return_pct"].cummax() == frontier["return_pct"]].reset_index(drop=True)


def optimise_portfolio(prices: pd.DataFrame, risk_free: float = 0.0,
                       holdout_frac: float = 0.3) -> dict | None:
    """
    Builds four portfolios and reports how each one held up out of sample.

    The holdout is the point of this function. Mean-variance optimisation is
    an error maximiser: it cannot distinguish a genuinely superior asset from
    one whose mean was overestimated by luck, so it piles into the latter.
    Fitting on the first 70% of history and measuring on the last 30% shows
    how much of the in-sample Sharpe was real — usually most of it was not.

    Equal weight is included as the baseline every optimiser has to beat, and
    frequently does not.
    """
    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    returns = returns.loc[:, returns.std() > 0]
    if returns.shape[1] < 2 or len(returns) < 120:
        return None

    split = int(len(returns) * (1 - holdout_frac))
    in_sample, out_sample = returns.iloc[:split], returns.iloc[split:]
    if len(in_sample) < 60 or len(out_sample) < 20:
        in_sample, out_sample = returns, None

    mu = in_sample.mean().to_numpy()
    cov = in_sample.cov().to_numpy()
    n = len(mu)

    portfolios = {
        "Maximum Sharpe": max_sharpe_weights(mu, cov, risk_free),
        "Minimum variance": min_variance_weights(cov),
        "Risk parity": risk_parity_weights(cov),
        "Equal weight": np.repeat(1.0 / n, n),
    }

    rows = []
    for name, w in portfolios.items():
        ret_in, vol_in = _portfolio_stats(w, mu, cov)
        row = {"Portfolio": name,
               "In-sample return %": ret_in * 100,
               "In-sample vol %": vol_in * 100,
               "In-sample Sharpe": (ret_in - risk_free) / vol_in if vol_in > 0 else np.nan}
        if out_sample is not None and len(out_sample) > 5:
            mu_out = out_sample.mean().to_numpy()
            cov_out = out_sample.cov().to_numpy()
            ret_out, vol_out = _portfolio_stats(w, mu_out, cov_out)
            row["Out-of-sample return %"] = ret_out * 100
            row["Out-of-sample vol %"] = vol_out * 100
            row["Out-of-sample Sharpe"] = (ret_out - risk_free) / vol_out if vol_out > 0 else np.nan
        rows.append(row)

    weights_frame = pd.DataFrame(
        {name: w for name, w in portfolios.items()}, index=returns.columns
    ).T

    return {
        "tickers": list(returns.columns),
        "summary": pd.DataFrame(rows),
        "weights": weights_frame,
        "frontier": efficient_frontier(mu, cov),
        "correlation": returns.corr(),
        "n_days": int(len(returns)),
        "in_sample_days": int(len(in_sample)),
        "out_sample_days": int(len(out_sample)) if out_sample is not None else 0,
        "individual": pd.DataFrame({
            "Ticker": returns.columns,
            "Return %": returns.mean().to_numpy() * TRADING_DAYS * 100,
            "Volatility %": returns.std().to_numpy() * np.sqrt(TRADING_DAYS) * 100,
        }),
    }


def optimiser_verdict(result: dict) -> str:
    """One honest sentence about how much of the optimisation survived."""
    summary = result.get("summary")
    if summary is None or "Out-of-sample Sharpe" not in summary.columns:
        return ("No holdout was available, so every figure here is in-sample and describes "
                "the past rather than testing anything.")

    best_in = summary.loc[summary["In-sample Sharpe"].idxmax(), "Portfolio"]
    best_out = summary.loc[summary["Out-of-sample Sharpe"].idxmax(), "Portfolio"]
    equal_out = float(summary.loc[summary["Portfolio"] == "Equal weight",
                                  "Out-of-sample Sharpe"].iloc[0])
    top_out = float(summary["Out-of-sample Sharpe"].max())

    if best_out == "Equal weight":
        return (f"{best_in} won in-sample, but out of sample equal weight beat every optimised "
                "portfolio. That is the usual result, and it is the strongest argument against "
                "trusting an optimiser's allocation: it fits estimation error as readily as signal.")
    if top_out - equal_out < 0.1:
        return (f"{best_in} won in-sample and {best_out} out of sample, but the margin over equal "
                f"weight is {top_out - equal_out:+.2f} Sharpe — inside the noise. The optimisation "
                "did not add anything you could rely on.")
    return (f"{best_in} won in-sample and {best_out} held up out of sample, beating equal weight by "
            f"{top_out - equal_out:+.2f} Sharpe. One holdout on one basket over one period is a "
            "single observation, not evidence the allocation generalises.")
