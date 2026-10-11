"""Statistics for many-trial research: Sharpe, deflated Sharpe ratio, probability of backtest overfitting.

References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014) and "The Probability of
Backtest Overfitting" (2015, combinatorially symmetric cross-validation). Undefined inputs return None.
"""
from __future__ import annotations

import itertools
import math
import statistics
from statistics import NormalDist

EULER = 0.5772156649
N = NormalDist()


def sharpe(rets: list[float], annualize: bool = True) -> float | None:
    """Sample Sharpe of per-session returns (risk-free 0); annualized with 252 sessions."""
    if len(rets) < 3:
        return None
    sd = statistics.stdev(rets)
    if sd < 1e-12:
        return None
    sr = statistics.mean(rets) / sd
    return sr * math.sqrt(252) if annualize else sr


def _moments(rets: list[float]) -> tuple[float, float]:
    m = statistics.mean(rets)
    sd = statistics.pstdev(rets)
    if sd < 1e-12:
        return 0.0, 3.0
    skew = sum((r - m) ** 3 for r in rets) / len(rets) / sd ** 3
    kurt = sum((r - m) ** 4 for r in rets) / len(rets) / sd ** 4
    return skew, kurt


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected maximum per-session Sharpe among n_trials unskilled strategies (the DSR benchmark SR0)."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER) * N.inv_cdf(1 - 1 / n_trials) + EULER * N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(rets: list[float], n_trials: int, trial_srs: list[float]) -> dict:
    """Probability that the true Sharpe exceeds what the best of n_trials lucky strategies would show.

    rets: the selected strategy's per-session returns; trial_srs: per-session Sharpes of all trials.
    """
    sr = sharpe(rets, annualize=False)
    if sr is None or len(rets) < 10:
        return {"dsr": None, "sr": sr, "sr0": None, "n_trials": n_trials, "sessions": len(rets)}
    var_sr = statistics.variance(trial_srs) if len(trial_srs) > 1 else 0.0
    sr0 = expected_max_sharpe(n_trials, var_sr)
    skew, kurt = _moments(rets)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return {"dsr": None, "sr": sr, "sr0": sr0, "n_trials": n_trials, "sessions": len(rets)}
    z = (sr - sr0) * math.sqrt(len(rets) - 1) / math.sqrt(denom)
    return {"dsr": N.cdf(z), "sr": sr, "sr0": sr0, "n_trials": n_trials, "sessions": len(rets)}


def pbo(matrix: list[list[float]], groups: int = 8) -> dict:
    """CSCV probability of backtest overfitting.

    matrix[t][k]: return of trial k on session t (aligned sessions). Sessions are cut into `groups`
    contiguous blocks; for every half/half combination the in-sample best trial's out-of-sample rank
    is recorded. PBO = share of combinations where that trial lands in the bottom half out of sample.
    """
    t, k = len(matrix), len(matrix[0]) if matrix else 0
    groups -= groups % 2
    if k < 2 or groups < 2 or t < groups * 3:
        return {"pbo": None, "trials": k, "sessions": t, "combinations": 0}
    size = t // groups
    blocks = [list(range(g * size, (g + 1) * size if g < groups - 1 else t)) for g in range(groups)]

    def perf(rows, j):
        s = sharpe([matrix[r][j] for r in rows], annualize=False)
        return s if s is not None else 0.0

    logits = []
    for ins in itertools.combinations(range(groups), groups // 2):
        is_rows = [r for g in ins for r in blocks[g]]
        oos_rows = [r for g in range(groups) if g not in ins for r in blocks[g]]
        is_perf = [perf(is_rows, j) for j in range(k)]
        best = max(range(k), key=lambda j: is_perf[j])
        oos = [perf(oos_rows, j) for j in range(k)]
        rank = sum(v < oos[best] for v in oos) + 0.5 * (sum(v == oos[best] for v in oos) - 1) + 1  # 1..k
        w = rank / (k + 1)
        logits.append(math.log(w / (1 - w)))
    return {"pbo": sum(x <= 0 for x in logits) / len(logits), "trials": k, "sessions": t, "combinations": len(logits)}


def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None
