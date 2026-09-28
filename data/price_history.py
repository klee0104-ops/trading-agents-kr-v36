"""
종목별 가격 이력 조회 + 변동성 이벤트 탐지.

FinanceDataReader 사용 (무료, API 키 불필요). 종목명 -> 코드 매핑은 KRX 전체
종목 리스트를 최초 1회만 받아 로컬에 캐시해두고 재사용한다.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta

_TICKER_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "ticker_map_cache.json")

# KRX 공식 상장명과 LLM/일반적으로 통용되는 한글 종목명이 다른 경우의 별칭 매핑.
# resolve_code()가 정확히 일치하는 문자열만 찾다 보니, "네이버"(공식명 NAVER),
# "포스코"(공식명 POSCO홀딩스) 같은 아주 흔한 이름조차 조용히 실패해서
# 그 종목의 예측이 검증 통계에서 영구히 빠지는 문제가 있었다. 실제 KRX 데이터에서
# 확인된 대표적인 불일치 건들을 여기 등록해서 우선 매칭한다.
_NAME_ALIASES = {
    "네이버": "NAVER",
    "포스코": "POSCO홀딩스",
    "포스코홀딩스": "POSCO홀딩스",
    "LG에너지": "LG에너지솔루션",
    "삼성바이오": "삼성바이오로직스",
    "SK바사": "SK바이오사이언스",
}


def _load_ticker_map(force_refresh: bool = False) -> dict:
    if not force_refresh and os.path.exists(_TICKER_CACHE_PATH):
        with open(_TICKER_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    import FinanceDataReader as fdr
    listing = fdr.StockListing("KRX")
    mapping = dict(zip(listing["Name"], listing["Code"]))
    with open(_TICKER_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    return mapping


def resolve_code(name_or_code: str) -> str:
    """종목명이면 코드로 변환, 이미 6자리 숫자 코드면 그대로 반환.

    조회 순서: (1) 캐시에서 정확히 일치 -> (2) 별칭 매핑 -> (3) 그래도 없으면
    캐시가 오래되어 신규 상장/개명 종목을 놓쳤을 수 있으니 한 번만 강제로
    새로 받아 재시도 -> (4) 그래도 없으면 실패.
    """
    name_or_code = name_or_code.strip()
    if name_or_code.isdigit() and len(name_or_code) == 6:
        return name_or_code

    mapping = _load_ticker_map()
    code = mapping.get(name_or_code)
    if code:
        return code

    alias = _NAME_ALIASES.get(name_or_code)
    if alias:
        code = mapping.get(alias)
        if code:
            return code

    # 캐시가 오래돼서 못 찾았을 가능성 -> 한 번만 새로 받아서 재시도.
    # (매번 새로고침하면 FinanceDataReader에 매 호출마다 부담을 주므로, 실패했을 때만.)
    try:
        fresh_mapping = _load_ticker_map(force_refresh=True)
    except Exception:
        fresh_mapping = {}
    code = fresh_mapping.get(name_or_code) or fresh_mapping.get(alias or "")
    if code:
        return code

    raise ValueError(f"종목명을 찾을 수 없습니다: {name_or_code}")


@dataclass
class PriceBar:
    date: str
    close: float
    change_pct: float  # 전일 대비 등락률(%)


@dataclass
class VolatilityEvent:
    stock_name: str
    stock_code: str
    event_date: str
    change_pct: float
    direction: str  # "up" | "down"


def fetch_price_history(name_or_code: str, start: date, end: date) -> list[PriceBar]:
    import FinanceDataReader as fdr
    code = resolve_code(name_or_code)
    df = fdr.DataReader(code, start, end)
    if df.empty:
        return []
    return [
        PriceBar(
            date=str(idx.date()),
            close=float(row["Close"]),
            change_pct=round(float(row["Change"]) * 100, 2),
        )
        for idx, row in df.iterrows()
    ]


def detect_volatility_events(stock_name: str, start: date, end: date,
                              threshold_pct: float = 3.0) -> list[VolatilityEvent]:
    """|등락률| >= threshold_pct 인 날짜를 변동성 이벤트로 탐지한다."""
    code = resolve_code(stock_name)
    bars = fetch_price_history(stock_name, start, end)
    return [
        VolatilityEvent(
            stock_name=stock_name,
            stock_code=code,
            event_date=bar.date,
            change_pct=bar.change_pct,
            direction="up" if bar.change_pct >= 0 else "down",
        )
        for bar in bars
        if abs(bar.change_pct) >= threshold_pct
    ]


def fetch_actual_stock_direction(name_or_code: str, target_date: date,
                                  after_date: date | None = None) -> dict:
    """예측 대상일의 실제 종목 등락을 조회한다.

    반환:
      {
        "name_or_code": ...,
        "ticker": "005930",
        "actual_date": "YYYY-MM-DD",
        "previous_close": ...,
        "actual_close": ...,
        "actual_return_pct": ...,
        "actual_direction": "up" | "down"
      }

    target_date가 휴장일이면 target_date 이전의 가장 최근 거래일을 사용한다.
    """
    import FinanceDataReader as fdr

    code = resolve_code(name_or_code)

    if after_date is not None:
        # (V3.6) 분석 실행일 이후 "실제로 처음 열린 거래일" 기준 — 공휴일 자동 처리
        from data.sessions import first_session_after
        df = fdr.DataReader(code, after_date - timedelta(days=14), after_date + timedelta(days=20))
        if df.empty:
            raise ValueError(f"{name_or_code}({code})의 {after_date} 전후 가격 데이터가 없습니다")
        dates = [d.date() for d in df.index]
        i = first_session_after(dates, after_date)
        if i is None:
            raise ValueError(f"{name_or_code}({code})의 {after_date} 이후 거래일 데이터가 아직 없습니다")
        prev = float(df.iloc[i - 1]["Close"])
        close = float(df.iloc[i]["Close"])
        actual_date = str(dates[i])
    else:
        # 전일 종가 비교를 위해 target_date보다 충분히 앞선 기간부터 조회한다.
        start = target_date - timedelta(days=14)
        df = fdr.DataReader(code, start, target_date)
        if df.empty or len(df) < 2:
            raise ValueError(f"{name_or_code}({code})의 {target_date} 기준 가격 데이터가 부족합니다")
        # target_date 이전/당일의 마지막 거래일을 사용
        df = df[df.index.date <= target_date]
        if len(df) < 2:
            raise ValueError(f"{name_or_code}({code})의 검증 가능한 거래일 데이터가 부족합니다")
        prev = float(df.iloc[-2]["Close"])
        close = float(df.iloc[-1]["Close"])
        actual_date = str(df.index[-1].date())

    ret = (close / prev - 1) * 100

    return {
        "name_or_code": str(name_or_code),
        "ticker": code,
        "actual_date": actual_date,
        "previous_close": round(prev, 4),
        "actual_close": round(close, 4),
        "actual_return_pct": round(ret, 4),
        "actual_direction": "up" if ret >= 0 else "down",
    }
