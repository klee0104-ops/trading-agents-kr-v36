"""(V3.8) 중재자: 정리·중립 신호·프롬프트 주입·기록 저장."""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from agents.base import MockLLMClient
from agents.moderator import run_moderator, normalize_moderation, build_moderation_text
from memory.store import MemoryStore
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote

T = str(date.today())
TR = [{"stance": "bull", "round": 1, "argument": "A", "evidence": ["SOX +1.1%"], "confidence": 0.6},
      {"stance": "bear", "round": 1, "argument": "B", "evidence": ["VIX +2%"], "confidence": 0.55}]


def test_moderator_runs_and_marks_unverified_evidence_in_prompt():
    seen = {}

    class Spy(MockLLMClient):
        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            seen["user"] = user
            return super().complete(system, user, max_tokens, use_web_search)
    qa = [{"stance": "bear", "round": 1, "verified_count": 0, "total_count": 1, "unverified": ["VIX +2%"]}]
    m = run_moderator(Spy(), TR, qa)
    assert "자료에 없는 근거: VIX +2%" in seen["user"]
    assert m["debate_quality"] == "contested" and m["stronger_side"] == "bull" and m["neutral_signal"] is False


def test_unresolved_gives_neutral_signal_text():
    m = normalize_moderation({"debate_quality": "unresolved", "summary": "평행선"}, TR)
    assert m["neutral_signal"] is True
    txt = build_moderation_text(m)
    assert "확신을 낮추고" in txt and "미해결" in txt
    assert "45~55" not in txt and "50 근처" not in txt


def test_repeated_turns_downgrade_resolved():
    rep = [dict(TR[0]), dict(TR[1]), dict(TR[0], round=2), dict(TR[1], round=2)]
    m = normalize_moderation({"debate_quality": "resolved", "stronger_side": "bull"}, rep)
    assert m["repeated_turns"] == 2 and m["debate_quality"] == "contested"


def test_invalid_fields_get_defaults():
    m = normalize_moderation({"debate_quality": "??", "stronger_side": "both"}, TR)
    assert m["debate_quality"] == "contested" and m["stronger_side"] == "none"
    assert build_moderation_text({}) == ""


def test_pipeline_includes_moderation_and_saves_quality():
    seen = []

    class Spy(MockLLMClient):
        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            seen.append((system[:20], user))
            return super().complete(system, user, max_tokens, use_web_search)
    mem = MemoryStore(None)
    snap = MarketSnapshot(as_of=T, us_indices=[IndexQuote("SOX", 1.1, "up", T)])
    out = TradingAgentsPipeline(Spy(), mem, debate_max_rounds=1).run(snapshot=snap)
    assert out["moderation"]["debate_quality"] == "contested"
    # 트레이더/리스크 매니저 프롬프트에 중재자 정리가 들어갔는지
    trader_prompts = [u for s_, u in seen if "[중재자 정리" in u]
    assert len(trader_prompts) >= 2
    rec = mem.all_records()[0]
    assert rec["debate_quality"] == "contested"
    mem.mark_verified(rec["target_date"], "up")
    assert mem.compute_stats()["by_debate_quality"]["contested"]["correct"] == 1
