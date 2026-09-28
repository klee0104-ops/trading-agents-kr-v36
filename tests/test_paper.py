from execution.paper import PaperAccount, FEE_RATE, SLIPPAGE


def _etf(lo, lc, so, sc):
    return {"long": {"ticker": "069500.KS", "open": lo, "close": lc, "prev_close": lo},
            "short": {"ticker": "114800.KS", "open": so, "close": sc, "prev_close": so}}


def test_paper_long_then_cash_then_short():
    a = PaperAccount(None, 10_000_000)
    d1 = a.settle_day("2026-09-29", 0.5, _etf(10000, 10200, 5000, 4900))   # 롱 50%
    assert d1["trades"] == 1 and d1["holdings"] == {"069500.KS": 499} or d1["holdings"].get("069500.KS", 0) > 0
    assert d1["equity"] > 10_000_000 and d1["day_return_pct"] > 0
    d2 = a.settle_day("2026-09-30", 0.0, _etf(10200, 10100, 4900, 4950))   # 전량 현금
    assert d2["holdings"] == {} and d2["cash"] == d2["equity"]
    d3 = a.settle_day("2026-10-01", -0.3, _etf(10100, 9900, 4950, 5050))   # 쇼트 30% = 인버스 매수
    assert "114800.KS" in d3["holdings"] and d3["day_return_pct"] > 0
    assert a.settle_day("2026-10-01", -0.3, _etf(1, 1, 1, 1)) is None       # 중복 방지
    s = a.summary()
    assert s["days"] == 3 and s["trades"] == 3 and s["fees"] > 0 and len(s["equity_curve"]) == 3


def test_paper_no_etf_data():
    a = PaperAccount(None)
    assert a.settle_day("2026-09-29", 1.0, {"long": None, "short": None}) is None
