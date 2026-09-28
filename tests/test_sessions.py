"""
공휴일 처리 회귀 테스트 (V3.6).
대상일이 휴장일이면 "분석 실행일 이후 실제 첫 거래일"로 채점해야 한다.
예: 2026-10-02(금) 실행 → 10-05(월)은 개천절 대체공휴일 → 10-06(화)로 채점.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from data.sessions import first_session_after
from memory.store import MemoryStore, PredictionRecord
from graph.pipeline import TradingAgentsPipeline
from agents.base import MockLLMClient


def test_first_session_after_skips_holiday():
    days = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 6)]
    assert days[first_session_after(days, date(2026, 10, 2))] == date(2026, 10, 6)


def test_first_session_after_none_when_not_opened_yet():
    days = [date(2026, 10, 1), date(2026, 10, 2)]
    assert first_session_after(days, date(2026, 10, 2)) is None


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def fetch_kospi_session_after(self, after):
        self.calls.append(after)
        return {"actual_date": "2026-10-06", "change_pct": 1.2, "actual_direction": "up"}

    def fetch_stock_actual(self, name, session, after_date=None):
        assert after_date == date(2026, 10, 2)
        return {"ticker": "005930", "actual_date": str(session), "previous_close": 100,
                "actual_close": 99, "actual_return_pct": -1.0, "actual_direction": "down"}


def test_verify_pending_scores_on_real_next_session():
    memory = MemoryStore(None)
    memory.save(PredictionRecord(
        target_date="2026-10-05", run_date="2026-10-02", direction="up", confidence="보통",
        stocks=[{"name": "삼성전자", "direction": "up"}],
    ))
    provider = _FakeProvider()
    pipe = TradingAgentsPipeline(MockLLMClient(), memory, data_provider=provider)
    import graph.pipeline as gp

    from datetime import datetime
    from data.sessions import KST
    orig = gp.kst_now
    gp.kst_now = lambda: datetime(2026, 10, 7, 20, 0, tzinfo=KST)
    try:
        pipe.verify_pending()
    finally:
        gp.kst_now = orig

    rec = memory.get("2026-10-05")
    assert provider.calls == [date(2026, 10, 2)]
    assert rec["verified"] and rec["correct"] is True
    assert rec["actual_session_date"] == "2026-10-06"
    assert rec["stocks"][0]["correct"] is False



# ---------------------------------------------------------------- 대상일 결정 (V3.6.1)
from datetime import datetime
from data.sessions import decide_score_after, next_weekday_after, KST


def _t(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_before_open_targets_today():
    # 월 07:30 실행, 최근 미국장 = 금요일(9/25) → 대상 = 오늘(월 9/28)
    after = decide_score_after(_t(2026, 9, 28, 7, 30), date(2026, 9, 25))
    assert next_weekday_after(after) == date(2026, 9, 28)


def test_after_open_targets_next_day():
    after = decide_score_after(_t(2026, 9, 28, 9, 21), date(2026, 9, 25))
    assert next_weekday_after(after) == date(2026, 9, 29)


def test_weekend_run_targets_monday():
    after = decide_score_after(_t(2026, 9, 27, 15, 0), date(2026, 9, 25))  # 일요일 오후
    assert next_weekday_after(after) == date(2026, 9, 28)


def test_never_before_latest_us_session():
    # 미국장 데이터가 오늘 날짜로 찍혀 있으면(시간대 차이), 그 이후 거래일을 대상으로
    after = decide_score_after(_t(2026, 9, 29, 7, 0), date(2026, 9, 29))
    assert next_weekday_after(after) == date(2026, 9, 30)
