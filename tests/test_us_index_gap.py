"""미국장 거래일 누락을 2일 등락률로 잘못 표시하지 않는 회귀."""
from datetime import date
import json

import pandas as pd
import pytest

from data import market_data


def _provider(monkeypatch, tmp_path, with_prior):
    path = tmp_path / "us_closes.json"
    monkeypatch.setenv("US_INDEX_CLOSE_CACHE_PATH", str(path))
    if with_prior:
        path.write_text(json.dumps({"schema": 1, "sessions": {"2026-10-21": {
            name: 100.0 for name in market_data.US_TICKERS}}}), encoding="utf-8")
    monkeypatch.setattr(market_data, "MACRO_TICKERS", {})
    monkeypatch.setattr("data.kr_flow.fetch_foreign_net_buy", lambda _: None)
    provider = market_data.MarketDataProvider()
    dates = pd.bdate_range("2026-08-25", "2026-10-20").append(pd.DatetimeIndex(["2026-10-22"]))
    closes = [99.0] * (len(dates) - 1) + [98.0]
    hist = pd.DataFrame({"Close": closes}, index=dates)
    monkeypatch.setattr(provider, "_download_cached", lambda *_: hist)
    return provider, path


def test_missing_us_session_uses_recorded_previous_close(monkeypatch, tmp_path):
    provider, path = _provider(monkeypatch, tmp_path, True)
    result = provider.fetch_us_snapshot(date(2026, 10, 23))
    assert all(q.change_pct == -2.0 for q in result.us_indices)
    saved = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    assert saved["2026-10-21"]["SOX"] == 100.0
    assert saved["2026-10-22"]["SOX"] == 98.0


def test_missing_us_session_without_prior_close_fails_closed(monkeypatch, tmp_path):
    provider, path = _provider(monkeypatch, tmp_path, False)
    with pytest.raises(market_data.DataAccessError, match="종가 누락"):
        provider.fetch_us_snapshot(date(2026, 10, 23))
    assert not path.exists()
