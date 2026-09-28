"""
이번 변경사항 회귀 테스트:
1. Bull/Bear가 더 이상 고정 direction을 반환하지 않고 evidence를 반환하는지
2. QA 검증팀(하위/상위)이 파이프라인에 통합되어 정상 동작하는지
3. by_agent 통계가 Bull/Bear는 evidence_verify_rate로, 나머지는 방향 적중률로 집계되는지
4. build_llm_client가 provider 설정에 따라 올바른 클라이언트를 만드는지
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date

from agents.base import MockLLMClient, build_llm_client, AnthropicLLMClient, GeminiLLMClient
from agents.qa_verifier import verify_debate_evidence, verify_final_result, summarize_debate_qa
from graph.debate_loop import run_debate_loop
from graph.pipeline import TradingAgentsPipeline
from memory.store import MemoryStore
from data.market_data import MarketSnapshot, IndexQuote


def _mock_snapshot():
    return MarketSnapshot(
        as_of=str(date.today()),
        us_indices=[
            IndexQuote("S&P500", 0.4, "up", str(date.today())),
            IndexQuote("SOX", 1.1, "up", str(date.today())),
            IndexQuote("VIX", 2.0, "up", str(date.today())),
        ]
    )


def test_bull_bear_no_longer_output_fixed_direction():
    """Bull/Bear 응답에 더 이상 direction 필드가 없어야 한다 (방향 고정 제거)."""
    llm = MockLLMClient()
    transcript = run_debate_loop(llm, {"summary": "test"}, max_rounds=1, convergence_threshold=-1)
    for turn in transcript:
        assert "direction" not in turn, "Bull/Bear가 여전히 고정 direction을 반환하고 있습니다"
        assert "evidence" in turn, "Bull/Bear는 반드시 evidence를 인용해야 합니다"


def test_qa_debate_evidence_verification():
    llm = MockLLMClient()
    transcript = run_debate_loop(llm, {"summary": "test"}, max_rounds=1, convergence_threshold=-1)
    qa_results = verify_debate_evidence(llm, transcript, {"summary": "test"}, "")

    assert len(qa_results) == len(transcript)
    for r in qa_results:
        assert r["total_count"] > 0
        assert r["verified_count"] == r["total_count"]  # Mock은 항상 verified=True를 돌려줌


def test_qa_final_result_consistency():
    llm = MockLLMClient()
    final_result = {
        "kospi_prediction": {"direction": "up", "confidence": "보통", "reasoning": ["test"]},
        "core_stocks": [], "watch_stocks": [],
    }
    qa = verify_final_result(llm, final_result, [], {})
    assert "consistent" in qa
    assert qa["consistent"] is True


def test_summarize_debate_qa_aggregates_by_stance():
    qa_results = [
        {"stance": "bull", "verified_count": 2, "total_count": 2},
        {"stance": "bear", "verified_count": 1, "total_count": 2},
        {"stance": "bull", "verified_count": 1, "total_count": 1},
    ]
    summary = summarize_debate_qa(qa_results)
    assert summary["bull"] == {"verified": 3, "total": 3}
    assert summary["bear"] == {"verified": 1, "total": 2}


def test_pipeline_end_to_end_with_qa_and_no_fixed_direction():
    """전체 파이프라인이 QA 검증 포함해서 끝까지 돌고, Bull/Bear가
    evidence_verify_rate로 기록되는지 확인한다."""
    memory = MemoryStore(None)
    pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=2)
    outcome = pipeline.run(snapshot=_mock_snapshot())

    assert "debate_qa" in outcome
    assert "final_qa" in outcome
    assert outcome["final_qa"]["consistent"] is True

    records = memory.all_records()
    assert len(records) == 1
    agent_preds = {a["agent"]: a for a in records[0]["agent_predictions"]}

    # Bull/Bear는 direction이 없어야 하고, evidence_total/verified가 채워져야 한다
    assert agent_preds["Bull"]["direction"] is None
    assert agent_preds["Bull"]["evidence_total"] > 0
    assert agent_preds["Bull"]["evidence_verified"] == agent_preds["Bull"]["evidence_total"]
    assert agent_preds["Bear"]["direction"] is None

    # MarketAnalyst/RiskManager/Trader는 여전히 direction 기반이어야 한다
    assert agent_preds["MarketAnalyst"]["direction"] is not None
    assert agent_preds["Trader"]["direction"] is not None


def test_by_agent_stats_split_direction_vs_evidence():
    """compute_stats의 by_agent가 Bull/Bear는 evidence_verify_rate로,
    나머지는 방향 적중률로 따로 집계하는지 확인."""
    memory = MemoryStore(None)
    from memory.store import PredictionRecord, AgentPrediction
    from dataclasses import asdict

    rec = PredictionRecord(
        target_date="2026-07-27", run_date="2026-07-24",
        direction="up", confidence="보통", verified=True,
        actual_direction="up", correct=True,
        agent_predictions=[
            asdict(AgentPrediction(agent="Trader", role="final_decision",
                                     direction="up", confidence="보통")),
            asdict(AgentPrediction(agent="Bull", role="debate_bull",
                                     evidence_total=4, evidence_verified=3)),
        ],
    )
    memory.save(rec)
    memory.mark_verified("2026-07-27", "up")

    stats = memory.compute_stats()
    assert stats["by_agent"]["Trader"]["total"] == 1
    assert stats["by_agent"]["Trader"]["accuracy"] == 1.0
    assert stats["by_agent"]["Bull"]["evidence_verify_rate"] == 0.75
    assert stats["by_agent"]["Bull"]["total"] == 0  # Bull은 방향 채점 대상이 아님


def test_build_llm_client_selects_provider():
    with pytest.raises(ValueError):
        build_llm_client("anthropic", anthropic_key="")  # 키 없으면 실패해야 함

    with pytest.raises(ValueError):
        build_llm_client("gemini", gemini_key="")

    with pytest.raises(ValueError):
        build_llm_client("openai_not_supported", anthropic_key="dummy")


def test_gemini_client_requires_sdk_and_key():
    with pytest.raises(ValueError):
        GeminiLLMClient(api_key="")


def test_complete_json_retries_once_on_truncated_json():
    from agents.base import LLMClient

    class Flaky(LLMClient):
        def __init__(self):
            self.calls = 0

        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            self.calls += 1
            return '{"a": 1' if self.calls == 1 else '{"a": 1}'

    c = Flaky()
    assert c.complete_json("s", "u") == {"a": 1}
    assert c.calls == 2
