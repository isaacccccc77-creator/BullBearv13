"""
The buy test: does a retail investor's edge survive their costs, and how
much should they stake?

Most "should I buy?" tools answer a question nobody actually faces. They
score the company and print BUY. But a retail investor with a working
signal still loses money if the round trip costs more than the edge is
worth, and still blows up if a good bet is sized badly. Those two things —
frictions and stake — are what separate a retail outcome from an
institutional one on identical information.

So this module does not score the company. It takes a signal that already
exists, measures how that signal has actually paid in this stock's own
history, subtracts what it costs this particular investor to act on it,
and returns the one number that follows from the arithmetic: the fraction
of the portfolio that maximises long-run growth, haircut for the fact that
the inputs are estimates.

Every step is a closed-form expression with a derivation, because the
point of the page is that the reader can check it.

The chain:

    1.  Odds           p, W, L measured from forward returns conditional
                       on the signal being where it is now.
    2.  Costs          c = round-trip frictions as a fraction of notional.
    3.  Net payoffs    W_net = W(1 - t) - c,   L_net = L + c
    4.  Break-even     p* = L_net / (W_net + L_net)
    5.  Edge           e  = p·W_net - (1-p)·L_net
    6.  Shrinkage      p_lo = Wilson lower bound on p at n_eff windows
    7.  Kelly          f* = e_lo / (W_net · L_net)
    8.  Haircut        f  = min(cap, kappa · max(0, f*))

Step 7 is exact rather than the textbook f = p - q/b, which assumes the
losing outcome costs the whole stake. A share position that falls 8% does
not; using the coin-flip formula on a stock overstates the correct stake
by roughly an order of magnitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Quarter-Kelly. Full Kelly is only optimal if p, W and L are known rather
# than estimated; they never are. A quarter of it keeps about 44% of the
# compound growth for roughly a sixteenth of the variance, and — more to
# the point — stays solvent when the estimate of p is 10 points too high.
DEFAULT_KAPPA = 0.25

# Below this many independent windows the hit rate is not an estimate, it
# is an anecdote. Twenty gives a Wilson interval roughly +/- 20 points
# wide, which is already barely usable.
MIN_WINDOWS = 20

# No single position above this share of the portfolio, whatever the
# arithmetic says. Kelly is indifferent to the possibility that the model
# is simply wrong about the stock; a cap is the only defence against that.
DEFAULT_MAX_WEIGHT = 0.10


@dataclass(frozen=True)
class Costs:
    """
    What one round trip actually costs this investor.

    Percentages are per leg and the round trip charges two of them. The
    flat minimum is separate because it is the term that decides most
    retail outcomes: a $5 floor is 0.1% on a $5,000 trade and 2% on a
    $250 one, so the same signal in the same stock can be worth acting
    on at one size and not at another.
    """
    commission_pct: float = 0.08      # per leg, % of notional
    commission_min: float = 1.00      # per leg, account currency
    spread_pct: float = 0.03          # half the quoted spread, per leg
    fx_pct: float = 0.0               # currency conversion, per leg
    tax_rate: float = 0.0             # on realised gains only

    def round_trip(self, notional: float) -> float:
        """Total frictions for a buy and a later sell, as a fraction of notional."""
        if notional <= 0:
            return float("inf")
        rate = 2.0 * (self.commission_pct + self.spread_pct + self.fx_pct) / 100.0
        flat = 2.0 * self.commission_min / notional
        return rate + flat

    def breakdown(self, notional: float) -> dict[str, float]:
        """The same number, itemised, so the dominant term is visible."""
        if notional <= 0:
            return {}
        return {
            "Commission (%)": 2.0 * self.commission_pct / 100.0,
            "Commission (flat)": 2.0 * self.commission_min / notional,
            "Spread": 2.0 * self.spread_pct / 100.0,
            "FX conversion": 2.0 * self.fx_pct / 100.0,
        }


@dataclass(frozen=True)
class Odds:
    """
    How the signal has paid, measured rather than assumed.

    `n_windows` is the honest sample size: overlapping forward returns are
    not independent observations, so what is counted is the largest set of
    windows that do not overlap each other. A year of daily data looks
    like 250 observations and is really about 12.
    """
    hit_rate: float            # p, fraction of windows that finished up
    avg_win: float             # W, mean gain across winning windows
    avg_loss: float            # L, mean loss across losing windows, positive
    n_windows: float           # independent windows behind the estimate
    n_rows: int                # overlapping rows, shown only to contrast
    horizon_days: int
    baseline_hit_rate: float   # p on all days, ignoring the signal
    baseline_avg_win: float
    baseline_avg_loss: float
    band: tuple[float, float] = (0.0, 0.0)   # signal range of the bucket
    borrowed_loss: bool = False              # L came from the baseline


def wilson_lower_bound(hit_rate: float, n: float, z: float = 1.0) -> float:
    """
    The pessimistic end of a hit rate estimated from n windows.

    Sizing on the point estimate means being wrong half the time in the
    direction that costs money. The Wilson interval is used rather than
    the textbook normal one because it stays inside [0, 1] and keeps its
    coverage at the small sample sizes that honest window counting
    produces.

    z = 1 is roughly an 84% one-sided bound: deliberately mild, because
    stacking a 95% bound on top of a quarter-Kelly haircut double-counts
    the same caution and sizes everything to zero.
    """
    if n <= 0:
        return 0.0
    p = min(max(hit_rate, 0.0), 1.0)
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def net_payoffs(avg_win: float, avg_loss: float, cost: float,
                tax_rate: float = 0.0) -> tuple[float, float]:
    """
    Payoffs after frictions.

    Costs are paid on both outcomes, so they shrink the win and deepen the
    loss — the asymmetry is why frictions hurt more than their headline
    size suggests. Tax lands only on the gain, which is the one piece of
    good news in the arithmetic.
    """
    w_net = avg_win * (1.0 - tax_rate) - cost
    l_net = avg_loss + cost
    return w_net, l_net


def breakeven_hit_rate(w_net: float, l_net: float) -> float:
    """
    How often you must be right for this to be worth doing at all.

    Set expected value to zero:  p·W = (1-p)·L  =>  p* = L / (W + L).

    This is the most useful single number on the page, because it converts
    an argument about a company into a testable claim about frequency.
    """
    if w_net <= 0:
        return 1.0        # a win that loses money cannot be broken even on
    total = w_net + l_net
    return 1.0 if total <= 0 else l_net / total


def edge_per_trade(p: float, w_net: float, l_net: float) -> float:
    """Expected return per unit staked: p·W - (1-p)·L."""
    return p * w_net - (1.0 - p) * l_net


def kelly_fraction(p: float, w_net: float, l_net: float) -> float:
    """
    The stake that maximises the expected log of wealth.

    Growth as a function of stake f is

        G(f) = p·ln(1 + f·W) + (1-p)·ln(1 - f·L)

    Differentiating and setting to zero,

        p·W/(1 + f·W) = (1-p)·L/(1 - f·L)
        p·W - (1-p)·L = f·W·L
        f* = [p·W - (1-p)·L] / (W·L)

    which is edge divided by the product of the payoffs. Negative edge
    returns a negative fraction; the caller clamps it rather than this
    function, so that "the maths says short it" stays visible instead of
    being quietly rounded to zero.
    """
    if w_net <= 0 or l_net <= 0:
        return 0.0
    return edge_per_trade(p, w_net, l_net) / (w_net * l_net)


def growth_rate(f: float, p: float, w_net: float, l_net: float) -> float:
    """
    Long-run compound growth per trade at stake f.

    Plotting this is the argument against betting big: the curve is not a
    slope but a hill, and it crosses back through zero at roughly twice
    the optimal stake. Someone who doubles the "correct" bet does not earn
    double; they earn nothing.
    """
    if f <= 0:
        return 0.0
    if f * l_net >= 1.0:
        return float("-inf")      # a full loss of the stake; ruin
    return p * math.log1p(f * w_net) + (1.0 - p) * math.log1p(-f * l_net)


def independent_windows(positions: np.ndarray, horizon_days: int) -> int:
    """
    How many of these observations could be non-overlapping windows.

    Dividing the row count by the holding period is the usual shortcut and
    it is wrong in both directions: a signal that visits this level in one
    long stretch gets credited with far more evidence than it has, and one
    that visits briefly but repeatedly over a decade gets less.

    Walking the dates in order and greedily taking each one that starts at
    least `horizon_days` after the last one taken gives the exact size of
    the largest non-overlapping subset, which is the honest sample size.
    """
    taken = 0
    last = -(10 ** 9)
    for pos in positions:
        if pos - last >= horizon_days:
            taken += 1
            last = pos
    return taken


def measure_odds(signal: pd.Series, close: pd.Series, horizon_days: int = 21,
                 bucket_width: float = 0.20) -> Odds | None:
    """
    Measure p, W and L from the days this signal looked like it looks now.

    The conditional sample is every historical day whose signal fell in the
    same bucket as the latest reading, and the payoff is the return over
    the following `horizon_days`. The unconditional sample over the same
    horizon is measured alongside it, because a hit rate means nothing
    without the hit rate you would have got by buying on any day at all.
    """
    frame = pd.concat([
        pd.to_numeric(signal, errors="coerce").rename("signal"),
        pd.to_numeric(close, errors="coerce").rename("close"),
    ], axis=1).dropna()
    if len(frame) < horizon_days + 30:
        return None

    # Today's reading is what the reader is deciding on, so it is captured
    # before the tail is dropped. The last `horizon_days` rows have a signal
    # but no forward return yet; measuring the bucket from the truncated
    # frame would silently condition on where the signal stood weeks ago.
    latest = float(frame["signal"].iloc[-1])

    frame["fwd"] = frame["close"].shift(-horizon_days) / frame["close"] - 1.0
    frame = frame.dropna(subset=["fwd"])
    if frame.empty:
        return None

    def split(returns: pd.Series) -> tuple[float, float, float]:
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        p = float(len(wins) / len(returns)) if len(returns) else float("nan")
        w = float(wins.mean()) if len(wins) else 0.0
        l = float(-losses.mean()) if len(losses) else 0.0
        return p, w, l

    base_p, base_w, base_l = split(frame["fwd"])

    # The bucket: today's reading's percentile, widened either side.
    pct = float((frame["signal"] <= latest).mean())
    # Slide rather than clip at the ends of the distribution. A reading in
    # the top 3% would otherwise get a half-width bucket and half the
    # evidence — worst exactly when the signal is most extreme, which is
    # when the reader most wants an answer.
    half = bucket_width / 2
    lo_pct = min(max(0.0, pct - half), max(0.0, 1.0 - bucket_width))
    hi_pct = max(min(1.0, pct + half), min(1.0, bucket_width))
    lo = float(frame["signal"].quantile(lo_pct))
    hi = float(frame["signal"].quantile(hi_pct))
    mask = (frame["signal"] >= lo) & (frame["signal"] <= hi)
    bucket = frame[mask]
    if len(bucket) < horizon_days:
        return None
    n_independent = independent_windows(np.flatnonzero(mask.to_numpy()), horizon_days)

    p, w, l = split(bucket["fwd"])
    borrowed = False
    if l <= 0:
        # Every window in the bucket finished up. That is a sample artefact,
        # not a stock that cannot fall, so the downside is borrowed from the
        # unconditional history and the substitution is flagged.
        l = base_l
        borrowed = True
    if l <= 0 or w <= 0:
        return None

    return Odds(
        hit_rate=p,
        avg_win=w,
        avg_loss=l,
        n_windows=float(max(1, n_independent)),
        n_rows=int(len(bucket)),
        horizon_days=horizon_days,
        baseline_hit_rate=base_p,
        baseline_avg_win=base_w,
        baseline_avg_loss=base_l,
        band=(lo, hi),
        borrowed_loss=borrowed,
    )


@dataclass
class Assessment:
    """Everything the page needs, with each intermediate step kept."""
    odds: Odds
    costs: Costs
    notional: float
    portfolio: float
    cost_fraction: float
    w_net: float
    l_net: float
    breakeven: float
    edge: float
    hit_lower: float
    edge_lower: float
    kelly_raw: float
    kelly_shrunk: float
    stake_fraction: float
    stake_cash: float
    verdict: str                 # "buy" | "no" | "thin"
    headline: str
    reasons: list[str] = field(default_factory=list)

    @property
    def cost_share_of_win(self) -> float:
        """Frictions as a share of the gross average win — the retail tax."""
        gross = self.odds.avg_win
        return self.cost_fraction / gross if gross > 0 else float("inf")

    @property
    def signal_adds(self) -> float:
        """Hit rate above what buying on any random day would have given."""
        return self.odds.hit_rate - self.odds.baseline_hit_rate


def assess(odds: Odds, notional: float, portfolio: float,
           costs: Costs | None = None, kappa: float = DEFAULT_KAPPA,
           max_weight: float = DEFAULT_MAX_WEIGHT,
           min_windows: float = MIN_WINDOWS) -> Assessment:
    """
    Run the chain and return a stake, with the reasoning kept intact.

    The three ways this says no are kept distinct, because they call for
    different responses: too few windows means come back with more
    history, negative edge after costs means trade larger or not at all,
    and a hit rate at the baseline means the signal is not doing anything
    and the stock is just drifting.
    """
    costs = costs or Costs()
    cost = costs.round_trip(notional)
    w_net, l_net = net_payoffs(odds.avg_win, odds.avg_loss, cost, costs.tax_rate)

    be = breakeven_hit_rate(w_net, l_net)
    edge = edge_per_trade(odds.hit_rate, w_net, l_net)
    p_lo = wilson_lower_bound(odds.hit_rate, odds.n_windows)
    edge_lo = edge_per_trade(p_lo, w_net, l_net)
    f_raw = kelly_fraction(odds.hit_rate, w_net, l_net)
    f_shrunk = kelly_fraction(p_lo, w_net, l_net)

    stake = min(max_weight, kappa * max(0.0, f_shrunk))

    reasons: list[str] = []
    verdict = "buy"

    if odds.n_windows < min_windows:
        verdict = "thin"
        stake = 0.0
        reasons.append(
            f"Only {odds.n_windows:.0f} independent {odds.horizon_days}-day windows sit behind "
            f"this hit rate ({odds.n_rows} overlapping rows, which is not the same thing). "
            f"Below {min_windows:.0f} the estimate is an anecdote and no stake is defensible."
        )
    elif w_net <= 0:
        verdict = "no"
        stake = 0.0
        reasons.append(
            f"The average winning trade gains {odds.avg_win:.1%} and the round trip costs "
            f"{cost:.1%}. After costs the winners do not win, so there is no stake at which "
            f"this is worth doing."
        )
    elif edge_lo <= 0:
        verdict = "no"
        stake = 0.0
        reasons.append(
            f"Break-even needs a {be:.0%} hit rate. The measured rate is {odds.hit_rate:.0%}, "
            f"but on {odds.n_windows:.0f} windows it could honestly be as low as {p_lo:.0%} — "
            f"below break-even. The edge does not survive its own error bar."
        )

    if verdict == "buy" and odds.hit_rate - odds.baseline_hit_rate < 0.02:
        reasons.append(
            f"Caveat: buying on any day at all would have won {odds.baseline_hit_rate:.0%} of the "
            f"time against this setup's {odds.hit_rate:.0%}. Almost all of the edge is the stock's "
            f"own drift, not the signal."
        )

    if odds.borrowed_loss:
        reasons.append(
            "No window in this bucket finished down, so the downside is borrowed from the "
            "stock's full history. A sample with no losses in it is a small sample, not a "
            "stock that cannot fall."
        )

    if verdict == "buy":
        reasons.insert(0, (
            f"Break-even needs {be:.0%}. History gave {odds.hit_rate:.0%}, and the cautious read "
            f"of that estimate is {p_lo:.0%} — still clear. Full Kelly on the cautious hit rate "
            f"is {f_shrunk:.1%} of the portfolio; a quarter of that is {kappa * f_shrunk:.1%}."
        ))
        if stake >= max_weight - 1e-9:
            reasons.append(
                f"Capped at {max_weight:.0%}. The arithmetic asked for more, but Kelly assumes "
                f"the model is right about the stock, and no single name earns that much trust."
            )

    headline = {
        "buy": f"Buy — up to {stake:.1%} of the portfolio",
        "no": "No — the edge does not survive the costs",
        "thin": "No — not enough independent evidence to size anything",
    }[verdict]

    return Assessment(
        odds=odds, costs=costs, notional=notional, portfolio=portfolio,
        cost_fraction=cost, w_net=w_net, l_net=l_net, breakeven=be, edge=edge,
        hit_lower=p_lo, edge_lower=edge_lo, kelly_raw=f_raw, kelly_shrunk=f_shrunk,
        stake_fraction=stake, stake_cash=stake * portfolio,
        verdict=verdict, headline=headline, reasons=reasons,
    )


def breakeven_curve(odds: Odds, costs: Costs,
                    sizes: np.ndarray | None = None) -> pd.DataFrame:
    """
    Break-even hit rate against trade size.

    The flat commission is a fixed cost, so it falls as a share of a larger
    trade and the required hit rate falls with it. This is the clearest
    demonstration that position size is not a detail bolted on after the
    decision — at small sizes it *is* the decision.
    """
    if sizes is None:
        sizes = np.unique(np.round(np.geomspace(100, 50_000, 60)))
    rows = []
    for size in sizes:
        cost = costs.round_trip(float(size))
        w_net, l_net = net_payoffs(odds.avg_win, odds.avg_loss, cost, costs.tax_rate)
        rows.append({
            "Trade size": float(size),
            "Break-even hit rate": breakeven_hit_rate(w_net, l_net),
            "Round-trip cost": cost,
        })
    return pd.DataFrame(rows)


def growth_curve(assessment: Assessment, points: int = 160,
                 clamp: float = 1.0) -> pd.DataFrame:
    """
    G(f) across stakes, for plotting the hill and where it falls off it.

    Reaching 2.4x the optimum puts both the peak and the zero crossing on
    screen, which is the whole argument. But a large measured edge sends
    the unconstrained optimum past 100% of the portfolio — Kelly is happy
    to recommend leverage — and an axis running to 1,700% makes the stake
    actually taken an invisible sliver at the origin. `clamp` stops the
    axis at a stake a reader could plausibly take; when it bites, the peak
    is off-chart and the caller says where it went instead of pretending
    the visible left-hand slope is a hill.
    """
    a = assessment
    if a.l_net <= 0 or a.kelly_raw <= 0:
        # No stake has positive growth, so there is no hill — returning an
        # empty frame says that, where plotting a monotonic slide downhill
        # and labelling its left end "the peak" would not.
        return pd.DataFrame(columns=["Stake", "Growth"])
    top = min(2.4 * max(a.kelly_raw, 0.01), clamp, 0.95 / a.l_net)
    # Whatever else, the stake being recommended has to be on the chart.
    top = max(top, min(4.0 * a.stake_fraction, clamp))
    fs = np.linspace(0.0, top, points)
    return pd.DataFrame({
        "Stake": fs,
        "Growth": [growth_rate(f, a.odds.hit_rate, a.w_net, a.l_net) for f in fs],
    })
