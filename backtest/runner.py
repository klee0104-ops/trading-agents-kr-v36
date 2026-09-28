"""
백테스팅 하네스.

과거 날짜 구간에 대해 파이프라인을 반복 실행하고, 각 날짜의 예측 방향을
그 다음 실제 거래일의 KOSPI 방향과 비교해 적중률을 계산한다.

⚠️ 초반 단계 한계: LLM 기반 에이전트(분석가/Bull/Bear/리스크/트레이더)는 학습 시점
지식에 의존하므로, 아주 과거 시점을 완벽히 재현하지는 못한다. 그래도 "그날의 실제
지표 수치"는 look-ahead 없이 정확히 주입하므로, 최소한 데이터 기반 판단 로직 자체는
검증할 수 있다. 완전히 신뢰할 수 있는 백테스트를 원한다면 이후 단계에서
"과거 뉴스 스냅샷 데이터셋"을 별도로 구축하는 걸 권장한다 (Phase 2+).
"""
from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass

from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketDataProvider
from execution.portfolio import optimize_portfolio
from execution.simulator import simulate_day
from backtest.performance import compute_performance


@dataclass
class BacktestResult:
    date: str
    predicted_direction: str
    actual_direction: str
    correct: bool
    daily_return: float = 0.0
    benchmark_return: float = 0.0
    baselines: dict | None = None  # {key: 예측방향}


def run_backtest(pipeline: TradingAgentsPipeline, start: date, end: date) -> list[BacktestResult]:
    provider = MarketDataProvider(backtest_mode=True)
    results: list[BacktestResult] = []

    d = start
    while d <= end:
        if d.weekday() >= 5:  # 주말 skip
            d += timedelta(days=1)
            continue

        try:
            snapshot = provider.fetch_us_snapshot(as_of_date=d)
            outcome = pipeline.run(snapshot=snapshot)
            final_result = outcome["final_result"]
            predicted = final_result["kospi_prediction"]["direction"]
            risk_level = outcome.get("verification", {}).get("final_risk_level") \
                or outcome["risk_assessment"].get("risk_level", "보통")

            # (V3.6) 공휴일을 건너뛰도록 "d 이후 실제 첫 거래일"로 채점
            k = provider.fetch_kospi_session_after(d)
            actual_change = k["change_pct"]
            actual = "up" if actual_change >= 0 else "down"

            # Portfolio Optimizer + Execution Simulator: 예측/리스크 판단을 실제
            # 비중 배분과 다음날 KOSPI 실등락에 대입해 일별 수익률을 근사한다.
            portfolio = optimize_portfolio(final_result, risk_level)
            daily_return = simulate_day(portfolio, actual_change)
            benchmark_return = actual_change / 100.0  # KOSPI 매수 후 보유 대비 벤치마크

            results.append(BacktestResult(
                date=str(d),
                predicted_direction=predicted,
                actual_direction=actual,
                correct=(predicted == actual),
                daily_return=daily_return,
                benchmark_return=benchmark_return,
                baselines={k: v["direction"] for k, v in (outcome.get("baselines") or {}).items()},
            ))
        except Exception as e:
            print(f"[백테스트 스킵] {d}: {e}")

        d += timedelta(days=1)

    return results


def summarize(results: list[BacktestResult]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r.correct)
    returns = [r.daily_return for r in results]
    benchmark = [r.benchmark_return for r in results]
    # 같은 날짜 표본으로 베이스라인(단순 규칙)과 비교 — AI가 이보다 못하면 의미가 없다
    from rules.baselines import BASELINES
    base = {}
    for key, label in BASELINES.items():
        rows = [r for r in results if r.baselines and key in r.baselines]
        c = sum(1 for r in rows if r.baselines[key] == r.actual_direction)
        base[key] = {"label": label, "total": len(rows), "correct": c,
                     "accuracy": (c / len(rows)) if rows else None}
    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "performance": compute_performance(returns, benchmark) if returns else {},
        "baseline_compare": base,
    }
