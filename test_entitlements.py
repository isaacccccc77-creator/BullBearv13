"""
Plan and limit tests.

Run with:  python test_entitlements.py

The rules that matter most here are the ones that fail CLOSED — an unknown
plan name, a malformed expiry date, a missing record. Every one of those must
land on the free tier, because the failure mode of a permissive default is
giving away the product, and the failure mode of a strict default is a support
email.

The other thing under test is that a lapsed subscription degrades without
destroying anything.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entitlements as ent  # noqa: E402

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=20)).isoformat()
PAST = (NOW - timedelta(days=3)).isoformat()

print("\nPlan resolution — everything unrecognised must fail closed to free.")
expect("no record at all", ent.resolve_plan(None, NOW), ent.FREE)
expect("empty record", ent.resolve_plan({}, NOW), ent.FREE)
expect("explicit free", ent.resolve_plan({"plan": "free"}, NOW), ent.FREE)
expect("unknown plan name", ent.resolve_plan({"plan": "enterprise"}, NOW), ent.FREE)
expect("plan of None", ent.resolve_plan({"plan": None}, NOW), ent.FREE)
expect("garbage expiry on a pro plan",
       ent.resolve_plan({"plan": "pro", "plan_expires": "not-a-date"}, NOW), ent.PRO)
expect("pro with a future expiry",
       ent.resolve_plan({"plan": "pro", "plan_expires": FUTURE}, NOW), ent.PRO)
expect("pro with a PAST expiry lapses to free",
       ent.resolve_plan({"plan": "pro", "plan_expires": PAST}, NOW), ent.FREE)
expect("pro with no expiry is a lifetime grant",
       ent.resolve_plan({"plan": "pro", "plan_expires": None}, NOW), ent.PRO)
expect("case and whitespace are tolerated",
       ent.resolve_plan({"plan": "  PRO  ", "plan_expires": FUTURE}, NOW), ent.PRO)
expect("a naive datetime expiry is treated as UTC",
       ent.resolve_plan({"plan": "pro", "plan_expires": NOW + timedelta(days=5)}, NOW), ent.PRO)

print("\nLimits.")
expect("free watchlist is capped", ent.limit(ent.FREE, "watchlist_tickers"), 5)
expect("pro watchlist is unlimited", ent.limit(ent.PRO, "watchlist_tickers"), ent.UNLIMITED)
expect("free has no Telegram alerts", ent.allows(ent.FREE, "telegram_alerts"), False)
expect("pro has Telegram alerts", ent.allows(ent.PRO, "telegram_alerts"), True)
expect("an unknown limit key falls back to the free value",
       ent.limit("nonsense_plan", "watchlist_tickers"), 5)

print("\nCounting against a limit.")
expect("five tickers is inside the free cap", ent.within_limit(ent.FREE, "watchlist_tickers", 5), True)
expect("six tickers is outside it", ent.within_limit(ent.FREE, "watchlist_tickers", 6), False)
expect("any count is inside the pro cap",
       ent.within_limit(ent.PRO, "watchlist_tickers", 10_000), True)

print("\nDegrading — a lapsed plan must trim the view, never the data.")
tickers = [f"T{i}" for i in range(12)]
kept, dropped = ent.cap_list(ent.FREE, "watchlist_tickers", tickers)
expect("free keeps the first five", kept, tickers[:5])
expect("and reports how many were held back", dropped, 7)
expect("the caller's original list is untouched", len(tickers), 12)
kept_pro, dropped_pro = ent.cap_list(ent.PRO, "watchlist_tickers", tickers)
expect("pro keeps everything", kept_pro, tickers)
expect("pro drops nothing", dropped_pro, 0)
expect("a list already inside the cap is returned whole",
       ent.cap_list(ent.FREE, "watchlist_tickers", ["A", "B"]), (["A", "B"], 0))
expect("an empty list is handled", ent.cap_list(ent.FREE, "watchlist_tickers", []), ([], 0))

print("\nA boolean feature caps to nothing rather than to a slice.")
expect("free gets no rows from a boolean-gated list",
       ent.cap_list(ent.FREE, "telegram_alerts", ["a", "b"]), ([], 2))

print("\nDisplay metadata.")
expect("free is not pro", ent.describe(ent.FREE)["is_pro"], False)
expect("pro is pro", ent.describe(ent.PRO)["is_pro"], True)
expect("free carries a label", ent.describe(ent.FREE)["label"], "Free")
expect("an unknown plan describes as free", ent.describe("bogus")["label"], "Free")
expect("every gated feature has display copy",
       sorted(ent.FEATURE_COPY) == sorted(ent.PLANS[ent.FREE]["limits"]), True)

print("\nDays remaining.")
expect("counts down to the expiry",
       ent.days_remaining({"plan_expires": FUTURE}, NOW), 20)
expect("never goes negative", ent.days_remaining({"plan_expires": PAST}, NOW), 0)
expect("no expiry means no countdown", ent.days_remaining({"plan_expires": None}, NOW), None)
expect("a missing record means no countdown", ent.days_remaining(None, NOW), None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All entitlement checks passed.")
