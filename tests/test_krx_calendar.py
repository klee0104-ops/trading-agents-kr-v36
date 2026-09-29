"""(V3.29) KRX 휴장일 달력: 대상일·장 상태·아침 분석 건너뛰기."""
from __future__ import annotations
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from data.krx_calendar import KRX_HOLIDAYS, is_trading_day, next_trading_day_after, calendar_known, holiday_name
from data.sessions import KST, market_status, next_weekday_after

ROOT = Path(__file__).resolve().parents[1]


def test_2026_remaining_holidays_present():
    for d in (date(2026, 10, 5), date(2026, 10, 9), date(2026, 12, 25), date(2026, 12, 31)):
        assert d in KRX_HOLIDAYS and not is_trading_day(d)
    assert len([d for d in KRX_HOLIDAYS if d.year == 2026]) == 17
    assert all(d.weekday() < 5 for d in KRX_HOLIDAYS)  # 주말은 달력에 넣을 필요 없음


@pytest.mark.parametrize("after,expected", [
    (date(2026, 10, 2), date(2026, 10, 6)),    # 금 → 토·일·월(개천절 대체) 건너뛰고 화
    (date(2026, 10, 8), date(2026, 10, 12)),   # 목 → 한글날(금)·주말 건너뛰고 월
    (date(2026, 12, 24), date(2026, 12, 28)),  # 성탄절
    (date(2026, 12, 30), date(2027, 1, 1)),    # 연말 휴장 건너뜀. 2027 달력 미확정 → 1/1(금)을 거래일로 봄 — 달력 추가 시 갱신
])
def test_target_day_skips_holidays(after, expected):
    assert next_weekday_after(after) == expected == next_trading_day_after(after)


def test_market_status_holiday():
    s = market_status(datetime(2026, 10, 9, 10, 0, tzinfo=KST))
    assert s["code"] == "holiday" and "한글날" in s["label"]
    assert market_status(datetime(2026, 10, 6, 10, 0, tzinfo=KST))["code"] == "open"


def test_calendar_coverage_flag():
    assert calendar_known(date(2026, 11, 1)) and not calendar_known(date(2027, 1, 4))
    assert holiday_name(date(2026, 10, 5)) == "개천절 대체공휴일"


@pytest.mark.parametrize("when,code", [(datetime(2026, 10, 5, 7, 30, tzinfo=KST), 2),
                                       (datetime(2026, 10, 6, 7, 30, tzinfo=KST), 0),
                                       (datetime(2026, 10, 3, 7, 30, tzinfo=KST), 2)])
def test_cli_market_day_exit_code(monkeypatch, when, code):
    import cli
    import data.sessions as ss
    monkeypatch.setattr(ss, "kst_now", lambda: when)
    assert cli.cmd_market_day() == code


def test_morning_batch_skips_on_holiday_before_any_ai_call():
    text = (ROOT / "자동분석_실행.bat").read_bytes().decode("cp949")
    assert '-B -m tools.morning_run' in text
    from tools.morning_run import orchestrate
    from datetime import datetime
    from data.sessions import KST
    from data.krx_calendar import is_trading_day
    calls=[];states=[]
    result=orchestrate(lambda:datetime(2026,9,26,7,30,tzinfo=KST),
                       lambda step:calls.append(step) or 0,lambda _:None,
                       is_trading_day,states.append,lambda:None)
    assert result==0 and calls==[] and states[-1]['status']=='skipped_holiday'



def test_citation_trial_runs_offline_without_writing_records(monkeypatch):
    """출처 실전 시험 도구: Mock LLM·가짜 시세로 돌려도 통계가 나오고, 기록 DB는 만들지 않는다(MemoryStore(None))."""
    monkeypatch.setenv("TA_NO_NEWS", "1")
    from agents.base import MockLLMClient
    from memory.store import MemoryStore
    from graph.pipeline import TradingAgentsPipeline
    from tests.test_pipeline_quant import _HistProvider, _snap
    from tools.citation_trial import run_trial

    class P(_HistProvider):
        def fetch_us_snapshot(self, as_of_date=None):
            return _snap()
    llm = MockLLMClient()
    mem = MemoryStore(None)
    out = run_trial(llm, TradingAgentsPipeline(llm, mem, data_provider=P(), debate_max_rounds=1))
    # 가짜 시세에 환율이 없어 거시 분석가의 "원/달러" 문장은 근거 번호가 없어 폐기돼야 한다 (나머지는 통과)
    assert out["analyst_points"]["total"] == 3 and out["analyst_points"]["valid"] == 2
    assert out["reject_reasons"] == {"출처 ID 없음": 1} and out["analysts"]["거시·수급"]["citation_ok"] is False
    assert out["debate_evidence"]["citation_rejected"] == 0 and out["debate_evidence"]["total"] >= 2
    assert out["llm_calls"] <= 8 and mem.all_records() == []
    assert "news_titles" in out and isinstance(out["news_titles"], int)  # 제목 원문은 저장하지 않음
