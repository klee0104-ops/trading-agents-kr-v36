"""
네트워크 없이 Mock LLM으로 전체 파이프라인(특히 토론 루프)이 올바르게 도는지 검증.
실제 CI에 넣고 돌릴 수 있는 테스트들.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from agents.base import MockLLMClient
from data.market_data import MarketSnapshot, IndexQuote
from memory.store import MemoryStore, next_trading_day
from graph.pipeline import TradingAgentsPipeline
from graph.debate_loop import run_debate_loop


def _mock_snapshot():
    return MarketSnapshot(
        as_of=str(date.today()),
        us_indices=[
            IndexQuote("S&P500", 0.4, "up", str(date.today())),
            IndexQuote("SOX", 1.1, "up", str(date.today())),
            IndexQuote("VIX", 2.0, "up", str(date.today())),
        ]
    )


def test_debate_loop_runs_expected_rounds():
    llm = MockLLMClient()
    analyst_report = {"summary": "test", "notable_points": []}
    transcript = run_debate_loop(llm, analyst_report, max_rounds=3, convergence_threshold=-1)
    # convergence_threshold=-1 이면 절대 조기 종료 안 하므로 3라운드 x 2턴 = 6턴이어야 한다
    assert len(transcript) == 6
    assert transcript[0]["stance"] == "bull"
    assert transcript[1]["stance"] == "bear"
    assert transcript[0]["round"] == 1
    assert transcript[-1]["round"] == 3


def test_debate_loop_early_stops_on_convergence():
    llm = MockLLMClient()
    analyst_report = {"summary": "test"}
    # Mock은 각 역할의 주장·근거·확신도를 반복하므로 2라운드 후 반복 감지로 종료한다.
    transcript = run_debate_loop(llm, analyst_report, max_rounds=5, convergence_threshold=0.1)
    assert len(transcript) == 4  # round 1(2턴) + round 2(2턴) 후 종료


def test_pipeline_end_to_end_with_mock():
    memory = MemoryStore(None)
    pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=3)
    outcome = pipeline.run(snapshot=_mock_snapshot())

    assert "kospi_prediction" in outcome["final_result"]
    assert outcome["final_result"]["kospi_prediction"]["direction"] in ("up", "down")
    assert len(outcome["final_result"]["core_stocks"]) == 2
    assert len(outcome["debate_transcript"]) > 0

    # 기록이 메모리에 저장됐는지 확인
    all_records = memory.all_records()
    assert len(all_records) == 1
    # 대상일은 실행 시각(장 시작 전/후)에 따라 달라진다 (V3.6.1) — 기록된 기준일과 일관적인지 확인
    from data.sessions import next_weekday_after
    rec = all_records[0]
    assert rec["target_date"] == str(next_weekday_after(date.fromisoformat(rec["score_after_date"])))


def test_memory_stats_and_history_context():
    memory = MemoryStore(None)
    from memory.store import PredictionRecord

    memory.save(PredictionRecord(
        target_date="2026-07-20", run_date="2026-07-17",
        direction="up", confidence="보통", verified=True,
        actual_direction="up", correct=True,
        us_indices=[{"name": "SOX", "direction": "up"}],
        stocks=[{"name": "삼성전자", "sensitivity": "high", "direction": "up",
                 "actual_direction": "up", "correct": True}],
    ))
    memory.save(PredictionRecord(
        target_date="2026-07-21", run_date="2026-07-20",
        direction="down", confidence="낮음", verified=True,
        actual_direction="up", correct=False,
        us_indices=[{"name": "SOX", "direction": "down"}],
        stocks=[{"name": "삼성전자", "sensitivity": "high", "direction": "down",
                 "actual_direction": "up", "correct": False}],
    ))

    stats = memory.compute_stats()
    assert stats["total"] == 2
    assert stats["correct"] == 1
    assert stats["by_stock"]["삼성전자"]["total"] == 2
    assert stats["by_stock"]["삼성전자"]["correct"] == 1
    assert stats["by_indicator"]["SOX"]["total"] == 2
    assert stats["by_indicator"]["SOX"]["match"] == 1  # up==up 1회만 일치

    ctx = memory.build_history_context_text()
    assert "삼성전자" in ctx
    assert "SOX" in ctx


def test_next_trading_day_skips_weekend():
    import datetime
    fri = datetime.date(2026, 7, 24)  # 금요일
    assert next_trading_day(fri) == datetime.date(2026, 7, 27)  # 다음 월요일


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_agent_predictions_are_saved_and_scored():
    memory = MemoryStore(None)
    pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=3)
    pipeline.run(snapshot=_mock_snapshot())
    rec = memory.all_records()[0]
    agents = {a["agent"]: a for a in rec["agent_predictions"]}
    assert {"MarketAnalyst", "Bull", "Bear", "RiskManager", "Trader"} <= set(agents)
    assert agents["MarketAnalyst"]["direction"] == "up"
    # Bull/Bear는 설계 변경으로 더 이상 고정 direction을 갖지 않는다 (QA 검증팀의
    # evidence_verify_rate로 대신 품질을 추적한다 — tests/test_qa_and_gemini.py 참고).
    assert agents["Bull"]["direction"] is None
    assert agents["Bear"]["direction"] is None
    assert agents["RiskManager"]["direction"] == "up"
    assert agents["Trader"]["direction"] == "up"

    target = rec["target_date"]
    memory.mark_verified(target, "up")
    stats = memory.compute_stats()
    assert stats["by_agent"]["Trader"]["accuracy"] == 1.0
    # Bull/Bear는 방향 채점 대상이 아니므로 total이 0이어야 한다
    assert stats["by_agent"]["Bull"]["total"] == 0
    assert stats["by_agent"]["Bull"]["evidence_verify_rate"] == 1.0
