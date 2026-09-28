"""
agents/verification.py 검증. "검증은 코드, 판단만 LLM" 원칙에 따라
상충이 없으면 LLM을 아예 호출하지 않는지, 상충이 있으면 호출하는지를 확인한다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base import MockLLMClient
from agents.verification import code_verify, run_verification
from rules.engine import MarketAnalysis
from memory.similarity_search import SimilarCase


def _analysis(risk_level_hint="낮음", flags=None):
    return MarketAnalysis(
        regime_trend="횡보", regime_volatility="보통",
        risk_flags=flags or [], risk_level_hint=risk_level_hint, notes=[],
    )


def test_no_conflict_when_llm_risk_at_least_as_high_as_rule():
    risk_assessment = {"leaning": "up", "risk_level": "보통"}
    checks = code_verify(risk_assessment, _analysis("낮음"), [])
    assert checks["has_conflict"] is False


def test_conflict_when_llm_understates_rule_engine_risk():
    risk_assessment = {"leaning": "up", "risk_level": "낮음"}
    checks = code_verify(risk_assessment, _analysis("높음", ["VIX_HIGH_LEVEL"]), [])
    assert checks["has_conflict"] is True
    assert "VIX_HIGH_LEVEL" in checks["conflicts"][0]


def test_conflict_when_leaning_disagrees_with_dominant_similar_cases():
    risk_assessment = {"leaning": "up", "risk_level": "보통"}
    cases = [
        SimilarCase("2026-01-01", "삼성전자", -3.0, "down", [], 0.5),
        SimilarCase("2026-01-02", "삼성전자", -2.5, "down", [], 0.4),
        SimilarCase("2026-01-03", "삼성전자", -2.0, "down", [], 0.3),
    ]
    checks = code_verify(risk_assessment, _analysis("보통"), cases)
    assert checks["has_conflict"] is True


def test_run_verification_skips_llm_call_when_no_conflict():
    llm = MockLLMClient()
    risk_assessment = {"leaning": "up", "risk_level": "보통"}
    result = run_verification(llm, risk_assessment, _analysis("낮음"), [])
    assert result["verification_passed"] is True
    assert result["final_risk_level"] == "보통"
    assert len(llm.call_log) == 0  # LLM 호출이 아예 없었어야 함


def test_run_verification_calls_llm_when_conflict_exists():
    llm = MockLLMClient()
    risk_assessment = {"leaning": "up", "risk_level": "낮음"}
    result = run_verification(llm, risk_assessment, _analysis("높음", ["US_SELLOFF"]), [])
    assert len(llm.call_log) == 1
    assert "final_risk_level" in result
    assert result["code_checks"]["has_conflict"] is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
