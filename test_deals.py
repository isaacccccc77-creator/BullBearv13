"""
Tests for the deal models.

Run with:  python test_deals.py

These matter more than usual. An LBO that forgets to tax interest, or an
accretion model that nets financing costs pre-tax, does not crash — it
returns a number that looks entirely reasonable and is wrong by several
points of IRR. So the tests here assert *properties that must hold*
(identities, monotonicities, closed-form cases) rather than snapshots of
whatever the code happened to produce on the day it was written.
"""

import sys
from dataclasses import replace

import numpy as np
import pandas as pd

import deals

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def close(label, actual, wanted, tol=1e-6):
    ok = actual is not None and abs(actual - wanted) <= tol
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r} (+/-{tol}), got {actual!r}")
        failures.append(label)


print("\nIRR — checked against cases with a known answer.")
close("doubling in one year is 100%", deals.irr([-100, 200]), 1.0, 1e-6)
close("a flat return is 0%", deals.irr([-100, 100]), 0.0, 1e-6)
# 100 -> 200 over 5 years is 2^(1/5) - 1.
close("2x over five years is 14.87%", deals.irr([-100, 0, 0, 0, 0, 200]),
      2 ** 0.2 - 1, 1e-6)
close("a halving is -50%", deals.irr([-100, 50]), -0.5, 1e-6)
expect("no sign change has no IRR", deals.irr([-100, -50]), None)
expect("an empty stream has no IRR", deals.irr([]), None)

print("\nIRR must invert MoIC exactly for a single-exit hold.")
for years in (3, 5, 7):
    for multiple in (1.5, 2.0, 3.0):
        flows = [-100.0] + [0.0] * (years - 1) + [100.0 * multiple]
        close(f"{multiple}x over {years}y", deals.irr(flows),
              multiple ** (1 / years) - 1, 1e-6)


print("\nLBO — sources must equal uses, or the balance sheet does not balance.")
base = deals.LBOAssumptions()
res = deals.run_lbo(base)
su = res.sources_uses
close("sources equal uses",
      sum(su["sources"].values()) - sum(su["uses"].values()), 0.0, 1e-6)
expect("sponsor equity is positive", res.entry_equity > 0, True)

# Existing debt on the target is refinanced, not bought twice. Listing
# enterprise value and existing net debt as separate uses would inflate
# total uses — and therefore the equity cheque — by the whole debt balance.
levered_target = deals.run_lbo(replace(base, entry_net_debt=200e6))
close("existing target debt does not inflate total uses",
      sum(levered_target.sources_uses["uses"].values()),
      sum(res.sources_uses["uses"].values()), 1e-6)
close("so the equity cheque is unchanged by how the target was financed",
      levered_target.entry_equity, res.entry_equity, 1e-6)


print("\nLBO — the value bridge must reconcile to the equity actually created.")
# The decomposition is an identity, so it has to close to the cent on
# every set of assumptions, not just the default one.
for turns in (3.0, 4.5, 6.0):
    for exit_mult in (7.0, 9.0, 11.0):
        a = replace(base, debt_turns=turns, exit_multiple=exit_mult)
        r = deals.run_lbo(a)
        created = sum(r.bridge.values())
        # The bridge reconciles to what the sponsor actually gained: exit
        # proceeds less the cheque they wrote, fees included. Fees sit in
        # the bridge as their own negative bar precisely so this closes.
        close(f"bridge closes at {turns}x turns, {exit_mult}x exit",
              created - (r.exit_equity - r.entry_equity), 0.0, 1.0)


print("\nLBO — the tax shield must actually be worth something.")
# Interest is deducted before tax. Doubling the rate must cost less than
# the raw interest, because the taxman funds part of it. If the model
# taxed EBIT instead, these two would move together exactly.
low = deals.run_lbo(replace(base, interest_rate=0.05))
high = deals.run_lbo(replace(base, interest_rate=0.10))
extra_interest = (high.schedule["Interest"].sum() - low.schedule["Interest"].sum())
lost_income = (low.schedule["Net income"].sum() - high.schedule["Net income"].sum())
expect("higher interest costs less than the interest itself",
       lost_income < extra_interest, True)
close("the difference is exactly the tax shield",
      lost_income, extra_interest * (1 - base.tax_rate), abs(extra_interest) * 1e-9)


print("\nLBO — directional sanity that a wrong sign would break.")
expect("a higher exit multiple raises IRR",
       deals.run_lbo(replace(base, exit_multiple=11.0)).irr >
       deals.run_lbo(replace(base, exit_multiple=7.0)).irr, True)
expect("more leverage raises IRR when the deal works",
       deals.run_lbo(replace(base, debt_turns=6.0)).irr >
       deals.run_lbo(replace(base, debt_turns=3.0)).irr, True)
