"""
rules/engine.py (Rule Engine + Market Regime Detection) 검증. 네트워크 불필요,
전부 코드 기반 임계값 로직이므로 결정적으로 테스트 가능하다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from data.market_data import MarketSnapshot, IndexQuote
from rules.engine import analyze_market, build_context_text


def test_calm_market_has_low_risk_and_no_flags():
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", 0.3, "up", str(date.today()), value=5000),
        IndexQuote("SOX", 0.5, "up", str(date.today()), value=4000),
        IndexQuote("VIX", 1.0, "up", str(date.today()), value=14.0),
    ])
    analysis = analyze_market(snapshot)
    assert analysis.risk_level_hint == "낮음"
    assert analysis.risk_flags == []
    assert analysis.regime_volatility == "낮음"


def test_vix_high_level_triggers_high_risk():
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", -0.5, "down", str(date.today()), value=4800),
        IndexQuote("VIX", 5.0, "up", str(date.today()), value=32.0),
    ])
    analysis = analyze_market(snapshot)
    assert "VIX_HIGH_LEVEL" in analysis.risk_flags
    assert analysis.risk_level_hint == "높음"
    assert analysis.regime_volatility == "높음"


def test_us_selloff_triggers_high_risk_and_bearish_trend():
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", -2.5, "down", str(date.today()), value=4700),
        IndexQuote("SOX", -3.0, "down", str(date.today()), value=3800),
    ])
    analysis = analyze_market(snapshot)
    assert "US_SELLOFF" in analysis.risk_flags
    assert analysis.regime_trend == "하락"
    assert analysis.risk_level_hint == "높음"


def test_broad_based_decline_flag():
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", -0.9, "down", str(date.today())),
        IndexQuote("나스닥", -0.8, "down", str(date.today())),
        IndexQuote("다우존스", -0.6, "down", str(date.today())),
        IndexQuote("SOX", 0.2, "up", str(date.today())),
    ])
    analysis = analyze_market(snapshot)
    assert "BROAD_BASED_DECLINE" in analysis.risk_flags


def test_tiny_dips_are_not_broad_decline():
    """(V3.6.2) -0.02% 같은 보합 수준 하락은 '광범위한 약세'가 아니다 — 하락 편향 원인이었음."""
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", -0.02, "down", str(date.today())),
        IndexQuote("나스닥", -0.08, "down", str(date.today())),
        IndexQuote("다우존스", -0.21, "down", str(date.today())),
        IndexQuote("SOX", 0.2, "up", str(date.today())),
        IndexQuote("VIX", -1.55, "down", str(date.today()), value=15.0),
    ])
    analysis = analyze_market(snapshot)
    assert "BROAD_BASED_DECLINE" not in analysis.risk_flags
    assert analysis.risk_level_hint == "낮음"


def test_sox_direction_hint_is_separate_from_risk():
    # SOX 급등 + 다른 지수 소폭 하락: 방향 힌트 up, 리스크는 낮음
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", -0.38, "down", str(date.today())),
        IndexQuote("나스닥", -0.3, "down", str(date.today())),
        IndexQuote("다우존스", -0.4, "down", str(date.today())),
        IndexQuote("SOX", 3.37, "up", str(date.today())),
        IndexQuote("VIX", 1.47, "up", str(date.today()), value=16.0),
    ])
    a = analyze_market(snapshot)
    assert a.direction_hint == "up" and "SOX_STRONG_UP" in a.risk_flags
    assert a.risk_level_hint == "낮음"
    assert "방향 힌트" in build_context_text(a)
    # SOX 급락은 방향 down + 리스크 높음
    snapshot2 = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("SOX", -5.86, "down", str(date.today()))])
    a2 = analyze_market(snapshot2)
    assert a2.direction_hint == "down" and a2.risk_level_hint == "높음"


def test_build_context_text_includes_risk_hint_and_notes():
    snapshot = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("VIX", 12.0, "up", str(date.today()), value=32.0),
    ])
    analysis = analyze_market(snapshot)
    text = build_context_text(analysis)
    assert "규칙엔진 리스크 힌트" in text
    assert "VIX 절대 수준 32.0" in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
