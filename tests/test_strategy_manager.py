"""
memory/strategy_manager.py 검증 (V3.6 병합본: 근거검증률 기반).
표본이 적으면 중립 가중치를 쓰고, 근거검증률이 높을수록/낮을수록 가중치가
기대한 방향으로 움직이는지 확인한다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import asdict
from memory.store import MemoryStore, PredictionRecord, AgentPrediction
from memory.strategy_manager import compute_weights, build_weight_context_text, BASE_WEIGHT


def _memory_with(bull=(0, 0), bear=(0, 0), n_records=1, verified=False) -> MemoryStore:
    """bull=(verified, total), bear=(verified, total) 를 n_records개 기록에 걸쳐 저장."""
    memory = MemoryStore(None)
    for i in range(n_records):
        memory.save(PredictionRecord(
            target_date=f"2026-01-{i + 1:02d}", run_date=f"2026-01-{i + 1:02d}",
            direction="up", confidence="보통", verified=verified,
            agent_predictions=[
                asdict(AgentPrediction(agent="Bull", role="debate_bull",
                                        evidence_verified=bull[0], evidence_total=bull[1])),
                asdict(AgentPrediction(agent="Bear", role="debate_bear",
                                        evidence_verified=bear[0], evidence_total=bear[1])),
            ],
        ))
    return memory


def test_insufficient_sample_uses_neutral_weight():
    memory = _memory_with(bull=(2, 2), bear=(1, 2))  # 표본 2건 < MIN_SAMPLE(5)
    weights = compute_weights(memory)
    assert weights["bull"]["weight"] == BASE_WEIGHT
    assert weights["bull"]["evidence_verify_rate"] is None


def test_weight_follows_evidence_verify_rate():
    # Bull은 근거를 전부 사실대로 인용, Bear는 절반 이하만 확인됨
    memory = _memory_with(bull=(3, 3), bear=(1, 3), n_records=3)
    weights = compute_weights(memory)
    assert weights["bull"]["evidence_verify_rate"] == 1.0
    assert round(weights["bear"]["evidence_verify_rate"], 3) == 0.333
    assert weights["bull"]["weight"] > BASE_WEIGHT
    assert weights["bear"]["weight"] < BASE_WEIGHT


def test_evidence_counts_even_before_kospi_verification():
    """근거검증은 실행 즉시 확정되므로, KOSPI 결과 검증 전 기록도 가중치에 반영돼야 한다."""
    memory = _memory_with(bull=(2, 2), bear=(2, 2), n_records=3, verified=False)
    weights = compute_weights(memory)
    assert weights["bull"]["sample_size"] == 6


def test_market_base_rate_no_longer_drives_weights():
    """V3.5 브랜치의 '실제 상승 비율=Bull 적중률' 지표가 다시 섞이지 않았는지 확인."""
    memory = MemoryStore(None)
    for i in range(8):
        memory.save(PredictionRecord(
            target_date=f"2026-03-{i + 1:02d}", run_date=f"2026-03-{i + 1:02d}",
            direction="up", confidence="보통", verified=True,
            actual_direction="up", correct=True,
        ))
    weights = compute_weights(memory)
    assert weights["bull"]["weight"] == BASE_WEIGHT
    assert weights["bear"]["weight"] == BASE_WEIGHT


def test_weight_context_text_empty_without_data():
    assert build_weight_context_text(compute_weights(MemoryStore(None))) == ""


def test_weight_context_text_present_with_enough_data():
    memory = _memory_with(bull=(3, 3), bear=(2, 3), n_records=2)
    text = build_weight_context_text(compute_weights(memory))
    assert "Bull" in text and "Bear" in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