expect("faster EBITDA growth raises IRR",
       deals.run_lbo(replace(base, revenue_growth=0.08)).irr >
       deals.run_lbo(replace(base, revenue_growth=0.01)).irr, True)
expect("a longer hold at a flat multiple still repays debt",
       deals.run_lbo(replace(base, hold_years=7)).exit_net_debt <
       deals.run_lbo(replace(base, hold_years=3)).exit_net_debt, True)
expect("debt never goes negative", bool((res.schedule["Ending debt"] >= -1e-6).all()), True)
expect("the sweep never repays more than is outstanding",
       bool((res.schedule["Debt repaid"] <= res.schedule["Ending debt"]
             + res.schedule["Debt repaid"] + 1e-6).all()), True)

print("\nLBO — leverage that cannot be serviced must be reported, not hidden.")
broken = deals.run_lbo(replace(base, debt_turns=12.0, interest_rate=0.14,
                               exit_multiple=5.0, revenue_growth=-0.05,
                               margin_improvement=-0.02))
expect("a doomed structure is flagged", broken.covenant_breach_year is not None, True)
expect("and its verdict says so", "covenant" in deals.lbo_verdict(broken).lower()
       or "wiped" in deals.lbo_verdict(broken).lower(), True)

print("\nLBO — the verdict must call out a return bought with multiple expansion.")
rerate = deals.run_lbo(replace(base, entry_multiple=8.0, exit_multiple=13.0))
expect("re-rating is named", "multiple" in deals.lbo_verdict(rerate).lower(), True)
flat = deals.run_lbo(replace(base, entry_multiple=9.0, exit_multiple=9.0))
expect("a flat exit is credited", "flat" in deals.lbo_verdict(flat).lower(), True)

print("\nLBO sensitivity grid.")
grid = deals.lbo_sensitivity(base, "entry_multiple", [8.0, 9.0, 10.0],
                             "exit_multiple", [8.0, 9.0, 10.0])
expect("the grid is the requested shape", grid.shape, (3, 3))
expect("paying less for the same exit returns more",
       grid.loc[8.0, 9.0] > grid.loc[10.0, 9.0], True)
expect("selling higher from the same entry returns more",
       grid.loc[9.0, 10.0] > grid.loc[9.0, 8.0], True)


print("\nM&A — an all-stock merger of identical companies must be EPS-neutral.")
# Two companies with the same P/E, no premium, no synergies: the acquirer
# issues exactly enough shares to buy exactly the earnings it dilutes.
twin = deals.MergerAssumptions(
    acq_price=100.0, acq_shares=100e6, acq_net_income=500e6,
    tgt_price=100.0, tgt_shares=100e6, tgt_net_income=500e6,
    premium=0.0, pct_cash=0.0, pct_stock=1.0, pct_debt=0.0,
    synergies=0.0, tgt_net_debt=0.0,
)
twin_out = deals.run_merger(twin)
close("identical twins at no premium are neutral", twin_out["accretion"], 0.0, 1e-12)
close("and breakeven synergies are zero", twin_out["breakeven_synergies"], 0.0, 1.0)

print("\nM&A — a premium paid in stock must dilute by a predictable amount.")
prem = deals.run_merger(replace(twin, premium=0.25))
expect("paying 25% more in stock dilutes", prem["accretion"] < 0, True)
# Pro forma EPS = 1000m / (100m + 125m) = 4.444 vs 5.00 standalone.
close("dilution is exactly -11.1%", prem["accretion"], (1000 / 225) / 5.0 - 1, 1e-12)

print("\nM&A — breakeven synergies must be the number that closes the gap.")
for premium in (0.10, 0.30, 0.50):
    case = replace(twin, premium=premium, synergies=0.0)
    out = deals.run_merger(case)
    solved = deals.run_merger(replace(case, synergies=out["breakeven_synergies"]))
    close(f"feeding breakeven back at a {premium:.0%} premium is neutral",
          solved["accretion"], 0.0, 1e-9)

print("\nM&A — financing costs must be taken after tax.")
cash_deal = replace(twin, pct_cash=1.0, pct_stock=0.0, cash_yield=0.05, tax_rate=0.30)
out = deals.run_merger(cash_deal)
close("foregone interest is net of tax",
      out["foregone_interest"],
      out["cash_used"] * 0.05 * 0.70, 1.0)
debt_deal = replace(twin, pct_cash=0.0, pct_stock=0.0, pct_debt=1.0,
                    new_debt_rate=0.06, tax_rate=0.30)
out = deals.run_merger(debt_deal)
close("new interest is net of tax", out["new_interest"],
      out["debt_raised"] * 0.06 * 0.70, 1.0)

