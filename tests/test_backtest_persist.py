"""저장형 백테스트: 이어하기, 운영 기록 분리, 베이스라인 요약."""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from agents.base import MockLLMClient
from memory.store import MemoryStore
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote
from backtest.persist import run_backtest_persist


class FakeProvider:
    def __init__(self):
        self.snap_calls = []

    def fetch_kospi_sessions(self, start, end):
        return [{"date": date(2026, 9, 22), "change_pct": 1.0, "direction": "up"},
                {"date": date(2026, 9, 23), "change_pct": -0.5, "direction": "down"},
                {"date": date(2026, 9, 28), "change_pct": 0.8, "direction": "up"}]

    def fetch_us_snapshot(self, as_of_date=None):
        self.snap_calls.append(as_of_date)
        return MarketSnapshot(as_of=str(as_of_date), us_indices=[
            IndexQuote("S&P500", -0.3, "down", str(as_of_date)),
            IndexQuote("VIX", 3.0, "up", str(as_of_date))])


def test_persist_and_resume(tmp_path):
    path = str(tmp_path / "bt.json")
    prov = FakeProvider()
    mem = MemoryStore(None)
    pipe = TradingAgentsPipeline(MockLLMClient(), mem, backtest_mode=True, debate_max_rounds=1)
    s = run_backtest_persist(pipe, prov, date(2026, 9, 21), date(2026, 9, 28), path=path, log_path=None)
    assert s["total"] == 3 and s["correct"] == 2  # mock은 항상 up
    # 연휴 뒤 9/28은 전날(9/27)까지의 미국장으로 예측
    assert prov.snap_calls[-1] == date(2026, 9, 27)
    assert s["baseline_compare"]["sp500"]["correct"] == 1
    assert mem.all_records() == []  # 운영 기록에 섞이지 않음

    prov2 = FakeProvider()
    run_backtest_persist(pipe, prov2, date(2026, 9, 21), date(2026, 9, 28), path=path, log_path=None)
    assert prov2.snap_calls == []  # 이미 끝난 날은 다시 돌리지 않음
    assert len(json.load(open(path, encoding="utf-8"))["days"]) == 3
