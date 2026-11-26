"""Offline checks for the separate U.S. market paper path."""
from datetime import datetime, timedelta, timezone

from us_market.feed import AlpacaIEXFeed
from us_market.ledger import fresh, public_summary, tick
from us_market.research import analyze
from web.export_site import render_fragment


NOW = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)  # 10:00 New York, DST


def _quote(symbol, now=NOW):
    return {"symbol": symbol, "bid": 99.9, "ask": 100.1,
            "at": now.isoformat(), "source": "Alpaca IEX", "feed": "iex"}


def _plan(now=NOW, symbol="SPY"):
    return {"schema": 1, "market": "US", "approved": True,
            "created_at": now.isoformat(), "trade_date": "2026-10-02",
            "short_symbol": symbol, "up_probability": 0.62, "long_symbols": []}


def test_us_ledger_enters_once_then_exits_with_realized_pnl():
    initial = fresh(NOW)
    quotes = {"SPY": _quote("SPY")}
    entered = tick(initial, _plan(), quotes, {"is_open": True}, NOW)
    assert initial["trades"] == []
    assert len(entered["trades"]) == 1
    assert entered["trades"][0]["side"] == "buy"
    second = tick(entered, _plan(), {"SPY": _quote("SPY", NOW + timedelta(minutes=1))},
                  {"is_open": True}, NOW + timedelta(minutes=1))
    assert len(second["trades"]) == 1
    exit_at = NOW + timedelta(hours=5, minutes=45)
    exited = tick(second, _plan(), {"SPY": _quote("SPY", exit_at)},
                  {"is_open": True}, exit_at)
    assert len(exited["trades"]) == 2
    assert exited["trades"][-1]["side"] == "sell"
    assert exited["trades"][-1]["realized_pnl"] < 0  # spread plus slippage
    assert not exited["sleeves"]["short"]["holdings"]


def test_us_ledger_rejects_stale_quote_and_foreign_plan():
    state = fresh(NOW)
    bad = _plan()
    bad["long_symbols"] = ["../../../secret"]
    result = tick(state, bad, {}, {"is_open": True}, NOW)
    assert result["trades"] == []
    stale = tick(state, _plan(), {"SPY": _quote("SPY", NOW - timedelta(minutes=3))},
                 {"is_open": True}, NOW)
    assert stale["valuation"]["status"] == "stale_quote"
    assert stale["trades"] == []


def test_long_sleeve_can_enter_when_short_loss_limit_is_blocked():
    state = fresh(NOW)
    state["day"] = "2026-10-02"
    state["short_blocked"] = True
    plan = _plan()
    plan["long_symbols"] = ["AAPL"]
    updated = tick(state, plan, {"SPY": _quote("SPY"), "AAPL": _quote("AAPL")},
                   {"is_open": True}, NOW)
    assert "AAPL" in updated["sleeves"]["long"]["holdings"]
    assert "SPY" not in updated["sleeves"]["short"]["holdings"]


def test_feed_only_accepts_fresh_iex_quotes_and_fixed_url():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"quotes": {"SPY": {"bp": 99.9, "ap": 100.1, "t": NOW.isoformat()},
                               "SH": {"bp": 20, "ap": 20.1,
                                      "t": (NOW - timedelta(minutes=3)).isoformat()}}}

    class Session:
        def get(self, url, **kwargs):
            assert url == "https://data.alpaca.markets/v2/stocks/quotes/latest"
            assert kwargs["params"]["feed"] == "iex"
            assert kwargs["headers"]["APCA-API-SECRET-KEY"] == "example-secret"
            return Response()

    feed = AlpacaIEXFeed("example-id", "example-secret", Session())
    assert list(feed.quotes(["SPY", "SH"], NOW)) == ["SPY"]


def test_public_page_requires_opt_in_and_never_emits_raw_quote(monkeypatch):
    summary = public_summary(fresh(NOW))
    summary["private_token"] = "private-sentinel"
    summary["quote"] = {"bid": 123.45}
    monkeypatch.delenv("US_PUBLIC_SUMMARY", raising=False)
    html = render_fragment({"us_paper": summary})
    assert "private-sentinel" not in html
    assert '"us_paper": null' in html
    monkeypatch.setenv("US_PUBLIC_SUMMARY", "true")
    html = render_fragment({"us_paper": summary})
    assert "private-sentinel" not in html
    assert '"quote":' not in html
    assert '"currency": "USD"' in html


def test_us_research_requires_cited_company_filing():
    bars = {symbol: [{"date": "2026-10-01", "close": 100.0} for _ in range(60)]
            for symbol in ("SPY", "SH", "AAPL")}
    filings = {"AAPL": {"metrics": {"eps_diluted": {
        "value": 5.0, "unit": "USD/shares", "form": "10-K",
        "period_end": "2026-09-30", "filed": "2026-10-01",
        "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"}}}}

    class Model:
        n = 0

        def complete_json(self, *args, **kwargs):
            self.n += 1
            return {"direction": "up", "up_probability": 0.65,
                    "long_candidates": ["AAPL"],
                    "risk_approved": True,
                    "points": ["AAPL 희석 EPS 5.0 USD/shares [M4]" if self.n == 3 else
                               "SPY 종가 100.00 USD [M1]"]}

    plan = analyze(Model(), bars, filings, NOW)
    assert plan["approved"] is True
    assert plan["short_symbol"] == "SPY"
    assert plan["long_symbols"] == ["AAPL"]

    class Uncited(Model):
        def complete_json(self, *args, **kwargs):
            out = super().complete_json(*args, **kwargs)
            if self.n == 4:
                out["points"] = ["근거가 없습니다"]
            return out

    rejected = analyze(Uncited(), bars, filings, NOW)
    assert rejected["approved"] is False
    assert rejected["short_symbol"] is None
    assert rejected["long_symbols"] == []

    class Veto(Model):
        def complete_json(self, *args, **kwargs):
            out = super().complete_json(*args, **kwargs)
            if self.n == 4:
                out["risk_approved"] = False
            return out

    assert analyze(Veto(), bars, filings, NOW)["approved"] is False
