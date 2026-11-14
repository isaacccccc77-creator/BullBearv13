"""
Turns price history into the JSON the pages draw.

Nothing here invents a formula. Each function fetches data through
`market`, calls the tested modules at the repository root, and reshapes
the answer. A value that could not be computed is None, never zero:
zero is a reading, and "no data" is a different claim.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import deals
import forecast
import quant
import scoring
import verdict

from . import market

RANGES = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "5Y": 1260}


def jsonable(value):
    """Recursively make numpy/pandas values JSON-safe; NaN and inf become None."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# Overview
# ----------------------------------------------------------------------
def overview(ticker: str, range_key: str = "1Y") -> dict:
    df = market.history(ticker)
    facts = market.info(ticker)
    close = df["Close"]

    window = df.tail(RANGES.get(range_key, 252))
    sma50 = close.rolling(50).mean().reindex(window.index)
    sma200 = close.rolling(200).mean().reindex(window.index)
    series = [
        {"t": _day(i), "c": r.Close, "v": r.Volume,
         "sma50": sma50.loc[i], "sma200": sma200.loc[i]}
        for i, r in window.iterrows()
    ]

    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    year = close.tail(252)
    log_ret = np.log(close / close.shift()).dropna().tail(252)
    drawdown = (year / year.cummax() - 1).min()
    start = float(window["Close"].iloc[0])

    return jsonable({
        "ticker": ticker,
        "demo": market.DEMO,
        "name": facts.get("shortName") or facts.get("longName") or ticker,
        "currency": facts.get("currency") or "USD",
        "sector": facts.get("sector"),
        "industry": facts.get("industry"),
        "price": last,
        "change": last - prev,
        "change_pct": (last / prev - 1) * 100,
        "as_of": _day(df.index[-1]),
        "range": range_key,
        "range_change_pct": (last / start - 1) * 100,
        "series": series,
        "stats": {
            "high_52w": float(year.max()),
            "low_52w": float(year.min()),
            "vol_annual_pct": float(log_ret.std() * np.sqrt(252) * 100),
            "max_drawdown_pct": float(drawdown * 100),
            "market_cap": facts.get("marketCap"),
            "trailing_pe": facts.get("trailingPE"),
            "forward_pe": facts.get("forwardPE"),
            "beta": facts.get("beta"),
            "profit_margin_pct": _pct(facts.get("profitMargins")),
            "revenue_growth_pct": _pct(facts.get("revenueGrowth")),
            "debt_to_equity": facts.get("debtToEquity"),
            "analyst_target": facts.get("targetMeanPrice"),
            "analyst_count": facts.get("numberOfAnalystOpinions"),
            "analyst_view": facts.get("recommendationKey"),
        },
    })


def _pct(fraction):
    return None if fraction is None else float(fraction) * 100


# ----------------------------------------------------------------------
# Headlines and their tone
# ----------------------------------------------------------------------
# Finance phrases are checked before general tone, negatives first:
# "record profit misses expectations" is a miss, and a general-purpose
# sentiment model reads it as good news because of "record" and "profit".
NEGATIVE = [
    r"miss(?:es|ed)?\b.{0,25}\b(expectations|estimates|forecasts?)",
    r"below\b.{0,15}\b(expectations|estimates)",
    r"cuts?\b.{0,15}\b(guidance|forecast|outlook)",
    r"\bprofit warning\b", r"\bdowngrad\w*", r"\brecall(?:s|ed)?\b",
    r"\blawsuit\b", r"\blayoffs?\b", r"\binvestigat\w*", r"\bfraud\b",
    r"\bplunge\w*", r"\bslump\w*", r"warns?\b.{0,15}\b(profit|outlook|guidance)",
]
POSITIVE = [
    r"beat(?:s)?\b.{0,25}\b(expectations|estimates|forecasts?)",
    r"above\b.{0,15}\b(expectations|estimates)",
    r"raise(?:s|d)?\b.{0,15}\b(guidance|forecast|outlook)",
    r"\bupgrad\w*", r"\bsurge\w*", r"\brally\w*",
    r"record\b.{0,15}\b(profit|revenue|earnings|high)",
]
_vader = None


def tone_of(text: str) -> str:
    lower = text.lower()
    if any(re.search(p, lower) for p in NEGATIVE):
        return "negative"
    if any(re.search(p, lower) for p in POSITIVE):
        return "positive"
    global _vader
    if _vader is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    compound = _vader.polarity_scores(text)["compound"]
    return "positive" if compound >= 0.05 else "negative" if compound <= -0.05 else "neutral"


