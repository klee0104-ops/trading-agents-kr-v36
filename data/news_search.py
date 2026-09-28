"""
(V3.13) 네이버 개발자센터 '검색 > 뉴스' 공식 API 로 종목 뉴스 제목을 가져온다.

- 네이버가 개발자용으로 공개한 경로(https://openapi.naver.com/v1/search/news.json).
  증권 모바일 내부 API(data/news_collector.py)와 달리 약관상 정상 이용 경로다.
- 무료 하루 25,000건, 호출당 최대 100건. 우리는 종목당 1회 → 하루 12~14회.
- 인증: X-Naver-Client-Id / X-Naver-Client-Secret 헤더. 키는 security.secret_store
  (Windows 자격 증명 관리자) 에서 SecretStr 로 꺼내며 로그·예외에는 마스킹된다.
- 키워드 검색이라 종목과 무관한 기사(스포츠·연예·광고성)가 섞일 수 있어
  "제목에 종목명 포함" + 잡음 키워드 제외 + 기간 필터를 건다.
- 결과는 그날 분석에만 쓰고 last_outcome.json 에 하루치만 남긴다(장기 DB화 안 함).
"""
from __future__ import annotations
import html
import re
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from data.news_collector import NewsItem

# (2026-07-31) 개발자센터 검색 API 신규 발급 종료 → 네이버 클라우드 "NAVER API HUB" 로 이관.
# 응답 JSON 은 동일, 주소와 인증 헤더 이름만 다르다. 기존 openapi.naver.com 키는 2027-06-30 까지.
ENDPOINTS = {
    "hub": ("https://naverapihub.apigw.ntruss.com/search/v1/news", "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    "legacy": ("https://openapi.naver.com/v1/search/news.json", "X-Naver-Client-Id", "X-Naver-Client-Secret"),
}
SEARCH_URL = ENDPOINTS["hub"][0]
_TAG_RE = re.compile(r"</?b>|<[^>]+>")
# 종목명이 들어가도 주가와 무관한 기사가 많은 키워드 (야구단·스폰서·채용·연예 등)
NOISE_WORDS = ("라이온즈", "타이거즈", "야구", "축구", "농구", "배구", "골프", "e스포츠", "채용", "공채",
               "임직원 봉사", "사회공헌", "콘서트", "드라마", "예능", "당첨", "이벤트 응모", "경품")
_QUERY_SUFFIX = {"삼성전자": "삼성전자 주가", "SK하이닉스": "SK하이닉스 주가"}


class NaverSearchError(RuntimeError):
    pass


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def build_query(name: str) -> str:
    return _QUERY_SUFFIX.get(name, name)


def parse_search_payload(payload: dict, name: str, start: date, end: date) -> list[NewsItem]:
    """검색 API 응답 → 기간 안, 제목에 종목명 포함, 잡음 제외, 링크 중복 제거. 최신순."""
    out: list[NewsItem] = []
    seen: set[str] = set()
    key = name.replace(" ", "").lower()
    for it in (payload or {}).get("items", []) or []:
        title = _clean(it.get("title", ""))
        if not title or key not in title.replace(" ", "").lower():
            continue
        if any(w in title for w in NOISE_WORDS):
            continue
        try:
            dt = parsedate_to_datetime(it.get("pubDate", ""))
        except (TypeError, ValueError):
            continue
        d = dt.date()
        if d < start or d > end:
            continue
        link = it.get("originallink") or it.get("link") or ""
        dedupe = link or title
        if dedupe in seen:
            continue
        seen.add(dedupe)
        src = ""
        m = re.match(r"https?://([^/]+)/", link)
        if m:
            src = m.group(1)
        out.append(NewsItem(date=str(d), time=dt.strftime("%H:%M"), title=title, link=link, source=src))
    out.sort(key=lambda x: (x.date, x.time), reverse=True)
    return out


def search_news(name: str, start: date, end: date, client_id: str, client_secret: str,
                display: int = 40, sleep_sec: float = 0.1, mode: str = "hub") -> list[NewsItem]:
    """종목명으로 최신순 검색 1회. 429/401 은 NaverSearchError 로 올려서 호출측이 전체를 중단하게 한다.
    mode: hub(NAVER API HUB, 기본) | legacy(구 개발자센터 키)"""
    url, h_id, h_secret = ENDPOINTS.get(mode, ENDPOINTS["hub"])
    resp = requests.get(
        url,
        params={"query": build_query(name), "display": max(10, min(100, display)), "start": 1, "sort": "date"},
        headers={h_id: client_id, h_secret: client_secret, "User-Agent": "TradingAgents-KR/1.0"},
        timeout=10,
    )
    if resp.status_code == 429:
        raise NaverSearchError("네이버 검색 API 일일 한도(25,000건) 초과")
    if resp.status_code in (401, 403):
        raise NaverSearchError(f"네이버 검색 API 인증 실패(HTTP {resp.status_code}) — 키 확인: python cli.py key-status")
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError:
        return []
    time.sleep(sleep_sec)
    return parse_search_payload(payload, name, start, end)


def naver_search_available() -> bool:
    """검색 API 키 2개가 모두 있으면 True. 값은 꺼내지 않고 존재만 본다."""
    try:
        from security.secret_store import get_api_key
        return bool(get_api_key("naver_id")) and bool(get_api_key("naver_secret"))
    except Exception:  # noqa: BLE001
        return False


def fetch_titles_via_search(stocks: list[dict], as_of: date, days: int, per_core: int, per_extra: int,
                            display: Optional[int] = None) -> list[str]:
    """공식 검색 API 로 종목 목록 전체의 제목을 모은다. 형식 "[날짜][종목명] 제목" (기존과 동일)."""
    from config import Config
    from security.secret_store import get_api_key
    cid, csec = get_api_key("naver_id"), get_api_key("naver_secret")
    if not (cid and csec):
        return []
    start = as_of - timedelta(days=days)
    titles: list[str] = []
    for st in stocks:
        limit = per_core if st.get("core") else per_extra
        try:
            items = search_news(st["name"], start, as_of, cid.reveal(), csec.reveal(),
                                display=display or Config.NEWS_SEARCH_DISPLAY, mode=Config.NAVER_SEARCH_MODE)
        except NaverSearchError:
            raise
        except Exception:  # noqa: BLE001 — 종목 하나 실패는 건너뛴다
            continue
        titles += [f"[{it.date}][{st['name']}] {it.title}" for it in items[:limit]]
    return titles
