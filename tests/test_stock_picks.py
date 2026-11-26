"""(V3.11) 관심 종목 추첨 강제, 보합 신호, 종목 중재자 조정, 보합 채점."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from agents.base import MockLLMClient
from agents.trader import align_watch_stocks
from agents.stock_moderator import apply_reviews
from memory.store import MemoryStore, PredictionRecord, stock_is_correct
from graph.pipeline import TradingAgentsPipeline
from data.market_data import MarketSnapshot, IndexQuote

T = str(date.today())


def test_align_forces_todays_list_and_flat_default():
    r = {"watch_stocks": [{"name": "현대차", "directional_signal": "up", "reason": "x"},
                          {"name": "엉뚱한종목", "directional_signal": "down"},
                          {"name": "기아", "directional_signal": "sideways"}]}
    out = align_watch_stocks(r, ["현대차", "기아", "NAVER"])
    assert [s["name"] for s in out["watch_stocks"]] == ["현대차", "기아", "NAVER"]
    assert out["watch_stocks"][1]["directional_signal"] == "flat"
    assert out["watch_stocks"][2]["directional_signal"] == "flat" and "판단 보류" in out["watch_stocks"][2]["reason"]


def test_apply_reviews_only_keep_or_flat():
    fr = {"core_stocks": [{"name": "삼성전자", "directional_signal": "up"}],
          "watch_stocks": [{"name": "현대차", "directional_signal": "down"}, {"name": "기아", "directional_signal": "up"}]}
    out = apply_reviews(fr, {"reviews": [{"name": "현대차", "action": "flat", "reason": "근거 없음"},
                                         {"name": "기아", "action": "down", "reason": "뒤집기 시도"}]})
    hd = out["watch_stocks"][0]
    assert hd["directional_signal"] == "flat" and hd["moderated"] and hd["original_signal"] == "down"
    assert out["watch_stocks"][1]["directional_signal"] == "up" and not out["watch_stocks"][1]["moderated"]
    assert out["stock_moderation"] == {"reviewed": 2, "matched": 2, "adjusted_to_flat": 1}  # (V3.31) 매칭 수 추가


def test_flat_scoring_threshold():
    assert stock_is_correct({"direction": "flat", "actual_return_pct": 0.3}) is True
    assert stock_is_correct({"direction": "flat", "actual_return_pct": -1.2}) is False
    assert stock_is_correct({"direction": "up", "actual_return_pct": 1.2, "actual_direction": "up"}) is True
    assert stock_is_correct({"direction": "flat"}) is None


def test_pipeline_records_watch_picks_and_moderation():
    mem = MemoryStore(None)
    snap = MarketSnapshot(as_of=T, us_indices=[IndexQuote("SOX", 1.1, "up", T), IndexQuote("S&P500", 0.4, "up", T)])
    out = TradingAgentsPipeline(MockLLMClient(), mem, debate_max_rounds=1).run(snapshot=snap)
    names = [s["name"] for s in out["final_result"]["watch_stocks"]]
    assert names == ["현대차", "기아"]  # 데모 유니버스 그대로
    # mock 종목 중재자는 3번째 종목(=관심 1번째, 현대차)을 보합으로 조정
    hd = out["final_result"]["watch_stocks"][0]
    assert hd["directional_signal"] == "flat" and hd["moderated"] and hd["original_signal"] == "up"
    assert out["final_result"]["stock_moderation"]["adjusted_to_flat"] == 1
    assert out["final_qa"]["llm_verification_triggered"] is False  # 종목 수는 오늘 목록 기준
    rec = mem.all_records()[0]
    st = {s["name"]: s for s in rec["stocks"]}
    assert st["현대차"]["moderated"] and st["현대차"]["original_direction"] == "up" and st["삼성전자"]["is_core"]
    mem.mark_verified(rec["target_date"], "up", stock_actuals={
        "현대차": {"actual_direction": "up", "actual_return_pct": 0.2},
        "기아": {"actual_direction": "down", "actual_return_pct": -1.5}})
    sm = mem.compute_stats()["stock_moderation"]
    assert sm["adjusted"] == 1 and sm["flat_correct"] == 1 and sm["original_would_be_correct"] == 1
    assert sm["watch_total"] == 2 and sm["watch_correct"] == 1
