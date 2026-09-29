"""
(V3.15) 키 없이 쓰는 뉴스 출처 — RSS.

1) Google 뉴스 RSS 검색: https://news.google.com/rss/search?q=<종목명>&hl=ko&gl=KR&ceid=KR:ko
   - 국내 경제지·통신사 기사가 대부분 포함되고, RSS는 구독(배포)용으로 공개된 형식이라
     비공개 API를 긁는 것과 달리 약관 문제가 없다. 키·가입 불필요. 제목 형식 "제목 - 매체명".
2) 언론사 공식 RSS(연합뉴스 경제, 한국경제, 매일경제 증권): 한 번 받아서 종목명으로 거른다.
   - 종목별 검색은 안 되므로 보조. 피드 주소가 바뀌면 조용히 건너뛴다.

두 출처 모두 결과 형식은 data.news_collector.NewsItem 으로 맞춰 파이프라인 변경이 없다.
"""
from __future__ import annotations
import html
import re
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import requests

from data.news_collector import NewsItem
from data.news_search import NOISE_WORDS

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
PRESS_FEEDS = {
    "연합뉴스 경제": "https://www.yna.co.kr/rss/economy.xml",
    "한국경제": "https://www.hankyung.com/feed/economy",
    "매일경제 증권": "https://www.mk.co.kr/rss/50200011/",
}
_HEADERS = {"User-Agent": "Mozilla/5.0 (TradingAgents-KR RSS reader)"}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _pub_date(text: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, name: str, start: date, end: date, require_name: bool = True) -> list[NewsItem]:
    """RSS 2.0 <item> 목록 → 기간 안 + (옵션) 제목에 종목명 포함 + 잡음 제외 + 중복 제거. 최신순."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    key = name.replace(" ", "").lower()
    out, seen = [], set()
    for it in root.iter("item"):
        title = _clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = _clean(src_el.text) if src_el is not None and src_el.text else ""
        # Google 뉴스 제목은 "제목 - 매체" 형식 (source 태그가 있어도 제목에 매체명이 붙는다)
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            if not source or tail.strip() == source or len(tail) <= 12:
                title, source = head.strip(), (source or tail.strip())
        if not title:
            continue
        if require_name and key not in title.replace(" ", "").lower():
            continue
        if any(w in title for w in NOISE_WORDS):
            continue
        dt = _pub_date(it.findtext("pubDate") or "")
        if dt is None:
            continue
        if dt.tzinfo is not None:  # KST 날짜로 맞춘다
            dt = dt.astimezone(_KST)
        d = dt.date()
        if d < start or d > end:
            continue
        dedupe = link or title
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append(NewsItem(date=str(d), time=dt.strftime("%H:%M"), title=title, link=link, source=source))
    out.sort(key=lambda x: (x.date, x.time), reverse=True)
    return out


from datetime import timezone as _tz  # noqa: E402
_KST = _tz(timedelta(hours=9))


def fetch_google_news(name: str, start: date, end: date, query: Optional[str] = None,
                      sleep_sec: float = 0.2, timeout: int = 10) -> list[NewsItem]:
    q = query or name
    resp = requests.get(GOOGLE_NEWS_RSS, params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                        headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    time.sleep(sleep_sec)
    return parse_rss(resp.text, name, start, end, require_name=True)


def fetch_press_feeds(timeout: int = 10) -> dict[str, str]:
    """언론사 피드 원문을 한 번에 받는다 {피드명: xml}. 실패한 피드는 뺀다."""
    out = {}
    for label, url in PRESS_FEEDS.items():
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            if r.ok and r.text.lstrip().startswith("<"):
                out[label] = r.text
        except Exception:  # noqa: BLE001
            continue
    return out


def fetch_titles_via_rss(stocks: list[dict], as_of: date, days: int, per_core: int, per_extra: int) -> list[str]:
    """Google 뉴스 RSS(종목별 검색) + 언론사 피드(종목명 필터). 형식 "[날짜][종목명][매체] 제목"."""
    from data.news_search import build_query
    start = as_of - timedelta(days=days)
    press = fetch_press_feeds()
    from agents.specialists import _press
    titles: list[str] = []
    for st in stocks:
        limit = per_core if st.get("core") else per_extra
        items: list[NewsItem] = []
        try:
            items = fetch_google_news(st["name"], start, as_of, query=build_query(st["name"]))
        except Exception:  # noqa: BLE001
            pass
        for feed_name, xml_text in press.items():
            for it in parse_rss(xml_text, st["name"], start, as_of, require_name=True):
                if not it.source:  # (V3.24) 언론사 피드는 source 태그가 없으므로 피드명(매체)으로 채운다
                    it.source = feed_name.split(" ")[0]
                items.append(it)
        seen, uniq = set(), []
        for it in sorted(items, key=lambda x: (x.date, x.time), reverse=True):
            if it.title in seen:
                continue
            seen.add(it.title)
            uniq.append(it)
        titles += [f"[{it.date}][{st['name']}][{_press(it)}] {it.title}" for it in uniq[:limit]]
    return titles