print("\nM&A — a cash deal is accretive when the earnings yield beats the cash yield.")
# Buying 500m of earnings for 10bn is a 5% yield, funded from cash
# earning 3% after tax: accretive, and no shares are issued.
cash_out = deals.run_merger(replace(twin, pct_cash=1.0, pct_stock=0.0, cash_yield=0.03))
expect("a cash purchase at 5% against 3% cash is accretive",
       cash_out["accretion"] > 0, True)
expect("and issues no shares", cash_out["new_shares"], 0.0)

print("\nM&A — consideration that does not sum to one is normalised, not rejected.")
sloppy = deals.MergerAssumptions(pct_cash=2.0, pct_stock=2.0, pct_debt=0.0)
close("the mix renormalises", sloppy.pct_cash + sloppy.pct_stock + sloppy.pct_debt, 1.0, 1e-12)

print("\nM&A — ownership and leverage arithmetic.")
half = deals.run_merger(replace(twin, premium=0.0, pct_stock=1.0, pct_cash=0.0))
close("equals buying an equal-sized twin for half the company",
      half["target_ownership_pct"], 50.0, 1e-9)


print("\nComps — quartiles, implied prices, and the peers that must be dropped.")
peers = pd.DataFrame({
    "ticker": list("ABCDEFG"),
    "EV/EBITDA": [8.0, 9.0, 10.0, 11.0, 12.0, -4.0, 900.0],
    "P/E": [15.0, 18.0, 20.0, 22.0, 25.0, np.nan, np.nan],
})
target = {"price": 50.0, "shares": 100e6, "net_debt": 200e6,
          "ebitda": 150e6, "eps": 2.5, "revenue": 800e6}
comps = deals.build_comps(target, peers)
row = comps["stats"].set_index("Multiple").loc["EV/EBITDA"]
expect("a negative multiple is excluded", int(row["n"]), 5)
close("the median is the median of what is left", float(row["Median"]), 10.0, 1e-9)
# Median EV/EBITDA of 10 on 150m EBITDA is 1.5bn EV, less 200m net debt,
# over 100m shares = 13.00.
field = comps["football_field"].set_index("Method")
close("implied price nets off debt and divides by shares",
      float(field.loc["EV/EBITDA", "Mid"]), 13.0, 1e-9)
close("an equity multiple lands on the price directly",
      float(field.loc["P/E", "Mid"]), 50.0, 1e-9)
expect("a multiple with too few clean peers is skipped",
       "EV/Revenue" in comps["stats"]["Multiple"].values, False)

print("\nComps — a thin set must fail loudly rather than quietly.")
thin = deals.build_comps(target, pd.DataFrame({"EV/EBITDA": [8.0, 9.0]}))
expect("two peers produce no field", thin["football_field"].empty, True)
expect("and the verdict says why", "thin" in deals.comps_verdict(thin).lower(), True)


print("\nOperating analysis — margins, cycles and growth off real statement shapes.")
years = pd.to_datetime(["2021-12-31", "2022-12-31", "2023-12-31"])
income = pd.DataFrame(
    {years[0]: [1000.0, 400.0, 600.0, 150.0, 100.0],
     years[1]: [1100.0, 440.0, 660.0, 176.0, 120.0],
     years[2]: [1210.0, 484.0, 726.0, 205.7, 140.0]},
    index=["Total Revenue", "Gross Profit", "Cost Of Revenue",
           "Operating Income", "Net Income"])
balance = pd.DataFrame(
    {years[0]: [100.0, 80.0, 60.0, 500.0, 300.0, 50.0],
     years[1]: [110.0, 88.0, 66.0, 550.0, 300.0, 55.0],
     years[2]: [121.0, 96.8, 72.6, 600.0, 300.0, 60.0]},
    index=["Accounts Receivable", "Inventory", "Accounts Payable",
           "Stockholders Equity", "Total Debt", "Cash And Cash Equivalents"])
cashflow = pd.DataFrame(
    {years[0]: [50.0, -40.0, 180.0], years[1]: [55.0, -44.0, 198.0],
     years[2]: [60.5, -48.4, 217.8]},
    index=["Depreciation And Amortization", "Capital Expenditure", "Operating Cash Flow"])

history = deals.operating_analysis(income, balance, cashflow)
expect("one row per year", len(history), 3)
expect("oldest first", list(history["Year"]), [2021, 2022, 2023])
close("revenue growth is computed off the prior year",
      float(history["Revenue growth %"].iloc[1]), 10.0, 1e-9)
expect("the first year has no growth figure",
       bool(pd.isna(history["Revenue growth %"].iloc[0])), True)
close("EBITDA adds depreciation back to operating income",
      float(history["EBITDA"].iloc[0]), 200.0, 1e-9)
close("EBITDA margin follows", float(history["EBITDA margin %"].iloc[0]), 20.0, 1e-9)
close("DSO is receivables over revenue in days",
      float(history["DSO"].iloc[0]), 100.0 / 1000.0 * 365, 1e-9)
