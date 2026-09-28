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
DEFAULT_CUTOFF = dtime(8, 50)       # 이 시각 이전 실행 = "오늘 장" 예측 (09:00 개장 전 여유 10분)


def kst_now() -> datetime:
    return datetime.now(KST)


def next_weekday_after(d: date) -> date:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


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
