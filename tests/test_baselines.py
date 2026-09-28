"""베이스라인(단순 규칙) 예측과 AI 비교 통계 테스트."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from rules.baselines import baseline_predictions, score_baselines
from memory.store import MemoryStore, PredictionRecord
from agents.base import MockLLMClient
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote


def test_baseline_rules():
    idx = [{"name": "S&P500", "direction": "down"}, {"name": "나스닥", "direction": "up"},
           {"name": "다우존스", "direction": "down"}, {"name": "SOX", "direction": "up"},
           {"name": "VIX", "direction": "up"}]
    b = baseline_predictions(idx)
    assert b == {"always_up": "up", "sp500": "down", "sox": "up", "us_majority": "up", "vix_inverse": "down"}


def test_missing_indicator_skips_baseline():
    b = baseline_predictions([{"name": "SOX", "direction": "down"}])
    assert "sp500" not in b and b["sox"] == "down"


def test_score_on_same_sample():
    recs = [
        {"verified": True, "direction": "up", "actual_direction": "up",
         "us_indices": [{"name": "S&P500", "direction": "down"}]},
        {"verified": True, "direction": "down", "actual_direction": "up",
         "us_indices": [{"name": "S&P500", "direction": "up"}]},
        {"verified": False, "direction": "up", "us_indices": []},
    ]
    s = score_baselines(recs)
    assert s["ai"]["total"] == 2 and s["ai"]["correct"] == 1
    assert s["baselines"]["always_up"]["accuracy"] == 1.0
    assert s["baselines"]["sp500"]["correct"] == 1
    assert s["best_baseline"] == "always_up"


def test_stats_include_baseline_compare():
    m = MemoryStore(None)
    m.save(PredictionRecord(target_date="2026-09-29", run_date="2026-09-28", direction="up",
                            confidence="보통", verified=True, actual_direction="down", correct=False,
                            us_indices=[{"name": "S&P500", "direction": "down"}]))
    bc = m.compute_stats()["baseline_compare"]
    assert bc["ai"]["accuracy"] == 0.0
    assert bc["baselines"]["sp500"]["accuracy"] == 1.0


def test_pipeline_outcome_has_baselines_and_score_after():
    m = MemoryStore(None)
    snap = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", 0.4, "up", str(date.today())), IndexQuote("VIX", 2.0, "up", str(date.today()))])
    out = TradingAgentsPipeline(MockLLMClient(), m, debate_max_rounds=1).run(snapshot=snap)
    assert out["baselines"]["sp500"]["direction"] == "up"
    assert out["baselines"]["vix_inverse"]["direction"] == "down"
    rec = m.all_records()[0]
    assert rec["score_after_date"] and rec["run_at"]
