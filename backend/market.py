"""
Market data: prices, company facts and headlines.

One live source (Yahoo, through yfinance) and one demo source. The demo
source exists so the site can be developed and tested where Yahoo is
unreachable. It is switched on only by TICKVEIL_DEMO=1 and never as a
fallback: when the live feed fails the caller gets an error, because a
made-up price that looks real is worse than no price. Every payload built
from demo data carries demo=True so the page can say so loudly.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timezone
from functools import wraps

import numpy as np
import pandas as pd

DEMO = os.environ.get("TICKVEIL_DEMO") == "1"

TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")


class FeedError(Exception):
    """The data source could not supply what was asked for."""


def normalise_ticker(raw: str) -> str:
    ticker = (raw or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise FeedError("That does not look like a ticker symbol.")
    return ticker


def ttl_cache(seconds: int, max_entries: int = 256):
    """Small thread-safe cache. Endpoints run in a thread pool."""
    def decorate(fn):
        store: dict = {}
        lock = threading.Lock()

        @wraps(fn)
        def wrapper(*args):
            now = time.monotonic()
            with lock:
                hit = store.get(args)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            value = fn(*args)
            with lock:
                if len(store) >= max_entries:
                    store.pop(min(store, key=lambda k: store[k][0]))
                store[args] = (now, value)
            return value
        return wrapper
    return decorate


# ----------------------------------------------------------------------
# Demo source
# ----------------------------------------------------------------------
def _demo_history(ticker: str, days: int = 1260) -> pd.DataFrame:
    """A seeded random walk. Same ticker, same path, every run."""
    seed = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    is_vix = ticker == "^VIX"
    index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)

    if is_vix:
        level = np.empty(days)
        level[0] = 18.0
        for i in range(1, days):
            level[i] = max(9.0, level[i - 1] + 0.08 * (18.0 - level[i - 1])
                           + rng.normal(0, 1.1))
        close = level
    else:
        drift = rng.uniform(-0.00005, 0.0006)
        vol = rng.uniform(0.012, 0.028)
        shocks = rng.standard_t(df=5, size=days) * vol / np.sqrt(5 / 3)
        close = rng.uniform(20, 400) * np.exp(np.cumsum(drift + shocks))

    spread = np.abs(rng.normal(0, 0.008, days)) + 0.002
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.004, days))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(mean=15.5, sigma=0.35, size=days).round()
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": volume}, index=index)


# ----------------------------------------------------------------------
# Live source
# ----------------------------------------------------------------------
def _yf_call(fn, retries: int = 2, delay: float = 2.0):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # yfinance raises a zoo of types
            last = exc
            text = str(exc).lower()
            if "rate" not in text and "429" not in text and "too many" not in text:
                break
            time.sleep(delay * (attempt + 1))
    raise FeedError(f"The market data feed did not answer ({type(last).__name__}).")


def _clean(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise FeedError(f"No price history found for '{ticker}'.")
    out = df[df["Close"] > 0].dropna(subset=["Close"]).copy()
    if out.empty:
        raise FeedError(f"No usable price history for '{ticker}'.")
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    return out[["Open", "High", "Low", "Close", "Volume"]]


@ttl_cache(900)
def history(ticker: str) -> pd.DataFrame:
    """Up to five years of daily bars."""
    if DEMO:
        return _demo_history(ticker)
    import yfinance as yf
    stock = yf.Ticker(ticker)
    return _clean(_yf_call(lambda: stock.history(period="5y", interval="1d")), ticker)


@ttl_cache(3600, max_entries=64)
def history_long(ticker: str) -> pd.DataFrame:
    """
    Every daily bar the feed has. The forecast test needs decades: a monthly
    call is one data point a month, and grading it fairly takes hundreds.
    """
    if DEMO:
        return _demo_history(ticker, days=5040)
    import yfinance as yf
    stock = yf.Ticker(ticker)
    return _clean(_yf_call(lambda: stock.history(period="max", interval="1d")), ticker)


@ttl_cache(900)
def info(ticker: str) -> dict:
    """Company facts. Every field may be None."""
    if DEMO:
        return {"shortName": f"{ticker} (demo)", "currency": "USD"}
    import yfinance as yf
    try:
        return _yf_call(lambda: yf.Ticker(ticker).info) or {}
    except FeedError:
        return {}


@ttl_cache(600)
def news(ticker: str, max_items: int = 8) -> list[dict]:
    """Recent headlines tied to the ticker. Empty in demo mode: no invented news."""
    if DEMO:
        return []
    import yfinance as yf
    try:
        raw = _yf_call(lambda: yf.Ticker(ticker).news) or []
    except FeedError:
        return []

    items = []
    for n in raw[:max_items]:
        if not isinstance(n, dict):
            continue
        content = n.get("content", n) or {}
        title = _strip_tags(content.get("title") or n.get("title") or "")
        if not title:
            continue
        link = ((content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or n.get("link") or "")
        items.append({
            "title": title,
            "summary": _strip_tags(content.get("summary") or content.get("description") or "")[:400],
            "link": link if link.startswith("https://") else "",
            "publisher": ((content.get("provider") or {}).get("displayName")
                          or n.get("publisher") or ""),
            "published": _parse_time(content.get("pubDate") or n.get("providerPublishTime")),
        })
    return items


def vix() -> pd.Series | None:
    try:
        return history("^VIX")["Close"]
    except FeedError:
        return None


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text))).strip()


def _parse_time(value) -> str | None:
    """ISO-8601 UTC string, or None when the feed gave nothing parseable."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            stamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None
