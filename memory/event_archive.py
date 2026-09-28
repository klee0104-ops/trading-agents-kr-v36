"""
가격 변동성 이벤트 + 관련 뉴스를 SQLite에 누적 저장하는 아카이브.

memory/store.py(예측 적중 기록)와는 별개의 저장소다. 여기 쌓이는 데이터는
"과거에 이런 변동성 + 이런 뉴스가 있었다"는 원재료로, 이후 유사 사례 검색
(RAG)의 기반이 된다.

⚠️ data/news_collector.py 주석 참고: 뉴스 목록은 항상 "오늘" 기준 최신순
1페이지부터 시작해서, 페이지 번호는 이벤트 날짜가 아니라 "오늘부터 그
날짜까지의 거리"에 비례해 커진다. 그래서 예전에는(1) 여러 이벤트를 하나의
공유 page 예산으로 훑거나, (2) 이벤트별로 예산을 나눠도 여전히 1페이지부터
순서대로 넘기는 방식이라, 오늘로부터 먼 이벤트일수록(=몇 주~몇 달 전) 뉴스를
거의 못 찾았다(기아처럼 뉴스량이 많은 종목은 최근 1~2일만 남고 나머지는
전부 비는 식). 지금은 목표 날짜가 있을 페이지를 이진 탐색으로 먼저 추정해
바로 그 근처부터 조회하므로(`data.news_collector._find_anchor_page`),
이벤트가 오늘로부터 얼마나 떨어져 있는지와 거의 무관하게 빠르게 찾는다.
그래도 예산 소진으로 못 찾은 것과 진짜 뉴스가 없는 것은 구분해
`budget_exhausted_events`로 드러낸다. news 건수가 0이어도 이벤트 자체는
저장된다.
"""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from typing import Optional

from data.price_history import detect_volatility_events
from data.news_collector import fetch_news_for_range_detailed

DEFAULT_DB_PATH = "./event_archive.db"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_name TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            event_date TEXT NOT NULL,
            change_pct REAL NOT NULL,
            direction TEXT NOT NULL,
            UNIQUE(stock_code, event_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id),
            news_date TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            source TEXT,
            UNIQUE(event_id, link)
        )
    """)
    return conn


def collect_and_store(stock_name: str, start: date, end: date,
                       threshold_pct: float = 3.0, db_path: str = DEFAULT_DB_PATH,
                       days_before: int = 2, days_after: int = 1,
                       news_max_pages: int = 20) -> dict:
    """
    지정 기간의 변동성 이벤트를 탐지하고, 각 이벤트 날짜 근처 뉴스를 모아 저장한다.
    반환값: {"events": 탐지 이벤트 수, "news": 새로 저장된 뉴스 건수,
             "events_without_news": 뉴스가 안 잡힌 이벤트 날짜 목록,
             "budget_exhausted_events": 그중 news_max_pages 예산 소진으로
             (진짜 없어서가 아니라) 못 찾았을 가능성이 있는 날짜 목록}

    이벤트마다 독립적으로 news_max_pages 페이지 예산을 준다 — 최근 이벤트가
    예산을 다 써버려서 오래된 이벤트가 아예 조회조차 안 되는 걸 막기 위함.
    최신 이벤트부터 처리하면서 page_cache를 공유해, 겹치는 날짜 구간(예:
    며칠 간격으로 연속된 이벤트)은 중복 HTTP 요청 없이 캐시로 재사용한다.
    """
    events = detect_volatility_events(stock_name, start, end, threshold_pct=threshold_pct)
    conn = _connect(db_path)
    news_count = 0
    events_without_news: list[str] = []
    budget_exhausted_events: list[str] = []
    try:
        page_cache: dict = {}
        for ev in sorted(events, key=lambda e: e.event_date, reverse=True):
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (stock_name, stock_code, event_date, change_pct, direction)
                   VALUES (?, ?, ?, ?, ?)""",
                (ev.stock_name, ev.stock_code, ev.event_date, ev.change_pct, ev.direction),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT id FROM events WHERE stock_code=? AND event_date=?",
                    (ev.stock_code, ev.event_date),
                ).fetchone()
                event_id = row[0]
            else:
                event_id = cur.lastrowid

            ev_date = date.fromisoformat(ev.event_date)
            ev_start = ev_date - timedelta(days=days_before)
            ev_end = ev_date + timedelta(days=days_after)
            matched, reached_start = fetch_news_for_range_detailed(
                ev.stock_code, ev_start, ev_end, max_pages=news_max_pages, page_cache=page_cache
            )

            for n in matched:
                res = conn.execute(
                    """INSERT OR IGNORE INTO news (event_id, news_date, title, link, source)
                       VALUES (?, ?, ?, ?, ?)""",
                    (event_id, n.date, n.title, n.link, n.source),
                )
                if res.rowcount:
                    news_count += 1

            if not matched:
                events_without_news.append(ev.event_date)
                if not reached_start:
                    budget_exhausted_events.append(ev.event_date)
        conn.commit()
    finally:
        conn.close()

    return {
        "events": len(events),
        "news": news_count,
        "events_without_news": events_without_news,
        "budget_exhausted_events": budget_exhausted_events,
    }


def query_events(stock_name: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = _connect(db_path)
    try:
        if stock_name:
            rows = conn.execute(
                "SELECT * FROM events WHERE stock_name=? ORDER BY event_date", (stock_name,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY event_date").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def query_news_for_event(event_id: int, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM news WHERE event_id=? ORDER BY news_date", (event_id,)
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM news LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def list_stock_names(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT stock_name FROM events ORDER BY stock_name").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_all_cases_with_news(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """유사 사례 검색(RAG)용: 이벤트마다 관련 뉴스 제목을 합쳐서 반환한다."""
    conn = _connect(db_path)
    try:
        events = conn.execute(
            "SELECT id, stock_name, event_date, change_pct, direction FROM events"
        ).fetchall()
        cases = []
        for event_id, name, event_date, change_pct, direction in events:
            titles = [row[0] for row in conn.execute(
                "SELECT title FROM news WHERE event_id=?", (event_id,)
            ).fetchall()]
            cases.append({
                "stock_name": name, "event_date": event_date, "change_pct": change_pct,
                "direction": direction, "news_titles": titles,
            })
        return cases
    finally:
        conn.close()
