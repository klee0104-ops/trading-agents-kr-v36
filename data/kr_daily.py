"""
(V3.22) 국내 일별 시세 보충 — Yahoo(yfinance)의 ^KS11·국내 ETF 는 최근 며칠이 빠지거나(연휴 직후 등)
하루 늦게 들어오는 일이 있다. 그럴 때만 네이버 증권 모바일 일별 시세로 빈 날짜를 채운다.
(비공개 API라 호출은 최소화: 필요할 때 1페이지 10일치, 숫자만 사용)

반환 행: {"date": "YYYY-MM-DD", "open", "high", "low", "close", "prev_close"}  (최신순 아님: 오래된→최신)
장중(오늘, 15:40 이전) 행은 include_intraday=True 일 때만 포함한다.
"""
from __future__ import annotations
from datetime import date
from typing import Optional

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}
INDEX_URL = "https://m.stock.naver.com/api/index/{code}/price"
STOCK_URL = "https://m.stock.naver.com/api/stock/{code}/price"


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_daily(payload) -> list[dict]:
    rows = []
    for it in payload or []:
        if not isinstance(it, dict):
            continue
        d = str(it.get("localTradedAt") or "")[:10]
        c = _num(it.get("closePrice"))
        if len(d) != 10 or c is None:
            continue
        diff = _num(it.get("compareToPreviousClosePrice"))
        rows.append({"date": d, "open": _num(it.get("openPrice")), "high": _num(it.get("highPrice")),
                     "low": _num(it.get("lowPrice")), "close": c,
                     "prev_close": round(c - diff, 2) if diff is not None else None,
                     "traded_at": str(it.get("localTradedAt") or "")[:19]})  # (V3.26) 장중이면 마지막 체결 시각
    rows.sort(key=lambda r: r["date"])
    return rows


def _intraday_cut(rows: list[dict], include_intraday: bool) -> list[dict]:
    if include_intraday:
        return rows
    from data.sessions import kst_now
    now = kst_now()
    closed = now.hour > 15 or (now.hour == 15 and now.minute >= 40)
    return [r for r in rows if r["date"] < str(now.date()) or closed]


def fetch_daily(code: str, kind: str = "index", pages: int = 1, include_intraday: bool = False) -> list[dict]:
    url = (INDEX_URL if kind == "index" else STOCK_URL).format(code=code)
    rows: list[dict] = []
    for page in range(1, pages + 1):
        r = requests.get(url, params={"pageSize": 10, "page": page}, headers=_HEADERS, timeout=10)
        r.raise_for_status()
        rows += parse_daily(r.json())
    uniq = {r["date"]: r for r in rows}
    return _intraday_cut(sorted(uniq.values(), key=lambda r: r["date"]), include_intraday)


def merge_series(dates: list[str], closes: list[float], extra: list[dict], as_of: Optional[date] = None,
                 highs: Optional[list[float]] = None, lows: Optional[list[float]] = None,
                 drop_after: Optional[str] = None) -> tuple:
    """Yahoo 시계열의 빈 날짜(중간 누락 포함)를 네이버 행으로 채운다. 겹치는 날짜는 Yahoo 우선.
    drop_after 이후 날짜(예: 아직 장 마감 전인 오늘)는 양쪽 모두 뺀다. 반환 (dates, closes, highs, lows, added)"""
    rows = {}
    for i, d in enumerate(dates):
        rows[d] = (closes[i], highs[i] if highs is not None else None, lows[i] if lows is not None else None)
    first = dates[0] if dates else ""
    added = []
    for r in extra:
        d = r["date"]
        if d in rows or d < first or (as_of and d > str(as_of)):
            continue
        rows[d] = (r["close"], r["high"] if r["high"] is not None else r["close"], r["low"] if r["low"] is not None else r["close"])
        added.append(d)
    keys = sorted(k for k in rows if not (drop_after and k > drop_after))
    out_d = keys
    out_c = [rows[k][0] for k in keys]
    out_h = [rows[k][1] for k in keys] if highs is not None else None
    out_l = [rows[k][2] for k in keys] if lows is not None else None
    return out_d, out_c, out_h, out_l, sorted(added)
