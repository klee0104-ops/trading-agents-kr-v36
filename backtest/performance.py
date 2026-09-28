"""
Performance Analyzer. V3.5 Execution Layer 컴포넌트: 백테스트 일별 수익률로부터
CAGR/MDD/Sharpe/Sortino/Alpha/Beta/Win Rate를 순수 계산한다 (LLM 미사용).
"""
from __future__ import annotations
import math
from typing import Optional


def compute_performance(returns: list[float], benchmark_returns: Optional[list[float]] = None,
                         periods_per_year: int = 252) -> dict:
    n = len(returns)
    if n == 0:
        return {}

    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    total_return = equity[-1] - 1

    years = n / periods_per_year
    cagr = (equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else None

    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (v - peak) / peak
        max_dd = min(max_dd, dd)

    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / n if n > 1 else 0.0
    std = math.sqrt(variance)
    sharpe = (mean_r / std * math.sqrt(periods_per_year)) if std > 0 else None

    downside = [r for r in returns if r < 0]
    if downside:
        down_var = sum(r ** 2 for r in downside) / n
        down_std = math.sqrt(down_var)
        sortino = (mean_r / down_std * math.sqrt(periods_per_year)) if down_std > 0 else None
    else:
        sortino = None

    win_rate = sum(1 for r in returns if r > 0) / n

    alpha = beta = None
    if benchmark_returns and len(benchmark_returns) == n:
        mean_b = sum(benchmark_returns) / n
        cov = sum((returns[i] - mean_r) * (benchmark_returns[i] - mean_b) for i in range(n)) / n
        var_b = sum((b - mean_b) ** 2 for b in benchmark_returns) / n
        if var_b > 0:
            beta = cov / var_b
            alpha = (mean_r - beta * mean_b) * periods_per_year

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4) if cagr is not None else None,
        "mdd": round(max_dd, 4),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "win_rate": round(win_rate, 4),
        "alpha": round(alpha, 4) if alpha is not None else None,
        "beta": round(beta, 4) if beta is not None else None,
        "n_days": n,
    }
