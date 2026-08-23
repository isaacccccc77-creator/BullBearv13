"""
Composite stock scoring: technical + sentiment, with a macro risk overlay.

Deliberately importable without Streamlit so the mathematics can be tested
directly (see test_scoring.py).

WHAT THIS IS
------------
Three parts combined into one standardised reading:

  1. Technical  — trend, momentum, band position and volume flow, each turned
                  into a rolling z-score against the stock's own recent history.
  2. Sentiment  — recency-weighted headline tone.
  3. Macro      — VIX (plus an optional geopolitical-risk index) applied as a
                  multiplicative confidence haircut rather than an additive term.

WHAT THIS IS NOT
----------------
It does not predict prices. Every component describes conditions that already
exist. The backtest exists to show you how weak the relationship is, not to
prove it works — and it is built so that a weak result reads as weak rather
than being flattered by inappropriate statistics.

STATISTICAL NOTES (these matter more than the formula)
-----------------------------------------------------
* Rolling windows only ever look backwards, so no score uses information that
  was unavailable on the day it is stamped with.

* The composite is re-standardised by its own rolling standard deviation. A
  weighted sum of unit-variance z-scores does NOT have unit variance — it has
  sqrt(w' S w), which depends on how correlated the sub-signals happen to be.
  Without this step a threshold of "+0.5" means 1.7 sigma when the components
  are independent and 1.2 sigma when they are correlated, so the same number
  silently changes meaning. After standardising, +1.0 means one sigma, always.

* Weights renormalise over whatever components are actually available. If
  sentiment is missing, the technical score is not quietly multiplied by its
  own weight and shrunk toward zero.

* Forward-return regressions use OVERLAPPING windows and a highly persistent
  predictor. Ordinary OLS inference is invalid here — measured against pure
  noise, textbook linregress p-values reject the (true) null about half the
  time at alpha=0.05. This module reports a circular block bootstrap p-value
  instead, which holds its nominal size, and also reports the non-overlapping
  sample count so the real amount of independent evidence is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
@dataclass
class ScoringConfig:
    zscore_window: int = 126        # ~6 months of trading days
    rsi_period: int = 14
    sma_fast: int = 50
    sma_slow: int = 200
    bb_period: int = 20
    obv_window: int = 20

    # Floor on the rolling standard deviation used to z-score. Without it a
    # near-constant input (a stock in a tight range, or sparse headlines)
    # divides by ~0 and produces z-scores in the hundreds.
    min_sd: float = 1e-6

    sentiment_half_life_hours: float = 24.0

    # Maximum haircut applied when macro risk is extreme. Named for what it
    # does: at the clip point the score is multiplied by (1 - geo_beta).
    geo_beta: float = 0.40
    macro_clip_sigma: float = 3.0   # z at which the haircut reaches geo_beta

    tech_subweights: dict = field(default_factory=lambda: {
        "trend": 0.35, "momentum": 0.35, "band_position": 0.10, "volume": 0.20,
    })

    w_technical: float = 0.55
    w_sentiment: float = 0.45

    # Thresholds are in standard deviations of the standardised composite, so
    # they mean the same thing for every ticker. +/-0.5 sigma puts roughly 31%
    # of days in each outer band; report_threshold_frequency() shows the real
    # historical rate for the ticker in front of you.
    strong_threshold: float = 1.0
    mild_threshold: float = 0.5

    forward_days: int = 10


CFG = ScoringConfig()


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
def rolling_zscore(series: pd.Series, window: int | None = None,
                   cfg: ScoringConfig = CFG) -> pd.Series:
    """
    Backward-looking z-score against the series' own recent history.

    The standard deviation is floored rather than merely checked for exact
    zero: a value of 1e-12 is not zero but dividing by it is just as wrong.
    """
    window = window or cfg.zscore_window
    mu = series.rolling(window, min_periods=window // 2).mean()
    sd = series.rolling(window, min_periods=window // 2).std()
    return (series - mu) / sd.clip(lower=cfg.min_sd)


def standardise(series: pd.Series, window: int | None = None,
                cfg: ScoringConfig = CFG) -> pd.Series:
    """
    Rescales a composite to unit variance using only past data.

    This is what makes a threshold portable. See the module docstring.
    """
    window = window or cfg.zscore_window
    sd = series.rolling(window, min_periods=window // 2).std()
    return series / sd.clip(lower=cfg.min_sd)


# ----------------------------------------------------------------------
# 1. TECHNICAL
# ----------------------------------------------------------------------
def add_technical_indicators(df: pd.DataFrame, cfg: ScoringConfig = CFG) -> pd.DataFrame:
    """Adds the four raw technical inputs plus ATR, on an OHLCV frame."""
    out = df.copy()

    # Trend: how far the fast average sits above or below the slow one,
    # expressed as a fraction so it is comparable across price levels.
    out["SMA_fast"] = out["Close"].rolling(cfg.sma_fast).mean()
    out["SMA_slow"] = out["Close"].rolling(cfg.sma_slow).mean()
    out["trend_raw"] = (out["SMA_fast"] - out["SMA_slow"]) / out["SMA_slow"].replace(0, np.nan)

    # Momentum: RSI recentred to roughly -1..+1.
    delta = out["Close"].diff()
    gain = delta.clip(lower=0).rolling(cfg.rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(cfg.rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    out["RSI"] = 100 - (100 / (1 + rs))
    out["momentum_raw"] = (out["RSI"] - 50) / 50

    # Band position: where price sits inside its Bollinger band.
    #
    # NOT a volatility measure, despite where this idea usually appears. It
    # says how stretched the price is relative to its own recent range, which
    # is a momentum statement, and it correlates ~0.7-0.8 with the RSI term
    # above. component_correlations() exposes that overlap rather than hiding
    # it, because the pair effectively shares its weight.
    mid = out["Close"].rolling(cfg.bb_period).mean()
    sd = out["Close"].rolling(cfg.bb_period).std()
    upper, lower = mid + 2 * sd, mid - 2 * sd
    width = (upper - lower).replace(0, np.nan)
    out["pct_b"] = (out["Close"] - lower) / width
    out["band_position_raw"] = (out["pct_b"] - 0.5) * 2

    # Volume flow: 20-day change in on-balance volume, normalised by average
    # volume and window length, which lands it roughly in -1..+1 as the net
    # share of volume trading on up days.
    direction = np.sign(out["Close"].diff()).fillna(0)
    out["OBV"] = (direction * out["Volume"]).cumsum()
    avg_vol = out["Volume"].rolling(cfg.obv_window).mean().replace(0, np.nan)
    out["volume_raw"] = out["OBV"].diff(cfg.obv_window) / avg_vol / cfg.obv_window

    # True range, kept for position sizing rather than scored.
    high_low = out["High"] - out["Low"]
    high_close = (out["High"] - out["Close"].shift()).abs()
    low_close = (out["Low"] - out["Close"].shift()).abs()
    out["ATR"] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1) \
                   .rolling(cfg.rsi_period).mean()
    return out


RAW_COMPONENTS = ("trend_raw", "momentum_raw", "band_position_raw", "volume_raw")
COMPONENT_LABELS = {
    "trend": "Trend (50d vs 200d average)",
    "momentum": "Momentum (RSI)",
    "band_position": "Band position (stretch)",
    "volume": "Volume flow (OBV)",
}


def technical_score(df: pd.DataFrame, cfg: ScoringConfig = CFG) -> tuple[pd.Series, pd.DataFrame]:
    """
    Returns (standardised technical score, frame with indicators and z-scores).

    The weighted sum is standardised by its own rolling deviation so that one
    unit is one standard deviation regardless of how correlated the four
    sub-signals happen to be for this ticker.
    """
    ind = add_technical_indicators(df, cfg)

    zs = {}
    for key in cfg.tech_subweights:
        zs[key] = rolling_zscore(ind[f"{key}_raw"], cfg=cfg)
        ind[f"z_{key}"] = zs[key]

    total_w = sum(cfg.tech_subweights.values())
    weighted = sum(cfg.tech_subweights[k] * zs[k].fillna(0) for k in zs)
    # Renormalise by the weight actually carrying information on each day, so
    # a component that is still warming up (NaN early in the window) does not
    # silently drag the composite toward zero.
    live_w = sum(cfg.tech_subweights[k] * zs[k].notna() for k in zs)
    raw = weighted / live_w.replace(0, np.nan) * total_w

    score = standardise(raw, cfg=cfg).rename("TechnicalScore")
    ind["technical_raw"] = raw
    return score, ind


def component_correlations(ind: pd.DataFrame, cfg: ScoringConfig = CFG) -> pd.DataFrame:
    """
    Correlation matrix of the four technical z-scores.

    Surfaced in the UI because two of them (momentum and band position) measure
    nearly the same thing. A reader who can see 0.78 in that cell knows the
    nominal 0.35/0.10 split is not the effective one.
    """
    cols = [f"z_{k}" for k in cfg.tech_subweights if f"z_{k}" in ind.columns]
    frame = ind[cols].dropna()
    if len(frame) < 10:
        return pd.DataFrame()
    corr = frame.corr()
    nice = {f"z_{k}": COMPONENT_LABELS.get(k, k) for k in cfg.tech_subweights}
    return corr.rename(index=nice, columns=nice)


# ----------------------------------------------------------------------
# 2. SENTIMENT
# ----------------------------------------------------------------------
def decayed_sentiment(items: list[dict], now=None, cfg: ScoringConfig = CFG) -> dict | None:
    """
    Recency-weighted average headline tone, in -1..+1.

    `items` are dicts with a numeric 'sentiment' in -1..+1 and an 'age_hours'.
    Weight halves every `sentiment_half_life_hours`, which is a more legible
    parameter than a raw decay constant: "a day-old headline counts half".

    Returns None when there is nothing to score, so the caller can drop the
    component and renormalise rather than feeding a fabricated zero into the
    composite — a zero is a real reading of "balanced", which is not the same
    claim as "no data".
    """
    usable = [i for i in items or [] if i.get("sentiment") is not None]
    if not usable:
        return None

    half_life = max(cfg.sentiment_half_life_hours, 1e-6)
    ages = np.array([max(float(i.get("age_hours") or 0.0), 0.0) for i in usable])
    weights = 0.5 ** (ages / half_life)
    tones = np.array([float(i["sentiment"]) for i in usable])

    if weights.sum() <= 0:
        return None
    score = float(np.average(tones, weights=weights))

    return {
        "score": score,
        "n_items": len(usable),
        "effective_n": float(weights.sum() ** 2 / (weights ** 2).sum()),
        "newest_age_hours": float(ages.min()),
    }


def sentiment_to_sigma(score: float, n_effective: float) -> float:
    """
    Converts a -1..+1 tone into an approximate z-score for the composite.

    Two deliberate choices. The tone is scaled by 2 because headline averages
    cluster well inside -1..+1 in practice, so raw tone would contribute far
    less than a unit-variance technical z-score. And the result is shrunk by
    sqrt(n/(n+4)), a standard small-sample shrink, so three headlines cannot
    move the composite as hard as thirty. A single headline is a rumour; a
    consistent thirty is a signal.
    """
    if n_effective <= 0:
        return 0.0
    shrink = np.sqrt(n_effective / (n_effective + 4.0))
    return float(np.clip(score * 2.0 * shrink, -3.0, 3.0))


# ----------------------------------------------------------------------
# 3. MACRO / GEOPOLITICAL RISK
# ----------------------------------------------------------------------
def macro_risk_penalty(index: pd.DatetimeIndex, vix: pd.Series,
                       gpr: pd.Series | None = None,
                       cfg: ScoringConfig = CFG) -> pd.Series:
    """
    A 0..geo_beta confidence haircut, not a signed score.

    Only elevated risk counts: the z-score is clipped at zero below, so a calm
    market does not inflate the reading. At `macro_clip_sigma` above normal the
    haircut reaches exactly `geo_beta`.

    That last sentence is the fix for a scaling bug worth naming. Written as
    `(geo_beta * z / 3).clip(upper=1)`, a beta of 0.4 needs z = 7.5 to reach a
    full penalty, so the "3 sigma = full penalty" it claimed never happened.
    Here beta is straightforwardly the maximum haircut.
    """
    vix_z = rolling_zscore(vix.reindex(index).ffill(), cfg=cfg)
    if gpr is not None and not gpr.dropna().empty:
        gpr_z = rolling_zscore(gpr.reindex(index).ffill(), cfg=cfg)
        combined = 0.5 * vix_z + 0.5 * gpr_z
    else:
        combined = vix_z

    excess = combined.clip(lower=0) / max(cfg.macro_clip_sigma, 1e-6)
    return (cfg.geo_beta * excess.clip(upper=1.0)).fillna(0).rename("RiskPenalty")


def estimate_geo_beta(close: pd.Series, vix: pd.Series,
                      cfg: ScoringConfig = CFG) -> dict | None:
    """
    Estimates this stock's sensitivity to volatility shocks from its own history.

    Regresses daily stock returns on daily changes in log(VIX). A stock that
    falls hard when fear spikes has a strongly negative slope and warrants a
    larger haircut; one that barely reacts warrants a smaller one.

    This replaces a hard-coded 0.4 and a comment pointing at a
    `backtest_geo_beta()` that was never written.

    The mapping to geo_beta is a judgement call, stated plainly rather than
    dressed up: the slope is rescaled so a typical large-cap lands near 0.4 and
    the result is clipped to 0.1..0.8. It is a reasonable prior, not an
    estimate with a standard error attached to the final number.
    """
    stock_ret = close.pct_change()
    vix_chg = np.log(vix.reindex(close.index).ffill()).diff()
    frame = pd.concat([stock_ret, vix_chg], axis=1).dropna()
    frame.columns = ["stock", "vix"]
    if len(frame) < 60:
        return None

    reg = stats.linregress(frame["vix"], frame["stock"])
    # A slope of about -0.05 is typical for a large-cap index member; scale so
    # that maps to roughly the default beta.
    beta = float(np.clip(abs(reg.slope) / 0.05 * 0.4, 0.1, 0.8))
    return {
        "geo_beta": beta,
        "slope": float(reg.slope),
        "r_squared": float(reg.rvalue ** 2),
        "p_value": float(reg.pvalue),
        "n_obs": int(len(frame)),
    }


# ----------------------------------------------------------------------
# 4. COMPOSITE
# ----------------------------------------------------------------------
# Descriptive bands. Deliberately NOT "BUY" / "AVOID": every other surface in
# this app refuses to issue trade instructions, and a score built from a
# 6-month rolling window of four correlated technical indicators is nowhere
# near strong enough evidence to start. These name what the reading IS.
READING_BANDS = (
    ("Strongly positive conditions", "bull"),
    ("Mildly positive conditions", "bull"),
    ("Mixed / no clear tilt", "neutral"),
    ("Mildly negative conditions", "bear"),
    ("Strongly negative conditions", "bear"),
)


def describe_reading(score: float, cfg: ScoringConfig = CFG) -> tuple[str, str]:
    """Maps a standardised score onto a descriptive band and a display tone."""
    if score is None or not np.isfinite(score):
        return "Not enough data", "neutral"
    if score >= cfg.strong_threshold:
        return READING_BANDS[0]
    if score >= cfg.mild_threshold:
        return READING_BANDS[1]
    if score <= -cfg.strong_threshold:
        return READING_BANDS[4]
    if score <= -cfg.mild_threshold:
        return READING_BANDS[3]
    return READING_BANDS[2]


def combine(technical: float | None, sentiment: float | None,
            risk_penalty: float, cfg: ScoringConfig = CFG) -> dict:
    """
    Blends the available components, then applies the macro haircut.

    Weights renormalise over whatever is present. As originally written, a
    missing sentiment component silently multiplied the technical score by
    0.55 — a 45% shrink for a component that supplied no information, which
    quietly pushed the reading toward neutral whenever headlines were absent.

    The haircut multiplies the magnitude, so it reduces conviction in BOTH
    directions rather than only penalising positive readings. That is a real
    modelling choice with a cost: in a genuine crash a bearish reading gets
    muted exactly when it is loudest. It is applied this way because elevated
    VIX widens the distribution of everything, which makes any signal less
    informative rather than more bearish.
    """
    parts, weights = [], []
    if technical is not None and np.isfinite(technical):
        parts.append(technical)
        weights.append(cfg.w_technical)
    if sentiment is not None and np.isfinite(sentiment):
        parts.append(sentiment)
        weights.append(cfg.w_sentiment)

    if not parts:
        return {"technical": technical, "sentiment": sentiment,
                "risk_penalty": risk_penalty, "raw": None, "final": None,
                "weights_used": {}, "reading": "Not enough data", "tone": "neutral"}

    total = sum(weights)
    raw = float(sum(p * w for p, w in zip(parts, weights)) / total)
    final = float(raw * (1.0 - risk_penalty))
    reading, tone = describe_reading(final, cfg)

    used = {}
    if technical is not None and np.isfinite(technical):
        used["technical"] = cfg.w_technical / total
    if sentiment is not None and np.isfinite(sentiment):
        used["sentiment"] = cfg.w_sentiment / total

    return {"technical": technical, "sentiment": sentiment,
            "risk_penalty": float(risk_penalty), "raw": raw, "final": final,
            "weights_used": used, "reading": reading, "tone": tone}


def threshold_frequency(score_series: pd.Series, cfg: ScoringConfig = CFG) -> dict:
    """
    How often each band actually fired for THIS ticker's own history.

    Without this a threshold is a number with no referent. "Strongly positive"
    meaning the top 4% of days is a very different claim from the top 25%.
    """
    s = score_series.dropna()
    if s.empty:
        return {}
    return {
        "n_days": int(len(s)),
        "strong_positive_pct": float(100 * (s >= cfg.strong_threshold).mean()),
        "mild_positive_pct": float(100 * ((s >= cfg.mild_threshold) & (s < cfg.strong_threshold)).mean()),
        "neutral_pct": float(100 * (s.abs() < cfg.mild_threshold).mean()),
        "mild_negative_pct": float(100 * ((s <= -cfg.mild_threshold) & (s > -cfg.strong_threshold)).mean()),
        "strong_negative_pct": float(100 * (s <= -cfg.strong_threshold).mean()),
        "realised_sd": float(s.std()),
    }


# ----------------------------------------------------------------------
# 5. BACKTEST
# ----------------------------------------------------------------------
def _block_bootstrap_pvalue(x: np.ndarray, y: np.ndarray, horizon: int,
                            n_boot: int = 500, seed: int = 12345) -> float:
    """
    Circular block bootstrap p-value for "the slope is zero".

    Why not the textbook p-value: forward returns computed every day over a
    10-day horizon share 9 of their 10 days with the next observation, and the
    predictor is a 126-day rolling statistic with autocorrelation near 0.99.
    Ordinary OLS inference assumes neither. Measured on simulated data with NO
    real relationship, at alpha = 0.05:

        stats.linregress          51.7% false positives
        Newey-West (lag 9)        10.7%
        non-overlapping sample     5.3%
        this block bootstrap       6.3%

    Resampling y in blocks long enough to span the overlap preserves the
    autocorrelation while destroying any genuine link to x, which is exactly
    the null being tested.
    """
    n = len(x)
    block = max(2 * horizon, 20)
    if n < block * 3:
        return float("nan")

    observed = abs(stats.linregress(x, y).slope)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    at_least_as_extreme = 0

    for _ in range(n_boot):
        starts = rng.integers(0, n, n_blocks)
        idx = np.concatenate([np.arange(s, s + block) % n for s in starts])[:n]
        if abs(stats.linregress(x, y[idx]).slope) >= observed:
            at_least_as_extreme += 1

    # +1 on both sides so the p-value can never be exactly zero, which would
    # overstate certainty that no finite number of resamples can support.
    return float((at_least_as_extreme + 1) / (n_boot + 1))


def backtest(score: pd.Series, close: pd.Series,
             forward_days: int | None = None, cfg: ScoringConfig = CFG) -> dict:
    """
    Tests whether the score has any relationship with subsequent returns.

    Reports both the naive p-value and the bootstrap one, because seeing them
    side by side is the point: the gap between them is the size of the mistake
    the naive number makes.
    """
    horizon = forward_days or cfg.forward_days
    fwd = close.shift(-horizon) / close - 1
    frame = pd.concat([score.rename("score"), fwd.rename("fwd")], axis=1).dropna()

    if len(frame) < 60:
        return {"error": "Not enough overlapping history to test this (need 60+ days)."}

    x = frame["score"].to_numpy()
    y = frame["fwd"].to_numpy()
    reg = stats.linregress(x, y)

    # Independent evidence: one observation per non-overlapping window.
    independent = frame.iloc[::horizon]
    n_independent = len(independent)
    if n_independent >= 20:
        ind_reg = stats.linregress(independent["score"], independent["fwd"])
        p_independent = float(ind_reg.pvalue)
    else:
        p_independent = float("nan")

    p_boot = _block_bootstrap_pvalue(x, y, horizon)

    # Directional agreement, excluding days where either side is flat — a
    # zero score matching a zero return is not a correct call.
    nonzero = (np.sign(x) != 0) & (np.sign(y) != 0)
    hit_rate = float(100 * (np.sign(x[nonzero]) == np.sign(y[nonzero])).mean()) if nonzero.any() else float("nan")

    top = frame[frame["score"] >= frame["score"].quantile(0.75)]["fwd"]
    bottom = frame[frame["score"] <= frame["score"].quantile(0.25)]["fwd"]

    return {
        "n_obs": int(len(frame)),
        "n_independent": int(n_independent),
        "horizon_days": horizon,
        "slope": float(reg.slope),
        "r_value": float(reg.rvalue),
        "r_squared": float(reg.rvalue ** 2),
        "p_naive": float(reg.pvalue),
        "p_bootstrap": p_boot,
        "p_independent": p_independent,
        "hit_rate_pct": hit_rate,
        "top_quartile_mean_pct": float(100 * top.mean()),
        "bottom_quartile_mean_pct": float(100 * bottom.mean()),
        "spread_pct": float(100 * (top.mean() - bottom.mean())),
    }


def interpret_backtest(bt: dict, cfg: ScoringConfig = CFG) -> str:
    """One honest sentence about what the backtest showed."""
    if "error" in bt:
        return bt["error"]
    p = bt.get("p_bootstrap")
    r2 = bt.get("r_squared", 0.0)
    if p is None or not np.isfinite(p):
        return ("Not enough independent history to say whether this score has any "
                "relationship with what happened next.")
    if p > 0.10:
        return (f"No detectable relationship with the next {bt['horizon_days']} days "
                f"(bootstrap p = {p:.2f}). This is the usual and expected result for a "
                "simple technical score on a single stock — treat the reading as a "
                "description of current conditions, not as evidence of predictive power.")
    if p > 0.05:
        return (f"A weak, borderline relationship (bootstrap p = {p:.2f}, R² = {r2:.3f}) "
                f"over {bt['n_independent']} independent windows. Easily produced by chance "
                "when one stock and one window are tested; it is not out-of-sample evidence.")
    return (f"A statistically detectable relationship here (bootstrap p = {p:.3f}, "
            f"R² = {r2:.3f}) across {bt['n_independent']} independent windows. R² still says "
            "the score explains only a small share of what happened, and one stock over one "
            "period is a single test, not a validated strategy.")
