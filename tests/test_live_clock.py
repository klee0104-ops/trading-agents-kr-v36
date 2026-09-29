"""(V3.26) 실시간 국내 지수 · 장 상태 · 시계 · 스냅샷 경과 표시."""
from __future__ import annotations
from datetime import datetime

import pytest

from data.sessions import KST, market_status


@pytest.mark.parametrize("dt,code", [
    (datetime(2026, 10, 3, 10, 0, tzinfo=KST), "weekend"),   # 토
    (datetime(2026, 9, 29, 8, 59, tzinfo=KST), "pre"),
    (datetime(2026, 9, 29, 9, 0, tzinfo=KST), "open"),
    (datetime(2026, 9, 29, 15, 29, tzinfo=KST), "open"),
    (datetime(2026, 9, 29, 15, 35, tzinfo=KST), "closing"),
    (datetime(2026, 9, 29, 15, 40, tzinfo=KST), "closed"),
])
def test_market_status(dt, code):
    assert market_status(dt)["code"] == code


def test_parse_daily_keeps_trade_time():
    from data.kr_daily import parse_daily
    rows = parse_daily([{"localTradedAt": "2026-09-29T10:12:34+09:00", "closePrice": "2,583.27",
                         "compareToPreviousClosePrice": "-12.34", "openPrice": "2,590", "highPrice": "2,600",
                         "lowPrice": "2,570"}])
    assert rows[0]["traded_at"] == "2026-09-29T10:12:34" and rows[0]["close"] == 2583.27
    assert rows[0]["prev_close"] == 2595.61


@pytest.fixture
def web(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    from web import server
    from config import Config
    monkeypatch.setattr(Config, "MEMORY_DB_PATH", str(tmp_path / "mem.json"))
    monkeypatch.chdir(tmp_path)
    server.app.config["TESTING"] = True
    server._MARKET_CACHE.pop("live", None)
    return server, server.app.test_client(), f"127.0.0.1:{Config.WEB_PORT}"


def test_api_live_returns_kospi_with_time_and_status(web, monkeypatch):
    server, client, host = web
    import data.kr_daily as kd
    import data.sessions as ss
    monkeypatch.setattr(ss, "kst_now", lambda: datetime(2026, 9, 29, 10, 15, tzinfo=KST))
    calls = []

    def fake(code, kind="index", pages=1, include_intraday=False):
        calls.append((code, include_intraday))
        return [{"date": "2026-09-29", "close": 2583.27, "prev_close": 2595.61, "traded_at": "2026-09-29T10:12:34"}]
    monkeypatch.setattr(kd, "fetch_daily", fake)
    r = client.get("/api/live", headers={"Host": host})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    j = r.get_json()
    assert j["status"]["code"] == "open" and j["server_time"] == "2026-09-29T10:15:00"
    k = j["rows"][0]
    assert k["name"] == "KOSPI" and k["is_today"] and k["traded_at"] == "2026-09-29T10:12:34" and k["change_pct"] == -0.48
    assert all(inc for _c, inc in calls)
    # 캐시: 20초 안에는 다시 조회하지 않는다 (네이버 호출 최소화)
    n = len(calls)
    client.get("/api/live", headers={"Host": host})
    assert len(calls) == n


def test_api_live_failure_is_safe_error(web, monkeypatch):
    server, client, host = web
    import data.kr_daily as kd

    def boom(*a, **k):
        raise RuntimeError("secret-ish detail http://x?key=abc")
    monkeypatch.setattr(kd, "fetch_daily", boom)
    r = client.get("/api/live", headers={"Host": host})
    assert r.status_code == 502 and "key=abc" not in r.get_data(as_text=True)


def test_api_live_allowed_in_readonly_and_rejects_foreign_host(web, monkeypatch):
    server, client, host = web
    from config import Config
    import data.kr_daily as kd
    monkeypatch.setattr(Config, "WEB_READONLY", True)
    monkeypatch.setattr(kd, "fetch_daily", lambda *a, **k: [{"date": "2026-09-29", "close": 1.0, "prev_close": 1.0}])
    assert client.get("/api/live", headers={"Host": host}).status_code == 200
    assert client.get("/api/live", headers={"Host": "evil.example"}).status_code == 403


def test_dashboard_has_clock_and_live_polling():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/static/index.html").read_text(encoding="utf-8")
    js = (root / "web/static/app.js").read_text(encoding="utf-8")
    assert 'id="clock"' in html
    assert "/api/live" in js and "Asia/Seoul" in js and "setInterval(tickClock, 1000)" in js
    assert "30000" in js and "300000" in js  # 장중 30초, 그 외 5분 — 더 잦게 바꾸지 말 것(비공식 API 부하)


def test_share_page_has_clock_and_stale_warning():
    from web.export_site import render_fragment
    html = render_fragment({"generated_at": "2026-09-28T09:00", "analysis_at": None, "prediction": {},
                            "stats": {}, "records": []})
    assert 'id="clock"' in html and 'id="stale"' in html and 'id="genAgo"' in html
    assert "Asia/Seoul" in html and "30*3600*1000" in html
