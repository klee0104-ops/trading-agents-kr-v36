import math, random
from rules.quant import (compute_indicators, rsi, macd, bollinger_pct_b, realized_vol_pct, check_claims,
                         contradiction_summary, quant_text, describe)
from execution.position import position_from_probability, strategy_return_pct, performance, direction_position


def _trend(n=300, start=2500.0, drift=0.0008, vol=0.01, seed=1):
    random.seed(seed)
    c = [start]
    for _ in range(n - 1):
        c.append(c[-1] * (1 + drift + random.gauss(0, vol)))
    return c


def test_rsi_bounds_and_extremes():
    up = [100 + i for i in range(40)]
    down = [200 - i for i in range(40)]
    assert rsi(up) == 100.0
    assert rsi(down) == 0.0
    assert rsi([1, 2, 3]) is None


def test_macd_and_bollinger():
    c = _trend()
    m = macd(c)
    assert set(m) >= {"macd", "signal", "hist", "cross"}
    b = bollinger_pct_b(c)
    assert -1.0 < b < 2.0
    assert realized_vol_pct(c) > 0


def test_compute_indicators_full_and_short():
    ind = compute_indicators(_trend(), flow_history=[100, -50, 200, 300, 250, 400, 500, 800, 900, 1000])
    assert ind["rsi14"] is not None and ind["ma"]["state"] in ("정배열", "역배열", "혼조")
    assert ind["mom_12_1"] is not None and ind["flow_z5"] is not None
    assert "rsi" in ind["labels"] and "flow" in ind["labels"]
    txt = quant_text(ind)
    assert "RSI(14" in txt and "Jegadeesh" in txt
    short = compute_indicators(_trend(n=30))
    assert short["mom_12_1"] is None and short["ma"] is None and quant_text(short)


def test_check_claims_contradiction():
    ind = {"rsi14": 45.0, "macd": {"hist": -1.0, "hist_prev": -0.5, "cross": None},
           "ma": {"state": "역배열", "ma20": 1, "ma60": 2, "ma120": 3, "above_ma20": False, "above_ma60": False},
           "realized_vol_20": 30.0, "flow_z5": -1.5}
    ind["labels"] = describe(ind)
    checks = check_claims([
        "RSI 기준 과매수 구간에 진입해 조정 압력",       # RSI 45 → 모순
        "20일선이 60일선을 뚫는 골든크로스 발생",         # 하락 모멘텀 → 모순
        "변동성이 높아 리스크 관리 필요",                 # 높음 → 지지
        "외국인 순매도 지속으로 수급 부담",               # 강한 순매도 → 지지
        "미국 SOX 지수 +1.4% 상승",                       # 지표 주장 없음 → 미포함
    ], ind)
    verdicts = [c["verdict"] for c in checks]
    assert verdicts == ["contradicted", "contradicted", "supported", "supported"]
    s = contradiction_summary(checks)
    assert s["checked"] == 4 and s["contradicted"] == 2


def test_position_from_probability():
    cash = position_from_probability(53, 15)
    assert cash["position"] == 0.0 and cash["instrument"] == "cash"
    long_ = position_from_probability(70, 15)
    assert long_["position"] == 0.4 and long_["instrument"].startswith("069500")   # edge .4 × 1.0
    short = position_from_probability(30, 30)           # 변동성 2배 → 스케일 0.5
    assert short["position"] == -0.2 and short["instrument"].startswith("114800")
    assert position_from_probability(99, 5)["position"] == 1.0                     # 상한
    assert position_from_probability(None)["position"] == 0.0
    assert direction_position("down", 15) == -1.0 and direction_position("flat") == 0.0


def test_strategy_return_and_performance():
    r = strategy_return_pct(0.5, 2.0)
    assert r["return_pct"] == 0.95 and r["proxy"] is True          # 0.5×2.0 − 0.05
    r2 = strategy_return_pct(-0.5, 2.0, short_etf_ret_pct=-1.9)
    assert r2["return_pct"] == -1.0 and r2["leg"] == "short" and r2["proxy"] is False
    assert strategy_return_pct(0.0, 1.0)["return_pct"] == 0.0
    perf = performance([1.0, -0.5, 0.0, 2.0], positions=[0.5, 0.5, 0, 1.0])
    assert perf["days"] == 4 and perf["traded_days"] == 3 and perf["win_rate"] == round(2 / 3, 3)
    assert math.isclose(perf["total_return_pct"], ((1.01 * 0.995 * 1.02) - 1) * 100, abs_tol=1e-3)
    assert perf["max_drawdown_pct"] < 0 and perf["avg_exposure"] == 0.5
    assert len(perf["equity_curve_pct"]) == 4
