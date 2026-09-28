"""
(V3.7) 외국인 수급 — KOSPI 외국인 순매수(억원).

출처 우선순위:
1. 네이버 모바일 증권 API `/api/index/KOSPI/trend` — 최신 거래일의 개인/외국인/기관 순매수(억원). 계정·키 불필요.
   (예전 finance.naver.com 투자자별 매매동향 페이지는 2026년 서비스 종료됨. 과거치는 제공하지 않아 백테스트에선 비움)
2. pykrx (설치되어 있고 KRX 로그인 환경변수가 있을 때만 — 2026년 현재 KRX가 로그인을 요구함)

as_of 기준 "가장 최근 거래일"의 값을 쓴다. 백테스트에서는 as_of 이후 데이터를 절대 쓰지 않는다.
실패하면 None (파이프라인은 계속 돈다).
"""
from __future__ import annotations
import re
from datetime import date, timedelta
from typing import Optional

STRONG_FLOW_BN = 3000.0  # 억원. 이 이상이면 "대규모"로 표시
_NAVER_TREND_URL = "https://m.stock.naver.com/api/index/KOSPI/trend"  # 최신 거래일 투자자별 순매수 (억원)
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}


def _to_num(text) -> Optional[float]:
    t = re.sub(r"[^\d\-\.]", "", str(text or ""))
    try:
        return float(t) if t not in ("", "-", ".") else None
    except ValueError:
        return None


def parse_naver_trend(payload, as_of: date) -> Optional[dict]:
    """
    {"bizdate":"20260928","personalValue":"+14,009","foreignValue":"-15,267","institutionalValue":"-6,274"}
    형태(억원). bizdate가 as_of보다 뒤면(look-ahead) None.
    """
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None
    bd = str(payload.get("bizdate") or "")
    if len(bd) != 8:
        return None
    row_date = date(int(bd[:4]), int(bd[4:6]), int(bd[6:]))
    if row_date > as_of:
        return None
    val = _to_num(payload.get("foreignValue"))
    if val is None:
        return None
    return {"date": str(row_date), "foreign_net_krw_bn": round(val, 1),
            "direction": "buy" if val >= 0 else "sell", "strong": abs(val) >= STRONG_FLOW_BN,
            "source": "naver", "personal_bn": _to_num(payload.get("personalValue")),
            "institution_bn": _to_num(payload.get("institutionalValue"))}


def fetch_foreign_net_buy_naver(as_of: date) -> Optional[dict]:
    import requests
    resp = requests.get(_NAVER_TREND_URL, params={"pageSize": 1, "page": 1}, headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return parse_naver_trend(resp.json(), as_of)


def fetch_foreign_net_buy_pykrx(as_of: date) -> Optional[dict]:
    try:
        from pykrx import stock
    except ImportError:
        return None
    start = (as_of - timedelta(days=10)).strftime("%Y%m%d")
    df = stock.get_market_trading_value_by_date(start, as_of.strftime("%Y%m%d"), "KOSPI")
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if "외국인" in str(c)), None)
    if col is None:
        return None
    df = df[df.index.date <= as_of]
    if df.empty:
        return None
    bn = round(float(df.iloc[-1][col]) / 1e8, 1)
    return {"date": str(df.index[-1].date()), "foreign_net_krw_bn": bn,
            "direction": "buy" if bn >= 0 else "sell", "strong": abs(bn) >= STRONG_FLOW_BN, "source": "pykrx"}


def fetch_foreign_net_buy(as_of: date) -> Optional[dict]:
    try:
        r = fetch_foreign_net_buy_naver(as_of)
        if r:
            return r
    except Exception:  # noqa: BLE001
        pass
    import os
    if os.environ.get("KRX_ID"):
        try:
            return fetch_foreign_net_buy_pykrx(as_of)
        except Exception:  # noqa: BLE001
            return None
    return None


def describe(flow: Optional[dict]) -> str:
    if not flow:
        return "외국인 수급: 데이터 없음"
    side = "순매수" if flow["direction"] == "buy" else "순매도"
    return f"외국인 {side} {abs(flow['foreign_net_krw_bn']):,.0f}억원 ({flow['date']})" + (" — 대규모" if flow.get("strong") else "")
