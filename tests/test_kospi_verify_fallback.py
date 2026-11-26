"""A completed KOSPI session remains gradeable when Yahoo is unavailable."""
from datetime import date, datetime, timedelta, timezone

import pytest

from data.market_data import DataAccessError, MarketDataProvider


KST = timezone(timedelta(hours=9))
ROWS = [
    {"date": "2026-10-22", "close": 5533.77, "prev_close": 5735.66},
    {"date": "2026-10-23", "close": 5550.30, "prev_close": 5533.77},
]


class EmptyHistory:
    empty = True


def setup_feeds(monkeypatch, rows=ROWS, hour=19, minute=40):
    import yfinance
    from data import kr_daily, sessions

    monkeypatch.setattr(yfinance, "download", lambda *args, **kwargs: EmptyHistory())
    monkeypatch.setattr(kr_daily, "fetch_daily", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        sessions, "kst_now", lambda: datetime(2026, 10, 23, hour, minute, tzinfo=KST)
    )


def test_verified_kospi_close_survives_empty_yahoo(monkeypatch):
    setup_feeds(monkeypatch)
    result = MarketDataProvider().fetch_kospi_session_after(date(2026, 10, 22))
    assert result == {
        "actual_date": "2026-10-23",
        "change_pct": round((5550.30 / 5533.77 - 1) * 100, 4),
        "actual_direction": "up",
        "source": "네이버 증권",
    }


def test_no_intraday_grading_or_mismatched_prior_close(monkeypatch):
    setup_feeds(monkeypatch, hour=15, minute=39)
    with pytest.raises(DataAccessError):
        MarketDataProvider().fetch_kospi_session_after(date(2026, 10, 22))

    setup_feeds(monkeypatch, rows=[ROWS[0], {**ROWS[1], "prev_close": 5500.0}])
    with pytest.raises(DataAccessError, match="대조"):
        MarketDataProvider().fetch_kospi_session_after(date(2026, 10, 22))


def test_backtest_never_uses_live_naver_fallback(monkeypatch):
    setup_feeds(monkeypatch)
    with pytest.raises(DataAccessError):
        MarketDataProvider(backtest_mode=True).fetch_kospi_session_after(date(2026, 10, 22))
