"""
Can a model call next month's direction? A walk-forward test.

Built from two Stanford CS230 projects, read critically.

Duemig, "Predicting stock prices with LSTM Networks", supplies the design:

  - features: rolling log return, volatility and volume over 23 look-backs,
    i in {1, 2, 3} U {4k : 1 <= k <= 20} trading weeks, so 69 in all
  - label: did the next trading month (21 trading days) finish up?
  - walk-forward validation instead of k-fold, a held-out test period, and
    AUC alongside accuracy because up-months outnumber down-months
  - and its finding: the LSTM did no better than logistic regression, and
    both scored a test AUC of about 0.5, which is a coin flip.

Because the LSTM added nothing over the logistic baseline in the paper's
own results table, the baseline is what is implemented here. It is the
part of the result that carried the weight, it fits in milliseconds rather
than needing a GPU, and every one of its 69 weights can be put on screen.

Three places this departs from the paper, each closing a leak the paper
leaves open:

  1. Standardisation uses training-window statistics only. The paper's
     equation (3) standardises with the mean and SD of the whole sample,
     which tells a model standing in 1995 what volumes look like in 2020.
  2. Training rows whose 21-day label reaches past the decision date are
     purged. Without that, the last month of training labels are answers
     to the first month of the test.
  3. Volume enters as log(window average / one-year average), not raw.
     Raw volume trends for decades and jumps at stock splits, so it says
     more about which year it is than about the setup.

Miao, "A Deep Learning Approach for Stock Market Prediction", supplies a
warning instead: its charts show a predicted price hugging the real one.
A "forecast" of tomorrow's price that simply repeats today's hugs the real
line just as closely. persistence_check() measures that.

Imports no Streamlit, so both front ends share it and test_forecast.py can
check it against data where the right answer is known in advance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import expit

DAYS_PER_WEEK = 5
WINDOWS_WEEKS: tuple[int, ...] = (1, 2, 3) + tuple(4 * k for k in range(1, 21))
FAMILIES = ("return", "volatility", "volume")
HORIZON_DAYS = 21
VOLUME_REFERENCE_DAYS = 252
PENALTIES = {"ridge": 0.0, "elastic net": 0.5, "lasso": 1.0}
STRENGTHS = (1e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1)
MIN_TRAIN_DAYS = 756          # three years before the first out-of-sample call
MIN_TEST_DAYS = 504           # two years of calls, about 24 independent months
INITIAL_TRAIN_SHARE = 0.5
REFIT_EVERY = 21              # retrain monthly, as a desk running this would
DEFAULT_COST = 0.001          # 0.10% each time the strategy buys or sells


# ----------------------------------------------------------------------
# Features and labels
# ----------------------------------------------------------------------
def build_features(df: pd.DataFrame,
                   windows: tuple[int, ...] = WINDOWS_WEEKS) -> pd.DataFrame:
    """
    The paper's three families over each look-back, one column per pair.

    return_{w}w      ln(P_t / P_{t-n}), n = 5w. Log returns add, so this is
                     the sum of the last n daily log returns.
    volatility_{w}w  SD of daily log returns over n days, times sqrt(n):
                     variances of independent days add, so SDs scale with
                     the square root of time.
    volume_{w}w      ln(average volume over n days / average over a year).

    Volume is dropped entirely when the feed has none (indices and FX often
    report zero), rather than filled with something invented.
    """
    close = df["Close"].astype(float)
    log_ret = np.log(close).diff()
    cols: dict[str, pd.Series] = {}
    for w in windows:
        n = w * DAYS_PER_WEEK
        cols[f"return_{w}w"] = np.log(close / close.shift(n))
        cols[f"volatility_{w}w"] = log_ret.rolling(n).std() * math.sqrt(n)

    volume = df["Volume"].astype(float) if "Volume" in df else None
    if volume is not None and (volume > 0).mean() > 0.95:
        v = volume.where(volume > 0)
        reference = v.rolling(VOLUME_REFERENCE_DAYS, min_periods=VOLUME_REFERENCE_DAYS // 2).mean()
        for w in windows:
            n = w * DAYS_PER_WEEK
            cols[f"volume_{w}w"] = np.log(v.rolling(n, min_periods=max(1, int(n * 0.8))).mean()
                                          / reference)

    ordered = [f"{family}_{w}w" for family in FAMILIES for w in windows
               if f"{family}_{w}w" in cols]
    return pd.DataFrame({k: cols[k] for k in ordered}, index=df.index)


def forward_log_return(close: pd.Series, horizon: int = HORIZON_DAYS) -> pd.Series:
    return np.log(close.shift(-horizon) / close)


def make_labels(close: pd.Series, horizon: int = HORIZON_DAYS) -> pd.Series:
    """1 if the next `horizon` trading days finished up, 0 if not, NaN if not yet known."""
    fwd = forward_log_return(close, horizon)
    return (fwd > 0).astype(float).where(fwd.notna())


# ----------------------------------------------------------------------
# Logistic regression with an elastic-net penalty
# ----------------------------------------------------------------------
def _split(params: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    k = (len(params) - 1) // 2
    return params[:k], params[k:2 * k], params[-1]


def _loss_and_grad(params: np.ndarray, X: np.ndarray, y: np.ndarray,
                   lam: float, mix: float):
    """
    Mean log loss + lam * ( mix * |w|_1 + (1 - mix)/2 * |w|_2^2 ).

    mix = 0 is ridge, mix = 1 is lasso, between is elastic net: the three
    penalties the paper tuned. The lasso term |w| has a kink at zero, which
    is what lets it switch a feature off entirely and also what stops a
    gradient method using it directly. The standard way round: write
    w = u - v with u, v >= 0, so |w|_1 = sum(u + v) is smooth, and let the
    optimiser enforce the bounds. At the optimum one of each pair is zero.

    log(1 + e^z) - y z is the log loss in a form that cannot overflow; the
    intercept is not penalised, because it only carries the base rate.
    """
    u, v, b = _split(params)
    w = u - v
    z = X @ w + b
    loss = float(np.mean(np.logaddexp(0.0, z) - y * z)
                 + lam * (mix * (u.sum() + v.sum()) + 0.5 * (1 - mix) * (w @ w)))
    residual = (expit(z) - y) / len(y)
    g_w = X.T @ residual + lam * (1 - mix) * w
    grad = np.concatenate([g_w + lam * mix, -g_w + lam * mix, [residual.sum()]])
    return loss, grad


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float, mix: float = 0.0,
                 start: np.ndarray | None = None) -> np.ndarray:
    """
    Returns [u..., v..., b]; weights() turns that into w. Convex in w, so the
    answer does not depend on where the search starts, only how fast it gets
    there. A floor of 1e-8 on the lasso share keeps u and v from drifting up
    together under pure ridge, where nothing else would pin them.
    """
    k = X.shape[1]
    if start is None:
        start = np.zeros(2 * k + 1)
        rate = float(np.clip(y.mean(), 1e-3, 1 - 1e-3))
        start[-1] = math.log(rate / (1 - rate))
    bounds = [(0.0, None)] * (2 * k) + [(None, None)]
    result = optimize.minimize(_loss_and_grad, start, args=(X, y, lam, max(mix, 1e-8)),
                               jac=True, method="L-BFGS-B", bounds=bounds,
                               options={"maxiter": 1000})
    return result.x


def weights(params: np.ndarray) -> np.ndarray:
    u, v, _ = _split(params)
    return u - v


def predict_proba(X: np.ndarray, params: np.ndarray) -> np.ndarray:
    u, v, b = _split(params)
    return expit(X @ (u - v) + b)


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _standardise(train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = train.mean(axis=0)
    sd = train.std(axis=0)
    return mu, np.where(sd > 1e-12, sd, 1.0)


# ----------------------------------------------------------------------
# Scoring a classifier
# ----------------------------------------------------------------------
def auc(y: np.ndarray, score: np.ndarray) -> float:
    """
    Area under the ROC curve, by its probability meaning: pick one up-month
    and one down-month at random; AUC is the chance the score ranked the
    up-month higher. 0.5 is a coin flip whatever the class balance, which
    is why the paper prefers it to accuracy. Ties count half.
    """
    y = np.asarray(y, dtype=bool)
    score = np.asarray(score, dtype=float)
    n_up = int(y.sum())
    n_down = len(y) - n_up
    if n_up == 0 or n_down == 0:
        return float("nan")
    ranks = stats.rankdata(score)
    return float((ranks[y].sum() - n_up * (n_up + 1) / 2) / (n_up * n_down))


def auc_interval(y: np.ndarray, score: np.ndarray, horizon: int = HORIZON_DAYS,
                 z: float = 1.645) -> tuple[float, float]:
    """
    A 90% range for the AUC, sized by how unsure a coin flip would be.

    Under "no skill" the AUC (the Mann-Whitney U statistic over n_up * n_down)
    has mean 1/2 and variance (n + 1) / (12 n_up n_down). The n here counts
    independent months, one every `horizon` days: consecutive daily labels
    share 20 of their 21 days, so 2,500 test days are about 120 pieces of
    evidence, not 2,500.

    Chosen over a block bootstrap after measuring both on simulated random
    walks, where the truth is 0.5. The bootstrap's 90% range missed 0.5 a
    quarter of the time: it captures luck in the test days but not luck in
    what the model learned. This range missed it 12% of the time on 24
    walks, against the 10% it promises.

    Those same walks averaged an AUC of 0.48 rather than 0.50: a small
    downward lean, under half the width of the range. It errs toward
    understating skill on noise, never toward inventing it.
    """
    months = np.asarray(y, dtype=float)[::horizon]
    n_up = float(months.sum())
    n_down = len(months) - n_up
    if n_up < 1 or n_down < 1:
        return float("nan"), float("nan")
    se = math.sqrt((len(months) + 1) / (12 * n_up * n_down))
    centre = auc(y, score)
    return centre - z * se, centre + z * se


# ----------------------------------------------------------------------
# Walk-forward
# ----------------------------------------------------------------------
@dataclass
class WalkForward:
    dates: pd.DatetimeIndex        # every row with a full feature set
    prob: np.ndarray               # out-of-sample P(up); NaN before the first call
    label: np.ndarray              # 1/0, NaN where the month has not finished yet
    base_rate: np.ndarray          # share of up-months the model had seen when it made each call
    first_test: int                # index of the first out-of-sample call
    n_labelled: int                # rows with a known outcome
    feature_names: list[str]
    coefficients: np.ndarray       # last refit, in standard-deviation units
    penalty: str                   # ridge, lasso or elastic net
    strength: float
    n_refits: int
    horizon: int

    @property
    def scored(self) -> slice:
        """Out-of-sample rows whose outcome is known: the ones that can be graded."""
        return slice(self.first_test, self.n_labelled)


def tune(X: np.ndarray, y: np.ndarray, horizon: int) -> tuple[str, float, float]:
    """
    Picks the penalty the way the paper did: walk-forward over the data
    that exists before the first test call, with dev blocks of 10%, scored
    by AUC. Three successive dev blocks rather than one, because a single
    block of a few hundred overlapping days is about a dozen independent
    months, and choosing among eighteen settings on a dozen months mostly
    picks whichever got lucky. Ties go to the stronger penalty: if more
    shrinkage scores the same, the simpler model is the one to trust.

    Returns (penalty name, mix, strength). Nothing from the test period is
    ever seen.
    """
    n = len(y)
    folds = [(int(n * a), int(n * (a + 0.1))) for a in (0.7, 0.8, 0.9)]
    best, best_score = ("ridge", 0.0, STRENGTHS[-1]), -np.inf
    for name, mix in PENALTIES.items():
        for lam in sorted(STRENGTHS, reverse=True):
            scores = []
            for lo, hi in folds:
                train = slice(0, lo - horizon + 1)
                mu, sd = _standardise(X[train])
                params = fit_logistic((X[train] - mu) / sd, y[train], lam, mix)
                scores.append(auc(y[lo:hi], predict_proba((X[lo:hi] - mu) / sd, params)))
            score = float(np.nanmean(scores))
            if score > best_score + 1e-4:
                best, best_score = (name, mix, lam), score
    return best


def walk_forward(df: pd.DataFrame, horizon: int = HORIZON_DAYS,
                 refit_every: int = REFIT_EVERY,
                 min_train: int = MIN_TRAIN_DAYS, min_test: int = MIN_TEST_DAYS,
                 penalty: tuple[str, float, float] | None = None) -> WalkForward | None:
    """
    Train on the past, call the next block, slide forward, repeat.

    At a decision on day t the only labels that exist are those of days
    r <= t - horizon: a later day's month has not finished yet. Training
    uses exactly those rows, and the standardisation is refitted on them
    each time. Returns None when the history cannot support the test.
    """
    features = build_features(df)
    labels = make_labels(df["Close"], horizon)
    usable = features.dropna()
    if usable.empty:
        return None

    X = usable.to_numpy()
    y = labels.reindex(usable.index).to_numpy()
    n = len(y)
    n_labelled = int(np.isfinite(y).sum())
    if not np.isfinite(y[:n_labelled]).all():
        return None

    first_test = max(min_train + horizon, int(n_labelled * INITIAL_TRAIN_SHARE))
    if n_labelled - first_test < min_test:
        return None

    if penalty is None:
        penalty = tune(X[:first_test - horizon + 1], y[:first_test - horizon + 1], horizon)
    name, mix, lam = penalty

    prob = np.full(n, np.nan)
    base_rate = np.full(n, np.nan)
    params = None
    refits = 0
    for start in range(first_test, n, refit_every):
        stop = min(start + refit_every, n)
        train = slice(0, start - horizon + 1)
        mu, sd = _standardise(X[train])
        params = fit_logistic((X[train] - mu) / sd, y[train], lam, mix, start=params)
        prob[start:stop] = predict_proba((X[start:stop] - mu) / sd, params)
        base_rate[start:stop] = y[train].mean()
        refits += 1

    return WalkForward(dates=usable.index, prob=prob, label=y, base_rate=base_rate,
                       first_test=first_test, n_labelled=n_labelled,
                       feature_names=list(usable.columns), coefficients=weights(params),
                       penalty=name, strength=float(lam), n_refits=refits, horizon=horizon)


def grade(wf: WalkForward, signal: pd.Series | None = None) -> dict:
    """
    The scoreboard: the learned model, the trap a lazy model falls into,
    and optionally a hand-built score graded on exactly the same days.
    """
    s = wf.scored
    y, p = wf.label[s], wf.prob[s]
    majority_up = wf.base_rate[s] >= 0.5
    contenders = {
        "always": {
            "accuracy": float(np.mean(np.where(majority_up, y == 1, y == 0))),
            "auc": 0.5, "auc_ci": (0.5, 0.5),
            "says": "up" if majority_up.mean() >= 0.5 else "down",
        },
        "model": {
            "accuracy": float(np.mean((p > 0.5) == (y == 1))),
            "auc": auc(y, p), "auc_ci": auc_interval(y, p),
        },
    }
    if signal is not None:
        aligned = signal.reindex(wf.dates[s]).to_numpy(dtype=float)
        ok = np.isfinite(aligned)
        if ok.sum() >= 4 * wf.horizon:
            contenders["factor"] = {
                "accuracy": float(np.mean((aligned[ok] > 0) == (y[ok] == 1))),
                "auc": auc(y[ok], aligned[ok]),
                "auc_ci": auc_interval(y[ok], aligned[ok]),
                "days": int(ok.sum()),
            }

    n = s.stop - s.start
    return {
        "test_start": wf.dates[s.start], "test_end": wf.dates[s.stop - 1],
        "test_days": int(n), "independent_months": int(n // wf.horizon),
        "up_share": float(np.mean(y)), "contenders": contenders,
        "penalty": wf.penalty, "strength": wf.strength,
        "refits": wf.n_refits, "horizon": wf.horizon,
        "n_features": len(wf.feature_names),
        "n_used": int(np.sum(np.abs(wf.coefficients) > 1e-6)),
    }


def describe(grades: dict) -> tuple[str, str, str]:
    """(headline, tone, one honest paragraph) for the learned model."""
    m = grades["contenders"]["model"]
    lo, hi = m["auc_ci"]
    always = grades["contenders"]["always"]["accuracy"]
    says = grades["contenders"]["always"]["says"]
    trap = (f" Note the accuracy: {m['accuracy']:.0%} sounds like something until you see that "
            f"answering \"{says}\" every single time scored {always:.0%}.")
    if not np.isfinite(lo):
        return ("Not enough test data to grade", "neutral",
                "The out-of-sample period is too short to say anything.")
    if lo > 0.5:
        return ("A measurable edge, and a small one", "positive",
                f"Out of sample the model ranked up-months above down-months {m['auc']:.0%} of the "
                f"time, and the 90% range ({lo:.2f} to {hi:.2f}) stays above a coin flip. One stock "
                "over one period is still a single test, not a strategy." + trap)
    if hi < 0.5:
        return ("Worse than a coin flip", "negative",
                f"The model's ranking ran the wrong way out of sample (AUC {m['auc']:.2f}, 90% range "
                f"{lo:.2f} to {hi:.2f}). Whatever it learned in the past did not hold." + trap)
    return ("No better than a coin flip", "neutral",
            f"AUC {m['auc']:.2f}, with a 90% range of {lo:.2f} to {hi:.2f} that includes 0.5. "
            "That is the same result the Stanford project reached: price, volume and volatility "
            "alone did not tell up-months from down-months." + trap)


# ----------------------------------------------------------------------
# What acting on it would have done
# ----------------------------------------------------------------------
def strategy(wf: WalkForward, close: pd.Series, cost: float = DEFAULT_COST) -> dict:
    """
    Hold the stock on days the model is more optimistic than the up-rate it
    was trained on, hold cash otherwise; compare with simply holding.

    The bar is the training up-rate rather than 50% because a stock that
    rose in 62% of past months gets a probability above 50% almost every
    day, and "invest when p > 0.5" collapses into buy-and-hold. Every
    switch pays `cost`. Cash earns nothing, which flatters neither side.
    """
    dates = wf.dates[wf.first_test:]
    px = close.reindex(dates).to_numpy(dtype=float)
    position = (wf.prob[wf.first_test:] > wf.base_rate[wf.first_test:]).astype(float)

    daily = px[1:] / px[:-1] - 1
    held = position[:-1]
    switches = np.abs(np.diff(np.r_[0.0, held]))
    strat = held * daily - cost * switches
    hold = daily.copy()
    hold[0] -= cost

    def summary(r: np.ndarray) -> dict:
        equity = np.cumprod(1 + r)
        years = len(r) / 252
        sd = r.std()
        peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
        return {
            "total_return": float(equity[-1] - 1),
            "cagr": float(equity[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
            "vol": float(sd * math.sqrt(252)),
            "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan"),
            "max_drawdown": float((equity / peak - 1).min()),
        }

    # Being out of a falling stock half the time helps whatever the reason
    # for being out. This is what the same time invested on random days
    # earns on average, from the stock's own mean daily log return, so the
    # model is credited only with what it adds beyond its exposure.
    exposure = float(held.mean())
    random_cagr = float(math.exp(252 * exposure * np.mean(np.log1p(daily))) - 1)
    years = len(daily) / 252

    frame = pd.DataFrame({"strategy": strat, "hold": hold}, index=dates[1:])
    yearly = (1 + frame).groupby(frame.index.year).prod() - 1
    equity = (1 + frame).cumprod()
    return {
        "strategy": summary(strat), "hold": summary(hold),
        "random_cagr": random_cagr,
        # The standard error of an annualised Sharpe ratio is close to
        # 1/sqrt(years) for the modest ratios stocks produce.
        "sharpe_se": float(1 / math.sqrt(years)) if years > 0 else float("nan"),
        "years": years,
        "time_invested": exposure, "switches": int(switches.sum()), "cost": cost,
        "yearly": [{"year": int(yr), "strategy": float(row.strategy), "hold": float(row.hold)}
                   for yr, row in yearly.iterrows()],
        "equity": equity,
    }


def coefficient_grid(wf: WalkForward) -> dict[str, list[float | None]]:
    """The last refit's weights, laid out family by look-back for display."""
    by_name = dict(zip(wf.feature_names, wf.coefficients))
    return {family: [float(by_name[f"{family}_{w}w"]) if f"{family}_{w}w" in by_name else None
                     for w in WINDOWS_WEEKS]
            for family in FAMILIES}


# ----------------------------------------------------------------------
# The chart that fools people
# ----------------------------------------------------------------------
def persistence_check(close: pd.Series, days: int = 250) -> dict:
    """
    Grades the laziest possible forecast: tomorrow's price equals today's.

    On price levels it scores an R-squared near 1, because today's price is
    an excellent guess at tomorrow's. On the day-to-day move, which is the
    only part worth paying for, it explains nothing. A predicted-vs-actual
    price chart shows the first number and hides the second.
    """
    c = close.dropna().astype(float).tail(days + 1)
    actual, guess = c.to_numpy()[1:], c.to_numpy()[:-1]
    move = actual - guess

    def r2(truth, pred):
        return float(1 - np.sum((truth - pred) ** 2) / np.sum((truth - truth.mean()) ** 2))

    return {
        "days": int(len(actual)),
        "r2_level": r2(actual, guess),
        "r2_move": r2(move, np.zeros_like(move)),
        "mean_abs_error_pct": float(np.mean(np.abs(move) / actual) * 100),
        "dates": c.index[1:],
        "actual": actual,
        "guess": guess,
    }
