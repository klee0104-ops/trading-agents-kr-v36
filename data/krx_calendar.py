"""
(V3.29) KRX 유가증권시장 휴장일 달력.

출처: 한국거래소 휴장일 공지를 정리한 장전(jangjeon.kr/events/holidays, 2026-09-25 최종 확인) 목록과
증권사 공지(토스증권·KB증권 등)를 대조해 2026년분을 입력했다. 2027년 휴장일은 한국거래소가 보통
전년 12월에 확정 공지하므로 아직 넣지 않았다 — 달력이 없는 해는 '평일 = 거래일'로 보고 경고를 남긴다.
임시공휴일이 새로 지정되면 여기에 추가하고 tests/test_krx_calendar.py 를 갱신한다.
"""
from __future__ import annotations
from datetime import date, timedelta

KRX_HOLIDAYS: dict[date, str] = {
    date(2026, 1, 1): "신정",
    date(2026, 2, 16): "설날 연휴",
    date(2026, 2, 17): "설날",
    date(2026, 2, 18): "설날 연휴",
    date(2026, 3, 2): "삼일절 대체공휴일",
    date(2026, 5, 1): "근로자의 날",
    date(2026, 5, 5): "어린이날",
    date(2026, 5, 25): "부처님오신날 대체공휴일",
    date(2026, 6, 3): "전국동시지방선거",
    date(2026, 7, 17): "제헌절",
    date(2026, 8, 17): "광복절 대체공휴일",
    date(2026, 9, 24): "추석 연휴",
    date(2026, 9, 25): "추석",
    date(2026, 10, 5): "개천절 대체공휴일",
    date(2026, 10, 9): "한글날",
    date(2026, 12, 25): "성탄절",
    date(2026, 12, 31): "연말 휴장",
}
COVERED_YEARS = {2026}


def holiday_name(d: date) -> str | None:
    return KRX_HOLIDAYS.get(d)


def calendar_known(d: date) -> bool:
    return d.year in COVERED_YEARS


def is_trading_day(d: date) -> bool:
    """주말·등록된 휴장일이면 False. 달력이 없는 해의 평일은 True(경고는 호출부에서)."""
    return d.weekday() < 5 and d not in KRX_HOLIDAYS


def next_trading_day_after(d: date) -> date:
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return nd
