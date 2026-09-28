"""
Rule Verification 회귀 테스트.

핵심 확인 사항:
1. 명백한 충돌(리스크 높음+확신도 높음, 스키마 위반 등)은 LLM 없이도 코드로 잡히는지
2. 정상 케이스는 conflict=False로 나와서 파이프라인이 LLM 상위 검증을 건너뛰는지
3. 파이프라인 outcome에 llm_verification_triggered 플래그가 정확히 찍히는지
   (이게 실제로 토큰이 절약됐는지 확인할 수 있는 유일한 방법이라 중요하다)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from graph.rule_verification import detect_conflicts
from agents.base import MockLLMClient
from graph.pipeline import TradingAgentsPipeline
from memory.store import MemoryStore
from data.market_data import MarketSnapshot, IndexQuote


def _valid_final_result(direction="up", confidence="보통"):
    return {
        "kospi_prediction": {"direction": direction, "confidence": confidence, "reasoning": ["test"]},
        "core_stocks": [{"name": "삼성전자"}, {"name": "SK하이닉스"}],
        "watch_stocks": [{"name": f"종목{i}"} for i in range(10)],
    }


def test_no_conflict_on_normal_case():
    result = detect_conflicts(
        risk_assessment={"risk_level": "보통", "leaning": "up"},
        final_result=_valid_final_result(direction="up", confidence="보통"),
        debate_qa=[{"unverified": []}],
    )
    assert result["conflict"] is False
    assert result["reasons"] == []


def test_conflict_high_risk_high_confidence():
    result = detect_conflicts(
        risk_assessment={"risk_level": "높음", "leaning": "up"},
        final_result=_valid_final_result(direction="up", confidence="높음"),
        debate_qa=None,
    )
    assert result["conflict"] is True
    assert any("확신도" in r for r in result["reasons"])


def test_conflict_direction_reversal_under_high_risk():
    result = detect_conflicts(
        risk_assessment={"risk_level": "높음", "leaning": "down"},
        final_result=_valid_final_result(direction="up", confidence="보통"),
        debate_qa=None,
    )
    assert result["conflict"] is True
    assert any("반대" in r for r in result["reasons"])


def test_conflict_schema_violation_stock_count():
    bad_result = _valid_final_result()
    bad_result["watch_stocks"] = bad_result["watch_stocks"][:5]  # 10개여야 하는데 5개
    result = detect_conflicts(
        risk_assessment={"risk_level": "보통", "leaning": "up"},
        final_result=bad_result,
        debate_qa=None,
    )
    assert result["conflict"] is True
    assert any("watch_stocks" in r for r in result["reasons"])


def test_conflict_from_unverified_debate_evidence():
    result = detect_conflicts(
        risk_assessment={"risk_level": "낮음", "leaning": "up"},
        final_result=_valid_final_result(),
        debate_qa=[{"unverified": ["근거X"]}],
    )
    assert result["conflict"] is True
    assert any("근거 검증 실패" in r for r in result["reasons"])


def test_pipeline_skips_llm_qa_when_no_conflict():
    """Mock은 항상 정상적인 결과를 내므로, 하이브리드 검증이 LLM 호출 없이
    통과되는지(llm_verification_triggered=False) 확인한다."""
    memory = MemoryStore(None)
    pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=1)
    snapshot = MarketSnapshot(
        as_of=str(date.today()),
        us_indices=[IndexQuote("SOX", 1.1, "up", str(date.today()))],
    )
    outcome = pipeline.run(snapshot=snapshot)

    assert outcome["final_qa"]["llm_verification_triggered"] is False
    assert outcome["final_qa"]["consistent"] is True


def test_pipeline_triggers_llm_qa_when_conflict_forced():
    """리스크 매니저가 Trader와 반대 방향으로, 그것도 '높음' 리스크로 판단하도록
    Mock을 강제해서 실제로 LLM 상위 검증이 호출되는지 확인한다.
    (Mock Trader는 항상 direction="up"을 내므로, risk leaning을 "down"으로
     충돌시키는 게 가장 확실하게 rule 2를 발동시키는 방법이다.)"""
    class ConflictMockLLM(MockLLMClient):
        def _route(self, system, user):
            s = system.lower()
            if "risk" in s:
                import json
                return json.dumps({"leaning": "down", "risk_level": "높음",
                                     "recommended_confidence": "높음", "summary": "테스트"},
                                    ensure_ascii=False)
            return super()._route(system, user)

    memory = MemoryStore(None)
    pipeline = TradingAgentsPipeline(ConflictMockLLM(), memory, debate_max_rounds=1)
    snapshot = MarketSnapshot(
        as_of=str(date.today()),
        us_indices=[IndexQuote("SOX", 1.1, "up", str(date.today()))],
    )
    outcome = pipeline.run(snapshot=snapshot)

    assert outcome["final_qa"]["llm_verification_triggered"] is True
    assert len(outcome["final_qa"]["rule_conflict_reasons"]) > 0