close("DIO uses cost of revenue, not revenue",
      float(history["DIO"].iloc[0]), 80.0 / 600.0 * 365, 1e-9)
close("the cash conversion cycle is DSO + DIO - DPO",
      float(history["Cash conversion cycle"].iloc[0]),
      (100 / 1000 + 80 / 600 - 60 / 600) * 365, 1e-9)
close("capex is taken as an absolute value",
      float(history["Capex % revenue"].iloc[0]), 4.0, 1e-9)

print("\nOperating analysis — statement labels vary, so lookup must not be brittle.")
renamed = income.rename(index={"Total Revenue": "Revenues",
                               "Operating Income": "EBIT",
                               "Net Income": "Net Income Common Stockholders"})
expect("alternative row names still resolve",
       len(deals.operating_analysis(renamed, balance, cashflow)), 3)
expect("a statement with no revenue line returns nothing",
       deals.operating_analysis(pd.DataFrame({years[0]: [1.0]}, index=["Mystery"]),
                                balance, cashflow).empty, True)
expect("missing statements do not raise",
       deals.operating_analysis(income, pd.DataFrame(), pd.DataFrame()).empty, False)

print("\nValue creation levers.")
levers = deals.value_creation_levers(history, peer_ebitda_margin=25.0,
                                     peer_growth=15.0, exit_multiple=10.0)
margin_row = levers.set_index("Lever").loc["Margin to peer median"]
# 2023 revenue 1210 at 22% vs a 25% peer median = 3pp x 1210 = 36.3.
close("margin gap prices at revenue times the gap",
      float(margin_row["EBITDA impact"]), 1210.0 * 0.03, 1e-6)
close("and at the exit multiple in enterprise value",
      float(margin_row["Enterprise value impact"]), 1210.0 * 0.03 * 10.0, 1e-6)
expect("working capital is cash, not EBITDA",
       float(levers.set_index("Lever").loc["Working capital release", "EBITDA impact"]), 0.0)
expect("no peer benchmark means no benchmark levers",
       len(deals.value_creation_levers(history, None, None)), 1)

# Being better than the peer set is not an opportunity to get worse. The
# gap still has to be reported, but as a premium rather than as an uplift.
ahead = deals.value_creation_levers(history, peer_ebitda_margin=5.0,
                                    peer_growth=1.0, exit_multiple=10.0)
ahead_rows = ahead.set_index("Lever")
expect("a company ahead on margin has no margin uplift",
       float(ahead_rows.loc["Margin to peer median", "EBITDA impact"]), 0.0)
expect("a company ahead on growth has no growth uplift",
       float(ahead_rows.loc["Growth to peer median", "EBITDA impact"]), 0.0)
expect("and neither is ever priced as negative value",
       bool((ahead["Enterprise value impact"] >= 0).all()), True)
expect("the gap is still stated",
       "above the peer median" in str(ahead_rows.loc["Margin to peer median", "Gap"]), True)


print("\nRecommendation — it has to be able to say pass.")
good = deals.run_lbo(replace(base, exit_multiple=9.0, revenue_growth=0.07,
                             margin_improvement=0.01))
bad = deals.run_lbo(replace(base, debt_turns=6.5, interest_rate=0.13,
                            exit_multiple=6.0, revenue_growth=-0.02,
                            margin_improvement=-0.01))
rec_bad = deals.recommendation(bad, {"upside": -0.4}, history)
expect("a bad deal is passed on", rec_bad["verdict"], "Pass")
expect("and its concerns are populated", len(rec_bad["concerns"]) > 0, True)
rec_good = deals.recommendation(good, {"upside": 0.3}, history)
expect("a good deal is not passed on", rec_good["verdict"] != "Pass", True)
expect("a re-rating deal is marked down",
       "multiple expansion" in " ".join(
           deals.recommendation(deals.run_lbo(replace(base, exit_multiple=14.0)),
                                None, None)["concerns"]).lower(), True)

print("\nMemo assembly.")
memo = deals.build_memo(
    "Test Manufacturing", {"sector": "Industrials", "market_cap": 1e9,
                           "revenue": 5e8, "ebitda": 1e8, "net_debt": 2e8},
    good, comps, history, levers, rec_good)
for heading in ["Investment memo", "Recommendation:", "The business",
                "Why this could work", "Why it might not", "Returns",
                "Diligence questions"]:
    expect(f"the memo contains {heading!r}", heading in memo, True)
expect("the recommendation appears near the top",
       memo.index("Recommendation:") < 200, True)
expect("a passed deal's memo says pass",
       "Recommendation: Pass" in deals.build_memo(
           "X", {}, bad, None, history, None, rec_bad), True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All deal checks passed.")
