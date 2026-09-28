"""확률 → 방향/5단계/신뢰도 도출 및 보정(calibration) 채점 테스트 (V3.6.3)."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from rules.outlook import derive, calibration, clamp_probability
from agents.trader import normalize_prediction
from agents.base import MockLLMClient
from memory.store import MemoryStore, PredictionRecord
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote


def test_derive_levels():
    assert derive(80) == {"direction": "up", "outlook": "strong_up", "outlook_label": "강한 상승", "confidence": "높음"}
    assert derive(62)["outlook"] == "mild_up" and derive(62)["confidence"] == "보통"
    assert derive(50)["outlook"] == "flat" and derive(50)["confidence"] == "낮음" and derive(50)["direction"] == "up"
    assert derive(49)["direction"] == "down"
    assert derive(35)["outlook"] == "mild_down"
    assert derive(10)["outlook"] == "strong_down" and derive(10)["confidence"] == "높음"
    assert derive(None) == {}


def test_clamp_accepts_fraction_and_string():
    assert clamp_probability("0.62") == 62.0
    assert clamp_probability(130) == 100.0
    assert clamp_probability("abc") is None


def test_normalize_prediction_fills_fields_and_legacy():
    r = normalize_prediction({"kospi_prediction": {"up_probability": 72, "reasoning": []}})
    p = r["kospi_prediction"]
    assert p["direction"] == "up" and p["confidence"] == "높음" and p["outlook"] == "strong_up"
    legacy = normalize_prediction({"kospi_prediction": {"direction": "down", "confidence": "높음"}})
    assert legacy["kospi_prediction"]["up_probability"] == 20 and legacy["kospi_prediction"]["probability_imputed"]


def test_calibration_bins_and_brier():
    rows = [{"up_probability": 80, "actual": "up"}, {"up_probability": 75, "actual": "down"},
            {"up_probability": 20, "actual": "down"}, {"up_probability": 50, "actual": "up"}]
    c = calibration(rows)
    hi = next(b for b in c["bins"] if b["range"] == "70~100%")
    assert hi["n"] == 2 and hi["actual_up_rate"] == 50.0 and hi["avg_prob"] == 77.5
    assert 0 < c["brier"] < 0.25


def test_pipeline_saves_probability_and_stats_have_calibration():
    m = MemoryStore(None)
    snap = MarketSnapshot(as_of=str(date.today()), us_indices=[IndexQuote("SOX", 1.1, "up", str(date.today()))])
    out = TradingAgentsPipeline(MockLLMClient(), m, debate_max_rounds=1).run(snapshot=snap)
    assert out["final_result"]["kospi_prediction"]["up_probability"] == 64
    rec = m.all_records()[0]
    assert rec["up_probability"] == 64 and rec["outlook"] == "mild_up" and rec["confidence"] == "보통"
    m.mark_verified(rec["target_date"], "up")
    st = m.compute_stats()
    assert st["calibration"]["n"] == 1 and st["by_outlook"]["mild_up"]["correct"] == 1
