"""(V3.9) 분석가 세분화·통합·기여도 채점."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import asdict
from datetime import date
from agents.base import MockLLMClient
from agents.specialists import combine_reports, run_all_specialists, run_news_sentiment
from rules.engine import analyze_market
from memory.store import MemoryStore, PredictionRecord, AgentPrediction
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote

T = str(date.today())


def _snap():
    return MarketSnapshot(as_of=T, us_indices=[IndexQuote("S&P500", 0.4, "up", T, value=5000, rsi14=55.0),
                                               IndexQuote("SOX", 1.1, "up", T, value=4000)],
                          macro=[IndexQuote("원/달러", -0.35, "down", T, value=1372.5)])


def test_combine_majority_and_tie_rules():
    r = combine_reports({"TechnicalAnalyst": {"direction": "up", "summary": "a", "notable_points": ["p1"]},
                         "MacroFlowAnalyst": {"direction": "down", "summary": "b"},
                         "SemiconductorAnalyst": {"direction": "up", "summary": "c"},
                         "NewsSentimentAnalyst": {"direction": None, "summary": "없음"}})
    assert r["direction"] == "up" and r["confidence"] == "보통" and r["up_votes"] == 2
    assert r["notable_points"] == ["(기술적) p1"] and "[기술적] a" in r["summary"]
    tie = combine_reports({"TechnicalAnalyst": {"direction": "up"}, "SemiconductorAnalyst": {"direction": "down"}})
    assert tie["direction"] == "down" and tie["confidence"] == "낮음"  # 동률이면 반도체 우선
    unanimous = combine_reports({n: {"direction": "down"} for n in ("TechnicalAnalyst", "MacroFlowAnalyst", "SemiconductorAnalyst")})
    assert unanimous["confidence"] == "높음"
    assert combine_reports({})["direction"] is None


def test_news_analyst_skips_llm_without_titles():
    llm = MockLLMClient()
    r = run_news_sentiment(llm, _snap(), [])
    assert r["skipped"] and r["direction"] is None and llm.call_log == []


def test_run_all_specialists_uses_four_prompts():
    llm = MockLLMClient()
    spec = run_all_specialists(llm, _snap(), analyze_market(_snap()), ["[2026-09-28] HBM 수주"])
    assert set(spec["reports"]) == {"TechnicalAnalyst", "MacroFlowAnalyst", "SemiconductorAnalyst", "NewsSentimentAnalyst"}
    assert len(llm.call_log) == 4
    assert spec["combined"]["direction"] == "up" and spec["combined"]["up_votes"] == 3


def test_pipeline_records_specialists_and_contribution():
    mem = MemoryStore(None)
    out = TradingAgentsPipeline(MockLLMClient(), mem, debate_max_rounds=1).run(snapshot=_snap())
    assert "analyst_reports" in out and out["analyst_report"]["votes"]
    rec = mem.all_records()[0]
    agents = {a["agent"]: a for a in rec["agent_predictions"]}
    assert agents["MacroFlowAnalyst"]["direction"] == "down" and agents["SemiconductorAnalyst"]["direction"] == "up"
    # 실제 하락: 거시 분석가만 맞았고 트레이더(up)는 틀림 → 거시 분석가 correct_but_ignored
    mem.mark_verified(rec["target_date"], "down")
    ba = mem.compute_stats()["by_agent"]
    assert ba["MacroFlowAnalyst"]["correct_but_ignored"] == 1 and ba["MacroFlowAnalyst"]["contribution"] == 1
    assert ba["SemiconductorAnalyst"]["agree_with_trader"] == 1 and ba["SemiconductorAnalyst"]["contribution"] == 0
    assert "contribution" not in {k: v for k, v in ba["Trader"].items() if v}  # 트레이더 자신은 기여도 채점 제외


def test_wrong_but_overruled_counts():
    mem = MemoryStore(None)
    mem.save(PredictionRecord(target_date="2026-10-01", run_date="2026-09-30", direction="up", confidence="보통",
                              agent_predictions=[asdict(AgentPrediction(agent="MacroFlowAnalyst", role="x", direction="down"))]))
    mem.mark_verified("2026-10-01", "up")
    b = mem.compute_stats()["by_agent"]["MacroFlowAnalyst"]
    assert b["wrong_but_overruled"] == 1 and b["contribution"] == -1