def headlines(ticker: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for item in market.news(ticker):
        age = None
        if item["published"]:
            age = max((now - datetime.fromisoformat(item["published"])).total_seconds() / 3600, 0.0)
        out.append({**item, "tone": tone_of(item["title"] + " " + item["summary"]),
                    "age_hours": age})
    return out


# ----------------------------------------------------------------------
# Factor Score
# ----------------------------------------------------------------------
def factor(ticker: str) -> dict:
    cfg = scoring.CFG
    df = market.history(ticker)
    tech, ind = scoring.technical_score(df, cfg)
    if tech.dropna().empty:
        raise market.FeedError("Not enough price history to score this ticker "
                               "(the trend component needs about a year).")

    vix = market.vix()
    penalty = (float(scoring.macro_risk_penalty(df.index, vix, None, cfg).iloc[-1])
               if vix is not None else 0.0)

    news = headlines(ticker)
    items = [{"sentiment": {"positive": 1.0, "negative": -1.0, "neutral": 0.0}[n["tone"]],
              "age_hours": n["age_hours"] if n["age_hours"] is not None else 24.0}
             for n in news]
    sentiment = scoring.decayed_sentiment(items, cfg=cfg)
    sentiment_sigma = (scoring.sentiment_to_sigma(sentiment["score"], sentiment["effective_n"])
                       if sentiment else None)

    latest_tech = float(tech.iloc[-1]) if pd.notna(tech.iloc[-1]) else None
    result = scoring.combine(latest_tech, sentiment_sigma, penalty, cfg)

    latest = ind.iloc[-1]
    components = []
    for key, weight in cfg.tech_subweights.items():
        z = latest.get(f"z_{key}")
        components.append({
            "key": key,
            "label": scoring.COMPONENT_LABELS.get(key, key),
            "weight": weight,
            "z": z,
            "contribution": None if pd.isna(z) else weight * float(z),
        })

    corr = scoring.component_correlations(ind, cfg)
    overlap = None
    a, b = scoring.COMPONENT_LABELS["momentum"], scoring.COMPONENT_LABELS["band_position"]
    if not corr.empty and a in corr.index and b in corr.columns:
        overlap = float(corr.loc[a, b])

    bt = scoring.backtest(tech, df["Close"], cfg=cfg)
    hist = tech.dropna().tail(252)

    return jsonable({
        "ticker": ticker,
        "demo": market.DEMO,
        "as_of": _day(df.index[-1]),
        "final": result["final"],
        "raw": result["raw"],
        "reading": result["reading"],
        "tone": result["tone"],
        "technical": latest_tech,
        "sentiment_sigma": sentiment_sigma,
        "sentiment": sentiment,
        "risk_penalty": penalty,
        "vix": None if vix is None else float(vix.iloc[-1]),
        "weights_used": result["weights_used"],
        "components": components,
        "momentum_band_correlation": overlap,
        "thresholds": {"mild": cfg.mild_threshold, "strong": cfg.strong_threshold},
        "frequency": scoring.threshold_frequency(tech, cfg),
        "backtest": bt,
        "backtest_reading": scoring.interpret_backtest(bt, cfg),
        "history": [{"t": _day(i), "v": v} for i, v in hist.items()],
        "config": {"zscore_window": cfg.zscore_window, "geo_beta": cfg.geo_beta,
                   "macro_clip_sigma": cfg.macro_clip_sigma,
                   "w_technical": cfg.w_technical, "w_sentiment": cfg.w_sentiment,
                   "half_life_hours": cfg.sentiment_half_life_hours},
        "headlines": news,
    })


# ----------------------------------------------------------------------
# Buy Test
# ----------------------------------------------------------------------
def buy_test(ticker: str, notional: float, portfolio: float, horizon_days: int,
             commission_pct: float, commission_min: float, spread_pct: float,
             fx_pct: float, tax_rate: float) -> dict:
    df = market.history(ticker)
    tech, _ = scoring.technical_score(df, scoring.CFG)
    signal = tech.dropna()
    odds = (verdict.measure_odds(signal, df["Close"], horizon_days)
            if len(signal) >= 120 else None)
    if odds is None:
        return {"ticker": ticker, "demo": market.DEMO, "verdict": "thin",
                "headline": "Not enough history to measure the odds",
                "reasons": ["The test needs a few years of daily prices to find enough "
                            "past days that looked like today."],
                "odds": None}

    costs = verdict.Costs(commission_pct=commission_pct, commission_min=commission_min,
                          spread_pct=spread_pct, fx_pct=fx_pct, tax_rate=tax_rate)
    a = verdict.assess(odds, notional, portfolio, costs)
    be = verdict.breakeven_curve(odds, costs)
    growth = verdict.growth_curve(a)

    return jsonable({
        "ticker": ticker,
        "demo": market.DEMO,
        "verdict": a.verdict,
        "headline": a.headline,
        "reasons": a.reasons,
        "odds": asdict(odds),
        "cost_breakdown": costs.breakdown(notional),
        "cost_fraction": a.cost_fraction,
        "cost_share_of_win": a.cost_share_of_win,
        "w_net": a.w_net, "l_net": a.l_net,
        "breakeven": a.breakeven,
        "edge": a.edge,
        "hit_lower": a.hit_lower,
        "edge_lower": a.edge_lower,
        "kelly_raw": a.kelly_raw,
        "kelly_shrunk": a.kelly_shrunk,
        "stake_fraction": a.stake_fraction,
        "stake_cash": a.stake_cash,
        "signal_adds": a.signal_adds,
        "kappa": verdict.DEFAULT_KAPPA,
        "max_weight": verdict.DEFAULT_MAX_WEIGHT,
        "min_windows": verdict.MIN_WINDOWS,
        "breakeven_curve": [{"x": r["Trade size"], "y": r["Break-even hit rate"]}
                            for _, r in be.iterrows()],
        "growth_curve": [{"x": r["Stake"], "y": r["Growth"]} for _, r in growth.iterrows()],
    })


# ----------------------------------------------------------------------
# Risk
# ----------------------------------------------------------------------
def risk(ticker: str, position: float = 10_000.0) -> dict:
    close = market.history(ticker)["Close"].tail(505)
    returns = close.pct_change().dropna()
    var = quant.value_at_risk(returns, confidence=0.95, horizon_days=1, portfolio_value=position)
    r = var.pop("returns")
    var.pop("historical_quantile", None)

    counts, edges = np.histogram(r * 100, bins=41)
    return jsonable({
        "ticker": ticker, "demo": market.DEMO, "position": position, "days": int(len(r)),
        **var,
        "histogram": [{"x0": edges[i], "x1": edges[i + 1], "n": counts[i]}
                      for i in range(len(counts))],
    })


# ----------------------------------------------------------------------
# Deal Room: LBO
# ----------------------------------------------------------------------
LBO_FIELDS = {"entry_ebitda", "entry_multiple", "debt_turns", "interest_rate",
              "revenue", "revenue_growth", "margin_improvement", "exit_multiple",
              "hold_years", "cash_sweep", "tax_rate"}


def lbo(params: dict) -> dict:
    clean = {k: v for k, v in params.items() if k in LBO_FIELDS and v is not None}
    if "hold_years" in clean:
        clean["hold_years"] = int(clean["hold_years"])
    result = deals.run_lbo(deals.LBOAssumptions(**clean))
    schedule = result.schedule[["Year", "Revenue", "EBITDA", "Free cash flow",
                                "Ending debt", "Leverage (ND/EBITDA)", "Interest coverage"]]
    return jsonable({
        "irr": result.irr, "moic": result.moic,
        "entry_equity": result.entry_equity, "exit_equity": result.exit_equity,
        "exit_ev": result.exit_ev, "entry_ev": result.sources_uses["entry_ev"],
        "sources": result.sources_uses["sources"], "uses": result.sources_uses["uses"],
        "bridge": result.bridge,
        "covenant_breach_year": result.covenant_breach_year,
        "equity_wiped": result.equity_wiped,
        "verdict": deals.lbo_verdict(result),
        "schedule": schedule.to_dict(orient="records"),
    })


# ----------------------------------------------------------------------
# Forecast Lab
# ----------------------------------------------------------------------
@market.ttl_cache(3600, max_entries=64)
def forecast_lab(ticker: str) -> dict:
    df = market.history_long(ticker)
    wf = forecast.walk_forward(df)
    years = len(df) / 252
    if wf is None:
        return {"ticker": ticker, "demo": market.DEMO, "status": "thin",
                "years": years,
                "message": f"The feed has {years:.1f} years of prices for {ticker}. Grading a "
                           "monthly call fairly needs about seven: roughly two years for the "
                           "80-week features to warm up, three to train on, and two to test."}

    tech, _ = scoring.technical_score(df, scoring.CFG)
    grades = forecast.grade(wf, tech)
    headline, tone, detail = forecast.describe(grades)
    strat = forecast.strategy(wf, df["Close"])
    equity = strat.pop("equity").iloc[::5]
    pc = forecast.persistence_check(df["Close"])
    last = len(wf.prob) - 1

    return jsonable({
        "ticker": ticker, "demo": market.DEMO, "status": "ok", "years": years,
        "headline": headline, "tone": tone, "detail": detail,
        "grades": grades,
        "today": {"date": _day(wf.dates[last]), "prob": wf.prob[last],
                  "base_rate": wf.base_rate[last],
                  "invested": bool(wf.prob[last] > wf.base_rate[last])},
        "windows": list(forecast.WINDOWS_WEEKS),
        "coefficients": forecast.coefficient_grid(wf),
        "strategy": strat,
        "equity": [{"t": _day(i), "strategy": r.strategy, "hold": r.hold}
                   for i, r in equity.iterrows()],
        "persistence": {k: pc[k] for k in ("days", "r2_level", "r2_move", "mean_abs_error_pct")}
                       | {"series": [{"t": _day(d), "actual": a, "guess": g} for d, a, g
                                     in zip(pc["dates"], pc["actual"], pc["guess"])]},
    })


def _lab_summary(ticker: str) -> dict:
    try:
        lab = forecast_lab(ticker)
    except market.FeedError:
        return {"status": "unavailable"}
    if lab["status"] != "ok":
        return {"status": "not enough history"}
    c = lab["grades"]["contenders"]
    return {
        "note": "Walk-forward test of a 69-feature model (Stanford CS230 design) predicting "
                "whether the next 21 trading days finish up. AUC 0.5 is a coin flip.",
        "verdict": lab["headline"],
        "model_auc": c["model"]["auc"], "model_auc_90pct_range": c["model"]["auc_ci"],
        "factor_score_auc": c.get("factor", {}).get("auc"),
        "always_up_accuracy": c["always"]["accuracy"], "model_accuracy": c["model"]["accuracy"],
        "independent_test_months": lab["grades"]["independent_months"],
        "today_prob_up": lab["today"]["prob"], "training_up_rate": lab["today"]["base_rate"],
    }


# ----------------------------------------------------------------------
# Dossier: what the debating agents are given
# ----------------------------------------------------------------------
def dossier(ticker: str) -> dict:
    """Everything the site has measured about a ticker, compact enough to read."""
    ov = overview(ticker, "1Y")
    fs = factor(ticker)
    bt = buy_test(ticker, 2_000, 20_000, 21, 0.08, 1.0, 0.03, 0.0, 0.0)
    rk = risk(ticker)

    close = market.history(ticker)["Close"]

    def ret(days):
        return None if len(close) <= days else float((close.iloc[-1] / close.iloc[-1 - days] - 1) * 100)

    odds = bt.get("odds") or {}
    return jsonable({
        "ticker": ticker,
        "demo": market.DEMO,
        "company": {k: ov[k] for k in ("name", "sector", "industry", "currency")},
        "price": {"last": ov["price"], "as_of": ov["as_of"], "day_change_pct": ov["change_pct"],
                  "return_1m_pct": ret(21), "return_3m_pct": ret(63),
                  "return_1y_pct": ret(252), "return_5y_pct": ret(len(close) - 1)},
        "fundamentals": ov["stats"],
        "factor_score": {
            "final_sigma": fs["final"], "reading": fs["reading"],
            "technical_sigma": fs["technical"], "sentiment_sigma": fs["sentiment_sigma"],
            "macro_haircut": fs["risk_penalty"], "vix": fs["vix"],
            "components_z": {c["key"]: c["z"] for c in fs["components"]},
            "backtest": fs["backtest_reading"],
        },
        "buy_test_21d": {
            "note": "Past 21-day outcomes on days the technical score looked like today. "
                    "Assumes a 2,000 trade in a 20,000 portfolio at a low-cost broker.",
            "verdict": bt["verdict"], "headline": bt["headline"],
            "hit_rate": odds.get("hit_rate"), "baseline_hit_rate": odds.get("baseline_hit_rate"),
            "avg_win": odds.get("avg_win"), "avg_loss": odds.get("avg_loss"),
            "independent_windows": odds.get("n_windows"),
            "breakeven_hit_rate": bt.get("breakeven"),
            "suggested_stake_fraction": bt.get("stake_fraction"),
        },
        "forecast_lab": _lab_summary(ticker),
        "risk": {k: rk[k] for k in ("historical_var_pct", "expected_shortfall_pct",
                                    "vol_annual_pct", "skew", "excess_kurtosis",
                                    "worst_day_pct")},
        "headlines": [{"title": h["title"], "publisher": h["publisher"],
                       "published": h["published"], "tone": h["tone"]}
                      for h in fs["headlines"]],
    })
