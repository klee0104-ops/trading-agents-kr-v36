"""
memory/event_archive.py의 collect_and_store()가 이벤트별 독립 page 예산으로
동작하는지, 그리고 "예산 소진으로 못 찾음"과 "진짜 뉴스 없음"을 구분해서
보고하는지 검증한다 (실제 네트워크/가격 조회 없이 monkeypatch로 검증).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from datetime import date

import memory.event_archive as ea
from data.news_collector import NewsItem


@dataclass
class _FakeEvent:
    stock_name: str
    stock_code: str
    event_date: str
    change_pct: float
    direction: str


def test_collect_and_store_distinguishes_exhausted_from_truly_empty(monkeypatch, tmp_path):
    events = [
        _FakeEvent("삼성전자", "005930", "2026-01-05", 4.0, "up"),      # 오래된 이벤트: 예산 소진
        _FakeEvent("삼성전자", "005930", "2026-06-01", -3.5, "down"),   # 최근 이벤트: 뉴스 있음
        _FakeEvent("삼성전자", "005930", "2026-06-10", 3.2, "up"),      # 최근 이벤트: 진짜 없음
    ]
    monkeypatch.setattr(ea, "detect_volatility_events", lambda *a, **k: events)

    def fake_fetch(code, start, end, max_pages=20, page_cache=None):
        if start == date(2026, 5, 30):  # 2026-06-01 이벤트의 조회 시작일 (days_before=2)
            return [NewsItem("2026-06-01", "09:00", "제목", "링크", "출처")], True
        if start == date(2026, 6, 8):  # 2026-06-10 이벤트의 조회 시작일
            return [], True  # 진짜로 뉴스가 없음 (start까지 확인함)
        return [], False  # 오래된 이벤트: 예산 소진으로 확인 못 함

    monkeypatch.setattr(ea, "fetch_news_for_range_detailed", fake_fetch)

    db_path = str(tmp_path / "events.db")
    result = ea.collect_and_store("삼성전자", date(2026, 1, 1), date(2026, 6, 30), db_path=db_path)

    assert result["events"] == 3
    assert result["news"] == 1
    assert set(result["events_without_news"]) == {"2026-01-05", "2026-06-10"}
    assert result["budget_exhausted_events"] == ["2026-01-05"]


def test_events_are_still_saved_even_without_news(monkeypatch, tmp_path):
    events = [_FakeEvent("삼성전자", "005930", "2026-01-05", 4.0, "up")]
    monkeypatch.setattr(ea, "detect_volatility_events", lambda *a, **k: events)
    monkeypatch.setattr(ea, "fetch_news_for_range_detailed", lambda *a, **k: ([], False))

    db_path = str(tmp_path / "events.db")
    ea.collect_and_store("삼성전자", date(2026, 1, 1), date(2026, 1, 31), db_path=db_path)

    rows = ea.query_events(stock_name="삼성전자", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["event_date"] == "2026-01-05"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
