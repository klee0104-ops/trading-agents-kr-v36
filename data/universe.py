"""
(V3.10) 뉴스 수집 대상 종목 유니버스.

KOSPI 시가총액 상위 100(네이버 모바일 증권 시총순 API)을 하루 한 번 받아 캐시하고,
거기서 핵심 종목(삼성전자·SK하이닉스)과 우선주를 뺀 **시총 상위 N개**를 뉴스 대상으로 고른다.
→ 핵심 2 + 추가 N(기본 10) = 12종목의 최근 뉴스 제목을 뉴스·심리 분석가가 본다.

조회가 실패하면 마지막 캐시를, 캐시도 없으면 내장 기본 목록을 쓴다 (파이프라인은 계속 돈다).
"""
from __future__ import annotations
import json
import os
from datetime import date
from typing import Optional

from config import Config

_CACHE_PATH = "./kospi_top_cache.json"
CORE_CODES = {"005930": "삼성전자", "000660": "SK하이닉스"}

# 조회 실패 시 최후의 기본값 (2026-09 기준 대략의 시총 상위, 코드는 KRX 종목코드)
_FALLBACK_TOP = [
    {"code": "373220", "name": "LG에너지솔루션"}, {"code": "207940", "name": "삼성바이오로직스"},
    {"code": "005380", "name": "현대차"}, {"code": "000270", "name": "기아"},
    {"code": "068270", "name": "셀트리온"}, {"code": "035420", "name": "NAVER"},
    {"code": "105560", "name": "KB금융"}, {"code": "012450", "name": "한화에어로스페이스"},
    {"code": "005490", "name": "POSCO홀딩스"}, {"code": "028260", "name": "삼성물산"},
]


def _is_preferred(name: str) -> bool:
    """우선주(삼성전자우, 현대차2우B 등) 제외 — 보통주와 뉴스가 겹친다."""
    return name.endswith("우") or name.endswith("우B") or name.endswith("우C")


_NAVER_MV_URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}


def _to_num(text) -> float:
    import re
    t = re.sub(r"[^\d\.\-]", "", str(text or ""))
    try:
        return float(t) if t else 0.0
    except ValueError:
        return 0.0


def parse_market_value_payload(payload) -> list[dict]:
    """네이버 모바일 시총순 목록 → [{code, name, marcap(억원)}] (응답 순서 = 시총 내림차순)."""
    rows = []
    for st in (payload or {}).get("stocks", []):
        code = str(st.get("itemCode") or "").zfill(6)
        name = str(st.get("stockName") or "")
        if not code or not name or st.get("stockEndType") not in (None, "stock"):
            continue
        rows.append({"code": code, "name": name, "marcap": _to_num(st.get("marketValue"))})
    rows.sort(key=lambda r: -r["marcap"])
    return rows


def fetch_kospi_top(n: int = 100, force_refresh: bool = False) -> list[dict]:
    """시총 상위 n개 [{code, name, marcap}]. 같은 날 캐시가 있으면 재사용.
    출처: 네이버 모바일 증권 시총순 API (FinanceDataReader는 장중에 시총이 비어 있어 쓰지 않음)."""
    today = str(date.today())
    if not force_refresh and os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == today and cached.get("rows"):
                return cached["rows"][:n]
        except Exception:  # noqa: BLE001
            pass
    try:
        import requests
        resp = requests.get(_NAVER_MV_URL, params={"page": 1, "pageSize": max(n, 100)}, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        rows = parse_market_value_payload(resp.json())
        if not rows:
            raise ValueError("빈 목록")
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": today, "rows": rows}, f, ensure_ascii=False)
        return rows[:n]
    except Exception:  # noqa: BLE001
        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f).get("rows", [])[:n]
            except Exception:  # noqa: BLE001
                pass
        return _FALLBACK_TOP[:n]


def select_watch_universe(top_rows: Optional[list[dict]] = None, n: Optional[int] = None,
                          seed_date: Optional[date] = None, mode: Optional[str] = None) -> list[dict]:
    """
    (V3.11) 관심 종목 n개를 KOSPI 시총 상위 100(핵심·우선주 제외)에서 고른다.
    mode:
      "random"     — 날짜를 시드로 한 무작위 추첨 (같은 날 재실행하면 같은 종목, 날마다 바뀜)
      "stratified" — 상위 30에서 절반, 31~100에서 절반을 무작위 추첨 (대형주 편중 완화 + 중소형 노출)  [기본]
      "topcap"     — 시총 순 상위 n개 (예전 방식)
    """
    import random
    n = Config.WATCH_STOCK_COUNT if n is None else n
    mode = mode or Config.WATCH_PICK_MODE
    rows = top_rows if top_rows is not None else fetch_kospi_top(100)
    pool = [{"code": r["code"], "name": r["name"], "marcap": r.get("marcap"), "rank": i + 1}
            for i, r in enumerate(rows) if r["code"] not in CORE_CODES and not _is_preferred(r["name"])]
    if mode == "topcap" or len(pool) <= n:
        picked = pool[:n]
    else:
        rng = random.Random(str(seed_date or date.today()))
        if mode == "random":
            picked = rng.sample(pool, n)
        else:
            head, tail = pool[:30], pool[30:]
            k_head = min(len(head), (n + 1) // 2)
            picked = rng.sample(head, k_head) + rng.sample(tail, min(len(tail), n - k_head))
        picked.sort(key=lambda r: r["rank"])
    return [{"code": r["code"], "name": r["name"], "core": False, "rank": r["rank"]} for r in picked]


def select_news_universe(top_rows: Optional[list[dict]] = None, extra: Optional[int] = None,
                         watch: Optional[list[dict]] = None) -> list[dict]:
    """핵심 2종목 + 관심 종목(오늘 추첨된 10개). 뉴스 대상과 종목 추천 대상을 같게 맞춘다."""
    core = [{"code": c, "name": nm, "core": True} for c, nm in CORE_CODES.items()]
    if watch is None:
        watch = select_watch_universe(top_rows, n=extra)
    return core + [dict(w, core=False) for w in watch]
