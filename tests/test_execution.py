"""
Portfolio Optimizer / Execution Simulator / Performance Analyzer 검증.
전부 순수 계산이라 네트워크 없이 결정적으로 테스트 가능하다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.portfolio import optimize_portfolio
from execution.simulator import simulate_day
from backtest.performance import compute_performance


def _trader_result():
    return {
        "kospi_prediction": {"direction": "up", "confidence": "높음"},
        "core_stocks": [
            {"name": "삼성전자", "sensitivity": "high", "directional_signal": "up"},
            {"name": "SK하이닉스", "sensitivity": "high", "directional_signal": "down"},
        ],
        "watch_stocks": [
            {"name": "LG에너지솔루션", "sensitivity": "mid", "directional_signal": "up"},
        ],
    }


def test_high_confidence_low_risk_gives_high_equity_weight():
    portfolio = optimize_portfolio(_trader_result(), risk_level="낮음")
    assert portfolio["equity_weight"] == 0.85
    assert portfolio["cash_weight"] == 0.15


def test_high_risk_reduces_equity_weight():
    low_risk = optimize_portfolio(_trader_result(), risk_level="낮음")
    high_risk = optimize_portfolio(_trader_result(), risk_level="높음")
    assert high_risk["equity_weight"] < low_risk["equity_weight"]


def test_only_aligned_direction_stocks_get_weight():
    portfolio = optimize_portfolio(_trader_result(), risk_level="보통")
    assert "삼성전자" in portfolio["stock_weights"]
    assert "LG에너지솔루션" in portfolio["stock_weights"]
    assert "SK하이닉스" not in portfolio["stock_weights"]  # direction이 반대(down)라 제외


def test_stock_weights_sum_to_equity_weight():
    portfolio = optimize_portfolio(_trader_result(), risk_level="보통")
    total = sum(portfolio["stock_weights"].values())
    assert abs(total - portfolio["equity_weight"]) < 1e-6


def test_simulate_day_scales_by_equity_weight():
    portfolio = {"equity_weight": 0.5}
    assert abs(simulate_day(portfolio, 2.0) - 0.01) < 1e-9
    assert abs(simulate_day(portfolio, -4.0) - (-0.02)) < 1e-9


def test_compute_performance_basic_metrics():
    returns = [0.01, -0.005, 0.02, -0.01, 0.015]
    benchmark = [0.008, -0.004, 0.018, -0.012, 0.01]
    perf = compute_performance(returns, benchmark, periods_per_year=252)
    assert perf["n_days"] == 5
    assert perf["win_rate"] == 3 / 5
    assert perf["mdd"] <= 0
    assert perf["sharpe"] is not None
    assert perf["beta"] is not None


def test_compute_performance_empty_returns_empty_dict():
    assert compute_performance([]) == {}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
