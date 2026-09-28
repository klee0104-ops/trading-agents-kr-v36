"""
네이버금융 종목뉴스 페이지 스크레이핑.

로그인/키 발급이 필요한 오픈API가 아니라 공개 웹페이지(finance.naver.com)를
그대로 파싱하는 방식이라 별도의 API 키나 토큰 소모가 없다.

⚠️ 진짜 근본 원인 (이전 버전에서 놓쳤던 부분): 이 목록은 항상 "오늘" 기준
최신순 1페이지부터 시작한다. 즉 페이지 번호는 각 이벤트의 날짜가 아니라
"오늘부터 그 날짜까지 며칠이나 떨어져 있는가"에 비례해서 커진다. 그래서
이벤트마다 개별적으로 조회하더라도(이전 수정), 오늘 기준 한 달 전 이벤트를
찾으려면 그사이 한 달치 뉴스 전체를 한 페이지씩 넘겨야 하는 건 똑같다 —
"이벤트 근처 며칠"이 아니라 "오늘부터 이벤트까지 전체 기간"이 페이지 수를
결정한다. 뉴스량이 많은 종목(기아, 삼성전자 등)은 이게 수십~수백 페이지가
되어, 순차 탐색(max_pages 예산 안에서 1,2,3...페이지 넘기기)으로는 최근
며칠을 벗어난 이벤트는 거의 다 놓친다.

해결: 페이지 번호가 커질수록 더 과거 뉴스라는 단조성(뉴스가 최신순 정렬)을
이용해 목표 날짜가 있을 페이지를 이진 탐색(_find_anchor_page)으로 먼저
추정한다. 1페이지부터 순서대로 넘기지 않고 O(log 페이지수) 번의 요청만으로
목표 날짜 근처에 바로 도달하기 때문에, 이벤트가 오늘로부터 얼마나
떨어져 있는지와 거의 무관하게 빠르게 찾을 수 있다.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import html
import requests

# (V3.10) 네이버 금융 PC 뉴스 페이지(finance.naver.com/item/news_news.naver)는 2026년 서비스 종료(410).
# 네이버 모바일 증권 API로 교체. 응답: [{"total":n,"items":[{"title","titleFull","datetime":"YYYYMMDDHHMM",
# "officeName","mobileNewsUrl",...}]}, ...] — 최신순, 페이지 단위(pageSize 최대 20 안팎).
_BASE_URL = "https://m.stock.naver.com/api/news/stock/{code}"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}
PAGE_SIZE = 20  # 400이 나면 자동으로 10 → 5로 줄인다
_page_size_state = {"size": PAGE_SIZE}


@dataclass
class NewsItem:
    date: str   # YYYY-MM-DD
    time: str   # HH:MM
    title: str
    link: str
    source: str


def _fetch_page_cached(code: str, page: int, page_cache: Optional[dict],
                        sleep_sec: float) -> list[NewsItem]:
    """page_cache가 주어지면 (code, page) 단위로 결과를 재사용해 중복 요청을 줄인다."""
    key = (code, page)
    if page_cache is not None and key in page_cache:
        return page_cache[key]

    items = _parse_page(code, page)
    if page_cache is not None:
        page_cache[key] = items
    if items:
        time.sleep(sleep_sec)
    return items


def parse_news_payload(payload) -> list[NewsItem]:
    """모바일 API 응답(그룹 목록 또는 items 목록)을 NewsItem 목록으로 바꾼다."""
    items: list[NewsItem] = []
    seen = set()
    groups = payload if isinstance(payload, list) else (payload or {}).get("items", [])
    for g in groups:
        rows = g.get("items", [g]) if isinstance(g, dict) else []
        for it in rows:
            if not isinstance(it, dict):
                continue
            dt_raw = str(it.get("datetime") or "")
            if len(dt_raw) < 12:
                continue
            try:
                dt = datetime.strptime(dt_raw[:12], "%Y%m%d%H%M")
            except ValueError:
                continue
            link = it.get("mobileNewsUrl") or (
                f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}"
                if it.get("officeId") and it.get("articleId") else "")
            if link in seen:
                continue
            seen.add(link)
            items.append(NewsItem(
                date=str(dt.date()), time=dt.strftime("%H:%M"),
                title=html.unescape(str(it.get("titleFull") or it.get("title") or "")).strip(),
                link=link, source=str(it.get("officeName") or ""),
            ))
    return items


def _parse_page(code: str, page: int) -> list[NewsItem]:
    for _ in range(3):
        size = _page_size_state["size"]
        resp = requests.get(_BASE_URL.format(code=code), params={"pageSize": size, "page": page},
                            headers=_HEADERS, timeout=10)
        if resp.status_code == 400 and size > 5:
            _page_size_state["size"] = 10 if size > 10 else 5  # 서버가 허용하는 크기로 낮춘다
            continue
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            return []
        return parse_news_payload(payload)
    return []


def _page_date_range(items: list[NewsItem]) -> tuple[date, date]:
    dates = [date.fromisoformat(it.date) for it in items]
    return min(dates), max(dates)


def _find_anchor_page(code: str, target_date: date, page_cache: Optional[dict],
                       sleep_sec: float, max_page_probe: int = 4000) -> int:
    """
    target_date가 걸려 있을 것으로 추정되는 페이지 번호를 이진 탐색으로 찾는다.
    1페이지부터 순서대로 넘기면 오래된 날짜일수록(=오늘로부터 멀수록) 그
    사이의 페이지 수만큼 요청이 필요하지만, 이진 탐색은 그 깊이와 거의
    무관하게 O(log 페이지수) 번의 요청으로 근접 지점에 도달한다.
    """
    page1 = _fetch_page_cached(code, 1, page_cache, sleep_sec)
    if not page1:
        return 1
    oldest1, _ = _page_date_range(page1)
    if oldest1 <= target_date:
        return 1  # target_date가 이미 최신 페이지 범위 안에 있음 (최근 이벤트)

    # 1) 지수 탐색: target_date보다 오래된 뉴스를 포함하는 상한(hi) 페이지를 찾는다
    lo, hi = 1, 2
    while True:
        items = _fetch_page_cached(code, hi, page_cache, sleep_sec)
        if not items:
            return hi  # 뉴스 목록 끝(더 과거 없음)에 먼저 도달
        oldest, _ = _page_date_range(items)
        if oldest <= target_date:
            break
        if hi >= max_page_probe:
            return hi  # 안전장치: 과도한 탐색 방지
        lo, hi = hi, hi * 2

    # 2) 이진 탐색: lo~hi 사이에서 target_date가 걸리는 페이지로 좁힌다
    while lo < hi:
        mid = (lo + hi) // 2
        items = _fetch_page_cached(code, mid, page_cache, sleep_sec)
        if not items:
            hi = mid
            continue
        oldest, newest = _page_date_range(items)
        if target_date > newest:
            hi = mid
        elif target_date < oldest:
            lo = mid + 1
        else:
            return mid
    return lo


def fetch_news_for_range_detailed(code: str, start: date, end: date,
                                   max_pages: int = 15, sleep_sec: float = 0.3,
                                   page_cache: Optional[dict] = None) -> tuple[list[NewsItem], bool]:
    """
    start~end 기간 뉴스를 모은다. end 날짜 근처 페이지를 이진 탐색으로 먼저
    추정한 뒤, 거기서부터 최대 max_pages만 순차적으로 더 걸어 [start, end]에
    해당하는 기사를 모은다 (이진 탐색 오차 보정을 위해 추정 페이지보다
    2페이지 앞에서 시작한다).

    반환값: (수집된 뉴스, reached_start)
    reached_start=True면 start 이전(또는 목록 끝)까지 실제로 확인했다는
    뜻이라 "뉴스 없음"을 신뢰할 수 있다. False면 max_pages 예산을 다 써서
    중간에 멈췄다는 뜻이므로, 이 구간의 "뉴스 없음"은 예산 부족일 수 있다.
    """
    cache = page_cache if page_cache is not None else {}
    anchor = _find_anchor_page(code, end, cache, sleep_sec)
    page = max(1, anchor - 2)

    collected: list[NewsItem] = []
    reached_start = False
    for _ in range(max_pages):
        items = _fetch_page_cached(code, page, cache, sleep_sec)
        if not items:
            reached_start = True  # 뉴스 목록 자체가 끝남
            break
        oldest, newest = _page_date_range(items)
        if newest < start:
            # 이 페이지조차 start보다 과거 -> 이미 구간을 지나쳤음(범위 안엔 뉴스가 없음)
            reached_start = True
            break
        for it in items:
            it_date = date.fromisoformat(it.date)
            if start <= it_date <= end:
                collected.append(it)
        if oldest < start:
            reached_start = True
            break
        page += 1
    return collected, reached_start


def fetch_news_for_range(code: str, start: date, end: date,
                          max_pages: int = 15, sleep_sec: float = 0.3,
                          page_cache: Optional[dict] = None) -> list[NewsItem]:
    items, _ = fetch_news_for_range_detailed(
        code, start, end, max_pages=max_pages, sleep_sec=sleep_sec, page_cache=page_cache
    )
    return items


def fetch_news_around_event(code: str, event_date: date,
                             days_before: int = 2, days_after: int = 1,
                             max_pages: int = 15,
                             page_cache: Optional[dict] = None) -> list[NewsItem]:
    start = event_date - timedelta(days=days_before)
    end = event_date + timedelta(days=days_after)
    return fetch_news_for_range(code, start, end, max_pages=max_pages, page_cache=page_cache)
