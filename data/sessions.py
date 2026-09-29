"""
"예측 대상 거래일" 판정 헬퍼.

예전에는 대상일을 "다음 평일"로 계산해서, 대상일이 공휴일(대체공휴일·한글날·설/추석 등)이면
검증 시 그 이전의 마지막 거래일(=분석 시점에 이미 알려진 날)의 등락으로 채점하는 문제가 있었다.
이제는 "분석 실행일(run_date) 이후 실제로 처음 열린 거래일"을 시세 데이터에서 직접 찾는다.
한국 휴장일 달력을 따로 관리할 필요가 없다.
"""
from __future__ import annotations
from datetime import date
from typing import Optional


def first_session_after(index_dates: list[date], after: date) -> Optional[int]:
    """정렬된 거래일 목록에서 after보다 뒤인 첫 거래일의 위치. 없으면 None (아직 장이 안 열림)."""
    for i, d in enumerate(index_dates):
        if d > after:
            return i if i > 0 else None  # 전일 종가 비교를 위해 앞 행이 있어야 함
    return None


# ---------------------------------------------------------------- 예측 대상일 결정 (V3.6.1)
from datetime import datetime, timedelta, timezone, time as dtime

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 오프셋으로 충분 (tzdata 불필요)
DEFAULT_CUTOFF = dtime(9, 0)        # (V3.22) 이 시각(개장) 이전에 저장된 실행 = "오늘 장" 예측. PC를 늦게 켜 08:5x에 돌아도 오늘 장을 대상으로 한다


def kst_now() -> datetime:
    return datetime.now(KST)


def next_weekday_after(d: date) -> date:
    """다음 거래일(예측 대상일 표시용). (V3.29) 주말뿐 아니라 KRX 휴장일(data/krx_calendar)도 건너뛴다.
    실제 채점은 여전히 시세 데이터에서 '실제로 열린 첫 거래일'을 찾는다."""
    from data.krx_calendar import next_trading_day_after
    return next_trading_day_after(d)


def decide_score_after(now: datetime, latest_us_session: Optional[date] = None,
                       cutoff: dtime = DEFAULT_CUTOFF) -> date:
    """
    "이 날짜 이후 처음 열리는 KOSPI 거래일"을 예측 대상으로 삼는 기준일(after_date)을 정한다.

    - 장 시작 전(cutoff 이전)에 실행: 오늘 장이 아직 안 열렸으므로 after = 어제 → 대상 = 오늘(거래일이면)
    - 장 시작 이후 실행: 오늘 장은 이미 움직이고 있어 예측 의미가 없으므로 after = 오늘 → 대상 = 다음 거래일
    - 가장 최근 미국장 날짜보다 앞설 수는 없다 (그 미국장을 보고 예측하는 것이므로)
    """
    today = now.astimezone(KST).date()
    base = today - timedelta(days=1) if now.astimezone(KST).time() < cutoff else today
    if latest_us_session and latest_us_session > base:
        base = latest_us_session
    return base


def market_status(now=None) -> dict:
    """(V3.26) 국내 정규장 상태 (KST). (V3.29) KRX 휴장일 달력 반영 — 달력에 없는 해(2027~)의 평일 휴장일은 모른다.
    pre: 09:00 전, open: 09:00~15:30, closing: 15:30~15:40(종가 확정 대기), closed: 15:40 이후, weekend: 토·일."""
    now = now or kst_now()
    if now.weekday() >= 5:
        return {"code": "weekend", "label": "휴장(주말)"}
    from data.krx_calendar import holiday_name  # (V3.29) KRX 휴장일
    hn = holiday_name(now.date())
    if hn:
        return {"code": "holiday", "label": f"휴장({hn})"}
    m = now.hour * 60 + now.minute
    if m < 9 * 60:
        return {"code": "pre", "label": "장 시작 전"}
    if m < 15 * 60 + 30:
        return {"code": "open", "label": "장중"}
    if m < 15 * 60 + 40:
        return {"code": "closing", "label": "종가 확정 중"}
    return {"code": "closed", "label": "장 마감"}
