"""
data/features.py (RSI/MACD/Bollinger Band) 순수 계산 검증. 네트워크 불필요.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from data.features import compute_indicators


def test_insufficient_data_returns_none():
    close = pd.Series([100.0, 101.0, 99.0])
    result = compute_indicators(close)
    assert result["rsi14"] is None
    assert result["macd_hist"] is None
    assert result["bb_percent_b"] is None


def test_strictly_rising_series_has_high_rsi():
    # 계속 오르기만 하면(하락 없음) RSI는 100에 가깝거나 100이어야 한다
    close = pd.Series([100.0 + i for i in range(40)])
    result = compute_indicators(close)
    assert result["rsi14"] is not None
    assert result["rsi14"] >= 90


def test_bollinger_percent_b_within_reasonable_range():
    close = pd.Series([100.0 + (i % 5) for i in range(40)])
    result = compute_indicators(close)
    assert result["bb_percent_b"] is not None
    # 변동폭이 좁은 시계열이므로 %B가 극단값으로 튀지 않아야 한다
    assert -1.0 <= result["bb_percent_b"] <= 2.0


def test_macd_hist_computed_when_enough_data():
    close = pd.Series([100.0 + i * 0.5 for i in range(40)])
    result = compute_indicators(close)
    assert result["macd_hist"] is not None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
