"""
Plans and feature limits.

Imports no Streamlit so the rules can be tested directly, and holds no
opinions about payment — it answers one question: given an account record,
what is this person allowed to do right now?

DESIGN
------
The free tier keeps the whole analytical product: any ticker, the full
technical read, the composite score with its backtest, explain mode, news,
fundamentals. Paywalling the analysis would gut the thing that makes this
worth using and would kill the funnel at the same time.

What the paid tier buys is PERSISTENCE, AUTOMATION and VOLUME — a bigger
watchlist, an unlimited journal, exports, alerts, the daily digest. Those are
the parts that cost real money to provide (storage, scheduled workers, API
quota), which is what makes the price feel like an exchange rather than a
toll on something that was already free.

DEGRADING, NOT DELETING
-----------------------
When a subscription lapses, nothing is destroyed. A 40-ticker watchlist stays
a 40-ticker watchlist; the scan just processes the first 5 and says so. If
they resubscribe everything is exactly where they left it. Deleting a lapsed
customer's data to enforce a limit is a good way to guarantee they never come
back — and for a journal of real trades it is unforgivable.
"""

from __future__ import annotations

from datetime import datetime, timezone

FREE = "free"
PRO = "pro"

# Sentinel for "no limit". A float so comparisons with ints are safe.
UNLIMITED = float("inf")


PLANS: dict[str, dict] = {
    FREE: {
        "label": "Free",
        "price_note": "Free forever",
        "limits": {
            "watchlist_tickers": 5,
            "journal_entries": 10,
            "telegram_alerts": False,
            "daily_digest": False,
            "multi_asset": False,
            "journal_export": False,
        },
    },
    PRO: {
        "label": "Pro",
        "price_note": "$5 / month or $45 / year",
        "limits": {
            "watchlist_tickers": UNLIMITED,
            "journal_entries": UNLIMITED,
            "telegram_alerts": True,
            "daily_digest": True,
            "multi_asset": True,
            "journal_export": True,
        },
    },
}

# Shown next to a locked feature. Written as what you get, not what you lack.
FEATURE_COPY = {
    "watchlist_tickers": ("Larger watchlist",
                          "Scan as many tickers as you like in one pass."),
    "journal_entries": ("Unlimited journal",
                        "Keep your full trading record, not just the last few."),
    "telegram_alerts": ("Telegram alerts",
                        "Send a tilt update to a bot you control."),
    "daily_digest": ("Daily digest",
                     "One screen each morning: what changed across your watchlist."),
    "multi_asset": ("Multi-asset view",
                    "FX, crypto and commodities alongside your equities."),
    "journal_export": ("Journal export",
                       "Download your trade history as CSV."),
}


def _parse_expiry(value) -> datetime | None:
    """Accepts a datetime or an ISO string; anything unreadable means no expiry."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_plan(user_record: dict | None, now: datetime | None = None) -> str:
    """
    The plan actually in force, which is not always the plan on the record.

    A stored plan of "pro" with an expiry in the past resolves to free. Doing
    this at read time rather than with a nightly job means a lapsed
    subscription takes effect immediately and correctly even if no background
    process ever runs — which matters, because on this stack there isn't one.

    An unrecognised plan name resolves to free: unknown input must never
    accidentally grant access.
    """
    if not user_record:
        return FREE

    plan = str(user_record.get("plan") or FREE).strip().lower()
    if plan not in PLANS:
        return FREE
    if plan == FREE:
        return FREE

    expiry = _parse_expiry(user_record.get("plan_expires"))
    if expiry is None:
        # A paid plan with no expiry is a lifetime or manually-granted account.
        return plan

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return plan if expiry > now else FREE


def limit(plan: str, key: str):
    """The value of one limit under a plan. Unknown keys fall back to free."""
    plan_limits = PLANS.get(plan, PLANS[FREE])["limits"]
    return plan_limits.get(key, PLANS[FREE]["limits"].get(key))


def allows(plan: str, key: str) -> bool:
    """True when a boolean feature is available under this plan."""
    return bool(limit(plan, key))


def is_pro(plan: str) -> bool:
    return plan != FREE and plan in PLANS


def within_limit(plan: str, key: str, count: int) -> bool:
    """Whether `count` items is still inside the plan's allowance."""
    allowed = limit(plan, key)
    if allowed is True or allowed == UNLIMITED:
        return True
    if allowed is False:
        return False
    return count <= allowed


def cap_list(plan: str, key: str, items: list) -> tuple[list, int]:
    """
    Trims a list to the plan's allowance.

    Returns (kept, dropped_count). Callers show the kept items and say plainly
    how many were held back — the underlying data is never modified, so the
    full list returns intact the moment the plan changes.
    """
    allowed = limit(plan, key)
    if allowed is True or allowed == UNLIMITED or allowed is None:
        return items, 0
    if allowed is False:
        return [], len(items)
    allowed = int(allowed)
    if len(items) <= allowed:
        return items, 0
    return items[:allowed], len(items) - allowed


def describe(plan: str) -> dict:
    """Plan metadata for display."""
    meta = PLANS.get(plan, PLANS[FREE])
    return {"plan": plan, "label": meta["label"], "price_note": meta["price_note"],
            "is_pro": is_pro(plan), "limits": dict(meta["limits"])}


def days_remaining(user_record: dict | None, now: datetime | None = None) -> int | None:
    """Whole days until the current plan lapses, or None if it doesn't."""
    expiry = _parse_expiry((user_record or {}).get("plan_expires"))
    if expiry is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, (expiry - now).days)
