"""
data/news_collector.py의 reached_start 플래그(예산 소진 vs 진짜 뉴스 없음 구분)와
page_cache(중복 페이지 요청 방지) 동작을 실제 네트워크 없이 검증한다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

import data.news_collector as nc
from data.news_collector import NewsItem, fetch_news_for_range, fetch_news_for_range_detailed


def _fake_pages(today: date) -> dict:
    return {
        1: [NewsItem(str(today), "10:00", "t1", "l1", "s"),
            NewsItem(str(today - timedelta(days=1)), "09:00", "t2", "l2", "s")],
        2: [NewsItem(str(today - timedelta(days=2)), "09:00", "t3", "l3", "s"),
            NewsItem(str(today - timedelta(days=5)), "09:00", "t4", "l4", "s")],
        3: [],
    }


def test_reaches_start_when_page_older_than_range(monkeypatch):
    today = date(2026, 7, 20)
    pages = _fake_pages(today)
    monkeypatch.setattr(nc, "_parse_page", lambda code, page: pages.get(page, []))

    items, reached_start = fetch_news_for_range_detailed(
        "005930", today - timedelta(days=3), today, max_pages=5, sleep_sec=0
    )
    assert reached_start is True
    dates = {it.date for it in items}
    assert str(today) in dates
    assert str(today - timedelta(days=2)) in dates
    assert str(today - timedelta(days=5)) not in dates  # 범위 밖이라 collected에는 없음


def test_budget_exhausted_when_max_pages_too_small(monkeypatch):
    today = date(2026, 7, 20)
    pages = _fake_pages(today)
    monkeypatch.setattr(nc, "_parse_page", lambda code, page: pages.get(page, []))

    # start가 today-10인데 max_pages=1이라 page1만 보고 멈춤 -> 진짜 없음이 아니라 예산 소진
    items, reached_start = fetch_news_for_range_detailed(
        "005930", today - timedelta(days=10), today, max_pages=1, sleep_sec=0
    )
    assert reached_start is False


def test_page_cache_prevents_duplicate_network_calls(monkeypatch):
    today = date(2026, 7, 20)
    pages = _fake_pages(today)
    call_log: list[int] = []

    def fake_parse_page(code, page):
        call_log.append(page)
        return pages.get(page, [])

    monkeypatch.setattr(nc, "_parse_page", fake_parse_page)

    cache: dict = {}
    fetch_news_for_range_detailed("005930", today - timedelta(days=1), today,
                                   max_pages=2, sleep_sec=0, page_cache=cache)
    first_call_count = len(call_log)
    assert first_call_count == 2  # page1, page2 둘 다 실제 호출

    fetch_news_for_range_detailed("005930", today - timedelta(days=1), today,
                                   max_pages=2, sleep_sec=0, page_cache=cache)
    assert len(call_log) == first_call_count  # 두 번째 호출은 캐시로 처리, 추가 호출 없음


def _make_deep_pages(today: date, n_pages: int = 300) -> dict:
    """page i(1-indexed)마다 정확히 하루치 뉴스 1건, date = today - (i-1)일.
    실제 네이버 뉴스처럼 페이지 번호가 커질수록 과거로 가는 단조성을 흉내낸다."""
    pages = {}
    for i in range(1, n_pages + 1):
        d = today - timedelta(days=i - 1)
        pages[i] = [NewsItem(str(d), "09:00", f"title-{i}", f"link-{i}", "source")]
    return pages


def test_binary_search_reaches_old_event_with_far_fewer_requests_than_linear(monkeypatch):
    """
    핵심 회귀 테스트: 뉴스 목록 페이지 번호는 '오늘'부터의 거리에 비례해 커지므로,
    100일 전 이벤트를 선형으로 찾으려면 최소 100페이지를 넘겨야 한다. 이진 탐색
    (_find_anchor_page)이 실제로 동작한다면 훨씬 적은 요청으로 정확히 찾아야 한다.
    """
    today = date(2026, 7, 29)
    pages = _make_deep_pages(today, n_pages=300)
    call_log: list[int] = []

    def fake_parse_page(code, page):
        call_log.append(page)
        return pages.get(page, [])

    monkeypatch.setattr(nc, "_parse_page", fake_parse_page)

    target = today - timedelta(days=100)
    start, end = target - timedelta(days=2), target + timedelta(days=1)

    items, reached_start = fetch_news_for_range_detailed(
        "000270", start, end, max_pages=15, sleep_sec=0
    )

    expected_dates = {str(start + timedelta(days=k)) for k in range((end - start).days + 1)}
    assert {it.date for it in items} == expected_dates
    assert reached_start is True
    assert len(set(call_log)) < 30  # 선형이면 100회 이상 필요했을 요청 수


def test_local_budget_exhausted_for_unusually_wide_window(monkeypatch):
    today = date(2026, 7, 29)
    pages = _make_deep_pages(today, n_pages=300)
    monkeypatch.setattr(nc, "_parse_page", lambda code, page: pages.get(page, []))

    target = today - timedelta(days=100)
    start = target - timedelta(days=50)  # anchor 이후로도 한참 더 걸어야 하는 넓은 구간
    end = target

    _, reached_start = fetch_news_for_range_detailed("000270", start, end, max_pages=5, sleep_sec=0)
    assert reached_start is False


def test_page_cache_reused_across_separate_calls_for_old_event(monkeypatch):
    today = date(2026, 7, 29)
    pages = _make_deep_pages(today, n_pages=300)
    call_log: list[int] = []

    def fake_parse_page(code, page):
        call_log.append(page)
        return pages.get(page, [])

    monkeypatch.setattr(nc, "_parse_page", fake_parse_page)

    cache: dict = {}
    target = today - timedelta(days=30)
    start, end = target - timedelta(days=2), target + timedelta(days=1)

    fetch_news_for_range_detailed("000270", start, end, max_pages=10, sleep_sec=0, page_cache=cache)
    first_count = len(call_log)
    assert first_count > 0

    fetch_news_for_range_detailed("000270", start, end, max_pages=10, sleep_sec=0, page_cache=cache)
    assert len(call_log) == first_count  # 완전히 캐시로 처리, 추가 네트워크 호출 없음


def test_fetch_news_for_range_wrapper_returns_items_only(monkeypatch):
    today = date(2026, 7, 20)
    pages = _fake_pages(today)
    monkeypatch.setattr(nc, "_parse_page", lambda code, page: pages.get(page, []))

    items = fetch_news_for_range("005930", today - timedelta(days=1), today, max_pages=2, sleep_sec=0)
    assert isinstance(items, list)
    assert all(isinstance(it, NewsItem) for it in items)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
