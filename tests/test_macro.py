"""(V3.7) 거시 지표·외국인 수급이 규칙엔진/프롬프트/outcome에 반영되는지."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from data.market_data import MarketSnapshot, IndexQuote
from rules.engine import analyze_market, build_context_text
from agents.analysts import run_market_analyst
from agents.base import MockLLMClient
from memory.store import MemoryStore
from graph.pipeline import TradingAgentsPipeline
from data.kr_flow import describe

T = str(date.today())


def _snap(krw=0.0, tnx=0.0, oil=0.0, dxy=0.0, flow=None):
    return MarketSnapshot(as_of=T, us_indices=[IndexQuote("S&P500", 0.3, "up", T), IndexQuote("SOX", 0.4, "up", T)],
                          macro=[IndexQuote("원/달러", krw, "up" if krw >= 0 else "down", T, value=1380.0),
                                 IndexQuote("미국10년금리", tnx, "up" if tnx >= 0 else "down", T, value=4.3),
                                 IndexQuote("WTI유가", oil, "up" if oil >= 0 else "down", T, value=80.0),
                                 IndexQuote("달러인덱스", dxy, "up" if dxy >= 0 else "down", T, value=104.0)],
                          kr_flow=flow)


def test_krw_weak_raises_risk_to_medium_only():
    a = analyze_market(_snap(krw=0.9))
    assert "KRW_WEAK" in a.risk_flags and a.risk_level_hint == "보통"
    assert "원화 약세" in build_context_text(a)


def test_calm_macro_keeps_low_risk_and_summary_present():
    a = analyze_market(_snap(krw=-0.1, tnx=0.5))
    assert a.risk_level_hint == "낮음"
    assert "원/달러" in a.macro_summary and "미국10년금리" in a.macro_summary


def test_big_foreign_sell_flag_and_describe():
    flow = {"date": T, "foreign_net_krw_bn": -4200.0, "direction": "sell", "strong": True, "source": "test"}
    a = analyze_market(_snap(flow=flow))
    assert "FOREIGN_BIG_SELL" in a.risk_flags
    assert "순매도" in describe(flow) and "대규모" in describe(flow)
    assert describe(None) == "외국인 수급: 데이터 없음"


def test_analyst_prompt_includes_macro():
    seen = {}

    class Spy(MockLLMClient):
        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            seen["user"] = user
            return super().complete(system, user, max_tokens, use_web_search)
    run_market_analyst(Spy(), _snap(krw=0.2), analyze_market(_snap(krw=0.2)))
    assert "거시 지표" in seen["user"] and "원/달러" in seen["user"]


def test_pipeline_outcome_has_snapshot_macro_and_notes():
    snap = _snap(krw=0.1)
    snap.data_notes.append("WTI유가 조회 실패: TestError")
    out = TradingAgentsPipeline(MockLLMClient(), MemoryStore(None), debate_max_rounds=1).run(snapshot=snap)
    assert [m["name"] for m in out["snapshot"]["macro"]][0] == "원/달러"
    assert out["snapshot"]["data_notes"] == ["WTI유가 조회 실패: TestError"]
    assert "조회 실패" in out["market_analysis"]["macro_summary"]


def test_snapshot_without_macro_still_works():
    snap = MarketSnapshot(as_of=T, us_indices=[IndexQuote("SOX", 1.0, "up", T)])
    a = analyze_market(snap)
    assert a.macro_summary == ""
    out = TradingAgentsPipeline(MockLLMClient(), MemoryStore(None), debate_max_rounds=1).run(snapshot=snap)
    assert out["snapshot"]["macro"] == []


def test_parse_naver_trend_payload():
    from data.kr_flow import parse_naver_trend
    payload = {"bizdate": "20260928", "personalValue": "+14,009", "foreignValue": "-15,267", "institutionalValue": "-6,274"}
    r = parse_naver_trend(payload, date(2026, 9, 28))
    assert r["date"] == "2026-09-28" and r["foreign_net_krw_bn"] == -15267.0 and r["direction"] == "sell" and r["strong"]
    assert r["personal_bn"] == 14009.0
    # look-ahead 방지: as_of보다 뒤의 데이터는 쓰지 않는다
    assert parse_naver_trend(payload, date(2026, 9, 27)) is None
    assert parse_naver_trend([payload], date(2026, 9, 30))["foreign_net_krw_bn"] == -15267.0
    assert parse_naver_trend({}, date(2026, 9, 28)) is None
