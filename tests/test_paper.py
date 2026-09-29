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


def test_unstarted_account_summary_has_displayable_balance():
    s = PaperAccount(None, 2500000).summary()
    assert s['initial_cash'] == s['cash'] == s['equity'] == 2500000
    assert s['days'] == s['trades'] == s['pnl'] == s['total_return_pct'] == s['fees'] == 0
    assert s['holdings'] == {} and s['recent_trades'] == []
    assert s['sharpe_annual'] is None and s['max_drawdown_pct'] is None


def test_paper_entry_prev_close_vs_open():
    etf = {"long": {"ticker": "069500.KS", "open": 10300, "close": 10400, "prev_close": 10000},
           "short": {"ticker": "114800.KS", "open": 4850, "close": 4800, "prev_close": 5000}}
    a = PaperAccount(None, 10_000_000); b = PaperAccount(None, 10_000_000)
    da = a.settle_day("2026-09-29", 1.0, etf, entry="prev_close")
    db = b.settle_day("2026-09-29", 1.0, etf, entry="open")
    assert da["entry"].startswith("장전") and db["entry"] == "시가"
    assert da["day_return_pct"] > db["day_return_pct"] > 0   # 갭(+3%)까지 먹는 쪽이 더 큼


def test_kr_daily_parse_and_merge():
    from data.kr_daily import parse_daily, merge_series
    rows = parse_daily([{"localTradedAt": "2026-09-29", "closePrice": "6,872.70", "compareToPreviousClosePrice": "-17.04",
                         "openPrice": "6,844.41", "highPrice": "6,877.07", "lowPrice": "6,834.82"},
                        {"localTradedAt": "2026-09-28", "closePrice": "6,889.74", "compareToPreviousClosePrice": "-191.18",
                         "openPrice": "7,000", "highPrice": "7,010", "lowPrice": "6,880"}])
    assert rows[0]["date"] == "2026-09-28" and rows[0]["prev_close"] == 7080.92
    from datetime import date
    d, c, h, l, added = merge_series(["2026-09-22", "2026-09-23"], [7017.91, 7080.92], rows, as_of=date(2026, 9, 28),
                                     highs=[1, 2], lows=[1, 2])
    assert added == ["2026-09-28"] and c[-1] == 6889.74 and len(h) == 3
    # 중간 누락 + 오늘 장중 행 제거
    d2, c2, _, _, added2 = merge_series(["2026-09-23", "2026-09-29"], [7080.92, 6865.0], rows, drop_after="2026-09-28")
    assert d2 == ["2026-09-23", "2026-09-28"] and c2[-1] == 6889.74 and added2 == ["2026-09-28"]
