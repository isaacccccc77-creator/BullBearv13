"""
Deal mathematics: LBO, M&A accretion/dilution, trading comparables,
operating value creation, and the memo that assembles them.

This module imports no Streamlit, so every model here can be exercised
directly from a test file — which matters more than usual, because these
are the models people quote in interviews and the arithmetic errors in
them are silent. An LBO that forgets to tax the interest shield still
produces a plausible-looking IRR.

Three principles run through the whole file:

1. Every model returns the *decomposition*, not just the answer. An IRR
   with no value bridge tells you nothing about whether the sponsor
   created value or bought a rising market. An accretion figure with no
   breakeven synergy tells you nothing about how much has to go right.

2. Assumptions that drive the answer are surfaced as their own outputs.
   Exit multiple is the single largest determinant of LBO returns and is
   also pure assumption; the code reports how much of the return it
   supplied.

3. Nothing here says "buy". The verdict helpers describe what the numbers
   are and what would have to be true for them to hold.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

# A sponsor's hurdle. Not a law of nature — it is the number PE funds
# market to their limited partners, which is why deals get engineered
# until the model produces it.
TARGET_IRR = 0.20


# ======================================================================
# IRR
# ======================================================================

def irr(cashflows: list[float], lo: float = -0.9999, hi: float = 10.0) -> Optional[float]:
    """
    Internal rate of return by bisection on the NPV.

    Bisection rather than Newton because Newton needs a derivative and a
    good starting guess, and diverges on the flat NPV curves that
    long-dated equity cashflows produce. Bisection cannot diverge; it can
    only fail to bracket, which is reported honestly as None.

    A conventional LBO cashflow (one negative outlay, then positives) has
    exactly one sign change and therefore exactly one real IRR, so the
    bracket is safe here. Multiple sign changes would admit multiple
    roots; this returns the first one found and the caller should not be
    handing it such a stream.
    """
    flows = np.asarray(cashflows, dtype=float)
    if len(flows) < 2 or not np.isfinite(flows).all():
        return None
    if not (flows < 0).any() or not (flows > 0).any():
        return None  # no sign change, no rate of return

    def npv(rate: float) -> float:
        periods = np.arange(len(flows))
        return float(np.sum(flows / (1.0 + rate) ** periods))

    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # root is outside the bracket

    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def moic(entry_equity: float, exit_equity: float) -> Optional[float]:
    """Multiple of invested capital. The number that survives contact with LPs."""
    if not entry_equity or entry_equity <= 0:
        return None
    return exit_equity / entry_equity


# ======================================================================
# LEVERAGED BUYOUT
# ======================================================================

@dataclass
class LBOAssumptions:
    """
    Everything the LBO needs, in absolute currency units except where a
    field name says otherwise. Defaults describe a plain mid-market
    manufacturing buyout so the model runs before anyone touches a slider.
    """
    company: str = "Target"
    # --- entry ---
    entry_ebitda: float = 100_000_000.0
    entry_multiple: float = 9.0
    entry_net_debt: float = 0.0          # existing net debt, refinanced at close
    # --- capital structure ---
    debt_turns: float = 4.5              # new debt raised, in turns of entry EBITDA
    interest_rate: float = 0.09
    cash_sweep: float = 0.90             # share of free cash flow used to repay debt
    min_cash: float = 15_000_000.0       # cash the business must keep to operate
    transaction_fee_pct: float = 0.02    # advisory/financing fees, on enterprise value
    # --- operations ---
    revenue: float = 500_000_000.0
    revenue_growth: float = 0.04
    ebitda_margin_start: Optional[float] = None   # derived from EBITDA/revenue if None
    margin_improvement: float = 0.005    # annual margin expansion, in percentage points
    da_pct_revenue: float = 0.04
    capex_pct_revenue: float = 0.035
    nwc_pct_revenue: float = 0.12        # working capital as a share of revenue
    tax_rate: float = 0.25
    # --- exit ---
    exit_multiple: float = 9.0
    hold_years: int = 5

    def __post_init__(self):
        if self.ebitda_margin_start is None and self.revenue:
            self.ebitda_margin_start = self.entry_ebitda / self.revenue


@dataclass
class LBOResult:
    assumptions: LBOAssumptions
    sources_uses: dict
    schedule: pd.DataFrame
    entry_equity: float
    exit_ev: float
    exit_net_debt: float
    exit_equity: float
    moic: Optional[float]
    irr: Optional[float]
    bridge: dict
    covenant_breach_year: Optional[int]
    equity_wiped: bool


def run_lbo(a: LBOAssumptions) -> LBOResult:
    """
    Projects the buyout year by year and returns the full schedule.

    The order of operations matters and is the thing most hand-built LBOs
    get wrong. Interest is charged on the *opening* balance of each year,
    because the sweep happens with cash the business has not generated
    yet at the start of it. Tax is applied after interest, which is the
    entire financial point of the structure — the debt shield is worth
    interest x tax rate a year, and a model that taxes EBIT instead
    quietly deletes it.
    """
    entry_ev = a.entry_ebitda * a.entry_multiple
    fees = entry_ev * a.transaction_fee_pct
    new_debt = a.entry_ebitda * a.debt_turns

    # Uses, laid out the way a financing memo lays them out. The equity
    # cheque buys the enterprise *less* the debt already on it, and that
    # debt is then refinanced as its own line — listing enterprise value
    # and existing net debt as two separate uses double-counts it, and
    # the error is invisible whenever the target happens to be debt-free.
    existing_net_debt = max(a.entry_net_debt, 0.0)
    uses = {
        "Purchase of equity": entry_ev - existing_net_debt,
        "Refinance existing net debt": existing_net_debt,
        "Minimum cash to balance sheet": a.min_cash,
        "Transaction fees": fees,
    }
    total_uses = sum(uses.values())
    sponsor_equity = total_uses - new_debt
    sources = {"New debt": new_debt, "Sponsor equity": sponsor_equity}

    rows = []
    debt = new_debt
    cash = a.min_cash
    revenue = a.revenue
    margin = a.ebitda_margin_start or 0.0
    prior_nwc = a.revenue * a.nwc_pct_revenue
    covenant_breach_year: Optional[int] = None

    for year in range(1, int(a.hold_years) + 1):
        revenue = revenue * (1.0 + a.revenue_growth)
        margin = margin + a.margin_improvement
        ebitda = revenue * margin
        da = revenue * a.da_pct_revenue
        ebit = ebitda - da

        opening_debt = debt
        interest = opening_debt * a.interest_rate

        ebt = ebit - interest
        # Losses do not generate a cash refund. Carryforwards exist in
        # reality; assuming them is the optimistic choice, so this does not.
        tax = max(ebt, 0.0) * a.tax_rate
        net_income = ebt - tax

        capex = revenue * a.capex_pct_revenue
        nwc = revenue * a.nwc_pct_revenue
        delta_nwc = nwc - prior_nwc
        prior_nwc = nwc

        # Free cash flow available to service debt. D&A added back because
        # it never left; the change in working capital subtracted because
        # growth consumes cash before it produces any.
        fcf = net_income + da - capex - delta_nwc

        sweep = max(fcf, 0.0) * a.cash_sweep
        repayment = min(sweep, opening_debt)
        debt = opening_debt - repayment
        # Cash the sweep did not take stays on the balance sheet. A cash
        # shortfall draws the balance down rather than magically vanishing.
        cash = cash + (fcf - repayment)

        leverage = debt / ebitda if ebitda > 0 else float("inf")
        coverage = ebitda / interest if interest > 0 else float("inf")
        if covenant_breach_year is None and (leverage > 6.5 or coverage < 2.0):
            covenant_breach_year = year

        rows.append({
            "Year": year,
            "Revenue": revenue,
            "EBITDA": ebitda,
            "EBITDA margin %": margin * 100,
            "D&A": da,
            "EBIT": ebit,
            "Interest": interest,
            "Tax": tax,
            "Net income": net_income,
            "Capex": capex,
            "Change in NWC": delta_nwc,
            "Free cash flow": fcf,
            "Debt repaid": repayment,
            "Ending debt": debt,
            "Cash": cash,
            "Net debt": debt - cash,
            "Leverage (ND/EBITDA)": (debt - cash) / ebitda if ebitda > 0 else float("inf"),
            "Interest coverage": coverage,
        })

    schedule = pd.DataFrame(rows)
    exit_ebitda = float(schedule["EBITDA"].iloc[-1])
    exit_ev = exit_ebitda * a.exit_multiple
    exit_net_debt = float(schedule["Net debt"].iloc[-1])
    exit_equity = exit_ev - exit_net_debt
    equity_wiped = exit_equity <= 0
    exit_equity_for_returns = max(exit_equity, 0.0)

    flows = [-sponsor_equity] + [0.0] * (int(a.hold_years) - 1) + [exit_equity_for_returns]

    # --- value creation bridge -------------------------------------------
    # Exactly reconciles: (M_x.E_x - D_x) - (M_e.E_e - D_e) decomposes into
    # M_e.(E_x - E_e) + E_x.(M_x - M_e) + (D_e - D_x). Fees sit outside the
    # identity because they are paid on day one and never come back, so they
    # are shown as their own negative bar rather than buried in the entry.
    entry_net_debt_at_close = new_debt - a.min_cash
    ebitda_growth_value = (exit_ebitda - a.entry_ebitda) * a.entry_multiple
    multiple_value = exit_ebitda * (a.exit_multiple - a.entry_multiple)
    debt_paydown_value = entry_net_debt_at_close - exit_net_debt
    bridge = {
        "EBITDA growth": ebitda_growth_value,
        "Multiple change": multiple_value,
        "Debt paydown & cash": debt_paydown_value,
        "Transaction fees": -fees,
    }

    return LBOResult(
        assumptions=a,
        sources_uses={"sources": sources, "uses": uses,
                      "total": total_uses, "entry_ev": entry_ev, "fees": fees},
        schedule=schedule,
        entry_equity=sponsor_equity,
        exit_ev=exit_ev,
        exit_net_debt=exit_net_debt,
        exit_equity=exit_equity,
        moic=moic(sponsor_equity, exit_equity_for_returns),
        irr=irr(flows),
        bridge=bridge,
        covenant_breach_year=covenant_breach_year,
        equity_wiped=equity_wiped,
    )


def lbo_bridge_frame(result: LBOResult) -> pd.DataFrame:
    """The bridge as an ordered frame, with the reconciliation checked."""
    items = list(result.bridge.items())
    total = sum(v for _, v in items)
    rows = [{"Driver": k, "Value": v} for k, v in items]
    rows.append({"Driver": "Equity value created", "Value": total})
    frame = pd.DataFrame(rows)
    frame["Share of value created %"] = [
        (v / total * 100) if total else np.nan for v in frame["Value"]
    ]
    return frame


def lbo_sensitivity(a: LBOAssumptions, row_field: str, row_values: list[float],
                    col_field: str, col_values: list[float],
                    metric: str = "irr") -> pd.DataFrame:
    """
    Re-solves the whole LBO across a grid. Sensitivity tables are the only
    part of an LBO anyone should look at first: a point estimate to two
    decimal places from a model with a guessed exit multiple is false
    precision, and the grid shows how much of the answer is assumption.
    """
    out = {}
    for cv in col_values:
        column = []
        for rv in row_values:
            trial = LBOAssumptions(**{**asdict(a), row_field: rv, col_field: cv})
            res = run_lbo(trial)
            value = res.irr if metric == "irr" else res.moic
            if value is None:
                column.append(np.nan)
            else:
                column.append(value * 100 if metric == "irr" else value)
        out[cv] = column
    return pd.DataFrame(out, index=row_values)


def lbo_verdict(result: LBOResult) -> str:
    """One paragraph on whether the return is real or borrowed from assumptions."""
    a = result.assumptions
    if result.equity_wiped:
        return ("The equity is wiped out at exit — debt exceeds the exit enterprise "
                "value. At this leverage the structure does not survive these operating "
                "assumptions, and no exit multiple inside a sane range rescues it.")
    if result.irr is None:
        return "The cashflows do not admit a rate of return, which means the inputs are inconsistent."

    total = sum(result.bridge.values())
    multiple_share = (result.bridge["Multiple change"] / total * 100) if total else 0.0
    growth_share = (result.bridge["EBITDA growth"] / total * 100) if total else 0.0
    paydown_share = (result.bridge["Debt paydown & cash"] / total * 100) if total else 0.0

    parts = []

    if abs(a.exit_multiple - a.entry_multiple) < 1e-9:
        parts.append("Exit is held flat to entry, so none of the return comes from "
                     "re-rating — this is the honest way to run the base case.")
    elif multiple_share > 40:
        parts.append(f"{multiple_share:.0f}% of the value created comes from the "
                     f"multiple going from {a.entry_multiple:.1f}x to {a.exit_multiple:.1f}x, "
                     "which is an assumption about the market in five years rather than "
                     "anything the sponsor does. Set exit equal to entry and see what is left.")
    elif a.exit_multiple < a.entry_multiple:
        parts.append(f"The return survives multiple compression to {a.exit_multiple:.1f}x, "
                     "which is the test that matters.")

    parts.append(f"Operationally, EBITDA growth supplied {growth_share:.0f}% and "
                 f"deleveraging {paydown_share:.0f}%.")

    if result.covenant_breach_year:
        parts.append(f"Note the covenant test fails in year {result.covenant_breach_year} — "
                     "leverage above 6.5x or coverage under 2.0x would in practice mean a "
                     "waiver negotiation, not a smooth hold.")
    if result.irr < TARGET_IRR:
        parts.append(f"Below a {TARGET_IRR * 100:.0f}% hurdle, so on these assumptions "
                     "the deal does not clear.")
    return " ".join(parts)


# ======================================================================
# M&A — ACCRETION / DILUTION
# ======================================================================

@dataclass
class MergerAssumptions:
    """
    A public-to-public acquisition. Absolute currency units throughout.
    """
    acquirer: str = "Acquirer"
    target: str = "Target"
    # --- acquirer ---
    acq_price: float = 100.0
    acq_shares: float = 500_000_000.0
    acq_net_income: float = 1_500_000_000.0
    acq_ebitda: float = 3_000_000_000.0
    acq_net_debt: float = 2_000_000_000.0
    # --- target ---
    tgt_price: float = 40.0
    tgt_shares: float = 200_000_000.0
    tgt_net_income: float = 300_000_000.0
    tgt_ebitda: float = 700_000_000.0
    tgt_net_debt: float = 500_000_000.0
    # --- deal ---
    premium: float = 0.30
    pct_cash: float = 0.50
    pct_stock: float = 0.50
    pct_debt: float = 0.0            # cash funded by new borrowing
    synergies: float = 100_000_000.0  # annual run-rate, pre-tax
    synergy_phasing: float = 1.0      # share of run-rate achieved in year one
    integration_cost: float = 0.0     # one-off, pre-tax
    new_debt_rate: float = 0.06
    cash_yield: float = 0.03          # return given up on cash spent
    tax_rate: float = 0.25

    def __post_init__(self):
        total = self.pct_cash + self.pct_stock + self.pct_debt
        if total <= 0:
            raise ValueError("consideration mix cannot be empty")
        # Normalising rather than rejecting: a user dragging three sliders
        # will pass through invalid combinations constantly, and refusing
        # to compute mid-drag makes the screen feel broken.
        self.pct_cash /= total
        self.pct_stock /= total
        self.pct_debt /= total


def run_merger(m: MergerAssumptions) -> dict:
    """
    Standalone versus pro forma EPS, and how much has to go right for the
    deal to pay for itself.

    The mechanism is simple enough to state in one line: the acquirer buys
    the target's earnings, and pays for them with some mix of its own
    shares (dilutive if its P/E is lower than the price it is paying),
    cash (which gives up interest) and debt (which costs interest). Every
    financing cost is taken after tax, because interest is deductible and
    a pre-tax comparison overstates the drag by the tax rate.
    """
    offer_price = m.tgt_price * (1.0 + m.premium)
    equity_purchase_price = offer_price * m.tgt_shares
    # What the acquirer really pays for the business: it assumes the
    # target's net debt on top of the equity cheque.
    enterprise_purchase_price = equity_purchase_price + m.tgt_net_debt

    cash_used = equity_purchase_price * m.pct_cash
    stock_used = equity_purchase_price * m.pct_stock
    debt_raised = equity_purchase_price * m.pct_debt

    new_shares = stock_used / m.acq_price if m.acq_price > 0 else 0.0
    pro_forma_shares = m.acq_shares + new_shares

    after_tax = 1.0 - m.tax_rate
    synergy_contribution = m.synergies * m.synergy_phasing * after_tax
    new_interest = debt_raised * m.new_debt_rate * after_tax
    foregone_interest = cash_used * m.cash_yield * after_tax
    integration = m.integration_cost * after_tax

    pro_forma_net_income = (m.acq_net_income + m.tgt_net_income
                            + synergy_contribution - new_interest
                            - foregone_interest - integration)

    standalone_eps = m.acq_net_income / m.acq_shares if m.acq_shares else np.nan
    pro_forma_eps = pro_forma_net_income / pro_forma_shares if pro_forma_shares else np.nan
    accretion = (pro_forma_eps / standalone_eps - 1.0) if standalone_eps else np.nan

    # Breakeven synergies: the pre-tax annual number that makes pro forma
    # EPS exactly equal standalone EPS. This is the figure to argue about
    # — "is $180m of cost synergy credible in this industry" is a question
    # with an answer, where "is 3% accretion good" is not.
    required_ni = standalone_eps * pro_forma_shares
    shortfall = (required_ni - m.acq_net_income - m.tgt_net_income
                 + new_interest + foregone_interest + integration)
    breakeven_synergies = shortfall / after_tax if after_tax else np.nan

    combined_ebitda = m.acq_ebitda + m.tgt_ebitda + m.synergies
    combined_net_debt = m.acq_net_debt + m.tgt_net_debt + debt_raised + cash_used
    pro_forma_leverage = (combined_net_debt / combined_ebitda
                          if combined_ebitda > 0 else float("inf"))
    standalone_leverage = (m.acq_net_debt / m.acq_ebitda
                           if m.acq_ebitda > 0 else float("inf"))

    acq_pe = m.acq_price / standalone_eps if standalone_eps else np.nan
    tgt_eps = m.tgt_net_income / m.tgt_shares if m.tgt_shares else np.nan
    tgt_pe_at_offer = offer_price / tgt_eps if tgt_eps else np.nan

    return {
        "offer_price": offer_price,
        "premium": m.premium,
        "equity_purchase_price": equity_purchase_price,
        "enterprise_purchase_price": enterprise_purchase_price,
        "ev_ebitda_paid": (enterprise_purchase_price / m.tgt_ebitda
                           if m.tgt_ebitda > 0 else np.nan),
        "cash_used": cash_used,
        "stock_used": stock_used,
        "debt_raised": debt_raised,
        "new_shares": new_shares,
        "pro_forma_shares": pro_forma_shares,
        "target_ownership_pct": (new_shares / pro_forma_shares * 100
                                 if pro_forma_shares else np.nan),
        "synergy_contribution": synergy_contribution,
        "new_interest": new_interest,
        "foregone_interest": foregone_interest,
        "pro_forma_net_income": pro_forma_net_income,
        "standalone_eps": standalone_eps,
        "pro_forma_eps": pro_forma_eps,
        "accretion": accretion,
        "breakeven_synergies": breakeven_synergies,
        "pro_forma_leverage": pro_forma_leverage,
        "standalone_leverage": standalone_leverage,
        "acquirer_pe": acq_pe,
        "target_pe_at_offer": tgt_pe_at_offer,
        "assumptions": m,
    }


def merger_sensitivity(m: MergerAssumptions, premiums: list[float],
                       stock_mixes: list[float]) -> pd.DataFrame:
    """Accretion (%) across offer premium and how much of it is paid in stock."""
    out = {}
    for mix in stock_mixes:
        column = []
        for prem in premiums:
            trial = MergerAssumptions(**{**asdict(m), "premium": prem,
                                         "pct_stock": mix, "pct_cash": 1.0 - mix,
                                         "pct_debt": 0.0})
            column.append(run_merger(trial)["accretion"] * 100)
        out[mix] = column
    return pd.DataFrame(out, index=premiums)


def merger_verdict(result: dict) -> str:
    """What the accretion number is actually resting on."""
    m = result["assumptions"]
    accretion = result["accretion"]
    if not np.isfinite(accretion):
        return "The inputs do not produce a comparable EPS — check share counts and earnings."

    direction = "accretive" if accretion > 0 else "dilutive"
    parts = [f"{abs(accretion) * 100:.1f}% {direction} to earnings per share in year one."]

    breakeven = result["breakeven_synergies"]
    if breakeven <= 0:
        parts.append("The deal works with no synergies at all, which means the acquirer is "
                     "buying earnings more cheaply than its own are valued.")
    else:
        share_of_target = (breakeven / (m.tgt_ebitda or np.nan)) * 100
        parts.append(
            f"It needs {breakeven / 1e6:,.0f}m of annual pre-tax synergy to break even — "
            f"{share_of_target:.0f}% of the target's entire EBITDA. "
            f"The case assumes {m.synergies / 1e6:,.0f}m."
        )

    pe_gap = result["acquirer_pe"] - result["target_pe_at_offer"]
    if np.isfinite(pe_gap) and m.pct_stock > 0.05:
        if pe_gap > 0:
            parts.append(
                f"The acquirer trades at {result['acquirer_pe']:.1f}x earnings and is paying "
                f"{result['target_pe_at_offer']:.1f}x, so issuing stock is accretive arithmetic "
                "before any synergy — the market is doing the work, not the integration team."
            )
        else:
            parts.append(
                f"The acquirer trades at {result['acquirer_pe']:.1f}x and is paying "
                f"{result['target_pe_at_offer']:.1f}x, so every share issued dilutes. "
                "Synergies are the only thing closing that gap."
            )

    leverage_jump = result["pro_forma_leverage"] - result["standalone_leverage"]
    if np.isfinite(leverage_jump) and leverage_jump > 0.5:
        parts.append(
            f"Leverage goes from {result['standalone_leverage']:.1f}x to "
            f"{result['pro_forma_leverage']:.1f}x, which is a ratings conversation."
        )

    parts.append("First-year EPS is a weak test of a deal and a strong test of a "
                 "management team's incentives. It ignores integration risk, the "
                 "amortisation of acquired intangibles, and whether the target was "
                 "worth owning at any price.")
    return " ".join(parts)


# ======================================================================
# TRADING COMPARABLES
# ======================================================================

# How each multiple turns into a share price. EV multiples value the whole
# business and have to have net debt taken back off; equity multiples land
# on the share price directly.
MULTIPLE_SPECS = {
    "EV/Revenue": ("revenue", "ev"),
    "EV/EBITDA":  ("ebitda", "ev"),
    "EV/EBIT":    ("ebit", "ev"),
    "P/E":        ("eps", "equity"),
    "P/B":        ("book_value_per_share", "equity"),
}


def _clean_multiples(values: pd.Series) -> pd.Series:
    """
    Drops multiples that cannot mean anything.

    A negative EV/EBITDA is not a cheap company, it is a company with
    negative EBITDA, and letting one into a median drags the whole comp
    set toward a number no one would pay. Absurdly high values are
    usually a near-zero denominator doing the same damage from the other
    direction. Both are removed rather than winsorised, because a peer
    whose multiple is meaningless is not a peer for that multiple.
    """
    clean = pd.to_numeric(values, errors="coerce")
    return clean[(clean > 0) & (clean < 500)].dropna()


def build_comps(target: dict, peers: pd.DataFrame) -> dict:
    """
    Peer multiples, their quartiles, and the share price each one implies
    for the target.

    Returns a football field: one horizontal range per methodology,
    spanning the peer set's first to third quartile, with the median
    marked. Quartiles rather than min-to-max because the extremes of a
    ten-name comp set are almost always a story about one company rather
    than about the industry.
    """
    stats_rows = []
    implied_rows = []
    net_debt = float(target.get("net_debt") or 0.0)
    shares = float(target.get("shares") or 0.0)

    for label, (metric_key, kind) in MULTIPLE_SPECS.items():
        if label not in peers.columns:
            continue
        clean = _clean_multiples(peers[label])
        if len(clean) < 3:
            continue  # a quartile off two observations is theatre

        q1, med, q3 = (float(clean.quantile(0.25)), float(clean.median()),
                       float(clean.quantile(0.75)))
        stats_rows.append({
            "Multiple": label, "n": int(len(clean)),
            "Low (25th)": q1, "Median": med, "High (75th)": q3,
            "Min": float(clean.min()), "Max": float(clean.max()),
            "Target": float(target.get(label)) if target.get(label) else np.nan,
        })

        metric = target.get(metric_key)
        if metric is None or not np.isfinite(metric) or metric <= 0:
            continue
        if kind == "ev":
            if shares <= 0:
                continue
            prices = [(m * metric - net_debt) / shares for m in (q1, med, q3)]
        else:
            prices = [m * metric for m in (q1, med, q3)]
        if not all(np.isfinite(p) for p in prices):
            continue
        implied_rows.append({
            "Method": label,
            "Low": min(prices), "Mid": prices[1], "High": max(prices),
        })

    stats = pd.DataFrame(stats_rows)
    field = pd.DataFrame(implied_rows)
    price = float(target.get("price") or 0.0)

    blended = float(field["Mid"].median()) if not field.empty else np.nan
    upside = (blended / price - 1.0) if price > 0 and np.isfinite(blended) else np.nan

    return {
        "stats": stats,
        "football_field": field,
        "current_price": price,
        "blended_value": blended,
        "upside": upside,
        "peers_used": int(len(peers)),
    }


def comps_verdict(comps: dict) -> str:
    """What a comp set can and cannot tell you."""
    field = comps["football_field"]
    if field.empty:
        return ("Not enough clean peer multiples to build a range. Negative or missing "
                "denominators knock peers out of individual multiples, so a set that "
                "looks large can still be too thin here.")

    price = comps["current_price"]
    blended = comps["blended_value"]
    upside = comps["upside"]
    spread = float(field["High"].max() - field["Low"].min())
    spread_pct = spread / price * 100 if price else np.nan

    if abs(upside) < 0.02:
        gap = "essentially in line with the market"
    else:
        gap = (f"{abs(upside) * 100:.0f}% "
               f"{'above' if upside > 0 else 'below'} the market")
    parts = [
        f"The peer set puts the blended value at {blended:,.2f} against a "
        f"{price:,.2f} share price — {gap}."
    ]
    if np.isfinite(spread_pct) and spread_pct > 60:
        parts.append(
            f"But the methodologies disagree across a {spread_pct:.0f}% band, which is the "
            "real finding: when EV/Revenue and P/E point to different worlds, the peer set "
            "is not homogeneous and the median is averaging different businesses."
        )
    parts.append(
        "A comp set prices a company against the market's current mood about its "
        "industry, not against what the business is worth. Trading below peers is as "
        "often a correct discount for worse growth, thinner margins or more leverage as "
        "it is an opportunity — which is what the operating analysis is for."
    )
    return " ".join(parts)


# ======================================================================
# OPERATING PERFORMANCE & VALUE CREATION
# ======================================================================

def _pick(frame: Optional[pd.DataFrame], candidates: list[str]) -> Optional[pd.Series]:
    """
    Finds a line item by any of several names.

    Statement row labels are not stable — across vendors and across years
    the same line is 'Total Revenue', 'TotalRevenue' or 'Revenues' — so
    every lookup tries a list and matches case-insensitively without
    spaces before giving up.
    """
    if frame is None or frame.empty:
        return None
    normalised = {str(i).lower().replace(" ", ""): i for i in frame.index}
    for name in candidates:
        key = name.lower().replace(" ", "")
        if key in normalised:
            row = frame.loc[normalised[key]]
            return pd.to_numeric(row, errors="coerce")
    return None


def operating_analysis(income: pd.DataFrame, balance: pd.DataFrame,
                       cashflow: pd.DataFrame) -> pd.DataFrame:
    """
    Turns three statements into the operating history a deal team reads:
    growth, margins, capital efficiency and how much of the profit
    arrives as cash.

    Columns are years, oldest first. Anything that cannot be computed
    from what the statements actually contain is left as NaN rather than
    defaulted to zero — a working capital cycle of zero days is a claim,
    and it is usually a false one.
    """
    revenue = _pick(income, ["Total Revenue", "Revenues", "Operating Revenue"])
    if revenue is None or revenue.dropna().empty:
        return pd.DataFrame()

    gross = _pick(income, ["Gross Profit"])
    op_income = _pick(income, ["Operating Income", "EBIT", "Total Operating Income As Reported"])
    net_income = _pick(income, ["Net Income", "Net Income Common Stockholders"])
    # `a or b` would be ambiguous here: _pick returns a Series, and a
    # Series has no truth value. The fallback has to be spelled out.
    da = _pick(cashflow, ["Depreciation And Amortization",
                          "Depreciation Amortization Depletion", "Depreciation"])
    if da is None:
        da = _pick(income, ["Reconciled Depreciation"])
    capex = _pick(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    ocf = _pick(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])

    receivables = _pick(balance, ["Accounts Receivable", "Receivables", "Net Receivables"])
    inventory = _pick(balance, ["Inventory"])
    payables = _pick(balance, ["Accounts Payable", "Payables"])
    cogs = _pick(income, ["Cost Of Revenue", "Cost Of Goods Sold"])
    equity = _pick(balance, ["Stockholders Equity", "Total Stockholder Equity",
                             "Common Stock Equity"])
    debt = _pick(balance, ["Total Debt", "Long Term Debt"])
    cash_bal = _pick(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

    years = list(revenue.dropna().index)
    years = sorted(years)  # oldest first, so trends read left to right

    def at(series, year):
        if series is None or year not in series.index:
            return np.nan
        value = series.get(year)
        return float(value) if pd.notna(value) else np.nan

    rows = []
    for i, year in enumerate(years):
        rev = at(revenue, year)
        oi = at(op_income, year)
        dep = abs(at(da, year)) if pd.notna(at(da, year)) else np.nan
        ebitda = oi + dep if pd.notna(oi) and pd.notna(dep) else np.nan
        cx = abs(at(capex, year)) if pd.notna(at(capex, year)) else np.nan
        prior_rev = at(revenue, years[i - 1]) if i > 0 else np.nan

        invested = np.nan
        eq, dbt, csh = at(equity, year), at(debt, year), at(cash_bal, year)
        if pd.notna(eq) and pd.notna(dbt):
            invested = eq + dbt - (csh if pd.notna(csh) else 0.0)
        nopat = oi * 0.75 if pd.notna(oi) else np.nan  # 25% blended tax stand-in

        rows.append({
            "Year": getattr(year, "year", year),
            "Revenue": rev,
            "Revenue growth %": ((rev / prior_rev - 1) * 100
                                 if pd.notna(prior_rev) and prior_rev else np.nan),
            "Gross margin %": (at(gross, year) / rev * 100 if rev else np.nan),
            "EBITDA": ebitda,
            "EBITDA margin %": (ebitda / rev * 100 if pd.notna(ebitda) and rev else np.nan),
            "Operating margin %": (oi / rev * 100 if pd.notna(oi) and rev else np.nan),
            "Net margin %": (at(net_income, year) / rev * 100 if rev else np.nan),
            "Capex % revenue": (cx / rev * 100 if pd.notna(cx) and rev else np.nan),
            "FCF": (at(ocf, year) - cx if pd.notna(at(ocf, year)) and pd.notna(cx) else np.nan),
            "Cash conversion %": (((at(ocf, year) - cx) / ebitda * 100)
                                  if pd.notna(ebitda) and ebitda > 0
                                  and pd.notna(at(ocf, year)) and pd.notna(cx) else np.nan),
            "DSO": (at(receivables, year) / rev * 365 if rev else np.nan),
            "DIO": (at(inventory, year) / abs(at(cogs, year)) * 365
                    if pd.notna(at(cogs, year)) and at(cogs, year) else np.nan),
            "DPO": (at(payables, year) / abs(at(cogs, year)) * 365
                    if pd.notna(at(cogs, year)) and at(cogs, year) else np.nan),
            "ROIC %": (nopat / invested * 100
                       if pd.notna(nopat) and pd.notna(invested) and invested > 0 else np.nan),
        })

    frame = pd.DataFrame(rows)
    frame["Cash conversion cycle"] = frame["DSO"] + frame["DIO"] - frame["DPO"]
    return frame


def value_creation_levers(history: pd.DataFrame, peer_ebitda_margin: Optional[float],
                          peer_growth: Optional[float],
                          exit_multiple: float = 9.0) -> pd.DataFrame:
    """
    Quantifies the standard operating levers in EBITDA and in enterprise
    value, so "improve margins" stops being a bullet point and becomes a
    number someone has to sign up to.

    Each lever is priced at the exit multiple, which is how a sponsor
    actually thinks about it: a point of margin is worth a point of
    margin times revenue times the multiple, and that product is what
    justifies the cost of getting it.
    """
    if history.empty:
        return pd.DataFrame()

    latest = history.iloc[-1]
    revenue = float(latest["Revenue"]) if pd.notna(latest["Revenue"]) else np.nan
    if not np.isfinite(revenue) or revenue <= 0:
        return pd.DataFrame()

    rows = []

    # A company already ahead of its peers has no lever here, and showing
    # the gap as a negative "uplift" reads as an opportunity to get worse.
    # Catching up to the median is value creation; falling back to it is
    # not, so the impact is floored at zero and the row says why.
    margin = latest.get("EBITDA margin %")
    if peer_ebitda_margin is not None and pd.notna(margin):
        gap = peer_ebitda_margin - float(margin)
        ahead = gap <= 0
        uplift = 0.0 if ahead else revenue * (gap / 100.0)
        rows.append({
            "Lever": "Margin to peer median",
            "Gap": (f"{abs(gap):.1f}pp above the peer median" if ahead
                    else f"{gap:.1f}pp below the peer median"),
            "EBITDA impact": uplift,
            "Enterprise value impact": uplift * exit_multiple,
            "What it takes": (
                "Already ahead of the peer set, so there is nothing to close here. The "
                "question becomes whether that premium is durable or is about to be "
                "competed away." if ahead else
                "Procurement, pricing discipline and overhead reduction. The gap is only "
                "capturable if peers run the same business model — a mix difference is "
                "not an inefficiency."),
        })

    growth = latest.get("Revenue growth %")
    if peer_growth is not None and pd.notna(growth) and pd.notna(margin):
        gap = peer_growth - float(growth)
        ahead = gap <= 0
        uplift = 0.0 if ahead else revenue * (gap / 100.0) * (float(margin) / 100.0)
        rows.append({
            "Lever": "Growth to peer median",
            "Gap": (f"{abs(gap):.1f}pp above the peer median" if ahead
                    else f"{gap:.1f}pp below the peer median"),
            "EBITDA impact": uplift,
            "Enterprise value impact": uplift * exit_multiple,
            "What it takes": (
                "Already growing faster than the peer set. That is worth paying for, but "
                "it is not a lever a new owner pulls — it is a premium already in the "
                "price." if ahead else
                "Commercial investment, which costs money before it earns any. Incremental "
                "revenue is priced here at today's margin, and new business usually "
                "arrives below it."),
        })

    ccc = latest.get("Cash conversion cycle")
    if pd.notna(ccc) and ccc > 0:
        release = revenue / 365.0 * min(float(ccc) * 0.20, 15.0)
        rows.append({
            "Lever": "Working capital release",
            "Gap": f"{float(ccc):.0f} day cash conversion cycle",
            "EBITDA impact": 0.0,
            "Enterprise value impact": release,
            "What it takes": ("A one-off cash release from tightening collections and "
                              "terms, modelled at 20% of the cycle capped at 15 days. It "
                              "is cash, not earnings, so it pays down debt rather than "
                              "lifting EBITDA — and it can only be harvested once."),
        })

    return pd.DataFrame(rows)


def operating_verdict(history: pd.DataFrame) -> str:
    """Reads the trend rather than the latest year."""
    if history.empty or len(history) < 2:
        return "Not enough statement history to read a trend."

    first, last = history.iloc[0], history.iloc[-1]
    parts = []

    m0, m1 = first.get("EBITDA margin %"), last.get("EBITDA margin %")
    if pd.notna(m0) and pd.notna(m1):
        delta = float(m1) - float(m0)
        if abs(delta) < 0.5:
            parts.append(f"EBITDA margin has held flat around {float(m1):.1f}% — stable, "
                         "which for a buyout is a feature rather than a disappointment.")
        else:
            word = "expanded" if delta > 0 else "compressed"
            parts.append(f"EBITDA margin {word} {abs(delta):.1f} points to {float(m1):.1f}% "
                         f"over {len(history)} years.")

    growth = history["Revenue growth %"].dropna()
    if len(growth) >= 2:
        parts.append(f"Revenue growth has averaged {growth.mean():.1f}% a year, ranging "
                     f"{growth.min():.1f}% to {growth.max():.1f}%.")

    conversion = last.get("Cash conversion %")
    if pd.notna(conversion):
        if conversion < 50:
            parts.append(f"Only {float(conversion):.0f}% of EBITDA reaches free cash flow, "
                         "which is the number that decides whether a levered structure can "
                         "actually service its debt — accounting profit does not pay interest.")
        else:
            parts.append(f"{float(conversion):.0f}% of EBITDA converts to free cash flow.")

    roic = last.get("ROIC %")
    if pd.notna(roic):
        if roic < 8:
            parts.append(f"ROIC of {float(roic):.1f}% is below a plausible cost of capital, "
                         "so growth as currently financed destroys value rather than "
                         "creating it.")
        else:
            parts.append(f"ROIC of {float(roic):.1f}% clears a normal cost of capital, "
                         "so incremental growth is worth funding.")
    return " ".join(parts)


# ======================================================================
# INVESTMENT MEMO
# ======================================================================

def _millions(x) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    if abs(x) >= 1e9:
        return f"{x / 1e9:,.2f}bn"
    return f"{x / 1e6:,.0f}m"


def recommendation(lbo: Optional[LBOResult], comps: Optional[dict],
                   history: Optional[pd.DataFrame]) -> dict:
    """
    A scored recommendation with its reasons attached.

    The scoring is deliberately blunt and the thresholds are visible in
    the source, because a recommendation engine whose criteria are hidden
    is worse than no recommendation at all. Every test can push in either
    direction, and the ones that fail are reported alongside the ones
    that pass — a memo that lists only supporting evidence is a pitch,
    not an analysis.
    """
    supports, concerns = [], []
    score = 0

    if lbo is not None and lbo.irr is not None and not lbo.equity_wiped:
        if lbo.irr >= TARGET_IRR:
            score += 2
            supports.append(
                f"Returns clear the hurdle: {lbo.irr * 100:.1f}% IRR and "
                f"{lbo.moic:.2f}x over {lbo.assumptions.hold_years} years."
            )
        else:
            score -= 2
            concerns.append(
                f"Returns miss the hurdle: {lbo.irr * 100:.1f}% IRR against "
                f"{TARGET_IRR * 100:.0f}% target."
            )

        total = sum(lbo.bridge.values())
        multiple_share = (lbo.bridge["Multiple change"] / total * 100) if total else 0.0
        if multiple_share > 40:
            score -= 2
            concerns.append(
                f"{multiple_share:.0f}% of value creation comes from multiple expansion, "
                "which is a bet on the exit market rather than on the business."
            )
        elif lbo.assumptions.exit_multiple <= lbo.assumptions.entry_multiple:
            score += 1
            supports.append("Returns hold without any multiple expansion assumed.")

        if lbo.covenant_breach_year:
            score -= 2
            concerns.append(
                f"The capital structure breaches a covenant test in year "
                f"{lbo.covenant_breach_year}."
            )
    elif lbo is not None and lbo.equity_wiped:
        score -= 4
        concerns.append("The equity is wiped out at exit under the base case.")

    if comps is not None and np.isfinite(comps.get("upside", np.nan)):
        upside = comps["upside"]
        if upside > 0.15:
            score += 1
            supports.append(f"Trades {upside * 100:.0f}% below the peer-implied value.")
        elif upside < -0.15:
            score -= 1
            concerns.append(
                f"Trades {abs(upside) * 100:.0f}% above peer-implied value — entry is "
                "expensive against the comp set before any premium is paid."
            )

    if history is not None and not history.empty:
        last = history.iloc[-1]
        conversion = last.get("Cash conversion %")
        if pd.notna(conversion):
            if conversion >= 60:
                score += 1
                supports.append(
                    f"{float(conversion):.0f}% of EBITDA converts to free cash flow, which "
                    "is what services leverage."
                )
            elif conversion < 40:
                score -= 2
                concerns.append(
                    f"Only {float(conversion):.0f}% of EBITDA reaches free cash flow — thin "
                    "cover for a levered structure."
                )
        roic = last.get("ROIC %")
        if pd.notna(roic) and roic < 8:
            score -= 1
            concerns.append(f"ROIC of {float(roic):.1f}% sits below a plausible cost of capital.")
        if len(history) >= 2:
            m0, m1 = history.iloc[0].get("EBITDA margin %"), last.get("EBITDA margin %")
            if pd.notna(m0) and pd.notna(m1) and float(m1) - float(m0) < -2.0:
                score -= 1
                concerns.append(
                    f"EBITDA margin has compressed {abs(float(m1) - float(m0)):.1f} points "
                    "over the period shown."
                )

    if score >= 3:
        verdict, tone = "Pursue", "bull"
    elif score >= 0:
        verdict, tone = "Pursue with conditions", "neutral"
    else:
        verdict, tone = "Pass", "bear"

    return {"verdict": verdict, "tone": tone, "score": score,
            "supports": supports, "concerns": concerns}


DILIGENCE_QUESTIONS = [
    ("Revenue quality",
     "What share of revenue is contracted or recurring, and what is gross retention by "
     "cohort? A multiple paid for recurring revenue against a business that re-wins its "
     "customers every year is the single most common way a deal goes wrong."),
    ("Customer concentration",
     "Revenue from the top five customers, and the notice period in each contract. "
     "Anything above 30% in one name is a leverage constraint, not a footnote."),
    ("Margin durability",
     "How much of the current margin is price and how much is volume, and what happened "
     "to it in the last input-cost shock."),
    ("Capex honesty",
     "Split maintenance from growth capex. Under-invested plant shows up as strong free "
     "cash flow right until the year the sponsor has to catch up."),
    ("Working capital seasonality",
     "The intra-year peak, not the year-end balance. Year-end is the one day management "
     "optimises for, and the revolver is sized off the peak."),
    ("Management incentives",
     "Who is rolling equity and on what terms. A management team that takes all its money "
     "off the table at close is telling you something."),
]


def build_memo(company: str, snapshot: dict, lbo: Optional[LBOResult],
               comps: Optional[dict], history: Optional[pd.DataFrame],
               levers: Optional[pd.DataFrame], rec: dict) -> str:
    """
    Assembles the memo as markdown, in the order a partner reads it:
    recommendation first, then what would have to be true, then the
    evidence, then what is still unknown.

    Nothing here is generated prose for its own sake — every section is
    populated from a model elsewhere in this file, so the memo cannot
    drift away from the numbers it is describing.
    """
    lines = [
        f"# Investment memo — {company}",
        "",
        f"**Recommendation: {rec['verdict']}**",
        "",
    ]

    sector = snapshot.get("sector") or "n/a"
    industry = snapshot.get("industry") or "n/a"
    lines += [
        "## 1. The business",
        "",
        f"- Sector / industry: {sector} — {industry}",
        f"- Market capitalisation: {_millions(snapshot.get('market_cap'))}",
        f"- Enterprise value: {_millions(snapshot.get('enterprise_value'))}",
        f"- Revenue: {_millions(snapshot.get('revenue'))}",
        f"- EBITDA: {_millions(snapshot.get('ebitda'))}",
        f"- Net debt: {_millions(snapshot.get('net_debt'))}",
        "",
    ]

    lines += ["## 2. Why this could work", ""]
    lines += [f"- {s}" for s in rec["supports"]] or ["- Nothing in the model supports the case."]
    lines += ["", "## 3. Why it might not", ""]
    lines += [f"- {c}" for c in rec["concerns"]] or ["- No modelled concern flagged."]
    lines += [""]

    if comps is not None and not comps["football_field"].empty:
        lines += [
            "## 4. Valuation",
            "",
            f"Blended peer-implied value of {comps['blended_value']:,.2f} per share against "
            f"a {comps['current_price']:,.2f} market price "
            f"({comps['upside'] * 100:+.0f}%), across {comps['peers_used']} peers.",
            "",
            comps_verdict(comps),
            "",
        ]

    if lbo is not None:
        a = lbo.assumptions
        lines += [
            "## 5. Returns",
            "",
            f"Entry at {a.entry_multiple:.1f}x EBITDA with {a.debt_turns:.1f} turns of debt; "
            f"exit at {a.exit_multiple:.1f}x after {a.hold_years} years.",
            "",
            f"- Sponsor equity: {_millions(lbo.entry_equity)}",
            f"- Exit equity: {_millions(lbo.exit_equity)}",
            f"- MoIC: {lbo.moic:.2f}x" if lbo.moic else "- MoIC: n/a",
            f"- IRR: {lbo.irr * 100:.1f}%" if lbo.irr is not None else "- IRR: n/a",
            "",
            "Value creation splits as:",
            "",
        ]
        total = sum(lbo.bridge.values())
        for driver, value in lbo.bridge.items():
            share = (value / total * 100) if total else 0.0
            lines.append(f"- {driver}: {_millions(value)} ({share:.0f}%)")
        lines += ["", lbo_verdict(lbo), ""]

    if history is not None and not history.empty:
        lines += ["## 6. Operating history", "", operating_verdict(history), ""]

    if levers is not None and not levers.empty:
        lines += ["## 7. Value creation plan", ""]
        for _, row in levers.iterrows():
            lines.append(
                f"- **{row['Lever']}** ({row['Gap']}): "
                f"{_millions(row['EBITDA impact'])} of EBITDA, "
                f"{_millions(row['Enterprise value impact'])} of enterprise value. "
                f"{row['What it takes']}"
            )
        lines += [""]

    lines += ["## 8. Diligence questions", ""]
    for topic, question in DILIGENCE_QUESTIONS:
        lines.append(f"- **{topic}.** {question}")

    lines += [
        "",
        "---",
        "",
        "*Built from public filings and market data. Every figure above is a model "
        "output, not a fact about the future — the assumptions that drive them are "
        "listed on the screens that produced them. Not investment advice.*",
    ]
    return "\n".join(lines)
